"""Rules (brief §10.6, page 1): the rule list on the left and the selected rule
on the right, so the statement, the SQL and the next action are never a scroll
away. A rule's whole lifecycle is one slim strip with one primary button, and
everything else (edit, delete, reject, retire) sits in a "More" menu. A new
rule is a dialog, not a page.
"""

from __future__ import annotations

import getpass

import streamlit as st

from dq import finding_ops, rule_ops
from dq.app import context, ui
from dq.sources import UnknownSourceError, render

settings, store, sources_config = context.load_context()
owner = ui.page_header(store, "Rules", icon="rule")
if owner is None:
    st.stop()

is_approver = bool(owner.get("IS_APPROVER"))
facilitated_by = getpass.getuser()
owner_email = owner["OWNER_EMAIL"]

_FILTER_KEYS = [
    "rules_pills",
    "rules_search",
    *[f"filter_{n}" for n in ("object", "domain", "scope", "dimension", "severity")],
]


def _after_action(message: str | None = None) -> None:
    """After any change to a rule: flash a toast on the next run, then rerun."""
    if message:
        st.session_state["_flash_rule"] = message
    st.rerun()


# --- Arrivals: a rule to show (from Findings), a flash, a filter reset ------------------

jump_code = st.query_params.get("rule")
if jump_code:
    st.query_params.clear()
    st.session_state["selected_rule_code"] = jump_code
    st.session_state["edit_mode"] = False
    st.session_state["_reset_rule_filters"] = True

if st.session_state.pop("_reset_rule_filters", False):
    for key in _FILTER_KEYS:
        st.session_state.pop(key, None)

flash = st.session_state.pop("_flash_rule", None)
if flash:
    st.toast(flash, icon=":material/check_circle:")


# --- New rule (dialog) -------------------------------------------------------------------


