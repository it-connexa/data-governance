"""Page 1 -- Rule Catalogue (brief §10.6): browse/filter the registry,
click a row to inspect it, and manage its whole lifecycle -- edit,
submit, clarify, confirm/reject, approve, retire, delete -- from the
panel below. Absorbs what used to be a separate Rule detail page -- one
rule-selection model instead of two.
"""

from __future__ import annotations

import getpass
from collections import Counter

import streamlit as st

from dq import rule_ops
from dq.app import context, ui
from dq.sources import UnknownSourceError, render

st.set_page_config(page_title="Rule Catalogue", layout="wide")

settings, store, sources_config = context.load_context()
owner = ui.render_sidebar(store)
st.title("Rule Catalogue")

if owner is None:
    st.stop()

flash = st.session_state.pop("_flash_created", None)
if flash:
    st.success(f"Created {flash}.")


def _rerun_after_action() -> None:
    """Use after any rule_ops call that can change a rule's status or
    fields -- bumps the table's selection epoch so the row-selection
    widget gets a fresh instance next run instead of possibly keeping its
    old row index highlighted on whatever rule now occupies that position
    (observed after delete, and after a status change that no longer
    matches the current filters)."""
    st.session_state["_table_epoch"] = st.session_state.get("_table_epoch", 0) + 1
    st.rerun()

all_rules = [r for r in store.list_rules() if r["OWNER_EMAIL"] == owner["OWNER_EMAIL"]]

st.subheader("Overview")
if not all_rules:
    st.caption("No rules for this owner yet -- add one on the Add rule page.")
    st.stop()

counts = Counter(r["STATUS"] for r in all_rules)
badges = "  ".join(
    f"{ui.status_badge(status)}&nbsp;{count}" for status, count in counts.most_common()
)
st.markdown(badges, unsafe_allow_html=True)
st.write("")

# --- Filters -----------------------------------------------------------------

object_filter = st.pills(
    "Object type",
    ui.TARGET_OBJECTS,
    selection_mode="multi",
    default=ui.TARGET_OBJECTS,
    key="filter_object",
)

filter_col1, filter_col2, filter_col3, filter_col4, filter_col5, filter_col6 = st.columns(6)
with filter_col1:
    status_filter = st.multiselect("Status", ui.STATUSES, key="filter_status")
with filter_col2:
    domain_filter = st.multiselect("Domain", ui.DOMAINS, key="filter_domain")
with filter_col3:
    scope_filter = st.multiselect("Scope", ui.SCOPES, key="filter_scope")
with filter_col4:
    dimension_filter = st.multiselect("Dimension", ui.DIMENSIONS, key="filter_dimension")
with filter_col5:
    severity_filter = st.multiselect("Severity", ui.SEVERITIES, key="filter_severity")
with filter_col6:
    search = st.text_input("Search code / name", key="filter_search")


def _matches(r: dict) -> bool:
    if r["TARGET_OBJECT"] not in object_filter:
        return False
    if status_filter and r["STATUS"] not in status_filter:
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
st.caption(f"{len(filtered)} of {len(all_rules)} rules")

st.session_state.setdefault("_table_epoch", 0)

if not filtered:
    st.info("No rules match the current filters.")
