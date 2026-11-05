"""Tests for the pure (non-st.*) helpers in dq.app.ui. render_lifecycle_stepper
and render_sidebar call st.* directly and are verified by running the app,
never by pytest -- see the module docstring."""

from datetime import UTC, datetime, timedelta

from dq.app import ui


def test_select_index_finds_value():
    assert ui.select_index(["A", "B", "C"], "B") == 1


def test_select_index_falls_back_to_default_when_missing():
    assert ui.select_index(["A", "B", "C"], "NOPE") == 0
    assert ui.select_index(["A", "B", "C"], "NOPE", default=2) == 2


def test_select_index_falls_back_to_default_when_none():
    assert ui.select_index(["A", "B", "C"], None, default=1) == 1


def test_status_badge_contains_friendly_label_and_color():
    html = ui.status_badge("READY_FOR_COMPILE")
    assert "Ready for compile" in html
    assert ui._STATUS_COLORS["READY_FOR_COMPILE"] in html


def test_status_badge_falls_back_to_raw_status_for_unknown_value():
    html = ui.status_badge("SOMETHING_NEW")
    assert "SOMETHING_NEW" in html


def test_next_step_guidance_covers_every_lifecycle_status():
    for status in [
        "DRAFT",
        "NEEDS_CLARIFICATION",
        "READY_FOR_COMPILE",
        "COMPILED",
        "OWNER_CONFIRMED",
        "ACTIVE",
        "RETIRED",
    ]:
        icon, message = ui.next_step_guidance(status)
        assert icon
        assert message


def test_next_step_guidance_owner_confirmed_differs_for_approver():
    _, owner_message = ui.next_step_guidance("OWNER_CONFIRMED", is_approver=False)
    _, approver_message = ui.next_step_guidance("OWNER_CONFIRMED", is_approver=True)
    assert "Approve" in approver_message
    assert "Approve" not in owner_message


# --- NZ time and finding status labels ------------------------------------------------

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def test_format_nz_shows_auckland_date():
    # 11:30pm UTC on 14 Oct is already 15 Oct (NZDT, UTC+13) in Auckland.
    assert ui.format_nz(datetime(2026, 10, 14, 23, 30, tzinfo=UTC)) == "15 Oct 2026"


def test_format_nz_datetime_includes_time():
    assert ui.format_nz_datetime(datetime(2026, 10, 14, 23, 30, tzinfo=UTC)) == (
        "15 Oct 2026, 12:30 pm"
    )


def test_format_nz_handles_none():
    assert ui.format_nz(None) == "—"
    assert ui.format_nz_datetime(None) == "—"


def test_finding_status_label_for_each_status():
    assert ui.finding_status_label({"STATUS": "OPEN"}, now=NOW) == "Open"
    assert ui.finding_status_label({"STATUS": "FALSE_POSITIVE"}, now=NOW) == "False positive"
    assert (
        ui.finding_status_label({"STATUS": "RESOLVED", "RESOLUTION_REASON": "FIXED"}, now=NOW)
        == "Resolved (fixed)"
    )
    assert (
        ui.finding_status_label(
            {"STATUS": "RESOLVED", "RESOLUTION_REASON": "OUT_OF_SCOPE"}, now=NOW
        )
        == "Resolved (out of scope)"
    )
    assert (
        ui.finding_status_label(
            {"STATUS": "RESOLVED", "RESOLUTION_REASON": "RULE_RETIRED"}, now=NOW
        )
        == "Resolved (rule retired)"
    )


def test_finding_status_label_for_accepted_exception_shows_expiry_or_lapse():
    live = {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=40)}
    lapsed = {"STATUS": "ACCEPTED_EXCEPTION", "DISPOSITION_EXPIRES_AT": NOW - timedelta(days=1)}

    assert ui.finding_status_label(live, now=NOW) == "Accepted until 12 Jul 2026"
    assert ui.finding_status_label(lapsed, now=NOW) == "Accepted — lapsed, reopens next run"
