"""Shared Streamlit widgets (brief §10.6). Uses `st.*`, so this is verified
by running the app itself against a temp store, never by pytest.
"""

from __future__ import annotations

import streamlit as st

from dq.store.base import GovernanceStore

DOMAINS = ["SITE_MASTER", "PROJECT_MILESTONE", "CROSS_DOMAIN"]
TARGET_OBJECTS = ["SITE", "PROJECT", "MILESTONE"]
SCOPES = ["OBJECT", "SITE"]
DIMENSIONS = ["COMPLETENESS", "VALIDITY", "CONSISTENCY", "TIMELINESS", "UNIQUENESS", "INTEGRITY"]
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def render_sidebar(store: GovernanceStore) -> dict | None:
    """The "acting as" owner selector + facilitated-session banner
    (brief §10.6: sidebar selector from DQ_OWNER, visible banner)."""
    owners = store.list_owners()
    if not owners:
        st.sidebar.warning("No active owners configured in DQ_OWNER.")
        return None

    labels = [f"{o['DISPLAY_NAME']} ({o['OWNER_EMAIL']})" for o in owners]
    choice = st.sidebar.selectbox("Acting as", labels, key="acting_as_label")
    chosen = owners[labels.index(choice)]
    st.session_state["acting_as"] = chosen
    st.sidebar.info(f"Facilitated session — acting as **{chosen['DISPLAY_NAME']}**")
    return chosen


def select_index(options: list[str], value: str | None, default: int = 0) -> int:
    """Index of `value` in `options`, or `default` if it's not there (e.g.
    an older rule using a value not in the current dropdown list)."""
    try:
        return options.index(value) if value is not None else default
    except ValueError:
        return default
