"""Page 1 -- My rules (brief §10.6): the acting-as owner's rules, an
editable form for DRAFT/NEEDS_CLARIFICATION rules, and Add rule.
"""

from __future__ import annotations

import getpass

import streamlit as st

from dq import rule_ops
from dq.app import context, ui

st.set_page_config(page_title="My rules", layout="wide")

settings, store, sources_config = context.load_context()
owner = ui.render_sidebar(store)
st.title("My rules")

if owner is None:
    st.stop()

all_rules = [r for r in store.list_rules() if r["OWNER_EMAIL"] == owner["OWNER_EMAIL"]]

st.subheader("Overview")
if not all_rules:
    st.caption("No rules for this owner yet -- add one below.")
else:
    st.dataframe(
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
            for r in all_rules
        ],
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Edit a draft rule")
editable = [r for r in all_rules if r["STATUS"] in ("DRAFT", "NEEDS_CLARIFICATION")]

if not editable:
    st.caption(
        "No DRAFT or NEEDS_CLARIFICATION rules to edit here. A compiled-or-later "
        "rule's statement can still be edited on the Rule detail page -- doing so "
        "resets it to DRAFT."
    )
else:
    selected_code = st.selectbox("Rule", [r["RULE_CODE"] for r in editable], key="edit_rule_code")
    rule = next(r for r in editable if r["RULE_CODE"] == selected_code)

    # Widget keys are scoped to selected_code so switching rules resets each
    # field to that rule's own value, instead of Streamlit preserving the
    # previously-selected rule's edited (or default) value under a reused key.
    with st.form(f"edit_rule_form_{selected_code}"):
        rule_name = st.text_input("Name", value=rule["RULE_NAME"], key=f"name_{selected_code}")
        statement = st.text_area(
            "Statement", value=rule["RULE_STATEMENT"], height=120, key=f"statement_{selected_code}"
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            domain = st.selectbox(
                "Domain",
                ui.DOMAINS,
                index=ui.select_index(ui.DOMAINS, rule["DOMAIN"]),
                key=f"domain_{selected_code}",
            )
            target_object = st.selectbox(
                "Target object",
                ui.TARGET_OBJECTS,
                index=ui.select_index(ui.TARGET_OBJECTS, rule["TARGET_OBJECT"]),
                key=f"object_{selected_code}",
            )
        with col2:
            scope = st.selectbox(
                "Scope",
                ui.SCOPES,
                index=ui.select_index(ui.SCOPES, rule["SCOPE"]),
                key=f"scope_{selected_code}",
            )
            dimension = st.selectbox(
                "Dimension",
                ui.DIMENSIONS,
                index=ui.select_index(ui.DIMENSIONS, rule["DIMENSION"]),
                key=f"dimension_{selected_code}",
            )
        with col3:
            severity = st.selectbox(
                "Severity",
                ui.SEVERITIES,
                index=ui.select_index(ui.SEVERITIES, rule["SEVERITY"]),
                key=f"severity_{selected_code}",
            )
            record_label = st.text_input(
                "Record label", value=rule["RECORD_LABEL"], key=f"record_label_{selected_code}"
            )
        item_label = st.text_input(
            "Item label (multi-item rules only)",
            value=rule["ITEM_LABEL"] or "",
            key=f"item_label_{selected_code}",
        )

        submitted = st.form_submit_button("Save changes")

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
                st.rerun()

st.subheader("Add a new rule")
with st.form("add_rule_form", clear_on_submit=True):
    new_name = st.text_input("Name")
    new_statement = st.text_area("Statement (plain English)", height=100)
    col1, col2, col3 = st.columns(3)
    with col1:
        new_domain = st.selectbox("Domain", ui.DOMAINS, key="new_domain")
        new_object = st.selectbox("Target object", ui.TARGET_OBJECTS, key="new_object")
    with col2:
        new_scope = st.selectbox("Scope", ui.SCOPES, key="new_scope")
        new_dimension = st.selectbox("Dimension", ui.DIMENSIONS, key="new_dimension")
    with col3:
        new_severity = st.selectbox("Severity", ui.SEVERITIES, key="new_severity")
        new_record_label = st.text_input("Record label", value="records")
    new_item_label = st.text_input("Item label (optional)")

    add_submitted = st.form_submit_button("Add rule")

if add_submitted:
    if not new_name.strip() or not new_statement.strip():
        st.error("Name and statement are both required.")
    else:
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
                owner_email=owner["OWNER_EMAIL"],
                record_label=new_record_label or "records",
                item_label=new_item_label or None,
                created_by=owner["OWNER_EMAIL"],
                facilitated_by=getpass.getuser(),
            )
        except rule_ops.RuleOpsError as exc:
            st.error(str(exc))
        else:
            st.success(f"Created {created['RULE_CODE']}.")
            st.rerun()
