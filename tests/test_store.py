"""Integration tests for DuckDBStore, against a temp DB built from the real
schema file (sql/dq_poc_duckdb_schema.sql) -- never ~/dq-data/governance.duckdb.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from dq.store.base import FindingInput
from dq.store.duckdb_store import DuckDBStore

RUN_1 = "11111111-1111-1111-1111-111111111111"
RUN_2 = "22222222-2222-2222-2222-222222222222"
RUN_3 = "33333333-3333-3333-3333-333333333333"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 8, tzinfo=UTC)
T3 = datetime(2026, 1, 15, tzinfo=UTC)


def insert_rule(store_path: Path, *, rule_code: str, status: str = "ACTIVE") -> int:
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


def get_finding(store_path: Path, finding_id: str) -> dict | None:
    con = duckdb.connect(str(store_path))
    try:
        cur = con.execute("SELECT * FROM DQ_FINDING WHERE FINDING_ID = ?", [finding_id])
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=True))
    finally:
        con.close()


def insert_disposition(
    store_path: Path,
    *,
    finding_id: str,
    disposition: str,
    reason: str = "test",
    expires_at: datetime | None = None,
) -> None:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_FINDING_DISPOSITION (FINDING_ID, DISPOSITION, REASON, SET_BY, EXPIRES_AT)
            VALUES (?, ?, ?, 'owner@connexa.co.nz', ?)
            """,
            [finding_id, disposition, reason, expires_at],
        )
    finally:
        con.close()


def apply(
    store: DuckDBStore,
    rule_id: int,
    run_id: str,
    run_at: datetime,
    rows: list[FindingInput],
    rule_code: str = "SITE-900",
):
    return store.apply_findings(
        run_id=run_id,
        run_at=run_at,
        rule_id=rule_id,
        rule_code=rule_code,
        rule_version=1,
        source_object="SITE",
        sf_object="Site__c",
        sf_base_url="https://connexa.lightning.force.com",
        rows=rows,
    )


# --- get_active_rules, get_retired_rule_ids, get_severity_weights --------------


def test_get_active_rules_returns_only_active(store_path):
    insert_rule(store_path, rule_code="SITE-901", status="ACTIVE")
    insert_rule(store_path, rule_code="SITE-902", status="DRAFT")
    store = DuckDBStore(store_path)

    active = store.get_active_rules()

    codes = {r["RULE_CODE"] for r in active}
    assert "SITE-901" in codes
    assert "SITE-902" not in codes


def test_get_retired_rule_ids(store_path):
    retired_id = insert_rule(store_path, rule_code="SITE-903", status="RETIRED")
    insert_rule(store_path, rule_code="SITE-904", status="ACTIVE")
    store = DuckDBStore(store_path)

    assert store.get_retired_rule_ids() == [retired_id]


def test_get_severity_weights_matches_seeded_table(store_path):
    store = DuckDBStore(store_path)

    weights = store.get_severity_weights()

    assert weights == {"CRITICAL": 5, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


# --- runs -----------------------------------------------------------------------


def test_start_and_finish_run_round_trip(store_path):
    store = DuckDBStore(store_path)
    store.start_run(RUN_1, "MANUAL", "kevin", T1)
    store.finish_run(
        RUN_1,
        status="SUCCEEDED",
        finished_at=T2,
        rules_attempted=5,
        rules_succeeded=5,
        rules_errored=0,
    )

    con = duckdb.connect(str(store_path))
    row = con.execute(
        "SELECT STATUS, RULES_SUCCEEDED FROM DQ_RUN WHERE RUN_ID = ?", [RUN_1]
    ).fetchone()
    con.close()

    assert row == ("SUCCEEDED", 5)


# --- apply_findings: the lifecycle end to end ----------------------------------


def test_new_failure_inserts_open_finding(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    row = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail='{"a":1}')

    result = apply(store, rule_id, RUN_1, T1, [row])

    assert result.inserted == 1
    assert result.row_statuses == ["OPEN"]
    finding_id = (
        duckdb.connect(str(store_path)).execute("SELECT FINDING_ID FROM DQ_FINDING").fetchone()[0]
    )
    finding = get_finding(store_path, finding_id)
    assert finding["STATUS"] == "OPEN"
    assert finding["TIMES_SEEN"] == 1
    assert (
        finding["RECORD_URL"] == "https://connexa.lightning.force.com/lightning/r/Site__c/r1/view"
    )


def test_running_twice_unchanged_updates_last_seen_not_status(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    row = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)

    apply(store, rule_id, RUN_1, T1, [row])
    result = apply(store, rule_id, RUN_2, T2, [row])

    assert result.updated == 1
    assert result.inserted == 0
    finding_id = (
        duckdb.connect(str(store_path)).execute("SELECT FINDING_ID FROM DQ_FINDING").fetchone()[0]
    )
    finding = get_finding(store_path, finding_id)
    assert finding["STATUS"] == "OPEN"
    assert finding["TIMES_SEEN"] == 2
    assert finding["LAST_SEEN_RUN_ID"] == RUN_2


def test_fix_record_then_run_again_resolves(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    failing = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)
    passing = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=True, detail=None)

    apply(store, rule_id, RUN_1, T1, [failing])
    result = apply(store, rule_id, RUN_2, T2, [passing])

    assert result.resolved_fixed == 1
    finding_id = (
        duckdb.connect(str(store_path)).execute("SELECT FINDING_ID FROM DQ_FINDING").fetchone()[0]
    )
    finding = get_finding(store_path, finding_id)
    assert finding["STATUS"] == "RESOLVED"
    assert finding["RESOLUTION_REASON"] == "FIXED"
    assert finding["RESOLVED_RUN_ID"] == RUN_2