@st.dialog("New rule", width="large")
def _new_rule_dialog() -> None:
    st.caption("Write the rule in plain English. A data steward turns it into a check.")
    with st.form("add_rule_form"):
        new_name = st.text_input("Name", placeholder="e.g. Sites have a commissioning date")
        new_statement = st.text_area(
            "Statement (plain English)",
            height=100,
            placeholder="Say exactly which records should pass, and when they should not.",
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            new_domain = st.selectbox("Domain", ui.DOMAINS, format_func=ui.enum_label)
            new_object = st.selectbox("Target object", ui.TARGET_OBJECTS, format_func=ui.enum_label)
        with col2:
            new_scope = st.selectbox(
                "Scope",
                ui.SCOPES,
                format_func=ui.enum_label,
                help="Site: findings roll up to a site score. Object: no site link needed.",
            )
            new_dimension = st.selectbox("Dimension", ui.DIMENSIONS, format_func=ui.enum_label)
        with col3:
            new_severity = st.selectbox("Severity", ui.SEVERITIES, format_func=ui.enum_label)
            new_record_label = st.text_input("Record label", value="records")
        new_item_label = st.text_input(
            "Item label (optional)",
            help="Only for rules that check several items per record, such as milestones.",
        )
        submitted = st.form_submit_button("Create rule", type="primary")

    if not submitted:
        return
    if not new_name.strip() or not new_statement.strip():
        st.error("Name and statement are both required.")
        return
    try:
        created = rule_ops.create_rule(
            store,
            rule_name=new_name,
            statement=new_statement,
            domain=new_domain,
            target_object=new_object,
            scope=new_scope,
            dimension=new_dimension,
            severity=new_severity,
            owner_email=owner_email,
            record_label=new_record_label or "records",
            item_label=new_item_label or None,
            created_by=owner_email,
            facilitated_by=facilitated_by,
        )
    except rule_ops.RuleOpsError as exc:
        st.error(str(exc))
        return
    st.session_state["selected_rule_code"] = created["RULE_CODE"]
    st.session_state["edit_mode"] = False
    st.session_state["_reset_rule_filters"] = True
    _after_action(f"Created {created['RULE_CODE']}.")


@st.dialog("Delete rule")
def _delete_dialog(code: str) -> None:
    st.warning(f"This permanently deletes {code}. This can't be undone.")
    yes_col, no_col = st.columns(2)
    with yes_col:
        if st.button("Delete permanently", type="primary", key=f"delete_confirm_{code}"):
            try:
                rule_ops.delete_rule(store, code)
            except rule_ops.RuleOpsError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("selected_rule_code", None)
                _after_action(f"Deleted {code}.")
    with no_col:
        if st.button("Cancel", key=f"delete_cancel_{code}"):
            st.rerun()


# --- Header controls: status pills, search, filters, new rule ---------------------------

# Approvers can include other owners' rules, so they can approve what others have confirmed.
# The checkbox is drawn in the Filters popover below; its state is read from session state here.
show_all_owners = bool(is_approver and st.session_state.get("rules_all_owners"))
owner_names = {o["OWNER_EMAIL"]: o["DISPLAY_NAME"] for o in store.list_owners()}
all_rules = [r for r in store.list_rules() if show_all_owners or r["OWNER_EMAIL"] == owner_email]
flagged_codes = set(store.list_rules_flagged_for_review())
pill_counts = ui.rule_pill_counts(all_rules, flagged_codes=flagged_codes)

pills_col, search_col, filters_col, new_col = st.columns(
    [6, 3, 1.5, 1.5], vertical_alignment="center"
)
with pills_col:
    pill_filter = st.pills(
        "Show",
        ui.RULE_PILLS,
        selection_mode="multi",
        format_func=lambda pill: f"{pill} · {pill_counts[pill]}",
        key="rules_pills",
        label_visibility="collapsed",
    )
with search_col:
    search = st.text_input(
        "Search",
        placeholder="Search code or name",
        key="rules_search",
        label_visibility="collapsed",
        icon=":material/search:",
    )
with filters_col:
    with st.popover("Filters", icon=":material/filter_list:", width="stretch"):
        if is_approver:
            st.checkbox("Include other owners' rules", key="rules_all_owners")
        object_filter = st.multiselect(
            "Object", ui.TARGET_OBJECTS, format_func=ui.enum_label, key="filter_object"
        )
        domain_filter = st.multiselect(
            "Domain", ui.DOMAINS, format_func=ui.enum_label, key="filter_domain"
        )
        scope_filter = st.multiselect(
            "Scope", ui.SCOPES, format_func=ui.enum_label, key="filter_scope"
        )
        dimension_filter = st.multiselect(
            "Dimension", ui.DIMENSIONS, format_func=ui.enum_label, key="filter_dimension"
        )
        severity_filter = st.multiselect(
            "Severity", ui.SEVERITIES, format_func=ui.enum_label, key="filter_severity"
        )
with new_col:
    if st.button("New rule", icon=":material/add:", type="primary", width="stretch"):
        _new_rule_dialog()

if not all_rules:
    st.info("No rules for this owner yet. Use **New rule** to write the first one.")
    st.stop()


def _matches(r: dict) -> bool:
    if pill_filter and not any(
        ui.rule_in_pill(r, pill, flagged_codes=flagged_codes) for pill in pill_filter
    ):
        return False
    if object_filter and r["TARGET_OBJECT"] not in object_filter:
        return False
    if domain_filter and r["DOMAIN"] not in domain_filter:
        return False
    if scope_filter and r["SCOPE"] not in scope_filter:
        return False
    if dimension_filter and r["DIMENSION"] not in dimension_filter:
        return False
    if severity_filter and r["SEVERITY"] not in severity_filter:
        return False
    if search:
        needle = search.lower()
        if needle not in r["RULE_CODE"].lower() and needle not in r["RULE_NAME"].lower():
            return False
    return True


filtered = [r for r in all_rules if _matches(r)]

# --- Selection: a plain list of clickable rows, defaulting to the first rule ----------------
# Plain buttons rather than a table: a click anywhere on the row selects it, the list
# is keyboard and screen-reader friendly, and there is no table selection state to go
# stale when a rule is deleted or filtered out.

codes = [r["RULE_CODE"] for r in filtered]
if codes and st.session_state.get("selected_rule_code") not in codes:
    st.session_state["selected_rule_code"] = codes[0]
    st.session_state["edit_mode"] = False

list_col, detail_col = st.columns([4, 7], gap="medium")

with list_col:
    st.caption(f"{len(filtered)} of {len(all_rules)} rules")
    if not filtered:
        st.info("No rules match the current filters.")
    else:
        with st.container(height=ui.PANE_HEIGHT - 36, key="rules_list"):
            for r in filtered:
                code = r["RULE_CODE"]
                flag = " 🚩" if code in flagged_codes else ""
                label = (
                    f"**{code}** · {r['RULE_NAME']}  \n:small[{ui.status_cell(r['STATUS'])}{flag}]"
                )
                is_selected = code == st.session_state["selected_rule_code"]
                if st.button(
                    label,
                    key=f"pick_{code}",
                    type="primary" if is_selected else "tertiary",
                    width="stretch",
                ):
                    st.session_state["selected_rule_code"] = code
                    st.session_state["edit_mode"] = False
                    st.rerun()

selected_code = st.session_state.get("selected_rule_code")
rule = store.get_rule(selected_code) if selected_code and filtered else None

if st.session_state.get("_detail_code") != selected_code:
    st.session_state["edit_mode"] = False
    st.session_state["_detail_code"] = selected_code


# --- Detail: header, lifecycle, tabs ---------------------------------------------------


def _render_clarification(rule: dict) -> None:
    code = rule["RULE_CODE"]
    with st.container(border=True):
        st.markdown(f"**The compiler needs a detail:** {rule['CLARIFICATION_QUESTION']}")
        st.text_area(
            "Your answer",
            value=rule.get("CLARIFICATION_ANSWER") or "",
            key=f"clarify_answer_{code}",
            height=90,
        )


def _run_primary(action: ui.RuleAction, rule: dict) -> None:
    code = rule["RULE_CODE"]
    try:
        if action.key == "submit":
            rule_ops.submit_rule(store, code, changed_by=owner_email, facilitated_by=facilitated_by)
            _after_action(f"{code} submitted. It is queued for compilation.")
        elif action.key == "answer":
            answer = st.session_state.get(f"clarify_answer_{code}", "")
            if not answer.strip():
                st.error("Write an answer first.")
                return
            rule_ops.answer_clarification(
                store, code, answer, changed_by=owner_email, facilitated_by=facilitated_by
            )
            _after_action("Answer saved. The rule can be compiled again.")
        elif action.key == "confirm":
            rule_ops.confirm_rule(store, code, confirmed_by=owner_email)
            _after_action("Confirmed.")
        elif action.key == "approve":
            rule_ops.approve_rule(store, code, approved_by=owner_email)
            _after_action(f"{code} is now active.")
    except rule_ops.RuleOpsError as exc:
        st.error(str(exc))


def _render_more_menu(rule: dict) -> None:
    code = rule["RULE_CODE"]
    actions = ui.secondary_actions(rule["STATUS"], is_approver=is_approver)
    with st.popover("More", icon=":material/more_horiz:"):
        if "edit" in actions and st.button(
            "Edit rule", icon=":material/edit:", key=f"edit_open_{code}", width="stretch"
        ):
            st.session_state["edit_mode"] = True
            st.rerun()
        if "delete" in actions and st.button(
            "Delete rule…", icon=":material/delete:", key=f"delete_open_{code}", width="stretch"
        ):
            _delete_dialog(code)
        if "reject" in actions:
            reject_reason = st.text_input(
                "Not right? Say why", key=f"reject_reason_{code}", placeholder="Reason (required)"
            )
            if st.button("Return to draft", key=f"reject_{code}", width="stretch"):
                try:
                    rule_ops.reject_confirmation(
                        store,
                        code,
                        reject_reason,
                        changed_by=owner_email,
                        facilitated_by=facilitated_by,
                    )
                except rule_ops.RuleOpsError as exc:
                    st.error(str(exc))
                else:
                    _after_action("Returned to draft.")
        if "retire" in actions:
            retire_reason = st.text_input(
                "Retire this rule", key=f"retire_reason_{code}", placeholder="Reason (required)"
            )
            if st.button("Retire rule", key=f"retire_{code}", width="stretch"):
                try:
                    rule_ops.retire_rule(
                        store,
                        code,
                        retire_reason,
                        retired_by=owner_email,
                        facilitated_by=facilitated_by,
                    )
                except rule_ops.RuleOpsError as exc:
                    st.error(str(exc))
                else:
                    _after_action(f"{code} retired.")
        if "retire" not in actions and rule["STATUS"] in ("COMPILED", "OWNER_CONFIRMED", "ACTIVE"):
            st.caption("Only an approver can retire a rule.")


def _render_definition(rule: dict) -> None:
    code = rule["RULE_CODE"]
    if not st.session_state.get("edit_mode", False):
        with st.container(border=True):
            st.markdown(rule["RULE_STATEMENT"])
        ui.key_values(
            [
                ("Domain", ui.enum_label(rule["DOMAIN"])),
                ("Target object", ui.enum_label(rule["TARGET_OBJECT"])),
                ("Scope", ui.enum_label(rule["SCOPE"])),
                ("Dimension", ui.enum_label(rule["DIMENSION"])),
                ("Severity", ui.severity_cell(rule["SEVERITY"])),
                ("Record label", rule["RECORD_LABEL"]),
                ("Item label", rule["ITEM_LABEL"] or "—"),
                ("Owner", owner_names.get(rule["OWNER_EMAIL"], rule["OWNER_EMAIL"])),
            ]
        )
        return

    # Widget keys are scoped to the rule code so switching rules resets each
    # field to that rule's own value.
    with st.form(f"edit_form_{code}"):
        rule_name = st.text_input("Name", value=rule["RULE_NAME"], key=f"edit_name_{code}")
        statement = st.text_area(
            "Statement",
            value=rule["RULE_STATEMENT"],
            height=110,
            key=f"edit_statement_{code}",
            help="Changing the statement sends the rule back to Draft.",
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            domain = st.selectbox(
                "Domain",
                ui.DOMAINS,
                index=ui.select_index(ui.DOMAINS, rule["DOMAIN"]),
                format_func=ui.enum_label,
                key=f"edit_domain_{code}",
            )
            target_object = st.selectbox(
                "Target object",
                ui.TARGET_OBJECTS,
                index=ui.select_index(ui.TARGET_OBJECTS, rule["TARGET_OBJECT"]),
                format_func=ui.enum_label,
                key=f"edit_object_{code}",
            )
        with col2:
            scope = st.selectbox(
                "Scope",
                ui.SCOPES,
                index=ui.select_index(ui.SCOPES, rule["SCOPE"]),
                format_func=ui.enum_label,
                key=f"edit_scope_{code}",
            )
            dimension = st.selectbox(
                "Dimension",
                ui.DIMENSIONS,
                index=ui.select_index(ui.DIMENSIONS, rule["DIMENSION"]),
                format_func=ui.enum_label,
                key=f"edit_dimension_{code}",
            )
        with col3:
            severity = st.selectbox(
                "Severity",
                ui.SEVERITIES,
                index=ui.select_index(ui.SEVERITIES, rule["SEVERITY"]),
                format_func=ui.enum_label,
                key=f"edit_severity_{code}",
            )
            record_label = st.text_input(
                "Record label", value=rule["RECORD_LABEL"], key=f"edit_record_label_{code}"
            )
        item_label = st.text_input(
            "Item label (multi-item rules only)",
            value=rule["ITEM_LABEL"] or "",
            key=f"edit_item_label_{code}",
        )
        save_col, cancel_col = st.columns(2)
        with save_col:
            submitted = st.form_submit_button("Save", type="primary", width="stretch")
        with cancel_col:
            cancelled = st.form_submit_button("Cancel", width="stretch")

    if cancelled:
        st.session_state["edit_mode"] = False
        st.rerun()

    if not submitted:
        return
    fields: dict = {}
    for field, new, old in [
        ("RULE_NAME", rule_name, rule["RULE_NAME"]),
        ("RULE_STATEMENT", statement, rule["RULE_STATEMENT"]),
        ("DOMAIN", domain, rule["DOMAIN"]),
        ("TARGET_OBJECT", target_object, rule["TARGET_OBJECT"]),
        ("SCOPE", scope, rule["SCOPE"]),
        ("DIMENSION", dimension, rule["DIMENSION"]),
        ("SEVERITY", severity, rule["SEVERITY"]),
        ("RECORD_LABEL", record_label, rule["RECORD_LABEL"]),
    ]:
        if new != old:
            fields[field] = new
    if (item_label or None) != rule["ITEM_LABEL"]:
        fields["ITEM_LABEL"] = item_label or None

    if not fields:
        st.info("Nothing changed.")
        return
    try:
        updated = rule_ops.update_rule(
            store, code, fields=fields, changed_by=owner_email, facilitated_by=facilitated_by
        )
    except rule_ops.RuleOpsError as exc:
        st.error(str(exc))
        return
    st.session_state["edit_mode"] = False
    if "RULE_STATEMENT" in fields:
        _after_action(
            f"Saved. The statement changed, so {code} is back to Draft (v{updated['VERSION']})."
        )
    _after_action(f"Saved {code}.")


def _render_sql_and_preview(rule: dict) -> None:
    code = rule["RULE_CODE"]
    if not rule.get("CHECK_SQL"):
        st.caption("Not compiled yet. The SQL appears here once a data steward has compiled it.")
        return

    st.caption(
        "Runnable Snowflake SQL: each `{{ src.NAME }}` is swapped for its real table. "
        "Edit the placeholder version in `rules/sql/`, not this one."
    )
    try:
        runnable_sql = render(rule["CHECK_SQL"], sources_config)
    except UnknownSourceError as exc:
        st.error(f"Couldn't render this SQL: {exc}")
    else:
        # Sized to the SQL (long statements scroll inside the block) and wrapped, because
        # rendered sources are long single-line subqueries.
        height = min(320, 24 * (runnable_sql.count("\n") + 1) + 40)
        st.code(runnable_sql, language="sql", wrap_lines=True, height=height)

    if rule["STATUS"] not in ("COMPILED", "OWNER_CONFIRMED", "ACTIVE"):
        return

    st.markdown("**Preview matching records**")
    slider_col, button_col = st.columns([3, 1], vertical_alignment="bottom")
    with slider_col:
        sample_size = st.slider("Sample size", 1, 25, 10, key=f"sample_size_{code}")
    with button_col:
        run_preview = st.button(
            "Run preview",
            icon=":material/play_arrow:",
            key=f"run_preview_{code}",
            width="stretch",
        )
    if not run_preview:
        return
    executor = context.make_executor(settings)
    config = context.engine_config(store)
    try:
        result = rule_ops.preview_rule(
            store,
            executor,
            sources_config,
            code,
            config=config,
            query_tag=context.next_query_tag("app:preview"),
            sample_size=sample_size,
            changed_by=owner_email,
        )
    except rule_ops.RuleOpsError as exc:
        st.error(str(exc))
        return
    if not result["ok"]:
        st.error("Validation failed: " + "; ".join(result["errors"]))
        return
    col1, col2 = st.columns(2)
    col1.metric("Evaluated", f"{result['records_evaluated']:,}")
    col2.metric("Failed", f"{result['records_failed']:,}")
    if not result["sample"]:
        st.caption("No failing records, so there is nothing to sample.")
    for row in result["sample"]:
        label = row["record_name"] or row["record_id"]
        site = f" — {row['site_name']}" if row["site_name"] else ""
        st.markdown(f"- [{label}]({row['record_url']}){site} — `{row['detail']}`")


def _render_history(rule: dict) -> None:
    history = store.list_rule_history(rule["RULE_CODE"])
    if not history:
        st.caption("No history recorded for this rule.")
        return
    st.dataframe(
        [
            {
                "When": ui.format_nz_datetime(h["CHANGED_AT"]),
                "Event": ui.history_event_label(h["CHANGE_TYPE"]),
                "Version": h["VERSION"],
                "Status after": ui.status_label(h["STATUS_AFTER"]),
                "By": h["CHANGED_BY"],
                "Comment": h["COMMENT"] or "",
            }
            for h in history
        ],
        hide_index=True,
        width="stretch",
        height=min(ui.PANE_HEIGHT - 190, 36 * (len(history) + 1) + 4),
    )


def _render_detail(rule: dict) -> None:
    code = rule["RULE_CODE"]
    status = rule["STATUS"]

    # Header: identity, then the findings link when the rule has raised any.
    title_col, link_col = st.columns([7, 3], vertical_alignment="center")
    with title_col:
        st.markdown(f"##### {code} · {rule['RULE_NAME']}")
        st.markdown(
            f"{ui.status_badge(status)} {ui.severity_cell(rule['SEVERITY'])} · "
            f"v{rule['VERSION']} · {ui.enum_label(rule['TARGET_OBJECT'])} / "
            f"{ui.enum_label(rule['SCOPE'])}"
        )
    with link_col:
        finding_counts = store.count_findings_by_status(code)
        if finding_counts:
            open_count = finding_counts.get("OPEN", 0)
            if st.button(
                f"View findings ({open_count:,} open)",
                icon=":material/arrow_forward:",
                key=f"view_findings_{code}",
                width="stretch",
            ):
                st.switch_page(ui.PAGE_FINDINGS, query_params={"rule": code})

    review_flag = store.get_rule_review_flag(code)
    if review_flag:
        flag_col, clear_col = st.columns([8, 2], vertical_alignment="center")
        with flag_col:
            st.warning(
                f"**Flagged for review** by {review_flag['FLAGGED_BY']} on "
                f"{ui.format_nz(review_flag['FLAGGED_AT'])}: {review_flag['REASON']}",
                icon=":material/flag:",
            )
        with clear_col:
            if st.button("Clear flag", key=f"clear_flag_{code}", width="stretch"):
                try:
                    finding_ops.clear_review_flag(
                        store, code, owner=owner, facilitated_by=facilitated_by
                    )
                except finding_ops.FindingOpsError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

    # Lifecycle: the strip, what it means, the one button the state asks for, a menu.
    strip_col, action_col = st.columns([6, 4], vertical_alignment="center")
    with strip_col:
        ui.render_lifecycle_strip(status)
    with action_col, st.container(horizontal=True, horizontal_alignment="right"):
        action = ui.primary_action(status, is_approver=is_approver)
        if action is not None and st.button(
            action.label, icon=action.icon, type="primary", key=f"primary_{code}_{action.key}"
        ):
            _run_primary(action, rule)
        _render_more_menu(rule)

    if status != "RETIRED":
        icon, message = ui.next_step_guidance(status, is_approver=is_approver)
        st.caption(f"{icon} {message}")
    if status == "NEEDS_CLARIFICATION":
        _render_clarification(rule)

    editing = st.session_state.get("edit_mode", False)
    default_tab = "SQL & preview" if status == "COMPILED" and not editing else "Definition"
    tab_definition, tab_sql, tab_history = st.tabs(
        ["Definition", "SQL & preview", "History"],
        default=default_tab,
        key=f"rule_tabs_{code}_{status}_{editing}",
    )
    with tab_definition:
        _render_definition(rule)
    with tab_sql:
        _render_sql_and_preview(rule)
    with tab_history:
        _render_history(rule)


with detail_col:
    with st.container(height=ui.PANE_HEIGHT, border=True, key="rules_detail"):
        if rule is None:
            st.caption("Select a rule on the left.")
        else:
            _render_detail(rule)
