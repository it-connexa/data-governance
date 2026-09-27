"""Tests for the pure (non-st.*) helpers in dq.app.ui. render_lifecycle_stepper
and render_sidebar call st.* directly and are verified by running the app,
never by pytest -- see the module docstring."""

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