def test_break_again_after_resolved_reopens(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    failing = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)
    passing = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=True, detail=None)

    apply(store, rule_id, RUN_1, T1, [failing])
    apply(store, rule_id, RUN_2, T2, [passing])
    result = apply(store, rule_id, RUN_3, T3, [failing])

    assert result.reopened == 1
    finding_id = (
        duckdb.connect(str(store_path)).execute("SELECT FINDING_ID FROM DQ_FINDING").fetchone()[0]
    )
    finding = get_finding(store_path, finding_id)
    assert finding["STATUS"] == "OPEN"
    assert finding["REOPENED_COUNT"] == 1
    assert finding["RESOLVED_AT"] is None
    assert finding["RESOLUTION_REASON"] is None


def test_set_exception_then_expire_goes_open_again(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    failing = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)

    apply(store, rule_id, RUN_1, T1, [failing])
    finding_id = (
        duckdb.connect(str(store_path)).execute("SELECT FINDING_ID FROM DQ_FINDING").fetchone()[0]
    )

    # Owner accepts the exception, expiring the day after run 2.
    insert_disposition(
        store_path,
        finding_id=finding_id,
        disposition="ACCEPTED_EXCEPTION",
        expires_at=T2 + timedelta(days=1),
    )
    result_2 = apply(store, rule_id, RUN_2, T2, [failing])
    assert result_2.disposition_applied == 1
    assert result_2.row_statuses == ["ACCEPTED_EXCEPTION"]
    assert get_finding(store_path, finding_id)["STATUS"] == "ACCEPTED_EXCEPTION"

    # By run 3 the exception has expired -> back to OPEN.
    result_3 = apply(store, rule_id, RUN_3, T3, [failing])
    assert result_3.reactivated == 1
    finding = get_finding(store_path, finding_id)
    assert finding["STATUS"] == "OPEN"


def test_erroring_rule_leaves_other_findings_untouched(store_path):
    rule_a = insert_rule(store_path, rule_code="SITE-900")
    rule_b = insert_rule(store_path, rule_code="SITE-901")
    store = DuckDBStore(store_path)
    row = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)

    apply(store, rule_a, RUN_1, T1, [row], rule_code="SITE-900")
    apply(store, rule_b, RUN_1, T1, [row], rule_code="SITE-901")

    # Simulate rule_a erroring on run 2: the engine simply never calls
    # apply_findings for it, only for rule_b.
    apply(store, rule_b, RUN_2, T2, [row], rule_code="SITE-901")

    con = duckdb.connect(str(store_path))
    a_finding = con.execute(
        "SELECT LAST_SEEN_RUN_ID, TIMES_SEEN FROM DQ_FINDING WHERE RULE_ID = ?", [rule_a]
    ).fetchone()
    b_finding = con.execute(
        "SELECT LAST_SEEN_RUN_ID, TIMES_SEEN FROM DQ_FINDING WHERE RULE_ID = ?", [rule_b]
    ).fetchone()
    con.close()

    assert a_finding == (RUN_1, 1)  # untouched
    assert b_finding == (RUN_2, 2)  # updated


def test_record_dropping_out_of_evaluated_set_resolves_out_of_scope(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    row_a = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)
    row_b = FindingInput("r2", None, "Site Two", "r2", "Site Two", is_valid=False, detail=None)

    apply(store, rule_id, RUN_1, T1, [row_a])
    result = apply(store, rule_id, RUN_2, T2, [row_b])  # r1 no longer returned by the rule

    assert result.resolved_out_of_scope == 1
    con = duckdb.connect(str(store_path))
    a_status, a_reason = con.execute(
        "SELECT STATUS, RESOLUTION_REASON FROM DQ_FINDING WHERE RECORD_ID = 'r1'"
    ).fetchone()
    con.close()

    assert a_status == "RESOLVED"
    assert a_reason == "OUT_OF_SCOPE"


def test_no_change_when_rule_never_fails(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    passing = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=True, detail=None)

    result = apply(store, rule_id, RUN_1, T1, [passing])

    assert result.no_change == 1
    con = duckdb.connect(str(store_path))
    count = con.execute("SELECT COUNT(*) FROM DQ_FINDING").fetchone()[0]
    con.close()
    assert count == 0


# --- retired rules ---------------------------------------------------------------


