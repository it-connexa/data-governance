"""Tests for the pure (non-st.*) helpers in dq.app.ui. The functions that call
st.* directly (page header, owner selector, lifecycle strip, layout CSS) are
verified by running the app and by the AppTest smoke tests, not here."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dq.app import ui


def test_select_index_finds_value():
    assert ui.select_index(["A", "B", "C"], "B") == 1


def test_select_index_falls_back_to_default_when_missing():
    assert ui.select_index(["A", "B", "C"], "NOPE") == 0
    assert ui.select_index(["A", "B", "C"], "NOPE", default=2) == 2


def test_select_index_falls_back_to_default_when_none():
    assert ui.select_index(["A", "B", "C"], None, default=1) == 1


def test_status_badge_is_a_native_colour_badge_with_the_friendly_label():
    assert ui.status_badge("READY_FOR_COMPILE") == ":violet-badge[Ready for compile]"
    assert ui.status_badge("ACTIVE") == ":green-badge[Active]"


def test_status_badge_falls_back_to_raw_status_for_unknown_value():
    assert ui.status_badge("SOMETHING_NEW") == ":gray-badge[SOMETHING_NEW]"


def test_every_status_has_a_distinct_label_and_a_valid_badge_colour():
    valid = {"red", "orange", "yellow", "blue", "green", "violet", "gray", "primary"}
    labels = [ui.status_label(s) for s in ui.STATUSES]
    assert len(set(labels)) == len(ui.STATUSES)
    assert {ui.status_color(s) for s in ui.STATUSES} <= valid


def test_status_cell_pairs_a_glyph_with_the_label_for_table_cells():
    cell = ui.status_cell("ACTIVE")
    assert cell.endswith("Active")
    assert cell != "Active"  # has a leading glyph
    assert ui.status_cell("SOMETHING_NEW").endswith("SOMETHING_NEW")


def test_severity_cell_pairs_a_glyph_with_the_title_case_label():
    assert ui.severity_cell("CRITICAL").endswith("Critical")
    assert ui.severity_cell("LOW").endswith("Low")
    assert ui.severity_cell(None) == "—"


def test_enum_label_turns_database_values_into_plain_words():
    assert ui.enum_label("CROSS_DOMAIN") == "Cross domain"
    assert ui.enum_label("MILESTONE") == "Milestone"
    assert ui.enum_label("SITE_MASTER") == "Site master"
    assert ui.enum_label(None) == "—"


# --- lifecycle strip, primary and secondary actions ---------------------------------


def _states(view):
    return [step.state for step in view.steps]


def test_lifecycle_view_marks_earlier_steps_done_and_later_steps_todo():
    view = ui.lifecycle_view("COMPILED")
    assert [s.label for s in view.steps] == ["Draft", "Ready", "Compiled", "Confirmed", "Active"]
    assert _states(view) == ["done", "done", "current", "todo", "todo"]
    assert not view.paused and not view.retired


def test_lifecycle_view_needs_clarification_is_a_pause_on_draft():
    view = ui.lifecycle_view("NEEDS_CLARIFICATION")
    assert _states(view) == ["current", "todo", "todo", "todo", "todo"]
    assert view.paused


def test_lifecycle_view_active_has_every_earlier_step_done():
    assert _states(ui.lifecycle_view("ACTIVE")) == ["done", "done", "done", "done", "current"]


def test_lifecycle_view_retired_is_terminal_not_a_position():
    view = ui.lifecycle_view("RETIRED")
    assert view.retired
    assert set(_states(view)) == {"todo"}


def test_primary_action_is_one_button_per_state_for_the_owner():
    assert ui.primary_action("DRAFT", is_approver=False).key == "submit"
    assert ui.primary_action("NEEDS_CLARIFICATION", is_approver=False).key == "answer"
    assert ui.primary_action("COMPILED", is_approver=False).key == "confirm"
    for status in ("READY_FOR_COMPILE", "OWNER_CONFIRMED", "ACTIVE", "RETIRED"):
        assert ui.primary_action(status, is_approver=False) is None


def test_primary_action_approve_is_for_approvers_only():
    assert ui.primary_action("OWNER_CONFIRMED", is_approver=True).key == "approve"
    assert ui.primary_action("OWNER_CONFIRMED", is_approver=False) is None


def test_secondary_actions_follow_the_lifecycle_state():
    assert ui.secondary_actions("DRAFT", is_approver=False) == ["edit", "delete"]
    assert ui.secondary_actions("COMPILED", is_approver=False) == ["edit", "reject"]
    assert ui.secondary_actions("COMPILED", is_approver=True) == ["edit", "reject", "retire"]
    assert ui.secondary_actions("ACTIVE", is_approver=False) == ["edit"]
    assert ui.secondary_actions("ACTIVE", is_approver=True) == ["edit", "retire"]
    assert ui.secondary_actions("READY_FOR_COMPILE", is_approver=True) == ["edit"]
    assert ui.secondary_actions("RETIRED", is_approver=True) == ["edit"]


def test_history_event_label_covers_every_change_type_in_the_schema():
    schema = Path("sql/dq_poc_duckdb_schema.sql").read_text()
    block = schema[schema.index("CHANGE_TYPE     VARCHAR NOT NULL CHECK") :]
    block = block[: block.index("STATUS_AFTER")]
    change_types = re.findall(r"'([A-Z_]+)'", block)
    assert len(change_types) >= 14
    for change_type in change_types:
        assert ui.history_event_label(change_type) != change_type, change_type
    assert ui.history_event_label("SOMETHING_NEW") == "Something new"


# --- rule filter pills ------------------------------------------------------------------

_RULES = [
    {"RULE_CODE": "A", "STATUS": "DRAFT"},
    {"RULE_CODE": "B", "STATUS": "NEEDS_CLARIFICATION"},
    {"RULE_CODE": "C", "STATUS": "READY_FOR_COMPILE"},
    {"RULE_CODE": "D", "STATUS": "COMPILED"},
    {"RULE_CODE": "E", "STATUS": "OWNER_CONFIRMED"},
    {"RULE_CODE": "F", "STATUS": "ACTIVE"},
    {"RULE_CODE": "G", "STATUS": "ACTIVE"},
    {"RULE_CODE": "H", "STATUS": "RETIRED"},
]


def test_rule_pill_counts_group_statuses_and_count_flagged_rules():
    counts = ui.rule_pill_counts(_RULES, flagged_codes={"F", "H"})
    assert counts == {"Draft": 2, "In progress": 3, "Active": 2, "Retired": 1, "Flagged": 2}


def test_rule_in_pill_matches_by_group_or_flag():
    active_flagged = {"RULE_CODE": "F", "STATUS": "ACTIVE"}
    assert ui.rule_in_pill(active_flagged, "Active", flagged_codes={"F"})
    assert ui.rule_in_pill(active_flagged, "Flagged", flagged_codes={"F"})
    assert not ui.rule_in_pill(active_flagged, "Draft", flagged_codes={"F"})
    assert not ui.rule_in_pill(active_flagged, "Flagged", flagged_codes=set())


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


# --- run results helpers ----------------------------------------------------------------


def test_format_age_in_plain_words():
    assert ui.format_age(NOW - timedelta(seconds=20), now=NOW) == "just now"
    assert ui.format_age(NOW - timedelta(minutes=1), now=NOW) == "1 minute ago"
    assert ui.format_age(NOW - timedelta(minutes=45), now=NOW) == "45 minutes ago"
    assert ui.format_age(NOW - timedelta(hours=1), now=NOW) == "1 hour ago"
    assert ui.format_age(NOW - timedelta(hours=5), now=NOW) == "5 hours ago"
    assert ui.format_age(NOW - timedelta(days=1), now=NOW) == "1 day ago"
    assert ui.format_age(NOW - timedelta(days=9), now=NOW) == "9 days ago"
    assert ui.format_age(None, now=NOW) == "never"


def test_format_duration():
    start = NOW
    assert ui.format_duration(start, start + timedelta(seconds=42)) == "42 s"
    assert ui.format_duration(start, start + timedelta(seconds=192)) == "3 min 12 s"
    assert ui.format_duration(start, start + timedelta(seconds=3600)) == "60 min 0 s"
    assert ui.format_duration(start, None) == "—"


def test_change_label_shows_direction_and_size():
    assert ui.change_label(5, 3) == "▼ 2"
    assert ui.change_label(3, 5) == "▲ 2"
    assert ui.change_label(4, 4) == "no change"
    assert ui.change_label(None, 4) == "first run"


def test_run_status_badge_is_a_native_colour_badge():
    assert ui.run_status_badge("PARTIAL") == ":orange-badge[Partial]"
    assert ui.run_status_badge("SUCCEEDED") == ":green-badge[Succeeded]"
    assert ui.run_status_badge("SOMETHING") == ":gray-badge[SOMETHING]"


# --- acting-as owner survives page changes ---------------------------------------------


class _FakeOwners:
    def list_owners(self):
        return [
            {"OWNER_EMAIL": "alice@example.test", "DISPLAY_NAME": "Alice"},
            {"OWNER_EMAIL": "carol@example.test", "DISPLAY_NAME": "Carol"},
        ]


def test_current_owner_remembers_the_choice_when_the_selector_state_is_dropped():
    import streamlit as st

    st.session_state.clear()
    store = _FakeOwners()

    st.session_state["acting_as_widget"] = "carol@example.test"
    assert ui.current_owner(store)["DISPLAY_NAME"] == "Carol"

    # Changing page drops the widget's own state; the choice must survive.
    del st.session_state["acting_as_widget"]
    assert ui.current_owner(store)["DISPLAY_NAME"] == "Carol"

    st.session_state.clear()
    assert ui.current_owner(store)["DISPLAY_NAME"] == "Alice"  # default: the first owner


def test_current_owner_ignores_a_remembered_owner_who_no_longer_exists():
    import streamlit as st

    st.session_state.clear()
    st.session_state["acting_as_email"] = "gone@example.test"
    assert ui.current_owner(_FakeOwners())["DISPLAY_NAME"] == "Alice"
    st.session_state.clear()


# --- findings pills and rules in view -----------------------------------------------------

_FINDINGS = [
    {
        "STATUS": "OPEN",
        "RULE_CODE": "A",
        "RULE_NAME": "Alpha",
        "SEVERITY": "LOW",
        "RULE_STATEMENT": "A holds",
        "OWNER_EMAIL": "o@x",
    },
    {
        "STATUS": "OPEN",
        "RULE_CODE": "B",
        "RULE_NAME": "Beta",
        "SEVERITY": "CRITICAL",
        "RULE_STATEMENT": "B holds",
        "OWNER_EMAIL": "o@x",
    },
    {
        "STATUS": "OPEN",
        "RULE_CODE": "B",
        "RULE_NAME": "Beta",
        "SEVERITY": "CRITICAL",
        "RULE_STATEMENT": "B holds",
        "OWNER_EMAIL": "o@x",
    },
    {
        "STATUS": "ACCEPTED_EXCEPTION",
        "RULE_CODE": "A",
        "RULE_NAME": "Alpha",
        "SEVERITY": "LOW",
        "RULE_STATEMENT": "A holds",
        "OWNER_EMAIL": "o@x",
        "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=5),
    },
    {
        "STATUS": "ACCEPTED_EXCEPTION",
        "RULE_CODE": "A",
        "RULE_NAME": "Alpha",
        "SEVERITY": "LOW",
        "RULE_STATEMENT": "A holds",
        "OWNER_EMAIL": "o@x",
        "DISPOSITION_EXPIRES_AT": NOW + timedelta(days=300),
    },
    {
        "STATUS": "FALSE_POSITIVE",
        "RULE_CODE": "A",
        "RULE_NAME": "Alpha",
        "SEVERITY": "LOW",
        "RULE_STATEMENT": "A holds",
        "OWNER_EMAIL": "o@x",
    },
    {
        "STATUS": "RESOLVED",
        "RULE_CODE": "A",
        "RULE_NAME": "Alpha",
        "SEVERITY": "LOW",
        "RULE_STATEMENT": "A holds",
        "OWNER_EMAIL": "o@x",
    },
]


def test_finding_pill_counts_cover_every_status_and_lapsing_exceptions():
    counts = ui.finding_pill_counts(_FINDINGS, now=NOW)
    assert counts == {"Open": 3, "Accepted": 2, "False positive": 1, "Resolved": 1, "Lapsing": 1}


def test_finding_in_pill_lapsing_is_a_subset_of_accepted():
    soon = _FINDINGS[3]
    far = _FINDINGS[4]
    assert ui.finding_in_pill(soon, "Accepted", now=NOW) and ui.finding_in_pill(
        soon, "Lapsing", now=NOW
    )
    assert ui.finding_in_pill(far, "Accepted", now=NOW) and not ui.finding_in_pill(
        far, "Lapsing", now=NOW
    )
    assert not ui.finding_in_pill(_FINDINGS[0], "Resolved", now=NOW)


def test_rules_in_view_groups_by_rule_most_severe_first_with_counts():
    rules = ui.rules_in_view(_FINDINGS)
    assert [r["code"] for r in rules] == ["B", "A"]  # critical before low
    assert rules[0] == {
        "code": "B",
        "name": "Beta",
        "severity": "CRITICAL",
        "statement": "B holds",
        "owner_email": "o@x",
        "findings": 2,
    }
    assert rules[1]["findings"] == 5


def test_rules_in_view_copes_with_a_missing_statement():
    rules = ui.rules_in_view(
        [{"RULE_CODE": "A", "RULE_NAME": "Alpha", "SEVERITY": "LOW", "OWNER_EMAIL": "o@x"}]
    )
    assert rules[0]["statement"] == ""


# --- the Overview to-do list ------------------------------------------------------------


def _rule(code, status, name=None):
    return {"RULE_CODE": code, "RULE_NAME": name or f"{code} name", "STATUS": status}


def test_todo_items_list_what_each_rule_state_waits_on():
    rules = [
        _rule("A", "DRAFT"),
        _rule("B", "NEEDS_CLARIFICATION"),
        _rule("C", "READY_FOR_COMPILE"),  # waiting on a data steward, not on you
        _rule("D", "COMPILED"),
        _rule("E", "OWNER_CONFIRMED"),
        _rule("F", "ACTIVE"),
    ]

    items = ui.todo_items(rules, flagged_codes=set(), is_approver=False, lapsing=0)

    assert [(i["rule"], i["verb"]) for i in items] == [
        ("B", "Answer a question on"),
        ("A", "Submit"),
        ("D", "Confirm"),
    ]
    assert all(i["page"] == ui.PAGE_RULES for i in items)


def test_todo_items_add_approvals_for_approvers_and_review_flags():
    rules = [_rule("E", "OWNER_CONFIRMED"), _rule("F", "ACTIVE")]

    items = ui.todo_items(rules, flagged_codes={"F"}, is_approver=True, lapsing=0)

    assert [(i["rule"], i["verb"]) for i in items] == [("E", "Approve"), ("F", "Review")]


def test_todo_items_end_with_lapsing_exceptions_that_link_to_findings():
    items = ui.todo_items([], flagged_codes=set(), is_approver=False, lapsing=3)

    assert len(items) == 1
    assert items[0]["page"] == ui.PAGE_FINDINGS
    assert items[0]["params"] == {"show": "Lapsing"}
    assert "3 accepted exceptions" in items[0]["label"]


def test_todo_items_singular_lapsing_and_empty_list():
    assert (
        "1 accepted exception "
        in ui.todo_items([], flagged_codes=set(), is_approver=False, lapsing=1)[0]["label"]
    )
    assert ui.todo_items([], flagged_codes=set(), is_approver=False, lapsing=0) == []


# --- Overview totals from the last run ----------------------------------------------------


def _summary(code, severity, open_, run="r2"):
    return {"RULE_CODE": code, "SEVERITY": severity, "ITEMS_FAILED_OPEN": open_, "RUN_ID": run}


def test_overview_totals_sum_open_and_critical_and_compare_with_the_previous_run():
    rows = [_summary("A", "CRITICAL", 5), _summary("B", "LOW", 7)]
    trend = {
        "A": [{"RUN_ID": "r1", "ITEMS_FAILED_OPEN": 8}, {"RUN_ID": "r2", "ITEMS_FAILED_OPEN": 5}],
        "B": [{"RUN_ID": "r1", "ITEMS_FAILED_OPEN": 4}, {"RUN_ID": "r2", "ITEMS_FAILED_OPEN": 7}],
    }

    totals = ui.overview_totals(rows, trend)

    assert totals == {"open": 12, "previous_open": 12, "critical_open": 5}


def test_overview_totals_have_no_previous_for_a_first_run_and_treat_null_as_zero():
    rows = [_summary("A", "HIGH", None)]
    trend = {"A": [{"RUN_ID": "r2", "ITEMS_FAILED_OPEN": None}]}

    assert ui.overview_totals(rows, trend) == {
        "open": 0,
        "previous_open": None,
        "critical_open": 0,
    }
    assert ui.overview_totals([], {}) == {"open": 0, "previous_open": None, "critical_open": 0}


# --- the Salesforce link on a finding -----------------------------------------------------


def test_record_url_for_uses_the_current_config_and_the_rules_source_object(fixtures_dir):
    from dq.sources import load_sources

    config = load_sources(fixtures_dir / "sources_test.yml")
    base = "https://connexa.lightning.force.com"
    site = {"SOURCE_OBJECT": "SITE", "RECORD_ID": "s1"}
    project = {"SOURCE_OBJECT": "PROJECT", "RECORD_ID": "p1"}
    milestone = {
        "SOURCE_OBJECT": "MILESTONE",
        "RECORD_ID": "p2",
    }  # a milestone's record is a project

    assert ui.record_url_for(site, config, base) == f"{base}/lightning/r/Site__c/s1/view"
    assert ui.record_url_for(project, config, base) == f"{base}/lightning/r/Project__c/p1/view"
    assert ui.record_url_for(milestone, config, base) == f"{base}/lightning/r/Project__c/p2/view"


def test_record_url_for_is_none_when_not_configured_or_the_source_is_unknown(fixtures_dir):
    from dq.sources import load_sources

    config = load_sources(fixtures_dir / "sources_test.yml")
    site = {"SOURCE_OBJECT": "SITE", "RECORD_ID": "s1"}

    assert ui.record_url_for(site, config, "") is None
    assert ui.record_url_for(site, config, "<SF_BASE_URL>") is None
    assert (
        ui.record_url_for({"SOURCE_OBJECT": "NOPE", "RECORD_ID": "x"}, config, "https://x") is None
    )
