"""Tests for the SOURCE_COLUMN (catalogue) and DQ_RULE mutation methods on
DuckDBStore -- against a temp DB built from the real schema file."""

from datetime import UTC, datetime

import duckdb

from dq.store.duckdb_store import DuckDBStore

T1 = datetime(2026, 1, 1, tzinfo=UTC)


def insert_rule(store_path, *, rule_code: str, status: str = "DRAFT") -> int:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (
                RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CREATED_BY
            ) VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'SITE', 'VALIDITY', 'HIGH',
                      'owner@connexa.co.nz', ?, 'test')
            """,
            [rule_code, f"{rule_code} test rule", status],
        )
        rule_id = con.execute(
            "SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code]
        ).fetchone()[0]
    finally:
        con.close()
    return rule_id


# --- DQ_CONFIG ------------------------------------------------------------------


def test_get_config_value_returns_seeded_default(store_path):
    store = DuckDBStore(store_path)

    assert store.get_config_value("RULE_TIMEOUT_SECONDS") == "120"


def test_get_config_value_returns_none_for_unknown_key(store_path):
    store = DuckDBStore(store_path)

    assert store.get_config_value("NOT_A_REAL_KEY") is None


# --- SOURCE_COLUMN (catalogue) --------------------------------------------------


def test_upsert_source_columns_inserts_new_rows(store_path):
    store = DuckDBStore(store_path)
    columns = [
        {"column_name": "ID", "data_type": "VARCHAR", "is_nullable": False, "ordinal_position": 1},
        {"column_name": "NAME", "data_type": "VARCHAR", "is_nullable": True, "ordinal_position": 2},
    ]

    store.upsert_source_columns("SITE", columns, T1)

    rows = store.get_source_columns(source_name="SITE")
    assert [r["COLUMN_NAME"] for r in rows] == ["ID", "NAME"]
    assert rows[0]["DATA_TYPE"] == "VARCHAR"


def test_upsert_source_columns_updates_existing_and_preserves_labels(store_path):
    store = DuckDBStore(store_path)
    store.upsert_source_columns(
        "SITE", [{"column_name": "ID", "data_type": "VARCHAR", "ordinal_position": 1}], T1
    )
    store.update_column_labels([("SITE", "ID", "Site ID")])

    # Re-refresh with a changed type; the label should survive.
    store.upsert_source_columns(
        "SITE", [{"column_name": "ID", "data_type": "VARCHAR(18)", "ordinal_position": 1}], T1
    )

    rows = store.get_source_columns(source_name="SITE")
    assert rows[0]["DATA_TYPE"] == "VARCHAR(18)"
    assert rows[0]["COLUMN_LABEL"] == "Site ID"


def test_upsert_source_columns_removes_stale_columns(store_path):
    store = DuckDBStore(store_path)
    store.upsert_source_columns(
        "SITE",
        [
            {"column_name": "ID", "data_type": "VARCHAR", "ordinal_position": 1},
            {"column_name": "OLD_COLUMN", "data_type": "VARCHAR", "ordinal_position": 2},
        ],
        T1,
    )

    store.upsert_source_columns(
        "SITE", [{"column_name": "ID", "data_type": "VARCHAR", "ordinal_position": 1}], T1
    )

    rows = store.get_source_columns(source_name="SITE")
    assert [r["COLUMN_NAME"] for r in rows] == ["ID"]


def test_get_source_columns_search_matches_label_or_name(store_path):
    store = DuckDBStore(store_path)
    store.upsert_source_columns(
        "SITE",
        [
            {
                "column_name": "SITETRACKER_SITE_STATUS_C",
                "data_type": "VARCHAR",
                "ordinal_position": 1,
            },
            {"column_name": "NAME", "data_type": "VARCHAR", "ordinal_position": 2},
        ],
        T1,
    )
    store.update_column_labels([("SITE", "SITETRACKER_SITE_STATUS_C", "Site Status")])

    by_label = store.get_source_columns(search="status")
    by_name = store.get_source_columns(search="SITETRACKER_SITE_STATUS")

    assert [r["COLUMN_NAME"] for r in by_label] == ["SITETRACKER_SITE_STATUS_C"]
    assert [r["COLUMN_NAME"] for r in by_name] == ["SITETRACKER_SITE_STATUS_C"]


# --- DQ_RULE mutations -----------------------------------------------------------


def test_list_rules_filters_by_status(store_path):
    # The schema seeds 12 DRAFT rules already (docs/SETUP.md step 6), so
    # check membership rather than an exact list.
    insert_rule(store_path, rule_code="SITE-900", status="DRAFT")
    insert_rule(store_path, rule_code="SITE-901", status="ACTIVE")
    store = DuckDBStore(store_path)

    draft_codes = {r["RULE_CODE"] for r in store.list_rules(status="DRAFT")}
    active_codes = {r["RULE_CODE"] for r in store.list_rules(status="ACTIVE")}

    assert "SITE-900" in draft_codes
    assert "SITE-901" not in draft_codes
    assert active_codes == {"SITE-901"}


def test_get_rule_returns_none_for_unknown_code(store_path):
    store = DuckDBStore(store_path)
    assert store.get_rule("NOPE-999") is None


def test_get_rule_returns_row_for_known_code(store_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)

    rule = store.get_rule("SITE-900")

    assert rule["RULE_CODE"] == "SITE-900"
    assert rule["STATUS"] == "DRAFT"


def test_compile_rule_sets_check_sql_status_and_bumps_version(store_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)

    updated = store.compile_rule(
        "SITE-900",
        check_sql="SELECT 1",
        validation_result={"ok": True, "errors": []},
        changed_by="kevin@connexa.co.nz",
        generated_at=T1,
    )

    assert updated["STATUS"] == "COMPILED"
    assert updated["CHECK_SQL"] == "SELECT 1"
    assert updated["VERSION"] == 2  # started at 1

    con = duckdb.connect(str(store_path))
    history = con.execute(
        "SELECT CHANGE_TYPE, STATUS_AFTER FROM DQ_RULE_HISTORY WHERE RULE_CODE = 'SITE-900' "
        "ORDER BY HISTORY_ID DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert history == ("SQL_COMPILED", "COMPILED")


def test_record_validation_does_not_change_check_sql_or_status(store_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    store.compile_rule(
        "SITE-900",
        check_sql="SELECT 1",
        validation_result={"ok": True},
        changed_by="kevin@connexa.co.nz",
        generated_at=T1,
    )

    updated = store.record_validation(
        "SITE-900", {"ok": True, "records_evaluated": 5}, changed_by="kevin@connexa.co.nz"
    )

    assert updated["STATUS"] == "COMPILED"  # unchanged
    assert updated["CHECK_SQL"] == "SELECT 1"  # unchanged
    assert updated["VERSION"] == 2  # unchanged

    con = duckdb.connect(str(store_path))
    history = con.execute(
        "SELECT CHANGE_TYPE FROM DQ_RULE_HISTORY WHERE RULE_CODE = 'SITE-900' "
        "ORDER BY HISTORY_ID DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert history == ("VALIDATED",)


def test_set_rule_clarification_sets_status_and_question(store_path):
    insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)

    updated = store.set_rule_clarification(
        "SITE-900", "What does 'active' mean for a site?", changed_by="claude-code"
    )

    assert updated["STATUS"] == "NEEDS_CLARIFICATION"
    assert updated["CLARIFICATION_QUESTION"] == "What does 'active' mean for a site?"

    con = duckdb.connect(str(store_path))
    history = con.execute(
        "SELECT CHANGE_TYPE, STATUS_AFTER FROM DQ_RULE_HISTORY WHERE RULE_CODE = 'SITE-900' "
        "ORDER BY HISTORY_ID DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert history == ("CLARIFICATION", "NEEDS_CLARIFICATION")


# --- create_rule / update_rule / answer_clarification / reject_confirmation / preview -------


def test_create_rule_generates_first_code_for_new_prefix(store_path):
    store = DuckDBStore(store_path)

    created = store.create_rule(
        prefix="EQP",
        rule_name="Test rule",
        statement="Every widget must be blue.",
        domain="SITE_MASTER",
        target_object="EQUIPMENT",
        scope="SITE",
        dimension="VALIDITY",
        severity="LOW",
        owner_email="owner@connexa.co.nz",
        created_by="kevin@connexa.co.nz",
    )

    assert created["RULE_CODE"] == "EQP-001"
    assert created["STATUS"] == "DRAFT"
    assert created["RULE_NAME"] == "Test rule"


def test_create_rule_increments_existing_prefix(store_path):
    # The schema seeds SITE-001..SITE-007 already.
    store = DuckDBStore(store_path)

    created = store.create_rule(
        prefix="SITE",
        rule_name="Another site rule",
        statement="Statement.",
        domain="SITE_MASTER",
        target_object="SITE",
        scope="SITE",
        dimension="VALIDITY",
        severity="LOW",
        owner_email="owner@connexa.co.nz",
        created_by="kevin@connexa.co.nz",
    )

    assert created["RULE_CODE"] == "SITE-008"


def test_update_rule_statement_resets_compiled_rule_to_draft(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="COMPILED")
    con = duckdb.connect(str(store_path))
    con.execute(
        "UPDATE DQ_RULE SET CHECK_SQL = 'SELECT 1', VERSION = 2, "
        "OWNER_CONFIRMED_BY = 'x', SQL_APPROVED_BY = 'y' WHERE RULE_CODE = 'SITE-900'"
    )
    con.close()
    store = DuckDBStore(store_path)

    updated = store.update_rule(
        "SITE-900", fields={"RULE_STATEMENT": "New statement"}, changed_by="kevin@connexa.co.nz"
    )

    assert updated["RULE_STATEMENT"] == "New statement"
    assert updated["STATUS"] == "DRAFT"
    assert updated["CHECK_SQL"] is None
    assert updated["OWNER_CONFIRMED_BY"] is None
    assert updated["SQL_APPROVED_BY"] is None
    assert updated["VERSION"] == 3


def test_update_rule_metadata_only_does_not_reset_status(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="ACTIVE")
    store = DuckDBStore(store_path)

    updated = store.update_rule(
        "SITE-900", fields={"SEVERITY": "LOW"}, changed_by="kevin@connexa.co.nz"
    )

    assert updated["SEVERITY"] == "LOW"
    assert updated["STATUS"] == "ACTIVE"
    assert updated["VERSION"] == 1


def test_answer_clarification_sets_answer(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="NEEDS_CLARIFICATION")
    store = DuckDBStore(store_path)

    updated = store.answer_clarification(
        "SITE-900", "Active means Operational status.", changed_by="owner@connexa.co.nz"
    )

    assert updated["CLARIFICATION_ANSWER"] == "Active means Operational status."
    assert updated["STATUS"] == "READY_FOR_COMPILE"  # answering re-queues it for compilation


def test_submit_rule_sets_ready_for_compile_and_records_history(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="DRAFT")
    store = DuckDBStore(store_path)

    updated = store.submit_rule("SITE-900", changed_by="owner@connexa.co.nz")

    assert updated["STATUS"] == "READY_FOR_COMPILE"

    con = duckdb.connect(str(store_path))
    history = con.execute(
        "SELECT CHANGE_TYPE, STATUS_AFTER FROM DQ_RULE_HISTORY WHERE RULE_CODE = 'SITE-900' "
        "ORDER BY HISTORY_ID DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert history == ("SUBMITTED", "READY_FOR_COMPILE")


def test_reject_confirmation_returns_to_draft_with_comment(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="OWNER_CONFIRMED")
    store = DuckDBStore(store_path)

    updated = store.reject_confirmation(
        "SITE-900", "Not what I meant", changed_by="owner@connexa.co.nz"
    )

    assert updated["STATUS"] == "DRAFT"
    con = duckdb.connect(str(store_path))
    history = con.execute(
        "SELECT CHANGE_TYPE, COMMENT FROM DQ_RULE_HISTORY WHERE RULE_CODE = 'SITE-900' "
        "ORDER BY HISTORY_ID DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert history == ("OWNER_REJECTED", "Not what I meant")


def test_record_preview_sets_counts_and_timestamp(store_path):
    insert_rule(store_path, rule_code="SITE-900", status="COMPILED")
    store = DuckDBStore(store_path)

    updated = store.record_preview(
        "SITE-900", evaluated=100, failed=5, changed_by="owner@connexa.co.nz"
    )

    assert updated["PREVIEW_EVALUATED_COUNT"] == 100
    assert updated["PREVIEW_FAILED_COUNT"] == 5
    assert updated["PREVIEW_AT"] is not None


def test_list_owners_returns_active_owners(store_path):
    con = duckdb.connect(str(store_path))
    con.execute(
        "INSERT INTO DQ_OWNER (OWNER_EMAIL, DISPLAY_NAME, IS_APPROVER, IS_ACTIVE) "
        "VALUES ('a@connexa.co.nz', 'A Owner', TRUE, TRUE), "
        "('b@connexa.co.nz', 'B Owner', FALSE, FALSE)"
    )
    con.close()
    store = DuckDBStore(store_path)

    owners = store.list_owners()

    emails = {o["OWNER_EMAIL"] for o in owners}
    assert "a@connexa.co.nz" in emails
    assert "b@connexa.co.nz" not in emails
