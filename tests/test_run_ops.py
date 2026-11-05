"""Tests for run_ops.run_checks_now -- the in-app "Run checks now" (brief
§10.6 page 5): same engine as `dq run --manual`, against a FakeExecutor and
a temp DuckDB store, never Snowflake."""

from datetime import UTC, datetime, timedelta

import duckdb
import pyarrow as pa
import pytest

from dq import run_ops
from dq.engine import EngineConfig, ExecutionResult
from dq.sources import load_sources
from dq.store.duckdb_store import DuckDBStore

CONFIG = EngineConfig(sf_base_url="https://connexa.lightning.force.com")
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class FakeExecutor:
    def __init__(self) -> None:
        self.rule_tables: dict[str, pa.Table] = {}
        self.scalar_error: Exception | None = None
        self.scalars: dict[str, object] = {}

    def execute_rule(self, rule_code, sql, *, query_tag, timeout_seconds):
        return ExecutionResult(table=self.rule_tables[rule_code], query_id="fake", duration_ms=1)

    def count_object(self, target_object, sql, *, query_tag):
        return 2

    def scalar(self, label, sql, *, query_tag):
        if self.scalar_error:
            raise self.scalar_error
        return self.scalars.get(label)


class FakeNarrator:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, model, prompt, *, query_tag):
        self.calls += 1
        return "Narrative text."


def make_table(rows: list[tuple[str, bool]]) -> pa.Table:
    n = len(rows)
    return pa.table(
        {
            "RECORD_ID": pa.array([r[0] for r in rows], type=pa.string()),
            "ITEM_KEY": pa.array([None] * n, type=pa.string()),
            "RECORD_NAME": pa.array([r[0] for r in rows], type=pa.string()),
            "SITE_ID": pa.array([r[0] for r in rows], type=pa.string()),
            "SITE_NAME": pa.array([r[0] for r in rows], type=pa.string()),
            "IS_VALID": pa.array([r[1] for r in rows], type=pa.bool_()),
            "DETAIL": pa.array([None] * n, type=pa.string()),
        }
    )


@pytest.fixture
def sources_config(fixtures_dir):
    return load_sources(fixtures_dir / "sources_test.yml")


@pytest.fixture
def store(store_path):
    con = duckdb.connect(str(store_path))
    con.execute(
        """
        INSERT INTO DQ_RULE (RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
            RECORD_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CHECK_SQL, CREATED_BY)
        VALUES ('SITE-900', 'SITE-900 rule', 'stmt', 'SITE_MASTER', 'SITE', 'sites', 'SITE',
                'VALIDITY', 'HIGH', 'owner@connexa.co.nz', 'ACTIVE',
                'SELECT s.ID AS RECORD_ID FROM {{ src.SITE }} s', 'test')
        """
    )
    con.execute(
        "INSERT INTO DQ_OWNER (OWNER_EMAIL, DISPLAY_NAME, DOMAINS, IS_APPROVER) "
        "VALUES ('owner@connexa.co.nz', 'Owner', ['SITE_MASTER'], FALSE)"
    )
    con.execute(
        "UPDATE DQ_CONFIG SET CONFIG_VALUE = 'test-model' WHERE CONFIG_KEY = 'CORTEX_MODEL'"
    )
    con.close()
    return DuckDBStore(store_path)


@pytest.fixture
def executor():
    ex = FakeExecutor()
    ex.rule_tables["SITE-900"] = make_table([("r1", False), ("r2", True)])
    ex.scalars["SITE"] = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    return ex


def test_run_checks_now_runs_the_engine_as_a_manual_run(store, executor, sources_config):
    summary = run_ops.run_checks_now(
        store, executor, sources_config, CONFIG, triggered_by="kevin", now=NOW
    )

    assert summary.status == "SUCCEEDED"
    run = store.list_runs(1)[0]
    assert run["RUN_ID"] == summary.run_id
    assert run["RUN_TYPE"] == "MANUAL"
    assert run["TRIGGERED_BY"] == "kevin"
    assert store.list_findings()[0]["RECORD_ID"] == "r1"


def test_run_checks_now_records_source_freshness(store, executor, sources_config):
    run_ops.run_checks_now(store, executor, sources_config, CONFIG, triggered_by="kevin", now=NOW)

    freshness = {r["SOURCE_NAME"]: r["LAST_SYNCED_AT"] for r in store.get_source_freshness_latest()}
    assert freshness["SITE"] == datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def test_run_checks_now_generates_narratives_when_given_a_completer(
    store, executor, sources_config
):
    narrator = FakeNarrator()

    run_ops.run_checks_now(
        store, executor, sources_config, CONFIG, triggered_by="kevin", narrator=narrator, now=NOW
    )

    assert narrator.calls == 1
    assert store.get_latest_narrative("owner@connexa.co.nz")["STATUS"] == "SUCCEEDED"


def test_run_checks_now_refuses_while_another_run_is_in_progress(store, executor, sources_config):
    store.start_run("running-1", "SCHEDULED", "kevin", NOW - timedelta(minutes=5))

    with pytest.raises(run_ops.RunOpsError, match="already in progress"):
        run_ops.run_checks_now(
            store, executor, sources_config, CONFIG, triggered_by="kevin", now=NOW
        )

    assert [r["RUN_ID"] for r in store.list_runs()] == ["running-1"]


def test_a_long_dead_running_row_does_not_block_a_new_run(store, executor, sources_config):
    store.start_run("crashed-1", "SCHEDULED", "kevin", NOW - timedelta(hours=3))

    summary = run_ops.run_checks_now(
        store, executor, sources_config, CONFIG, triggered_by="kevin", now=NOW
    )

    assert summary.status == "SUCCEEDED"


def test_an_unreachable_warehouse_is_a_clean_error_and_leaves_no_run_row(
    store, executor, sources_config
):
    executor.scalar_error = RuntimeError("could not connect")

    with pytest.raises(run_ops.RunOpsError, match="could not connect"):
        run_ops.run_checks_now(
            store, executor, sources_config, CONFIG, triggered_by="kevin", now=NOW
        )

    assert store.list_runs() == []
