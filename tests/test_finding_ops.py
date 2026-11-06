"""Tests for finding_ops: who may set a disposition, what it needs, and the
review flag -- against a temp DuckDB store, never ~/dq-data."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from dq import finding_ops
from dq.lifecycle import compute_finding_id
from dq.store.base import FindingInput
from dq.store.duckdb_store import DuckDBStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
OWNER = {"OWNER_EMAIL": "owner@connexa.co.nz", "IS_APPROVER": False}
OTHER = {"OWNER_EMAIL": "other@connexa.co.nz", "IS_APPROVER": False}
APPROVER = {"OWNER_EMAIL": "approver@connexa.co.nz", "IS_APPROVER": True}


def insert_rule(store_path, rule_code: str, owner: str = "owner@connexa.co.nz") -> int:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (
                RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CREATED_BY
            ) VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'SITE', 'VALIDITY', 'HIGH',
                      ?, 'ACTIVE', 'test')
            """,
            [rule_code, f"{rule_code} test rule", owner],
        )
        row = con.execute("SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code]).fetchone()
        return row[0]
    finally:
        con.close()


@pytest.fixture
def store(store_path):
    return DuckDBStore(store_path)


@pytest.fixture
def ids(store_path, store) -> dict[str, str]:
    """Three open findings on SITE-900 (owner) and one on SITE-901 (other)."""
    rule_a = insert_rule(store_path, "SITE-900")
    rule_b = insert_rule(store_path, "SITE-901", owner="other@connexa.co.nz")
    store.start_run("run-1", "MANUAL", "kevin", NOW)
    for rule_id, code, records in (
        (rule_a, "SITE-900", ["r1", "r2", "r3"]),
        (rule_b, "SITE-901", ["r4"]),
    ):
        store.apply_findings(
            run_id="run-1",
            run_at=NOW,
            rule_id=rule_id,
            rule_code=code,
            rule_version=1,
            source_object="SITE",
            sf_object="Site__c",
            sf_base_url="https://x.lightning.force.com",
            rows=[FindingInput(r, None, r, r, r, False, None) for r in records],
        )
    return {
        "r1": compute_finding_id("SITE-900", "r1", None),
        "r2": compute_finding_id("SITE-900", "r2", None),
        "r3": compute_finding_id("SITE-900", "r3", None),
        "r4": compute_finding_id("SITE-901", "r4", None),
    }


# --- accept_exception ------------------------------------------------------------


def test_accept_exception_defaults_expiry_from_config(store, ids):
    result = finding_ops.accept_exception(
        store, [ids["r1"]], "known issue", owner=OWNER, facilitated_by="kevin", now=NOW
    )

    assert result.count == 1
    assert result.expires_at == NOW + timedelta(days=365)  # seeded EXCEPTION_DEFAULT_DAYS
    finding = store.get_finding(ids["r1"])
    assert finding["STATUS"] == "ACCEPTED_EXCEPTION"
    assert finding["DISPOSITION_SET_BY"] == "owner@connexa.co.nz"


def test_accept_exception_uses_a_given_expiry(store, ids):
    expires = NOW + timedelta(days=30)

    result = finding_ops.accept_exception(
        store, [ids["r1"], ids["r2"]], "known", owner=OWNER, expires_at=expires, now=NOW
    )

    assert result.count == 2
    assert result.expires_at == expires


def test_accept_exception_rejects_an_expiry_in_the_past(store, ids):
    with pytest.raises(finding_ops.FindingOpsError, match="future"):
        finding_ops.accept_exception(
            store, [ids["r1"]], "known", owner=OWNER, expires_at=NOW - timedelta(days=1), now=NOW
        )

    assert store.get_finding(ids["r1"])["STATUS"] == "OPEN"


def test_accept_exception_falls_back_to_365_days_if_config_is_missing(store, store_path, ids):
    con = duckdb.connect(str(store_path))
    con.execute("DELETE FROM DQ_CONFIG WHERE CONFIG_KEY = 'EXCEPTION_DEFAULT_DAYS'")
    con.close()

    result = finding_ops.accept_exception(store, [ids["r1"]], "known", owner=OWNER, now=NOW)

    assert result.expires_at == NOW + timedelta(days=365)


# --- mark_false_positive ---------------------------------------------------------


def test_mark_false_positive_needs_no_expiry(store, ids):
    result = finding_ops.mark_false_positive(
        store, [ids["r1"]], "rule is wrong here", owner=OWNER, now=NOW
    )

    assert result.count == 1
    assert result.expires_at is None
    assert store.get_finding(ids["r1"])["STATUS"] == "FALSE_POSITIVE"


# --- revoke_disposition ----------------------------------------------------------


def test_revoke_returns_an_accepted_finding_to_open(store, ids):
    finding_ops.accept_exception(store, [ids["r1"]], "known", owner=OWNER, now=NOW)

    finding_ops.revoke_disposition(store, [ids["r1"]], "no longer true", owner=OWNER, now=NOW)

    assert store.get_finding(ids["r1"])["STATUS"] == "OPEN"


def test_revoke_rejects_a_finding_with_no_disposition(store, ids):
    with pytest.raises(finding_ops.FindingOpsError, match="nothing to revoke"):
        finding_ops.revoke_disposition(store, [ids["r1"]], "oops", owner=OWNER, now=NOW)


# --- shared validation -----------------------------------------------------------


@pytest.mark.parametrize("reason", ["", "   "])
def test_every_disposition_needs_a_reason(store, ids, reason):
    with pytest.raises(finding_ops.FindingOpsError, match="reason"):
        finding_ops.mark_false_positive(store, [ids["r1"]], reason, owner=OWNER, now=NOW)


def test_no_findings_selected_is_an_error(store):
    with pytest.raises(finding_ops.FindingOpsError, match="at least one"):
        finding_ops.mark_false_positive(store, [], "x", owner=OWNER, now=NOW)


def test_unknown_finding_is_an_error(store, ids):
    with pytest.raises(finding_ops.FindingOpsError, match="Unknown finding"):
        finding_ops.mark_false_positive(store, [ids["r1"], "nope"], "x", owner=OWNER, now=NOW)

    assert store.get_finding(ids["r1"])["STATUS"] == "OPEN"


def test_a_resolved_finding_cannot_be_disposed(store, store_path, ids):
    con = duckdb.connect(str(store_path))
    con.execute("UPDATE DQ_FINDING SET STATUS = 'RESOLVED' WHERE FINDING_ID = ?", [ids["r1"]])
    con.close()

    with pytest.raises(finding_ops.FindingOpsError, match="resolved"):
        finding_ops.accept_exception(store, [ids["r1"]], "known", owner=OWNER, now=NOW)


def test_owner_cannot_dispose_another_owners_finding(store, ids):
    with pytest.raises(finding_ops.FindingOpsError, match="own"):
        finding_ops.mark_false_positive(store, [ids["r4"]], "x", owner=OWNER, now=NOW)

    assert store.get_finding(ids["r4"])["STATUS"] == "OPEN"


def test_an_approver_may_dispose_any_finding(store, ids):
    finding_ops.mark_false_positive(store, [ids["r4"]], "approved call", owner=APPROVER, now=NOW)

    assert store.get_finding(ids["r4"])["STATUS"] == "FALSE_POSITIVE"


def test_bulk_is_all_or_nothing(store, ids):
    """One finding the owner doesn't own: none of the three change."""
    with pytest.raises(finding_ops.FindingOpsError, match="own"):
        finding_ops.mark_false_positive(
            store, [ids["r1"], ids["r2"], ids["r4"]], "x", owner=OWNER, now=NOW
        )

    for key in ("r1", "r2", "r4"):
        assert store.get_finding(ids[key])["STATUS"] == "OPEN"


def test_duplicate_ids_are_applied_once(store, ids):
    result = finding_ops.mark_false_positive(
        store, [ids["r1"], ids["r1"]], "x", owner=OWNER, now=NOW
    )

    assert result.count == 1
    assert len(store.list_dispositions(ids["r1"])) == 1


# --- review flag -----------------------------------------------------------------


def test_flag_rule_for_review_requires_a_reason(store, ids):
    with pytest.raises(finding_ops.FindingOpsError, match="reason"):
        finding_ops.flag_rule_for_review(store, "SITE-900", " ", owner=OWNER)


def test_flag_and_clear_round_trip(store, ids):
    finding_ops.flag_rule_for_review(
        store, "SITE-900", "too noisy", owner=OWNER, facilitated_by="k"
    )

    assert store.get_rule_review_flag("SITE-900")["REASON"] == "too noisy"

    finding_ops.clear_review_flag(store, "SITE-900", owner=OWNER, facilitated_by="k")

    assert store.get_rule_review_flag("SITE-900") is None


def test_only_the_rule_owner_or_an_approver_may_flag_or_clear(store, ids):
    with pytest.raises(finding_ops.FindingOpsError, match="own"):
        finding_ops.flag_rule_for_review(store, "SITE-900", "x", owner=OTHER)

    finding_ops.flag_rule_for_review(store, "SITE-900", "approver view", owner=APPROVER)
    with pytest.raises(finding_ops.FindingOpsError, match="own"):
        finding_ops.clear_review_flag(store, "SITE-900", owner=OTHER)


def test_flagging_an_unknown_rule_is_an_error(store):
    with pytest.raises(finding_ops.FindingOpsError, match="Unknown rule"):
        finding_ops.flag_rule_for_review(store, "NOPE-1", "x", owner=OWNER)


# --- summarise -------------------------------------------------------------------


def test_summarise_counts_by_status_and_flags_expiring_exceptions():
    findings = [
        {"STATUS": "OPEN"},
        {"STATUS": "OPEN"},
        {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=10)},
        {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=200)},
        {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW - timedelta(days=1)},
        {"STATUS": "FALSE_POSITIVE"},
        {"STATUS": "RESOLVED"},
    ]

    summary = finding_ops.summarise(findings, now=NOW)

    assert summary.open == 2
    assert summary.accepted == 3
    assert summary.expiring_soon == 1  # within 30 days, not already lapsed
    assert summary.expired == 1  # lapsed; reopens at the next run
    assert summary.false_positive == 1
    assert summary.resolved == 1


def test_is_lapsing_matches_the_summary_for_expiring_and_lapsed_exceptions():
    soon = {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=10)}
    far = {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=200)}
    lapsed = {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW - timedelta(days=1)}
    no_expiry = {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": None}

    assert finding_ops.is_lapsing(soon, now=NOW)
    assert finding_ops.is_lapsing(lapsed, now=NOW)
    assert not finding_ops.is_lapsing(far, now=NOW)
    assert not finding_ops.is_lapsing(no_expiry, now=NOW)
    assert not finding_ops.is_lapsing({"STATUS": "OPEN"}, now=NOW)
    assert not finding_ops.is_lapsing({"STATUS": "FALSE_POSITIVE"}, now=NOW)
