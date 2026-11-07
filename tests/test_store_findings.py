"""Tests for reading findings and writing dispositions on DuckDBStore --
against a temp DB built from the real schema file, never ~/dq-data."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from dq.lifecycle import compute_finding_id
from dq.store.base import FindingInput
from dq.store.duckdb_store import DuckDBStore

RUN_1 = "11111111-1111-1111-1111-111111111111"
RUN_2 = "22222222-2222-2222-2222-222222222222"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 8, tzinfo=UTC)


def insert_rule(
    store_path: Path, *, rule_code: str, owner: str = "owner@connexa.co.nz", severity: str = "HIGH"
) -> int:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (
                RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CREATED_BY
            ) VALUES (?, ?, ?, 'SITE_MASTER', 'SITE', 'SITE', 'VALIDITY', ?,
                      ?, 'ACTIVE', 'test')
            """,
            [rule_code, f"{rule_code} test rule", f"{rule_code} must hold", severity, owner],
        )
        return con.execute(
            "SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code]
        ).fetchone()[0]
    finally:
        con.close()


def fail(record_id: str, name: str | None = None) -> FindingInput:
    return FindingInput(
        record_id, None, name or record_id, record_id, name or record_id, False, '{"why": "x"}'
    )


def apply(store: DuckDBStore, rule_id: int, rule_code: str, run_id: str, at, rows):
    return store.apply_findings(
        run_id=run_id,
        run_at=at,
        rule_id=rule_id,
        rule_code=rule_code,
        rule_version=1,
        source_object="SITE",
        sf_object="Site__c",
        sf_base_url="https://connexa.lightning.force.com",
        rows=rows,
    )


def seed(store_path: Path, store: DuckDBStore) -> dict[str, str]:
    """Two rules (two owners), a few findings; returns finding ids by label."""
    a = insert_rule(store_path, rule_code="SITE-900", severity="CRITICAL")
    b = insert_rule(store_path, rule_code="SITE-901", owner="other@connexa.co.nz", severity="LOW")
    store.start_run(RUN_1, "MANUAL", "kevin", T1)
    apply(store, a, "SITE-900", RUN_1, T1, [fail("r1", "Site One"), fail("r2", "Site Two")])
    apply(store, b, "SITE-901", RUN_1, T1, [fail("r3", "Site Three")])
    return {
        "r1": compute_finding_id("SITE-900", "r1", None),
        "r2": compute_finding_id("SITE-900", "r2", None),
        "r3": compute_finding_id("SITE-901", "r3", None),
    }


# --- list_findings / get_finding -----------------------------------------------