else:
    # A changed filter selection invalidates the table's current selection
    # the same way a lifecycle action does (see _rerun_after_action) --
    # bump the epoch so it's reconciled below, including the very first
    # run (e.g. landing here pre-selected from the Add rule page, which
    # bumps the epoch itself before switching pages).
    filter_signature = (
        tuple(sorted(object_filter)),
        tuple(sorted(status_filter)),
        tuple(sorted(domain_filter)),
        tuple(sorted(scope_filter)),
        tuple(sorted(dimension_filter)),
        tuple(sorted(severity_filter)),
        search,
    )
    if st.session_state.get("_filter_signature") != filter_signature:
        st.session_state["_filter_signature"] = filter_signature
        st.session_state["_table_epoch"] += 1

    # The table widget is keyed by this epoch, not a fixed name: whenever
    # the epoch bumps, it gets a brand-new widget instance with no memory
    # of the previous selection, which we then seed from selected_rule_code
    # below. A fixed key with session_state manipulation wasn't reliable --
    # a deleted or filtered-out row's tick could stick to whatever row
    # shifted into its old position. Within one epoch (e.g. a plain click)
    # nothing here touches the key, so a fresh click is never fought over.
    table_key = f"rules_table_{st.session_state['_table_epoch']}"
    if st.session_state.get("_table_seeded_key") != table_key:
        st.session_state["_table_seeded_key"] = table_key
        selected_code = st.session_state.get("selected_rule_code")
        if selected_code is not None:
            position = next(
                (i for i, r in enumerate(filtered) if r["RULE_CODE"] == selected_code), None
            )
            if position is None:
                st.session_state.pop("selected_rule_code", None)
            else:
                st.session_state[table_key] = {
                    "selection": {"rows": [position], "columns": []}
                }

    event = st.dataframe(
        [
            {
                "Code": r["RULE_CODE"],
                "Name": r["RULE_NAME"],
                "Status": r["STATUS"],
                "Object": r["TARGET_OBJECT"],
                "Scope": r["SCOPE"],
                "Dimension": r["DIMENSION"],
                "Severity": r["SEVERITY"],
                "Version": r["VERSION"],
            }
            for r in filtered
        ],
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=table_key,
    )
    selected_rows = event.selection["rows"]
    if selected_rows:
        new_code = filtered[selected_rows[0]]["RULE_CODE"]
        if new_code != st.session_state.get("selected_rule_code"):
            st.session_state["selected_rule_code"] = new_code
            st.session_state["edit_mode"] = False

st.divider()

selected_code = st.session_state.get("selected_rule_code")
if not selected_code:
    st.caption("Click a row above to see its details.")
    st.stop()

rule = store.get_rule(selected_code)
if rule is None:
    st.session_state.pop("selected_rule_code", None)
    st.stop()

if st.session_state.get("_detail_code") != selected_code:
    st.session_state["edit_mode"] = False
    st.session_state["_detail_code"] = selected_code

st.subheader(f"{selected_code} — {rule['RULE_NAME']}")
st.caption(f"v{rule['VERSION']} · {rule['TARGET_OBJECT']} / {rule['SCOPE']}")

ui.render_lifecycle_stepper(rule["STATUS"])
is_approver = bool(owner.get("IS_APPROVER"))
if rule["STATUS"] != "RETIRED":  # the stepper already shows RETIRED's own banner
    icon, message = ui.next_step_guidance(rule["STATUS"], is_approver=is_approver)
    st.caption(f"{icon} {message}")

# --- Manage this rule: every lifecycle action lives here, together -------------

st.subheader("Manage this rule")

if rule["STATUS"] == "NEEDS_CLARIFICATION":
    st.write(f"**Clarification needed:** {rule['CLARIFICATION_QUESTION']}")
    answer = st.text_area(
        "Your answer",
        value=rule.get("CLARIFICATION_ANSWER") or "",
        key=f"clarify_answer_{selected_code}",
    )
    if st.button("Save answer", key=f"clarify_save_{selected_code}"):
        rule_ops.answer_clarification(
            store,
            selected_code,
            answer,
            changed_by=owner["OWNER_EMAIL"],
            facilitated_by=getpass.getuser(),
        )
        st.success("Answer saved -- this rule is eligible for compilation again.")
        _rerun_after_action()

