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
    col1, col2, col3 = st.columns(3)
    col1.metric("My rules", len(my_rules))
    col2.metric("Active", sum(1 for r in my_rules if r["STATUS"] == "ACTIVE"))
    col3.metric(
        "Needs attention",
        sum(1 for r in my_rules if r["STATUS"] in ("DRAFT", "NEEDS_CLARIFICATION")),
    )

st.divider()
st.page_link("pages/1_My_Rules.py", label="My rules — view, edit, add", icon=":material/list_alt:")
st.page_link("pages/2_Rule_Detail.py", label="Rule detail & preview", icon=":material/search:")
