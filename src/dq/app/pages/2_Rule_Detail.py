"""Page 2 -- Rule detail and preview (brief §10.6): statement, clarification
Q&A, compiled SQL, preview with real sample rows, confirm/reject, and a
minimal approve action folded in from page 3 for this single-approver setup.
"""

from __future__ import annotations

import getpass

import streamlit as st

from dq import rule_ops
from dq.app import context, ui

st.set_page_config(page_title="Rule detail", layout="wide")

settings, store, sources_config = context.load_context()
owner = ui.render_sidebar(store)
st.title("Rule detail & preview")

if owner is None:
    st.stop()

my_rules = [r for r in store.list_rules() if r["OWNER_EMAIL"] == owner["OWNER_EMAIL"]]
if not my_rules:
    st.info("No rules for this owner yet -- add one on the My rules page.")
    st.stop()

codes = [r["RULE_CODE"] for r in my_rules]
selected_code = st.selectbox("Rule", codes)
rule = store.get_rule(selected_code)

st.caption(f"{rule['STATUS']} · v{rule['VERSION']} · {rule['TARGET_OBJECT']} / {rule['SCOPE']}")
st.write(f"**Statement:** {rule['RULE_STATEMENT']}")

if rule["STATUS"] == "NEEDS_CLARIFICATION":
    st.subheader("Clarification needed")
    st.write(rule["CLARIFICATION_QUESTION"])
    answer = st.text_area("Your answer", value=rule.get("CLARIFICATION_ANSWER") or "")
    if st.button("Save answer"):
        rule_ops.answer_clarification(
            store,
            selected_code,
            answer,
            changed_by=owner["OWNER_EMAIL"],
            facilitated_by=getpass.getuser(),
        )
        st.success("Answer saved -- this rule is eligible for compilation again.")
        st.rerun()

with st.expander("Compiled SQL", expanded=False):
    if rule.get("CHECK_SQL"):
        st.code(rule["CHECK_SQL"], language="sql")
    else:
        st.caption("Not compiled yet.")

st.subheader("Edit statement")
st.caption("Editing this resets the rule to DRAFT and clears the compiled SQL and any approvals.")
new_statement = st.text_area(
    "Statement", value=rule["RULE_STATEMENT"], key=f"detail_statement_{selected_code}"
)
if st.button("Save statement"):
    if new_statement != rule["RULE_STATEMENT"]:
        updated = rule_ops.update_rule(
            store,
            selected_code,
            fields={"RULE_STATEMENT": new_statement},
            changed_by=owner["OWNER_EMAIL"],
            facilitated_by=getpass.getuser(),
        )
        st.warning(f"Reset to DRAFT, version {updated['VERSION']}.")
        st.rerun()
    else:
        st.info("Nothing changed.")

if rule.get("CHECK_SQL") and rule["STATUS"] in ("COMPILED", "OWNER_CONFIRMED", "ACTIVE"):
    st.subheader("Preview")
    sample_size = st.slider("Sample size", 1, 25, 10)
    if st.button("Run preview"):
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

    st.subheader("Confirm")
    col1, col2 = st.columns(2)
    with col1:
        if rule["STATUS"] == "COMPILED" and st.button("Yes, this is what I meant"):
            rule_ops.confirm_rule(store, selected_code, confirmed_by=owner["OWNER_EMAIL"])
            st.success("Confirmed.")
            st.rerun()
    with col2:
        reject_comment = st.text_input("Reason (required to reject)")
        if st.button("Not right"):
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
                st.rerun()

    if rule["STATUS"] == "OWNER_CONFIRMED" and owner.get("IS_APPROVER"):
        st.subheader("Approve (approvers only)")
        if st.button("Approve"):
            try:
                rule_ops.approve_rule(store, selected_code, approved_by=owner["OWNER_EMAIL"])
            except rule_ops.RuleOpsError as exc:
                st.error(str(exc))
            else:
                st.success("Rule is now ACTIVE.")
                st.rerun()
