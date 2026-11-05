"""dq app (brief §10.6). Denied to Claude Code -- the owner runs this
themselves, in a facilitated session.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from dq import finding_ops
from dq.app import context, ui

st.set_page_config(page_title="Data Governance PoC", layout="wide")

settings, store, sources_config = context.load_context()
owner = ui.render_sidebar(store)

st.title("Data Governance PoC")
st.write(
    "Plain-English data governance rules, compiled to deterministic Snowflake "
    "checks, tracked in a local governance store."
)

if owner is not None:
    my_rules = [r for r in store.list_rules() if r["OWNER_EMAIL"] == owner["OWNER_EMAIL"]]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("My rules", len(my_rules))
    col2.metric("Active", sum(1 for r in my_rules if r["STATUS"] == "ACTIVE"))
    col3.metric(
        "Ready / in progress",
        sum(
            1
            for r in my_rules
            if r["STATUS"] in ("READY_FOR_COMPILE", "COMPILED", "OWNER_CONFIRMED")
        ),
    )
    col4.metric(
        "Needs your attention",
        sum(1 for r in my_rules if r["STATUS"] in ("DRAFT", "NEEDS_CLARIFICATION")),
    )

    findings = finding_ops.summarise(store.list_findings(owner["OWNER_EMAIL"]))
    my_codes = {r["RULE_CODE"] for r in my_rules}
    flagged = [code for code in store.list_rules_flagged_for_review() if code in my_codes]
    latest_run = next(iter(store.list_runs(1)), None)
    lapsing = findings.expiring_soon + findings.expired

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Open findings", f"{findings.open:,}")
    col2.metric("Rules flagged for review", len(flagged))
    col3.metric("Exceptions lapsing soon", lapsing, help="Within 30 days, or already lapsed")
    col4.metric(
        "Last run",
        latest_run["STATUS"].title() if latest_run else "None yet",
        delta=ui.format_age(latest_run["STARTED_AT"], now=datetime.now(UTC))
        if latest_run
        else None,
        delta_color="off",
    )

st.divider()
st.page_link(
    "pages/1_Rule_Catalogue.py",
    label="Rule Catalogue — browse, inspect, manage",
    icon=":material/list_alt:",
)
st.page_link("pages/2_Add_Rule.py", label="Add a new rule", icon=":material/add_circle:")
st.page_link(
    "pages/3_Findings.py",
    label="Findings — review, accept or dismiss",
    icon=":material/fact_check:",
)
st.page_link(
    "pages/4_Run_Results.py",
    label="Run results — latest run, trends, your narrative",
    icon=":material/monitoring:",
)

with st.expander("How a rule moves from an idea to a live check"):
    st.write(
        "Every rule follows the same path, and you drive every step except "
        "the middle one (a data steward turns your statement into SQL):"
    )
    legend_html = "&nbsp;&nbsp;→&nbsp;&nbsp;".join(
        ui.status_badge(status)
        for status in [
            "DRAFT",
            "READY_FOR_COMPILE",
            "COMPILED",
            "OWNER_CONFIRMED",
            "ACTIVE",
        ]
    )
    st.markdown(legend_html, unsafe_allow_html=True)
    st.caption(
        "Write it in plain English (**Draft**) → tell us it's ready "
        "(**Submit for compilation**) → we write and validate the SQL "
        "(**Compiled**) → you preview real matching records and agree it's "
        "right (**Owner confirmed**) → an approver signs off the SQL "
        "(**Active**, now running every week). A statement that's unclear gets "
        "asked back to you as a question instead of guessed at; editing a "
        "statement at any point sends it back to Draft."
    )
