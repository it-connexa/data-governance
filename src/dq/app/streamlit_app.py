"""dq app (brief §10.6). Denied to Claude Code -- the owner runs this
themselves, in a facilitated session.
"""

from __future__ import annotations

import streamlit as st

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

st.divider()
st.page_link(
    "pages/1_Rule_Catalogue.py",
    label="Rule Catalogue — browse, inspect, manage",
    icon=":material/list_alt:",
)
st.page_link("pages/2_Add_Rule.py", label="Add a new rule", icon=":material/add_circle:")

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
