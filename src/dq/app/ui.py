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
STATUSES = [
    "DRAFT",
    "NEEDS_CLARIFICATION",
    "READY_FOR_COMPILE",
    "COMPILED",
    "OWNER_CONFIRMED",
    "ACTIVE",
    "RETIRED",
]

# --- Lifecycle stepper (brief §10.2 state machine, made visible to owners) --------

_STEPS = [
    ("DRAFT", "Draft"),
    ("READY_FOR_COMPILE", "Ready"),
    ("COMPILED", "Compiled"),
    ("OWNER_CONFIRMED", "Confirmed"),
    ("ACTIVE", "Active"),
]

_STATUS_COLORS = {
    "DRAFT": "#9aa0a6",
    "NEEDS_CLARIFICATION": "#d97706",
    "READY_FOR_COMPILE": "#8b5cf6",
    "COMPILED": "#3b82f6",
    "OWNER_CONFIRMED": "#0d9488",
    "ACTIVE": "#16a34a",
    "RETIRED": "#dc2626",
}

_STATUS_LABELS = {
    "DRAFT": "Draft",
    "NEEDS_CLARIFICATION": "Needs clarification",
    "READY_FOR_COMPILE": "Ready for compile",
    "COMPILED": "Compiled",
    "OWNER_CONFIRMED": "Owner confirmed",
    "ACTIVE": "Active",
    "RETIRED": "Retired",
}

_DONE_COLOR = "#16a34a"
_FUTURE_COLOR = "#9aa0a6"


def status_badge(status: str) -> str:
    """A small HTML pill for a rule's status -- for embedding in a caption
    or a markdown table cell (never in a plain st.dataframe cell, which
    can't render HTML)."""
    color = _STATUS_COLORS.get(status, _FUTURE_COLOR)
    label = _STATUS_LABELS.get(status, status)
    return (
        f'<span style="background:{color}22;color:{color};border:1px solid {color}66;'
        f'border-radius:999px;padding:1px 10px;font-size:0.78rem;font-weight:600;'
        f'white-space:nowrap;">{label}</span>'
    )


def render_lifecycle_stepper(status: str) -> None:
    """A horizontal progress indicator across the main lifecycle line,
    with NEEDS_CLARIFICATION shown as a pause on DRAFT and RETIRED shown
    as a terminal banner instead of a position on the line."""
    if status == "RETIRED":
        color = _STATUS_COLORS["RETIRED"]
        st.markdown(
            f'<div style="padding:10px 14px;border-radius:8px;background:{color}18;'
            f'border:1px solid {color}55;color:{color};font-weight:600;">'
            f"⏹️ Retired — excluded from future runs</div>",
            unsafe_allow_html=True,
        )
        return

    active_status = "DRAFT" if status == "NEEDS_CLARIFICATION" else status
    step_keys = [key for key, _ in _STEPS]
    current_index = step_keys.index(active_status) if active_status in step_keys else 0

    cells = []
    for i, (_key, label) in enumerate(_STEPS):
        if i < current_index:
            color, weight, opacity, icon = _DONE_COLOR, "600", "0.55", "✓"
        elif i == current_index:
            color = _STATUS_COLORS.get(status, _STATUS_COLORS["DRAFT"])
            weight, opacity, icon = "700", "1", str(i + 1)
        else:
            color, weight, opacity, icon = _FUTURE_COLOR, "500", "0.45", str(i + 1)
        cells.append(
            f'<div style="flex:1;text-align:center;opacity:{opacity};">'
            f'<div style="width:26px;height:26px;border-radius:50%;background:{color};'
            f"color:white;display:inline-flex;align-items:center;justify-content:center;"
            f'font-size:0.75rem;font-weight:{weight};margin:0 auto;">{icon}</div>'
            f'<div style="font-size:0.75rem;margin-top:4px;font-weight:{weight};color:{color};">'
            f"{label}</div></div>"
        )
    connector = '<div style="flex:1;height:2px;background:#9aa0a655;margin-top:13px;"></div>'
    row_html = connector.join(cells)

    html = f'<div style="display:flex;align-items:flex-start;">{row_html}</div>'
    if status == "NEEDS_CLARIFICATION":
        color = _STATUS_COLORS["NEEDS_CLARIFICATION"]
        html += (
            f'<div style="margin-top:10px;padding:8px 12px;border-radius:8px;background:{color}18;'
            f'border:1px solid {color}66;color:{color};font-size:0.85rem;">'
            "⏸️ Paused — waiting on the owner to answer a clarification question below."
            "</div>"
        )
    st.markdown(html, unsafe_allow_html=True)


def next_step_guidance(status: str, *, is_approver: bool = False) -> tuple[str, str]:
    """(icon, message) describing what happens next for a rule's status,
    so an owner never has to guess what a status word means."""
    if status == "DRAFT":
        return (
            "\U0001f4dd",
            "Still being drafted. Refine the wording below, then **Submit for "
            "compilation** when it says exactly what you mean.",
        )
    if status == "NEEDS_CLARIFICATION":
        return (
            "❓",
            "The compiler needs one more detail before it can write the check. "
            "Answer the question below.",
        )
    if status == "READY_FOR_COMPILE":
        return (
            "⏳",
            "Submitted. This is queued for a data steward to turn into a check "
            "— nothing more to do here yet.",
        )
    if status == "COMPILED":
        return (
            "\U0001f50d",
            "SQL is written and passed validation. Review it below, then **Run "
            "preview** to see real matching records before you sign off.",
        )
    if status == "OWNER_CONFIRMED":
        if is_approver:
            return (
                "✅",
                "You've confirmed this is what you meant. As an approver, you can "
                "**Approve** it below to make it live.",
            )
        return ("✅", "Confirmed — waiting on an approver to review the SQL and make it live.")
    if status == "ACTIVE":
        return ("\U0001f7e2", "Live. This rule runs on every `dq run` and feeds real findings.")
    if status == "RETIRED":
        return ("⏹️", "Retired — excluded from future runs.")
    return ("", "")


def render_sidebar(store: GovernanceStore) -> dict | None:
    """The "acting as" owner selector + facilitated-session banner
    (brief §10.6: sidebar selector from DQ_OWNER, visible banner).

    Explicitly re-derives the selectbox's `index` from the previously
    chosen owner's email each run, rather than relying only on Streamlit's
    own key-based state -- navigating between pages otherwise silently
    resets the selection back to the first owner alphabetically, which is
    confusing in a facilitated session (you'd be "acting as" the wrong
    person on the next page without any obvious sign of it)."""
    owners = store.list_owners()
    if not owners:
        st.sidebar.warning("No active owners configured in DQ_OWNER.")
        return None

    labels = [f"{o['DISPLAY_NAME']} ({o['OWNER_EMAIL']})" for o in owners]
    previous = st.session_state.get("acting_as")
    default_index = 0
    if previous is not None:
        for i, o in enumerate(owners):
            if o["OWNER_EMAIL"] == previous["OWNER_EMAIL"]:
                default_index = i
                break
    choice = st.sidebar.selectbox(
        "Acting as", labels, index=default_index, key="acting_as_label"
    )
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
