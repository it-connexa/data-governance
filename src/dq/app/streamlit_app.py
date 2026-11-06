"""dq app (brief §10.6): the router. Denied to Claude Code -- the owner runs
this themselves, in a facilitated session.

Three pages in a top navigation bar: Overview (the landing page), Findings and
Rules. Each page draws its own compact header with the owner selector, so there
is no sidebar and every page gets the full width.
"""

from __future__ import annotations

import streamlit as st

from dq import finding_ops
from dq.app import context, ui

st.set_page_config(
    page_title="Data Governance",
    page_icon=":material/rule:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

settings, store, sources_config = context.load_context()
ui.apply_layout_css()
owner = ui.current_owner(store)


def _label(name: str, count: int | None) -> str:
    return f"{name} · {count:,}" if count else name


open_findings = rule_count = None
if owner is not None:
    open_findings = finding_ops.summarise(store.list_findings(owner["OWNER_EMAIL"])).open
    rule_count = sum(1 for r in store.list_rules() if r["OWNER_EMAIL"] == owner["OWNER_EMAIL"])

pages = [
    st.Page(
        ui.PAGE_OVERVIEW,
        title="Overview",
        icon=":material/dashboard:",
        url_path="overview",
        default=True,
    ),
    st.Page(
        ui.PAGE_FINDINGS,
        title=_label("Findings", open_findings),
        icon=":material/fact_check:",
        url_path="findings",
    ),
    st.Page(
        ui.PAGE_RULES,
        title=_label("Rules", rule_count),
        icon=":material/rule:",
        url_path="rules",
    ),
]
st.navigation(pages, position="top").run()