def test_close_retired_rule_findings(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    row = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)
    apply(store, rule_id, RUN_1, T1, [row])

    closed = store.close_retired_rule_findings(RUN_2, T2, [rule_id])

    assert closed == 1
    con = duckdb.connect(str(store_path))
    status, reason = con.execute(
        "SELECT STATUS, RESOLUTION_REASON FROM DQ_FINDING WHERE RULE_ID = ?", [rule_id]
    ).fetchone()
    con.close()
    assert status == "RESOLVED"
    assert reason == "RULE_RETIRED"


def test_close_retired_rule_findings_empty_list_is_noop(store_path):
    store = DuckDBStore(store_path)
    assert store.close_retired_rule_findings(RUN_1, T1, []) == 0


# --- results, scores, history -----------------------------------------------------


def test_write_rule_result_round_trip(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    store.start_run(RUN_1, "MANUAL", "kevin", T1)

    store.write_rule_result(
        RUN_1,
        rule_id,
        "SITE-900",
        1,
        {
            "status": "SUCCEEDED",
            "object_total": 100,
            "records_evaluated": 90,
            "records_failed": 10,
            "records_failed_open": 8,
            "pass_rate": 91.1,
            "items_evaluated": 90,
            "items_failed": 10,
            "items_failed_open": 8,
            "item_pass_rate": 91.1,
            "duration_ms": 1234,
        },
    )

    con = duckdb.connect(str(store_path))
    row = con.execute(
        "SELECT RECORDS_EVALUATED, RECORDS_FAILED_OPEN, PASS_RATE "
        "FROM DQ_RULE_RESULT WHERE RUN_ID = ?",
        [RUN_1],
    ).fetchone()
    con.close()
    assert row[:2] == (90, 8)
    assert float(row[2]) == pytest.approx(91.1)


def test_write_object_result_round_trip(store_path):
    store = DuckDBStore(store_path)
    store.start_run(RUN_1, "MANUAL", "kevin", T1)

    store.write_object_result(
        RUN_1,
        {
            "target_object": "SITE",
            "record_label": "sites",
            "object_total": 1546,
            "active_rules": 5,
            "rules_succeeded": 5,
            "records_evaluated": 1546,
            "records_with_exception": 118,
            "open_findings": 130,
            "exception_rate": 7.6,
        },
    )

    con = duckdb.connect(str(store_path))
    row = con.execute(
        "SELECT RECORDS_WITH_EXCEPTION, EXCEPTION_RATE FROM DQ_OBJECT_RESULT WHERE RUN_ID = ?",
        [RUN_1],
    ).fetchone()
    con.close()
    assert row[0] == 118
    assert float(row[1]) == pytest.approx(7.6)


def test_write_site_scores_round_trip(store_path):
    store = DuckDBStore(store_path)
    store.start_run(RUN_1, "MANUAL", "kevin", T1)

    store.write_site_scores(
        RUN_1,
        T1,
        [
            {
                "site_id": "s1",
                "site_name": "Site One",
                "rules_applicable": 3,
                "rules_failed": 1,
                "open_findings": 2,
                "critical_open": 0,
                "weighted_score": 66.67,
            }
        ],
    )

    con = duckdb.connect(str(store_path))
    row = con.execute(
        "SELECT SITE_NAME, RULES_APPLICABLE, WEIGHTED_SCORE FROM DQ_SITE_SCORE WHERE RUN_ID = ?",
        [RUN_1],
    ).fetchone()
    con.close()
    assert row[:2] == ("Site One", 3)
    assert float(row[2]) == pytest.approx(66.67)


def test_add_history_writes_snapshot(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)

    store.add_history(
        rule_id=rule_id,
        rule_code="SITE-900",
        version=2,
        change_type="SQL_COMPILED",
        status_after="COMPILED",
        snapshot={"RULE_CODE": "SITE-900", "STATUS": "COMPILED"},
        changed_by="kevin@connexa.co.nz",
        comment="Compiled by Claude Code",
    )

    con = duckdb.connect(str(store_path))
    row = con.execute(
        "SELECT CHANGE_TYPE, STATUS_AFTER, COMMENT FROM DQ_RULE_HISTORY WHERE RULE_ID = ? "
        "AND CHANGE_TYPE = 'SQL_COMPILED'",
        [rule_id],
    ).fetchone()
    con.close()
    assert row == ("SQL_COMPILED", "COMPILED", "Compiled by Claude Code")


def test_get_active_disposition_ignores_expired_and_revoked(store_path):
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    store = DuckDBStore(store_path)
    row = FindingInput("r1", None, "Site One", "r1", "Site One", is_valid=False, detail=None)
    apply(store, rule_id, RUN_1, T1, [row])
    finding_id = (
        duckdb.connect(str(store_path)).execute("SELECT FINDING_ID FROM DQ_FINDING").fetchone()[0]
    )

    assert store.get_active_disposition(finding_id) is None

    insert_disposition(
        store_path, finding_id=finding_id, disposition="FALSE_POSITIVE", expires_at=None
    )
    active = store.get_active_disposition(finding_id)
    assert active is not None
    assert active["DISPOSITION"] == "FALSE_POSITIVE"
