"""CLI wiring tests, using typer's CliRunner. Settings/Snowflake are
monkeypatched -- never a real settings.toml, Keychain entry or connection.
"""

import json

import duckdb
import pyarrow as pa
import pytest
from typer.testing import CliRunner

from dq import cli
from dq.engine import ExecutionResult
from dq.settings import PathSettings, Settings, SnowflakeSettings

runner = CliRunner()


class FakeExecutor:
    def __init__(self):
        self.rule_tables: dict[str, pa.Table] = {}

    def execute_rule(self, rule_code, sql, *, query_tag, timeout_seconds):
        return ExecutionResult(table=self.rule_tables[rule_code], query_id="fake", duration_ms=1)

    def count_object(self, target_object, sql, *, query_tag):
        raise NotImplementedError


def make_table(rows: list[tuple[str, bool]]) -> pa.Table:
    record_ids = [r[0] for r in rows]
    is_valid = [r[1] for r in rows]
    n = len(record_ids)
    return pa.table(
        {
            "RECORD_ID": pa.array(record_ids, type=pa.string()),
            "ITEM_KEY": pa.array([None] * n, type=pa.string()),
            "RECORD_NAME": pa.array(record_ids, type=pa.string()),
            "SITE_ID": pa.array(record_ids, type=pa.string()),
            "SITE_NAME": pa.array(record_ids, type=pa.string()),
            "IS_VALID": pa.array(is_valid, type=pa.bool_()),
            "DETAIL": pa.array([None] * n, type=pa.string()),
        }
    )


def insert_rule(
    store_path, *, rule_code: str, check_sql: str | None = None, status: str = "DRAFT"
) -> None:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (
                RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CHECK_SQL, CREATED_BY
            ) VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'SITE', 'VALIDITY', 'HIGH',
                      'owner@connexa.co.nz', ?, ?, 'test')
            """,
            [rule_code, f"{rule_code} test rule", status, check_sql],
        )
    finally:
        con.close()


@pytest.fixture
def fake_settings(store_path, fixtures_dir):
    return Settings(
        snowflake=SnowflakeSettings(
            account="CONNEXA-DATA",
            user="DQ_POC_SVC",
            role="DQ_POC_READER",
            warehouse="DQ_POC_WH",
            keychain_service="dq-snowflake-key",
            keychain_account="dq_poc",
        ),
        paths=PathSettings(
            duckdb=store_path,
            sources=fixtures_dir / "sources_test.yml",
            export="/tmp/export",
            backups="/tmp/backups",
        ),
    )


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, fake_settings):
    monkeypatch.setattr(cli, "load_settings", lambda: fake_settings)


# --- dq rule list / show / clarify (no Snowflake needed) -----------------------


def test_rule_list_filters_by_status(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="DRAFT")
    insert_rule(store_path, rule_code="SITE-901", status="ACTIVE")

    result = runner.invoke(cli.app, ["rule", "list", "--status", "ACTIVE"])

    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert [r["RULE_CODE"] for r in rows] == ["SITE-901"]


def test_rule_show_known_code(store_path):
    insert_rule(store_path, rule_code="SITE-900")

    result = runner.invoke(cli.app, ["rule", "show", "SITE-900"])

    assert result.exit_code == 0
    assert json.loads(result.output)["RULE_CODE"] == "SITE-900"


def test_rule_show_unknown_code_exits_nonzero(store_path):
    result = runner.invoke(cli.app, ["rule", "show", "NOPE-999"])

    assert result.exit_code == 1
    assert "error" in json.loads(result.output)


def test_rule_clarify(store_path):
    insert_rule(store_path, rule_code="SITE-900")

    result = runner.invoke(
        cli.app, ["rule", "clarify", "SITE-900", "--question", "What is active?"]
    )

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["STATUS"] == "NEEDS_CLARIFICATION"
    assert body["CLARIFICATION_QUESTION"] == "What is active?"


# --- dq catalogue show (no Snowflake needed) -----------------------------------


def test_catalogue_show(store_path):
    from dq.store.duckdb_store import DuckDBStore

    DuckDBStore(store_path).upsert_source_columns(
        "SITE",
        [{"column_name": "ID", "data_type": "VARCHAR", "ordinal_position": 1}],
        __import__("datetime").datetime.now(),
    )

    result = runner.invoke(cli.app, ["catalogue", "show", "SITE"])

    assert result.exit_code == 0
    rows = json.loads(result.output)
    assert rows[0]["COLUMN_NAME"] == "ID"


# --- dq validate / dq rule set-sql (need a fake Snowflake executor) -----------


@pytest.fixture(autouse=True)
def patch_executor(monkeypatch):
    executor = FakeExecutor()
    monkeypatch.setattr(cli, "_make_executor", lambda: executor)
    return executor


def test_validate_success(store_path, patch_executor):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT s.ID FROM {{ src.SITE }} s")
    patch_executor.rule_tables["SITE-900"] = make_table([("r1", True)])

    result = runner.invoke(cli.app, ["validate", "SITE-900"])

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["ok"] is True
    assert body["records_evaluated"] == 1


def test_validate_failure_exits_nonzero(store_path, patch_executor):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT s.ID FROM {{ src.SITE }} s")
    patch_executor.rule_tables["SITE-900"] = make_table([(None, True)])  # null RECORD_ID

    result = runner.invoke(cli.app, ["validate", "SITE-900"])

    assert result.exit_code == 1
    body = json.loads(result.output)
    assert body["ok"] is False


def test_rule_set_sql_success(store_path, patch_executor, tmp_path):
    insert_rule(store_path, rule_code="SITE-900")
    patch_executor.rule_tables["SITE-900"] = make_table([("r1", True)])
    sql_path = tmp_path / "SITE-900.sql"
    sql_path.write_text("SELECT s.ID FROM {{ src.SITE }} s")

    result = runner.invoke(cli.app, ["rule", "set-sql", "SITE-900", "--file", str(sql_path)])

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["ok"] is True
    assert body["rule"]["STATUS"] == "COMPILED"


def test_rule_set_sql_lint_failure_exits_nonzero(store_path, patch_executor, tmp_path):
    insert_rule(store_path, rule_code="SITE-900")
    sql_path = tmp_path / "SITE-900.sql"
    sql_path.write_text("DROP TABLE {{ src.SITE }}")

    result = runner.invoke(cli.app, ["rule", "set-sql", "SITE-900", "--file", str(sql_path)])

    assert result.exit_code == 1
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["stage"] == "lint"