if rule["STATUS"] == "DRAFT":
    action_col1, action_col2 = st.columns(2)
    with action_col1:
        if st.button("\U0001f4e4 Submit for compilation", key=f"submit_{selected_code}"):
            rule_ops.submit_rule(
                store,
                selected_code,
                changed_by=owner["OWNER_EMAIL"],
                facilitated_by=getpass.getuser(),
            )
            st.success(f"{selected_code} submitted -- it's now queued for compilation.")
            _rerun_after_action()
    with action_col2:
        confirming_delete_key = f"confirming_delete_{selected_code}"
        if not st.session_state.get(confirming_delete_key):
            if st.button("\U0001f5d1️ Delete rule", key=f"delete_{selected_code}"):
                st.session_state[confirming_delete_key] = True
                st.rerun()
        else:
            st.warning("This permanently deletes the rule. This can't be undone.")
            delete_col1, delete_col2 = st.columns(2)
            with delete_col1:
                if st.button("Yes, delete permanently", key=f"delete_confirm_{selected_code}"):
                    try:
                        rule_ops.delete_rule(store, selected_code)
                    except rule_ops.RuleOpsError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.pop(confirming_delete_key, None)
                        st.success(f"Deleted {selected_code}.")
                        _rerun_after_action()
            with delete_col2:
                if st.button("Cancel", key=f"delete_cancel_{selected_code}"):
                    st.session_state[confirming_delete_key] = False
                    st.rerun()

if rule["STATUS"] == "COMPILED":
    confirm_col1, confirm_col2 = st.columns(2)
    with confirm_col1:
        if st.button("✅ Yes, this is what I meant", key=f"confirm_{selected_code}"):
            rule_ops.confirm_rule(store, selected_code, confirmed_by=owner["OWNER_EMAIL"])
            st.success("Confirmed.")
            _rerun_after_action()
    with confirm_col2:
        reject_comment = st.text_input(
            "Reason (required to reject)", key=f"reject_reason_{selected_code}"
        )
        if st.button("Not right", key=f"reject_{selected_code}"):
            try:
                rule_ops.reject_confirmation(
                    store,
                    selected_code,
                    reject_comment,
                    changed_by=owner["OWNER_EMAIL"],
                    facilitated_by=getpass.getuser(),
                )
            except rule_ops.RuleOpsError as exc:
                st.error(str(exc))
            else:
                st.warning("Returned to DRAFT.")
                _rerun_after_action()

if rule["STATUS"] == "OWNER_CONFIRMED" and is_approver:
    if st.button("✅ Approve (approvers only)", key=f"approve_{selected_code}"):
        try:
            rule_ops.approve_rule(store, selected_code, approved_by=owner["OWNER_EMAIL"])
        except rule_ops.RuleOpsError as exc:
            st.error(str(exc))
        else:
            st.success("Rule is now ACTIVE.")
            _rerun_after_action()

if rule["STATUS"] in ("COMPILED", "OWNER_CONFIRMED", "ACTIVE") and is_approver:
    with st.expander("Retire this rule (approvers only)", expanded=False):
        retire_reason = st.text_input("Reason (required)", key=f"retire_reason_{selected_code}")
        if st.button("Retire rule", key=f"retire_{selected_code}"):
            try:
                rule_ops.retire_rule(
                    store,
                    selected_code,
                    retire_reason,
                    retired_by=owner["OWNER_EMAIL"],
                    facilitated_by=getpass.getuser(),
                )
            except rule_ops.RuleOpsError as exc:
                st.error(str(exc))
            else:
                st.success(f"{selected_code} retired.")
                _rerun_after_action()

if rule["STATUS"] in ("READY_FOR_COMPILE", "RETIRED"):
    st.caption("Nothing to do here right now.")

# --- Rule details: read-only / editable field block ----------------------------

st.subheader("Rule details")

