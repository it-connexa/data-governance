"""Page 2 -- Add a new rule (brief §10.6), split out from the Rule
Catalogue so that page isn't a scroll marathon of overview + detail +
a whole extra form.
"""

from __future__ import annotations

import getpass

import streamlit as st

from dq import rule_ops
from dq.app import context, ui

st.set_page_config(page_title="Add rule", layout="wide")

settings, store, sources_config = context.load_context()
owner = ui.render_sidebar(store)
st.title("Add a new rule")

if owner is None:
    st.stop()

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
            st.session_state["selected_rule_code"] = created["RULE_CODE"]
            st.session_state["edit_mode"] = False
            st.session_state["_flash_created"] = created["RULE_CODE"]
            # Bump the Rule Catalogue table's selection epoch so it picks up
            # and highlights this new selection on arrival, the same way it
            # reconciles after a filter change or a lifecycle action there.
            st.session_state["_table_epoch"] = st.session_state.get("_table_epoch", 0) + 1
            st.switch_page("pages/1_Rule_Catalogue.py")
