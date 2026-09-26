import hashlib

from dq.lifecycle import (
    Action,
    DispositionType,
    FindingStatus,
    ResolutionReason,
    Verdict,
    build_record_url,
    compute_finding_id,
    decide,
    decide_retired,
)

# --- brief §10.2's table, row by row -----------------------------------------


def test_fails_no_finding_inserts_open():
    d = decide(None, None, Verdict.FAILED)
    assert d.action == Action.INSERT_OPEN
    assert d.new_status == FindingStatus.OPEN


def test_fails_finding_open_updates_last_seen():
    d = decide(FindingStatus.OPEN, None, Verdict.FAILED)
    assert d.action == Action.UPDATE_LAST_SEEN
    assert d.new_status == FindingStatus.OPEN
    assert d.increment_times_seen is True
    assert d.increment_reopened_count is False


def test_fails_finding_resolved_reopens():
    d = decide(FindingStatus.RESOLVED, None, Verdict.FAILED)
    assert d.action == Action.REOPEN
    assert d.new_status == FindingStatus.OPEN
    assert d.increment_reopened_count is True
    assert d.clear_resolved_fields is True


def test_fails_active_disposition_sets_disposition_status():
    d = decide(FindingStatus.ACCEPTED_EXCEPTION, DispositionType.ACCEPTED_EXCEPTION, Verdict.FAILED)
    assert d.action == Action.APPLY_DISPOSITION
    assert d.new_status == FindingStatus.ACCEPTED_EXCEPTION
    assert d.increment_reopened_count is False
    assert d.clear_resolved_fields is False


def test_fails_active_disposition_false_positive():
    d = decide(FindingStatus.FALSE_POSITIVE, DispositionType.FALSE_POSITIVE, Verdict.FAILED)
    assert d.action == Action.APPLY_DISPOSITION
    assert d.new_status == FindingStatus.FALSE_POSITIVE


def test_fails_disposition_expired_or_revoked_reopens_plainly():
    # Finding is still marked ACCEPTED_EXCEPTION in the store, but the active
    # disposition lookup found nothing (expired or revoked) -> OPEN.
    d = decide(FindingStatus.ACCEPTED_EXCEPTION, None, Verdict.FAILED)
    assert d.action == Action.REACTIVATE
    assert d.new_status == FindingStatus.OPEN
    assert d.increment_reopened_count is False


def test_passes_finding_not_resolved_resolves_fixed():
    for status in (
        FindingStatus.OPEN,
        FindingStatus.ACCEPTED_EXCEPTION,
        FindingStatus.FALSE_POSITIVE,
    ):
        d = decide(status, None, Verdict.PASSED)
        assert d.action == Action.RESOLVE_FIXED
        assert d.new_status == FindingStatus.RESOLVED
        assert d.resolution_reason == ResolutionReason.FIXED


def test_passes_no_finding_is_no_change():
    d = decide(None, None, Verdict.PASSED)
    assert d.action == Action.NO_CHANGE
    assert d.new_status is None


def test_passes_already_resolved_is_no_change():
    d = decide(FindingStatus.RESOLVED, None, Verdict.PASSED)
    assert d.action == Action.NO_CHANGE


def test_not_in_evaluated_set_not_resolved_resolves_out_of_scope():
    for status in (
        FindingStatus.OPEN,
        FindingStatus.ACCEPTED_EXCEPTION,
        FindingStatus.FALSE_POSITIVE,
    ):
        d = decide(status, None, Verdict.NOT_EVALUATED)
        assert d.action == Action.RESOLVE_OUT_OF_SCOPE
        assert d.new_status == FindingStatus.RESOLVED
        assert d.resolution_reason == ResolutionReason.OUT_OF_SCOPE


def test_not_in_evaluated_set_no_finding_is_no_change():
    d = decide(None, None, Verdict.NOT_EVALUATED)
    assert d.action == Action.NO_CHANGE


def test_not_in_evaluated_set_already_resolved_is_no_change():
    d = decide(FindingStatus.RESOLVED, None, Verdict.NOT_EVALUATED)
    assert d.action == Action.NO_CHANGE


# Rule ERRORED: the caller simply never calls decide() for that rule's
# findings, so there is nothing to assert here beyond "not our concern".


# --- rule retirement (brief §10.1 step 4) ------------------------------------


def test_retired_rule_closes_open_finding():
    d = decide_retired(FindingStatus.OPEN)
    assert d.action == Action.CLOSE_RULE_RETIRED
    assert d.new_status == FindingStatus.RESOLVED
    assert d.resolution_reason == ResolutionReason.RULE_RETIRED


def test_retired_rule_no_finding_is_no_change():
    assert decide_retired(None).action == Action.NO_CHANGE


def test_retired_rule_already_resolved_is_no_change():
    assert decide_retired(FindingStatus.RESOLVED).action == Action.NO_CHANGE


# --- idempotency: running twice with no change makes no further change -----


def test_running_twice_unchanged_still_fails_is_idempotent_in_shape():
    # First run: no finding -> INSERT_OPEN. Second run against the resulting
    # OPEN finding, still failing -> UPDATE_LAST_SEEN, not another insert.
    first = decide(None, None, Verdict.FAILED)
    assert first.action == Action.INSERT_OPEN
    second = decide(first.new_status, None, Verdict.FAILED)
    assert second.action == Action.UPDATE_LAST_SEEN


# --- finding id and record URL builders (brief §11: "the URL builder") ------


def test_compute_finding_id_matches_sha256_formula():
    expected = hashlib.sha256(b"SITE-001|r1|").hexdigest()
    assert compute_finding_id("SITE-001", "r1", None) == expected


def test_compute_finding_id_uses_item_key_when_present():
    expected = hashlib.sha256(b"MS-001|p1|Milestone A").hexdigest()
    assert compute_finding_id("MS-001", "p1", "Milestone A") == expected


def test_compute_finding_id_differs_by_item_key():
    a = compute_finding_id("MS-001", "p1", "Milestone A")
    b = compute_finding_id("MS-001", "p1", "Milestone B")
    assert a != b


def test_build_record_url():
    url = build_record_url("https://connexa.lightning.force.com", "Site__c", "a0B123")
    assert url == "https://connexa.lightning.force.com/lightning/r/Site__c/a0B123/view"


def test_build_record_url_strips_trailing_slash_on_base():
    url = build_record_url("https://connexa.lightning.force.com/", "Site__c", "a0B123")
    assert url == "https://connexa.lightning.force.com/lightning/r/Site__c/a0B123/view"