def test_findings_carry_the_rules_plain_english_statement(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    listed = {f["FINDING_ID"]: f for f in store.list_findings()}

    assert listed[ids["r1"]]["RULE_STATEMENT"] == "SITE-900 must hold"
    assert store.get_finding(ids["r3"])["RULE_STATEMENT"] == "SITE-901 must hold"


def test_list_findings_joins_rule_fields_and_sorts_by_severity(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    rows = store.list_findings()

    assert [r["FINDING_ID"] for r in rows][-1] == ids["r3"]  # LOW severity sorts last
    first = rows[0]
    assert first["RULE_NAME"] == "SITE-900 test rule"
    assert first["SEVERITY"] == "CRITICAL"
    assert first["OWNER_EMAIL"] == "owner@connexa.co.nz"
    assert first["STATUS"] == "OPEN"
    assert first["DISPOSITION"] is None
    assert isinstance(first["DETAIL"], str)  # JSON comes back as text, not a Python object
    assert first["AGE_DAYS"] >= 0


def test_list_findings_filters_by_owner(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    rows = store.list_findings("other@connexa.co.nz")

    assert [r["FINDING_ID"] for r in rows] == [ids["r3"]]


def test_get_finding_returns_one_or_none(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    assert store.get_finding(ids["r1"])["RECORD_NAME"] == "Site One"
    assert store.get_finding("nope") is None


# --- set_dispositions -----------------------------------------------------------


def test_accepting_an_exception_updates_status_immediately(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)
    expires = datetime.now(UTC) + timedelta(days=30)

    store.set_dispositions(
        [ids["r1"], ids["r2"]],
        "ACCEPTED_EXCEPTION",
        "Known issue, fixing next quarter",
        set_by="owner@connexa.co.nz",
        facilitated_by="kevin",
        expires_at=expires,
    )

    for key in ("r1", "r2"):
        finding = store.get_finding(ids[key])
        assert finding["STATUS"] == "ACCEPTED_EXCEPTION"
        assert finding["DISPOSITION"] == "ACCEPTED_EXCEPTION"
        assert finding["DISPOSITION_REASON"] == "Known issue, fixing next quarter"
        assert finding["DISPOSITION_SET_BY"] == "owner@connexa.co.nz"
        assert finding["DISPOSITION_EXPIRES_AT"] is not None
    assert store.get_finding(ids["r3"])["STATUS"] == "OPEN"


def test_false_positive_updates_status_and_needs_no_expiry(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    store.set_dispositions(
        [ids["r1"]], "FALSE_POSITIVE", "Rule wrongly flags this", set_by="owner@connexa.co.nz"
    )

    finding = store.get_finding(ids["r1"])
    assert finding["STATUS"] == "FALSE_POSITIVE"
    assert finding["DISPOSITION_EXPIRES_AT"] is None


def test_revoking_returns_finding_to_open_and_clears_disposition_fields(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)
    store.set_dispositions([ids["r1"]], "FALSE_POSITIVE", "wrong", set_by="owner@connexa.co.nz")

    store.set_dispositions([ids["r1"]], "REVOKED", "changed my mind", set_by="owner@connexa.co.nz")

    finding = store.get_finding(ids["r1"])
    assert finding["STATUS"] == "OPEN"
    assert finding["DISPOSITION"] is None
    assert finding["DISPOSITION_REASON"] is None


def test_list_dispositions_is_newest_first_and_keeps_revoked_rows(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)
    store.set_dispositions([ids["r1"]], "FALSE_POSITIVE", "first", set_by="a@connexa.co.nz")
    store.set_dispositions([ids["r1"]], "REVOKED", "second", set_by="b@connexa.co.nz")

    history = store.list_dispositions(ids["r1"])

    assert [h["DISPOSITION"] for h in history] == ["REVOKED", "FALSE_POSITIVE"]
    assert [h["SET_BY"] for h in history] == ["b@connexa.co.nz", "a@connexa.co.nz"]


def test_unknown_finding_id_raises_and_writes_nothing(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    with pytest.raises(ValueError, match="nope"):
        store.set_dispositions(
            [ids["r1"], "nope"], "FALSE_POSITIVE", "x", set_by="owner@connexa.co.nz"
        )

    assert store.get_finding(ids["r1"])["STATUS"] == "OPEN"
    assert store.list_dispositions(ids["r1"]) == []


def test_accepted_exception_without_expiry_is_rejected_atomically(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)

    with pytest.raises(duckdb.ConstraintException):
        store.set_dispositions(
            [ids["r1"], ids["r2"]], "ACCEPTED_EXCEPTION", "x", set_by="owner@connexa.co.nz"
        )

    assert store.get_finding(ids["r1"])["STATUS"] == "OPEN"
    assert store.get_finding(ids["r2"])["STATUS"] == "OPEN"


# --- interplay with the engine's next run ----------------------------------------


def test_accepted_exception_survives_the_next_run(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)
    rule_id = store.list_findings()[0]["RULE_ID"]
    store.set_dispositions(
        [ids["r1"]],
        "ACCEPTED_EXCEPTION",
        "known",
        set_by="owner@connexa.co.nz",
        expires_at=datetime.now(UTC) + timedelta(days=365),
    )

    apply(store, rule_id, "SITE-900", RUN_2, T2, [fail("r1", "Site One"), fail("r2", "Site Two")])

    assert store.get_finding(ids["r1"])["STATUS"] == "ACCEPTED_EXCEPTION"
    assert store.get_finding(ids["r2"])["STATUS"] == "OPEN"


def test_revoked_finding_is_open_after_the_next_run(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)
    rule_id = store.list_findings()[0]["RULE_ID"]
    store.set_dispositions([ids["r1"]], "FALSE_POSITIVE", "wrong", set_by="o@connexa.co.nz")
    store.set_dispositions([ids["r1"]], "REVOKED", "oops", set_by="o@connexa.co.nz")

    apply(store, rule_id, "SITE-900", RUN_2, T2, [fail("r1", "Site One")])

    assert store.get_finding(ids["r1"])["STATUS"] == "OPEN"


def test_count_findings_by_status_for_one_rule(store_path):
    store = DuckDBStore(store_path)
    ids = seed(store_path, store)
    store.set_dispositions([ids["r1"]], "FALSE_POSITIVE", "wrong", set_by="owner@connexa.co.nz")

    assert store.count_findings_by_status("SITE-900") == {"OPEN": 1, "FALSE_POSITIVE": 1}
    assert store.count_findings_by_status("SITE-901") == {"OPEN": 1}
    assert store.count_findings_by_status("NOPE-1") == {}


# --- stored links are repaired by a later run -------------------------------------------


def test_a_later_run_repairs_the_stored_link_of_every_finding_of_the_rule(store_path):
    store = DuckDBStore(store_path)
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    base = "https://connexa.lightning.force.com"

    # First run, with the sources.yml placeholder still in place: no link is stored.
    store.start_run(RUN_1, "MANUAL", "kevin", T1)
    store.apply_findings(
        run_id=RUN_1,
        run_at=T1,
        rule_id=rule_id,
        rule_code="SITE-900",
        rule_version=1,
        source_object="SITE",
        sf_object="<SITE_OBJECT_API_NAME>",
        sf_base_url=base,
        rows=[fail("r1"), fail("r2")],
    )
    con = duckdb.connect(str(store_path))
    assert con.execute(
        "SELECT COUNT(*) FROM DQ_FINDING WHERE RECORD_URL IS NOT NULL"
    ).fetchone() == (0,)
    con.close()

    # Second run, config fixed: r1 still fails, r2 has gone (resolved), and both get the link.
    store.start_run(RUN_2, "MANUAL", "kevin", T2)
    store.apply_findings(
        run_id=RUN_2,
        run_at=T2,
        rule_id=rule_id,
        rule_code="SITE-900",
        rule_version=1,
        source_object="SITE",
        sf_object="sitetracker__Site__c",
        sf_base_url=base + "/",
        rows=[fail("r1")],
    )
    con = duckdb.connect(str(store_path))
    urls = dict(con.execute("SELECT RECORD_ID, RECORD_URL FROM DQ_FINDING").fetchall())
    con.close()
    assert urls == {
        "r1": f"{base}/lightning/r/sitetracker__Site__c/r1/view",
        "r2": f"{base}/lightning/r/sitetracker__Site__c/r2/view",
    }


def test_repair_only_touches_the_rule_being_applied(store_path):
    store = DuckDBStore(store_path)
    a = insert_rule(store_path, rule_code="SITE-900")
    b = insert_rule(store_path, rule_code="SITE-901")
    base = "https://connexa.lightning.force.com"
    store.start_run(RUN_1, "MANUAL", "kevin", T1)
    for rule_id, code, obj in [(a, "SITE-900", "Site__c"), (b, "SITE-901", "Other__c")]:
        store.apply_findings(
            run_id=RUN_1,
            run_at=T1,
            rule_id=rule_id,
            rule_code=code,
            rule_version=1,
            source_object="SITE",
            sf_object=obj,
            sf_base_url=base,
            rows=[fail("r1")],
        )
    con = duckdb.connect(str(store_path))
    urls = dict(con.execute("SELECT RULE_CODE, RECORD_URL FROM DQ_FINDING").fetchall())
    con.close()
    assert urls["SITE-900"].endswith("/lightning/r/Site__c/r1/view")
    assert urls["SITE-901"].endswith("/lightning/r/Other__c/r1/view")
