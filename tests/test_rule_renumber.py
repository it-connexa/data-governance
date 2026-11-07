"""Renumbering rules (`dq rule renumber`): the rule's code is copied into its history,
run results and findings, and a finding's ID is a hash that includes the code. These
tests prove a renumber moves all of it, keeps exceptions attached to their findings,
keeps the next run matching the same findings, and refuses anything that could collide
-- against a temp store, never ~/dq-data."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from dq import rule_ops
from dq.lifecycle import compute_finding_id
from dq.store.base import FindingInput
from dq.store.duckdb_store import DuckDBStore

RUN_1 = "11111111-1111-1111-1111-111111111111"
RUN_2 = "22222222-2222-2222-2222-222222222222"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 8, tzinfo=UTC)
BASE = "https://connexa.lightning.force.com"


def insert_rule(store_path, code: str, *, status: str = "DRAFT", with_sql: bool = False) -> int:
    check_sql = f"-- {code} v1: a rule\nSELECT 1 AS RECORD_ID" if with_sql else None
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CHECK_SQL, CREATED_BY)
            VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'SITE', 'VALIDITY', 'HIGH',
                    'owner@connexa.co.nz', ?, ?, 'test')
            """,
            [code, f"{code} name", status, check_sql],
        )
        return con.execute("SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [code]).fetchone()[0]
    finally:
        con.close()


def fail(record_id: str, item_key: str | None = None) -> FindingInput:
    return FindingInput(record_id, item_key, record_id, record_id, record_id, False, '{"a": 1}')


def run_rule(store, rule_id, code, run_id, at, rows):
    store.apply_findings(
        run_id=run_id,
        run_at=at,
        rule_id=rule_id,
        rule_code=code,
        rule_version=1,
        source_object="SITE",
        sf_object="sitetracker__Site__c",
        sf_base_url=BASE,
        rows=rows,
    )
    store.write_rule_result(
        run_id,
        rule_id,
        code,
        1,
        {
            "status": "SUCCEEDED",
            "object_total": 10,
            "records_evaluated": len(rows),
            "records_failed": len(rows),
            "records_failed_open": len(rows),
            "pass_rate": 0.0,
            "items_evaluated": len(rows),
            "items_failed": len(rows),
            "items_failed_open": len(rows),
            "item_pass_rate": 0.0,
            "duration_ms": 5,
        },
    )


def history(store, rule_id, code, change="CREATED", status="DRAFT"):
    store.add_history(
        rule_id=rule_id,
        rule_code=code,
        version=1,
        change_type=change,
        status_after=status,
        snapshot={"RULE_CODE": code},
        changed_by="kevin",
        facilitated_by="kevin",
    )


def counts(store_path, sql, params):
    con = duckdb.connect(str(store_path))
    try:
        return con.execute(sql, params).fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def store(store_path):
    # The schema file seeds starter rules; these tests want only their own.
    con = duckdb.connect(str(store_path))
    con.execute("DELETE FROM DQ_RULE_HISTORY")
    con.execute("DELETE FROM DQ_RULE")
    con.close()
    return DuckDBStore(store_path)


def seed_active_rule_with_everything(store_path, store, code="SITE-003"):
    """An active rule with history, two runs, findings (one with an item key), and an
    accepted exception on one finding."""
    rule_id = insert_rule(store_path, code, status="ACTIVE", with_sql=True)
    history(store, rule_id, code)
    history(store, rule_id, code, "APPROVED", "ACTIVE")
    store.start_run(RUN_1, "MANUAL", "kevin", T1)
    run_rule(store, rule_id, code, RUN_1, T1, [fail("r1"), fail("r2", "Milestone A")])
    store.finish_run(
        RUN_1,
        status="SUCCEEDED",
        finished_at=T1,
        rules_attempted=1,
        rules_succeeded=1,
        rules_errored=0,
    )
    store.set_dispositions(
        [compute_finding_id(code, "r1", None)],
        "ACCEPTED_EXCEPTION",
        "Known issue",
        set_by="owner@connexa.co.nz",
        facilitated_by="kevin",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    return rule_id


# --- what moves -------------------------------------------------------------------------


def test_renumber_moves_history_results_findings_and_dispositions(store_path, store):
    rule_id = seed_active_rule_with_everything(store_path, store)
    other_id = insert_rule(store_path, "SITE-009", status="ACTIVE")
    run_rule(store, other_id, "SITE-009", RUN_1, T1, [fail("x1")])

    result = rule_ops.renumber_rules(store, {"SITE-003": "SITE-001"})

    assert result["dry_run"] is False
    assert result["renumbered"] == [
        {
            "from": "SITE-003",
            "to": "SITE-001",
            "rule_name": "SITE-003 name",
            "findings": 2,
            "dispositions": 1,
            "run_results": 1,
            "history": 2,
        }
    ]
    rule = store.get_rule("SITE-001")
    assert rule["RULE_ID"] == rule_id and rule["STATUS"] == "ACTIVE"
    assert rule["CHECK_SQL"].startswith("-- SITE-001 v1: a rule")  # stored header follows
    assert store.get_rule("SITE-003") is None
    assert len(store.list_rule_history("SITE-001")) == 2
    assert store.list_rule_history("SITE-003") == []
    assert (
        counts(store_path, "SELECT COUNT(*) FROM DQ_RULE_RESULT WHERE RULE_CODE = ?", ["SITE-001"])
        == 1
    )
    # Finding IDs are recomputed from the new code, and keep their status and exception.
    r1 = store.get_finding(compute_finding_id("SITE-001", "r1", None))
    r2 = store.get_finding(compute_finding_id("SITE-001", "r2", "Milestone A"))
    assert r1["RULE_CODE"] == "SITE-001" and r1["STATUS"] == "ACCEPTED_EXCEPTION"
    assert r1["DISPOSITION_REASON"] == "Known issue"
    assert len(store.list_dispositions(r1["FINDING_ID"])) == 1
    assert r2["STATUS"] == "OPEN"
    assert store.get_finding(compute_finding_id("SITE-003", "r1", None)) is None
    assert (
        counts(store_path, "SELECT COUNT(*) FROM DQ_FINDING WHERE RULE_CODE = ?", ["SITE-003"]) == 0
    )
    # The other rule is untouched.
    assert store.get_finding(compute_finding_id("SITE-009", "x1", None)) is not None


def test_the_next_run_still_matches_the_same_findings(store_path, store):
    rule_id = seed_active_rule_with_everything(store_path, store)
    rule_ops.renumber_rules(store, {"SITE-003": "SITE-001"})

    store.start_run(RUN_2, "MANUAL", "kevin", T2)
    result = store.apply_findings(
        run_id=RUN_2,
        run_at=T2,
        rule_id=rule_id,
        rule_code="SITE-001",
        rule_version=1,
        source_object="SITE",
        sf_object="sitetracker__Site__c",
        sf_base_url=BASE,
        rows=[fail("r1"), fail("r2", "Milestone A")],
    )

    assert result.inserted == 0  # nothing looks new, so nothing was lost
    assert result.resolved_out_of_scope == 0
    assert store.get_finding(compute_finding_id("SITE-001", "r1", None))["STATUS"] == (
        "ACCEPTED_EXCEPTION"
    )


def test_a_chain_of_renumbers_reuses_codes_without_colliding(store_path, store):
    ids = {code: insert_rule(store_path, code) for code in ("SITE-003", "SITE-007", "SITE-008")}

    rule_ops.renumber_rules(
        store, {"SITE-003": "SITE-001", "SITE-008": "SITE-002", "SITE-007": "SITE-003"}
    )

    assert store.get_rule("SITE-001")["RULE_ID"] == ids["SITE-003"]
    assert store.get_rule("SITE-002")["RULE_ID"] == ids["SITE-008"]
    assert store.get_rule("SITE-003")["RULE_ID"] == ids["SITE-007"]  # took over the vacated code
    assert store.get_rule("SITE-007") is None and store.get_rule("SITE-008") is None


def test_a_dry_run_reports_counts_and_writes_nothing(store_path, store):
    seed_active_rule_with_everything(store_path, store)

    result = rule_ops.renumber_rules(store, {"SITE-003": "SITE-001"}, dry_run=True)

    assert result["dry_run"] is True
    assert result["renumbered"][0]["findings"] == 2
    assert store.get_rule("SITE-003") is not None and store.get_rule("SITE-001") is None
    assert store.get_finding(compute_finding_id("SITE-003", "r1", None)) is not None


def test_a_rule_with_nothing_to_move_just_changes_code(store_path, store):
    insert_rule(store_path, "SITE-007")

    result = rule_ops.renumber_rules(store, {"SITE-007": "SITE-003"})

    assert result["renumbered"][0]["findings"] == 0
    assert store.get_rule("SITE-003") is not None


# --- what is refused, with nothing written ------------------------------------------------


@pytest.mark.parametrize(
    "mapping,message",
    [
        ({}, "nothing to renumber"),
        ({"SITE-099": "SITE-001"}, "Unknown rule"),
        ({"SITE-003": "SITE-003"}, "already has that code"),
        ({"SITE-003": "SITE-007"}, "already exists"),  # target taken and not being vacated
        ({"SITE-003": "SITE-001", "SITE-008": "SITE-001"}, "more than one rule"),
        ({"SITE-003": "PRJ-001"}, "same prefix"),
        ({"SITE-003": "site-1"}, "not a valid rule code"),
    ],
)
def test_bad_requests_are_refused_and_change_nothing(store_path, store, mapping, message):
    for code in ("SITE-003", "SITE-007", "SITE-008"):
        insert_rule(store_path, code)

    with pytest.raises(rule_ops.RuleOpsError, match=message):
        rule_ops.renumber_rules(store, mapping)

    assert {r["RULE_CODE"] for r in store.list_rules()} == {"SITE-003", "SITE-007", "SITE-008"}


def test_a_target_being_vacated_in_the_same_request_is_allowed(store_path, store):
    insert_rule(store_path, "SITE-003")
    insert_rule(store_path, "SITE-007")

    rule_ops.renumber_rules(store, {"SITE-003": "SITE-004", "SITE-007": "SITE-003"})

    assert {r["RULE_CODE"] for r in store.list_rules()} == {"SITE-003", "SITE-004"}


def test_a_failure_part_way_rolls_everything_back(store_path, store, monkeypatch):
    seed_active_rule_with_everything(store_path, store)

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    # Break the last step, after the rule, history and results have already been rewritten.
    monkeypatch.setattr("dq.store.duckdb_store.compute_finding_id", boom)

    with pytest.raises(RuntimeError, match="disk on fire"):
        rule_ops.renumber_rules(store, {"SITE-003": "SITE-001"})

    assert store.get_rule("SITE-003") is not None and store.get_rule("SITE-001") is None
    assert (
        counts(store_path, "SELECT COUNT(*) FROM DQ_RULE_RESULT WHERE RULE_CODE = ?", ["SITE-003"])
        == 1
    )
    assert len(store.list_rule_history("SITE-003")) == 2