edit_mode = st.session_state.get("edit_mode", False)
if not edit_mode:
    st.caption("\U0001f512 Read-only -- click Edit rule to make changes")

    st.text_input("Name", value=rule["RULE_NAME"], disabled=True, key=f"view_name_{selected_code}")
    st.text_area(
        "Statement",
        value=rule["RULE_STATEMENT"],
        height=120,
        disabled=True,
        key=f"view_statement_{selected_code}",
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        st.selectbox(
            "Domain",
            ui.DOMAINS,
            index=ui.select_index(ui.DOMAINS, rule["DOMAIN"]),
            disabled=True,
            key=f"view_domain_{selected_code}",
        )
        st.selectbox(
            "Target object",
            ui.TARGET_OBJECTS,
            index=ui.select_index(ui.TARGET_OBJECTS, rule["TARGET_OBJECT"]),
            disabled=True,
            key=f"view_object_{selected_code}",
        )
    with col2:
        st.selectbox(
            "Scope",
            ui.SCOPES,
            index=ui.select_index(ui.SCOPES, rule["SCOPE"]),
            disabled=True,
            key=f"view_scope_{selected_code}",
        )
        st.selectbox(
            "Dimension",
            ui.DIMENSIONS,
            index=ui.select_index(ui.DIMENSIONS, rule["DIMENSION"]),
            disabled=True,
            key=f"view_dimension_{selected_code}",
        )
    with col3:
        st.selectbox(
            "Severity",
            ui.SEVERITIES,
            index=ui.select_index(ui.SEVERITIES, rule["SEVERITY"]),
            disabled=True,
            key=f"view_severity_{selected_code}",
        )
        st.text_input(
            "Record label",
            value=rule["RECORD_LABEL"],
            disabled=True,
            key=f"view_record_label_{selected_code}",
        )
    st.text_input(
        "Item label (multi-item rules only)",
        value=rule["ITEM_LABEL"] or "",
        disabled=True,
        key=f"view_item_label_{selected_code}",
    )

    if st.button("Edit rule", key=f"edit_toggle_{selected_code}"):
        st.session_state["edit_mode"] = True
        st.rerun()
else:
    st.caption("✏️ Editing -- Save or Cancel below")

    # Widget keys are scoped to selected_code so switching rules resets each
    # field to that rule's own value, instead of Streamlit preserving the
    # previously-selected rule's edited (or default) value under a reused key.
    with st.form(f"edit_form_{selected_code}"):
        rule_name = st.text_input(
            "Name", value=rule["RULE_NAME"], key=f"edit_name_{selected_code}"
        )
        statement = st.text_area(
            "Statement",
            value=rule["RULE_STATEMENT"],
            height=120,
            key=f"edit_statement_{selected_code}",
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            domain = st.selectbox(
                "Domain",
                ui.DOMAINS,
                index=ui.select_index(ui.DOMAINS, rule["DOMAIN"]),
                key=f"edit_domain_{selected_code}",
            )
            target_object = st.selectbox(
                "Target object",
                ui.TARGET_OBJECTS,
                index=ui.select_index(ui.TARGET_OBJECTS, rule["TARGET_OBJECT"]),
                key=f"edit_object_{selected_code}",
            )
        with col2:
            scope = st.selectbox(
                "Scope",
                ui.SCOPES,
                index=ui.select_index(ui.SCOPES, rule["SCOPE"]),
                key=f"edit_scope_{selected_code}",
            )
            dimension = st.selectbox(
                "Dimension",
                ui.DIMENSIONS,
                index=ui.select_index(ui.DIMENSIONS, rule["DIMENSION"]),
                key=f"edit_dimension_{selected_code}",
            )
        with col3:
            severity = st.selectbox(
                "Severity",
                ui.SEVERITIES,
                index=ui.select_index(ui.SEVERITIES, rule["SEVERITY"]),
                key=f"edit_severity_{selected_code}",
            )
            record_label = st.text_input(
                "Record label", value=rule["RECORD_LABEL"], key=f"edit_record_label_{selected_code}"
            )
        item_label = st.text_input(
            "Item label (multi-item rules only)",
            value=rule["ITEM_LABEL"] or "",
            key=f"edit_item_label_{selected_code}",
        )

        submitted = st.form_submit_button("Save")

    if st.button("Cancel", key=f"edit_cancel_{selected_code}"):
        st.session_state["edit_mode"] = False
        st.rerun()

    if submitted:
        fields: dict = {}
        if rule_name != rule["RULE_NAME"]:
            fields["RULE_NAME"] = rule_name
        if statement != rule["RULE_STATEMENT"]:
            fields["RULE_STATEMENT"] = statement
        if domain != rule["DOMAIN"]:
            fields["DOMAIN"] = domain
        if target_object != rule["TARGET_OBJECT"]:
            fields["TARGET_OBJECT"] = target_object
        if scope != rule["SCOPE"]:
            fields["SCOPE"] = scope
        if dimension != rule["DIMENSION"]:
            fields["DIMENSION"] = dimension
        if severity != rule["SEVERITY"]:
            fields["SEVERITY"] = severity
        if record_label != rule["RECORD_LABEL"]:
            fields["RECORD_LABEL"] = record_label
        if (item_label or None) != rule["ITEM_LABEL"]:
            fields["ITEM_LABEL"] = item_label or None

        if not fields:
            st.info("Nothing changed.")
        else:
            try:
                updated = rule_ops.update_rule(
                    store,
                    selected_code,
                    fields=fields,
                    changed_by=owner["OWNER_EMAIL"],
                    facilitated_by=getpass.getuser(),
                )
            except rule_ops.RuleOpsError as exc:
                st.error(str(exc))
            else:
                if "RULE_STATEMENT" in fields:
                    st.warning(
                        f"Statement changed — {selected_code} reset to DRAFT, "
                        f"version {updated['VERSION']}."
                    )
                st.success(f"Saved {selected_code}.")
                st.session_state["edit_mode"] = False
                _rerun_after_action()

# --- Preview and SQL: kept last since they take up a lot of space --------------

st.divider()

if rule.get("CHECK_SQL") and rule["STATUS"] in ("COMPILED", "OWNER_CONFIRMED", "ACTIVE"):
    st.subheader("Preview")
    sample_size = st.slider("Sample size", 1, 25, 10, key=f"sample_size_{selected_code}")
    if st.button("Run preview", key=f"run_preview_{selected_code}"):
        executor = context.make_executor(settings)
        config = context.engine_config(store)
        try:
            result = rule_ops.preview_rule(
                store,
                executor,
                sources_config,
                selected_code,
                config=config,
                query_tag=context.next_query_tag("app:preview"),
                sample_size=sample_size,
                changed_by=owner["OWNER_EMAIL"],
            )
        except rule_ops.RuleOpsError as exc:
            st.error(str(exc))
        else:
            if not result["ok"]:
                st.error("Validation failed: " + "; ".join(result["errors"]))
            else:
                col1, col2 = st.columns(2)
                col1.metric("Evaluated", result["records_evaluated"])
                col2.metric("Failed", result["records_failed"])
                if not result["sample"]:
                    st.caption("No failing records -- nothing to sample.")
                for row in result["sample"]:
                    label = row["record_name"] or row["record_id"]
                    site = f" — {row['site_name']}" if row["site_name"] else ""
                    st.markdown(f"- [{label}]({row['record_url']}){site} — `{row['detail']}`")

with st.expander("SQL", expanded=False):
    if rule.get("CHECK_SQL"):
        st.caption(
            "The Snowflake-executable SQL for this rule -- every `{{ src.NAME }}` "
            "swapped for its real table. Paste straight into a worksheet to test "
            "it yourself. Don't save this version back into rules/sql/ -- the "
            "placeholders there are what keep the rule portable if a table ever "
            "moves."
        )
        try:
            st.code(render(rule["CHECK_SQL"], sources_config), language="sql")
        except UnknownSourceError as exc:
            st.error(f"Couldn't render this SQL: {exc}")
    else:
        st.caption("Not compiled yet.")
