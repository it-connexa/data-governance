"""Tests for dq validate / dq rule set-sql / dq rule clarify (brief §10.5,
§7), against a temp DuckDB store and a FakeExecutor -- never Snowflake."""

from datetime import datetime

import duckdb
import pyarrow as pa
import pytest

from dq import rule_ops
from dq.engine import EngineConfig, ExecutionResult
from dq.sources import load_sources
from dq.store.duckdb_store import DuckDBStore

QUERY_TAG = "dq_poc:test:1"
CONFIG = EngineConfig(sf_base_url="https://connexa.lightning.force.com")


class FakeExecutor:
    def __init__(self):
        self.rule_tables: dict[str, pa.Table] = {}
        self.rule_errors: dict[str, Exception] = {}
        self.calls: list[str] = []

    def execute_rule(self, rule_code, sql, *, query_tag, timeout_seconds):
        self.calls.append(rule_code)
        if rule_code in self.rule_errors:
            raise self.rule_errors[rule_code]
        return ExecutionResult(
            table=self.rule_tables[rule_code], query_id=f"fake-{rule_code}", duration_ms=1
        )

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
) -> int:
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
        rule_id = con.execute(
            "SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code]
        ).fetchone()[0]
    finally:
        con.close()
    return rule_id


@pytest.fixture
def sources_config(fixtures_dir):
    return load_sources(fixtures_dir / "sources_test.yml")


# --- dq validate -----------------------------------------------------------------


def test_validate_returns_ok_for_a_clean_result(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT s.ID FROM {{ src.SITE }} s")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", True), ("r2", False)])

    result = rule_ops.validate_rule(
        store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is True
    assert result["records_evaluated"] == 2
    assert result["records_failed"] == 1
    assert result["item_key_used"] is False
    assert result["duration_ms"] == 1
    assert result["query_id"] == "fake-SITE-900"


def test_validate_persists_result_and_history(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT s.ID FROM {{ src.SITE }} s")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", True)])

    rule_ops.validate_rule(
        store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
    )

    updated = store.get_rule("SITE-900")
    assert updated["VALIDATION_RESULT"] is not None
    assert updated["STATUS"] == "DRAFT"  # validate never changes STATUS

    con = duckdb.connect(str(store_path))
    history = con.execute(
        "SELECT CHANGE_TYPE FROM DQ_RULE_HISTORY WHERE RULE_CODE = 'SITE-900' "
        "ORDER BY HISTORY_ID DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert history == ("VALIDATED",)


def test_validate_reports_contract_violation_as_not_ok(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT s.ID FROM {{ src.SITE }} s")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", True), (None, False)])  # null RECORD_ID

    result = rule_ops.validate_rule(
        store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is False
    assert result["null_record_ids"] == 1
    assert any("RECORD_ID" in e for e in result["errors"])


def test_validate_unknown_rule_raises(store_path, sources_config):
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.validate_rule(
            store, executor, sources_config, "NOPE-999", config=CONFIG, query_tag=QUERY_TAG
        )


def test_validate_no_check_sql_raises(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql=None)
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    with pytest.raises(rule_ops.RuleOpsError, match="no CHECK_SQL"):
        rule_ops.validate_rule(
            store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
        )


def test_validate_lint_failure_is_reported_not_raised(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql="DROP TABLE {{ src.SITE }}")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    result = rule_ops.validate_rule(
        store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is False
    assert result["errors"]
    assert executor.calls == []  # never reached Snowflake


# --- dq rule set-sql --------------------------------------------------------------


def test_set_sql_compiles_rule_on_success(store_path, sources_config, tmp_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", True)])
    sql_path = tmp_path / "SITE-900.sql"
    sql_path.write_text("SELECT s.ID FROM {{ src.SITE }} s")

    result = rule_ops.set_rule_sql(
        store, executor, sources_config, "SITE-900", sql_path, config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is True
    assert result["rule"]["STATUS"] == "COMPILED"
    assert result["rule"]["CHECK_SQL"] == "SELECT s.ID FROM {{ src.SITE }} s"
    assert result["rule"]["VERSION"] == 2


def test_set_sql_does_not_write_on_lint_failure(store_path, sources_config, tmp_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    sql_path = tmp_path / "SITE-900.sql"
    sql_path.write_text("DROP TABLE {{ src.SITE }}")

    result = rule_ops.set_rule_sql(
        store, executor, sources_config, "SITE-900", sql_path, config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is False
    assert result["stage"] == "lint"
    updated = store.get_rule("SITE-900")
    assert updated["STATUS"] == "DRAFT"
    assert updated["CHECK_SQL"] is None
    assert executor.calls == []


def test_set_sql_does_not_write_on_validation_failure(store_path, sources_config, tmp_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([(None, True)])  # null RECORD_ID
    sql_path = tmp_path / "SITE-900.sql"
    sql_path.write_text("SELECT s.ID FROM {{ src.SITE }} s")

    result = rule_ops.set_rule_sql(
        store, executor, sources_config, "SITE-900", sql_path, config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is False
    assert result["stage"] == "validate"
    updated = store.get_rule("SITE-900")
    assert updated["STATUS"] == "DRAFT"
    assert updated["CHECK_SQL"] is None


def test_set_sql_missing_file_raises(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    with pytest.raises(rule_ops.RuleOpsError, match="not found"):
        rule_ops.set_rule_sql(
            store,
            executor,
            sources_config,
            "SITE-900",
            "/no/such.sql",
            config=CONFIG,
            query_tag=QUERY_TAG,
        )


def test_set_sql_unknown_rule_raises(store_path, sources_config, tmp_path):
    store = DuckDBStore(store_path)
    executor = FakeExecutor()
    sql_path = tmp_path / "X.sql"
    sql_path.write_text("SELECT s.ID FROM {{ src.SITE }} s")

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.set_rule_sql(
            store,
            executor,
            sources_config,
            "NOPE-999",
            sql_path,
            config=CONFIG,
            query_tag=QUERY_TAG,
        )


# --- dq rule clarify ---------------------------------------------------------------


def test_clarify_sets_needs_clarification(store_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)

    updated = rule_ops.clarify_rule(store, "SITE-900", "What does 'active' mean?")

    assert updated["STATUS"] == "NEEDS_CLARIFICATION"
    assert updated["CLARIFICATION_QUESTION"] == "What does 'active' mean?"


def test_clarify_unknown_rule_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.clarify_rule(store, "NOPE-999", "question?")


# --- dq rule confirm / approve -----------------------------------------------------


def insert_owner(store_path, *, email: str, is_approver: bool) -> None:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            "INSERT INTO DQ_OWNER (OWNER_EMAIL, DISPLAY_NAME, IS_APPROVER) VALUES (?, ?, ?)",
            [email, email, is_approver],
        )
    finally:
        con.close()


def test_confirm_rule_moves_compiled_to_owner_confirmed(store_path):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT 1", status="COMPILED")
    store = DuckDBStore(store_path)

    updated = rule_ops.confirm_rule(store, "SITE-900", confirmed_by="kevin@connexa.co.nz")

    assert updated["STATUS"] == "OWNER_CONFIRMED"
    assert updated["OWNER_CONFIRMED_BY"] == "kevin@connexa.co.nz"


def test_confirm_rule_rejects_wrong_starting_status(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="DRAFT")
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="not COMPILED"):
        rule_ops.confirm_rule(store, "SITE-900", confirmed_by="kevin@connexa.co.nz")


def test_confirm_rule_unknown_code_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.confirm_rule(store, "NOPE-999", confirmed_by="kevin@connexa.co.nz")


def test_approve_rule_moves_owner_confirmed_to_active(store_path):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT 1", status="OWNER_CONFIRMED")
    insert_owner(store_path, email="kevin@connexa.co.nz", is_approver=True)
    store = DuckDBStore(store_path)

    updated = rule_ops.approve_rule(store, "SITE-900", approved_by="kevin@connexa.co.nz")

    assert updated["STATUS"] == "ACTIVE"
    assert updated["SQL_APPROVED_BY"] == "kevin@connexa.co.nz"


def test_approve_rule_rejects_wrong_starting_status(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="COMPILED")
    insert_owner(store_path, email="kevin@connexa.co.nz", is_approver=True)
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="not OWNER_CONFIRMED"):
        rule_ops.approve_rule(store, "SITE-900", approved_by="kevin@connexa.co.nz")


def test_approve_rule_rejects_non_approver(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="OWNER_CONFIRMED")
    insert_owner(store_path, email="jane@connexa.co.nz", is_approver=False)
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="not an approver"):
        rule_ops.approve_rule(store, "SITE-900", approved_by="jane@connexa.co.nz")


def test_approve_rule_rejects_unknown_owner(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="OWNER_CONFIRMED")
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="not an approver"):
        rule_ops.approve_rule(store, "SITE-900", approved_by="ghost@connexa.co.nz")


def test_approve_rule_unknown_code_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.approve_rule(store, "NOPE-999", approved_by="kevin@connexa.co.nz")


def test_recompiling_an_active_rule_clears_stale_confirmation_and_approval(store_path):
    """A recompile (e.g. fixing SITE-004's SQL) must not leave a previous
    version's confirm/approve stamped on a rule that's back to COMPILED --
    otherwise `dq rule show` would misreport who signed off the new SQL."""
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT 1", status="COMPILED")
    insert_owner(store_path, email="kevin@connexa.co.nz", is_approver=True)
    store = DuckDBStore(store_path)
    rule_ops.confirm_rule(store, "SITE-900", confirmed_by="kevin@connexa.co.nz")
    rule_ops.approve_rule(store, "SITE-900", approved_by="kevin@connexa.co.nz")

    updated = store.compile_rule(
        "SITE-900",
        check_sql="SELECT 2",
        validation_result={"ok": True, "errors": []},
        changed_by="kevin@connexa.co.nz",
        generated_at=datetime(2026, 1, 1),
    )

    assert updated["STATUS"] == "COMPILED"
    assert updated["OWNER_CONFIRMED_BY"] is None
    assert updated["OWNER_CONFIRMED_AT"] is None
    assert updated["SQL_APPROVED_BY"] is None
    assert updated["SQL_APPROVED_AT"] is None


# --- create_rule / update_rule / answer_clarification / reject_confirmation --------


def test_create_rule_delegates_to_store_with_correct_prefix(store_path):
    store = DuckDBStore(store_path)

    created = rule_ops.create_rule(
        store,
        rule_name="Test",
        statement="Statement.",
        domain="SITE_MASTER",
        target_object="SITE",
        scope="SITE",
        dimension="VALIDITY",
        severity="LOW",
        owner_email="owner@connexa.co.nz",
        created_by="kevin@connexa.co.nz",
    )

    assert created["RULE_CODE"].startswith("SITE-")
    assert created["STATUS"] == "DRAFT"


def test_create_rule_unknown_target_object_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="No rule-code prefix"):
        rule_ops.create_rule(
            store,
            rule_name="Test",
            statement="Statement.",
            domain="SITE_MASTER",
            target_object="EQUIPMENT",
            scope="SITE",
            dimension="VALIDITY",
            severity="LOW",
            owner_email="owner@connexa.co.nz",
            created_by="kevin@connexa.co.nz",
        )


def test_update_rule_unknown_code_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.update_rule(store, "NOPE-999", fields={"SEVERITY": "LOW"}, changed_by="kevin")


def test_update_rule_edits_statement(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="COMPILED", check_sql="SELECT 1")
    store = DuckDBStore(store_path)

    updated = rule_ops.update_rule(
        store, "SITE-900", fields={"RULE_STATEMENT": "New wording"}, changed_by="kevin"
    )

    assert updated["RULE_STATEMENT"] == "New wording"
    assert updated["STATUS"] == "DRAFT"


def test_answer_clarification_unknown_code_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.answer_clarification(store, "NOPE-999", "answer", changed_by="owner")


def test_answer_clarification_moves_to_ready_for_compile(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="NEEDS_CLARIFICATION")
    store = DuckDBStore(store_path)

    updated = rule_ops.answer_clarification(
        store, "SITE-900", "Only active sites count.", changed_by="owner@connexa.co.nz"
    )

    assert updated["STATUS"] == "READY_FOR_COMPILE"
    assert updated["CLARIFICATION_ANSWER"] == "Only active sites count."


def test_submit_rule_moves_draft_to_ready_for_compile(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="DRAFT")
    store = DuckDBStore(store_path)

    updated = rule_ops.submit_rule(store, "SITE-900", changed_by="owner@connexa.co.nz")

    assert updated["STATUS"] == "READY_FOR_COMPILE"


def test_submit_rule_rejects_wrong_starting_status(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="COMPILED", check_sql="SELECT 1")
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="not DRAFT"):
        rule_ops.submit_rule(store, "SITE-900", changed_by="owner@connexa.co.nz")


def test_submit_rule_unknown_code_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.submit_rule(store, "NOPE-999", changed_by="owner@connexa.co.nz")


def test_reject_confirmation_requires_comment(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="OWNER_CONFIRMED")
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="comment is required"):
        rule_ops.reject_confirmation(store, "SITE-900", "   ", changed_by="owner")


def test_reject_confirmation_unknown_code_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.reject_confirmation(store, "NOPE-999", "not right", changed_by="owner")


# --- preview_rule ------------------------------------------------------------------


def test_preview_rule_returns_sample_failing_rows(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql="SELECT s.ID FROM {{ src.SITE }} s")
    store = DuckDBStore(store_path)
    store.compile_rule(
        "SITE-900",
        check_sql="SELECT s.ID FROM {{ src.SITE }} s",
        validation_result={"ok": True},
        changed_by="kevin",
        generated_at=datetime.now(),
    )
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", True), ("r2", False), ("r3", False)])

    result = rule_ops.preview_rule(
        store,
        executor,
        sources_config,
        "SITE-900",
        config=CONFIG,
        query_tag=QUERY_TAG,
        sample_size=1,
    )

    assert result["ok"] is True
    assert result["records_evaluated"] == 3
    assert result["records_failed"] == 2
    assert len(result["sample"]) == 1  # capped by sample_size
    row = result["sample"][0]
    assert row["record_id"] == "r2"
    assert row["record_url"].startswith("https://connexa.lightning.force.com/lightning/r/Site__c/")


def test_preview_rule_persists_preview_counts(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    store.compile_rule(
        "SITE-900",
        check_sql="SELECT s.ID FROM {{ src.SITE }} s",
        validation_result={"ok": True},
        changed_by="kevin",
        generated_at=datetime.now(),
    )
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False)])

    rule_ops.preview_rule(
        store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
    )

    updated = store.get_rule("SITE-900")
    assert updated["PREVIEW_EVALUATED_COUNT"] == 1
    assert updated["PREVIEW_FAILED_COUNT"] == 1
    assert updated["PREVIEW_AT"] is not None


def test_preview_rule_lint_failure_is_reported_not_raised(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql="DROP TABLE {{ src.SITE }}")
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    result = rule_ops.preview_rule(
        store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
    )

    assert result["ok"] is False
    assert result["sample"] == []
    assert executor.calls == []


def test_preview_rule_no_check_sql_raises(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", check_sql=None)
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    with pytest.raises(rule_ops.RuleOpsError, match="no CHECK_SQL"):
        rule_ops.preview_rule(
            store, executor, sources_config, "SITE-900", config=CONFIG, query_tag=QUERY_TAG
        )


def test_preview_rule_unknown_code_raises(store_path, sources_config):
    store = DuckDBStore(store_path)
    executor = FakeExecutor()

    with pytest.raises(rule_ops.RuleOpsError, match="Unknown rule"):
        rule_ops.preview_rule(
            store, executor, sources_config, "NOPE-999", config=CONFIG, query_tag=QUERY_TAG
        )
