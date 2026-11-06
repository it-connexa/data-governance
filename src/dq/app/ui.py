"""Shared Streamlit helpers (brief §10.6).

The pure helpers (labels, badges, lifecycle view, actions, filters, formatting)
are unit-tested. The few functions that call `st.*` directly -- the layout CSS,
page header, owner selector and lifecycle strip -- are verified by running the
app against a temp store and by the AppTest smoke tests, never against real data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import streamlit as st

from dq import finding_ops
from dq.store.base import GovernanceStore

NZ_TZ = ZoneInfo("Pacific/Auckland")

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

# Page files, relative to the entrypoint, for st.navigation / st.switch_page.
PAGE_OVERVIEW = "views/overview.py"
PAGE_FINDINGS = "views/findings.py"
PAGE_RULES = "views/rules.py"

# Height of the scrolling list and detail panes. Streamlit sizes containers in
# pixels, so this is tuned for a laptop screen (about 1440 x 900).
PANE_HEIGHT = 600

# --- Labels ------------------------------------------------------------------------


def enum_label(value: str | None) -> str:
    """A database enum in plain words: CROSS_DOMAIN -> "Cross domain"."""
    if not value:
        return "—"
    return value.replace("_", " ").capitalize()


# status: (label, Streamlit badge colour, table glyph, strip colour -- darker, white text on it)
_STATUS_STYLE = {
    "DRAFT": ("Draft", "gray", "🔘", "#4b5563"),
    "NEEDS_CLARIFICATION": ("Needs clarification", "orange", "🟠", "#b45309"),
    "READY_FOR_COMPILE": ("Ready for compile", "violet", "🟣", "#6d28d9"),
    "COMPILED": ("Compiled", "blue", "🔵", "#1d4ed8"),
    "OWNER_CONFIRMED": ("Owner confirmed", "yellow", "🟡", "#a16207"),
    "ACTIVE": ("Active", "green", "🟢", "#15803d"),
    "RETIRED": ("Retired", "red", "🔴", "#b91c1c"),
}


def status_label(status: str) -> str:
    return _STATUS_STYLE.get(status, (status,))[0]


def status_color(status: str) -> str:
    return _STATUS_STYLE[status][1] if status in _STATUS_STYLE else "gray"


def status_badge(status: str) -> str:
    """A native Streamlit colour badge for a rule's status, for st.markdown
    or st.caption (it can't render inside a dataframe cell; see status_cell)."""
    return f":{status_color(status)}-badge[{status_label(status)}]"


def status_cell(status: str) -> str:
    """Glyph plus words for a table cell, so status never relies on colour alone."""
    glyph = _STATUS_STYLE[status][2] if status in _STATUS_STYLE else "🔘"
    return f"{glyph} {status_label(status)}"


_SEVERITY_GLYPHS = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}


def severity_cell(severity: str | None) -> str:
    if not severity:
        return "—"
    return f"{_SEVERITY_GLYPHS.get(severity, '🔘')} {severity.capitalize()}"


_RUN_STATUS_STYLE = {
    "SUCCEEDED": ("Succeeded", "green"),
    "PARTIAL": ("Partial", "orange"),
    "FAILED": ("Failed", "red"),
    "RUNNING": ("Running", "blue"),
}


def run_status_badge(status: str) -> str:
    """A native colour badge for a run's status, like status_badge for rules."""
    label, color = _RUN_STATUS_STYLE.get(status, (status, "gray"))
    return f":{color}-badge[{label}]"


_HISTORY_EVENTS = {
    "CREATED": "Created",
    "STATEMENT_EDITED": "Statement edited",
    "CLARIFICATION": "Clarification",
    "SUBMITTED": "Submitted for compilation",
    "SQL_COMPILED": "SQL compiled",
    "VALIDATED": "Validated",
    "PREVIEWED": "Previewed",
    "OWNER_CONFIRMED": "Confirmed by owner",
    "OWNER_REJECTED": "Rejected by owner",
    "APPROVED": "Approved",
    "APPROVAL_REJECTED": "Approval rejected",
    "RETIRED": "Retired",
    "REVIEW_FLAGGED": "Flagged for review",
    "REVIEW_CLEARED": "Review flag cleared",
}


def history_event_label(change_type: str) -> str:
    """A rule history change type in plain words."""
    return _HISTORY_EVENTS.get(change_type) or enum_label(change_type)


# --- Lifecycle: one strip, one primary action, the rest in a menu ------------------

_STEPS = [
    ("DRAFT", "Draft"),
    ("READY_FOR_COMPILE", "Ready"),
    ("COMPILED", "Compiled"),
    ("OWNER_CONFIRMED", "Confirmed"),
    ("ACTIVE", "Active"),
]


@dataclass(frozen=True)
class LifecycleStep:
    key: str
    label: str
    state: str  # "done" | "current" | "todo"


@dataclass(frozen=True)
class LifecycleView:
    steps: list[LifecycleStep]
    paused: bool  # NEEDS_CLARIFICATION: a pause on Draft, waiting for the owner
    retired: bool  # terminal: not a position on the line


def lifecycle_view(status: str) -> LifecycleView:
    """The lifecycle line for a status: earlier steps done, the current one
    marked, later ones to do. NEEDS_CLARIFICATION is a pause on Draft and
    RETIRED is terminal, so it has no current step."""
    if status == "RETIRED":
        steps = [LifecycleStep(key, label, "todo") for key, label in _STEPS]
        return LifecycleView(steps=steps, paused=False, retired=True)

    active = "DRAFT" if status == "NEEDS_CLARIFICATION" else status
    keys = [key for key, _ in _STEPS]
    current = keys.index(active) if active in keys else 0
    steps = [
        LifecycleStep(key, label, "done" if i < current else "current" if i == current else "todo")
        for i, (key, label) in enumerate(_STEPS)
    ]
    return LifecycleView(steps=steps, paused=status == "NEEDS_CLARIFICATION", retired=False)


@dataclass(frozen=True)
class RuleAction:
    key: str
    label: str
    icon: str


def primary_action(status: str, *, is_approver: bool) -> RuleAction | None:
    """The one button a rule's current state asks for, if any."""
    if status == "DRAFT":
        return RuleAction("submit", "Submit for compilation", ":material/send:")
    if status == "NEEDS_CLARIFICATION":
        return RuleAction("answer", "Save answer", ":material/reply:")
    if status == "COMPILED":
        return RuleAction("confirm", "Confirm: this is what I meant", ":material/check_circle:")
    if status == "OWNER_CONFIRMED" and is_approver:
        return RuleAction("approve", "Approve", ":material/verified:")
    return None


def secondary_actions(status: str, *, is_approver: bool) -> list[str]:
    """Keys of the actions that live in the "more" menu for this state."""
    actions = ["edit"]
    if status == "DRAFT":
        actions.append("delete")
    if status == "COMPILED":
        actions.append("reject")
    if is_approver and status in ("COMPILED", "OWNER_CONFIRMED", "ACTIVE"):
        actions.append("retire")
    return actions


def next_step_guidance(status: str, *, is_approver: bool = False) -> tuple[str, str]:
    """(Material icon, one sentence) saying what a status means and what is
    next, so an owner never has to guess."""
    if status == "DRAFT":
        return (
            ":material/edit_note:",
            "Still a draft. Refine the statement, then submit it for compilation.",
        )
    if status == "NEEDS_CLARIFICATION":
        return (
            ":material/help:",
            "The compiler needs one more detail. Answer the question on the Definition tab.",
        )
    if status == "READY_FOR_COMPILE":
        return (
            ":material/hourglass_top:",
            "Submitted, waiting for a data steward to write the check.",
        )
    if status == "COMPILED":
        return (
            ":material/search:",
            "SQL written and validated. Check it on the SQL & preview tab, then confirm.",
        )
    if status == "OWNER_CONFIRMED":
        if is_approver:
            return (":material/verified:", "Confirmed. Approve it to make it live.")
        return (":material/schedule:", "Confirmed, waiting for an approver to make it live.")
    if status == "ACTIVE":
        return (":material/play_circle:", "Live. Runs every week and raises findings.")
    if status == "RETIRED":
        return (":material/block:", "Retired. Excluded from future runs.")
    return ("", "")


# --- Rule filter pills (statuses grouped, plus the review flag) ---------------------

RULE_PILLS = ["Draft", "In progress", "Active", "Retired", "Flagged"]
_PILL_STATUSES = {
    "Draft": {"DRAFT", "NEEDS_CLARIFICATION"},
    "In progress": {"READY_FOR_COMPILE", "COMPILED", "OWNER_CONFIRMED"},
    "Active": {"ACTIVE"},
    "Retired": {"RETIRED"},
}


def rule_in_pill(rule: dict, pill: str, *, flagged_codes: set[str]) -> bool:
    if pill == "Flagged":
        return rule["RULE_CODE"] in flagged_codes
    return rule["STATUS"] in _PILL_STATUSES[pill]


def rule_pill_counts(rules: list[dict], *, flagged_codes: set[str]) -> dict[str, int]:
    return {
        pill: sum(1 for r in rules if rule_in_pill(r, pill, flagged_codes=flagged_codes))
        for pill in RULE_PILLS
    }


# --- Overview totals ----------------------------------------------------------------------


def overview_totals(rule_rows: list[dict], trend_by_rule: dict[str, list[dict]]) -> dict:
    """Open findings (items) across the rules in the last run, how many are critical,
    and the open total at the run before, so the page can say what changed. The
    previous total is None when no rule has an earlier run to compare with."""
    open_total = critical = previous_total = 0
    has_previous = False
    for row in rule_rows:
        open_ = row["ITEMS_FAILED_OPEN"] or 0
        open_total += open_
        if row["SEVERITY"] == "CRITICAL":
            critical += open_
        history = trend_by_rule.get(row["RULE_CODE"], [])
        position = next((i for i, h in enumerate(history) if h["RUN_ID"] == row["RUN_ID"]), None)
        if position:
            has_previous = True
            previous_total += history[position - 1]["ITEMS_FAILED_OPEN"] or 0
    return {
        "open": open_total,
        "previous_open": previous_total if has_previous else None,
        "critical_open": critical,
    }


# --- The Overview to-do list -------------------------------------------------------------

# status: (verb, order). Rules in READY_FOR_COMPILE wait on a data steward, not on the owner.
_TODO_VERBS = {
    "NEEDS_CLARIFICATION": ("Answer a question on", 0),
    "DRAFT": ("Submit", 1),
    "COMPILED": ("Confirm", 2),
}


def todo_items(
    rules: list[dict], *, flagged_codes: set[str], is_approver: bool, lapsing: int
) -> list[dict]:
    """What is waiting on the owner this week: rules to answer, submit, confirm
    or (for approvers) approve, rules flagged for review, and lapsing exceptions.
    Each item says where to go."""
    waiting: list[tuple[int, dict]] = []
    listed: set[str] = set()
    for rule in rules:
        verb, order = _TODO_VERBS.get(rule["STATUS"], (None, 0))
        if verb is None and is_approver and rule["STATUS"] == "OWNER_CONFIRMED":
            verb, order = "Approve", 3
        if verb is None:
            continue
        listed.add(rule["RULE_CODE"])
        waiting.append((order, _todo(rule, verb)))
    items = [item for _, item in sorted(waiting, key=lambda pair: pair[0])]
    items += [
        _todo(rule, "Review")
        for rule in rules
        if rule["RULE_CODE"] in flagged_codes and rule["RULE_CODE"] not in listed
    ]
    if lapsing:
        noun = "exception lapses" if lapsing == 1 else "exceptions lapse"
        items.append(
            {
                "rule": None,
                "verb": "Review",
                "label": f"{lapsing} accepted {noun} soon",
                "page": PAGE_FINDINGS,
                "params": {"show": "Lapsing"},
            }
        )
    return items


def _todo(rule: dict, verb: str) -> dict:
    return {
        "rule": rule["RULE_CODE"],
        "verb": verb,
        "label": f"{verb} {rule['RULE_CODE']} · {rule['RULE_NAME']}",
        "page": PAGE_RULES,
        "params": {"rule": rule["RULE_CODE"]},
    }


# --- Findings: status pills and the rules a view covers ----------------------------------

FINDING_PILLS = ["Open", "Accepted", "False positive", "Resolved", "Lapsing"]
_FINDING_PILL_STATUS = {
    "Open": "OPEN",
    "Accepted": "ACCEPTED_EXCEPTION",
    "False positive": "FALSE_POSITIVE",
    "Resolved": "RESOLVED",
}


def finding_in_pill(finding: dict, pill: str, *, now: datetime | None = None) -> bool:
    """Whether a finding belongs to a status pill. "Lapsing" is the accepted
    exceptions that have lapsed or lapse soon, so it overlaps "Accepted"."""
    if pill == "Lapsing":
        return finding_ops.is_lapsing(finding, now=now)
    return finding["STATUS"] == _FINDING_PILL_STATUS[pill]


def finding_pill_counts(findings: list[dict], *, now: datetime | None = None) -> dict[str, int]:
    return {
        pill: sum(1 for f in findings if finding_in_pill(f, pill, now=now))
        for pill in FINDING_PILLS
    }


def rules_in_view(findings: list[dict]) -> list[dict]:
    """The distinct rules behind a set of findings, most severe first, each
    with its plain-English statement and how many of the findings it raised."""
    by_code: dict[str, dict] = {}
    for f in findings:
        entry = by_code.setdefault(
            f["RULE_CODE"],
            {
                "code": f["RULE_CODE"],
                "name": f["RULE_NAME"],
                "severity": f["SEVERITY"],
                "statement": f.get("RULE_STATEMENT") or "",
                "owner_email": f["OWNER_EMAIL"],
                "findings": 0,
            },
        )
        entry["findings"] += 1

    def severity_rank(severity: str) -> int:
        return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)

    return sorted(
        by_code.values(), key=lambda r: (severity_rank(r["severity"]), -r["findings"], r["code"])
    )


# --- Page chrome (st.*) ---------------------------------------------------------------

# One place for the CSS that tightens Streamlit's default spacing and styles the
# lifecycle strip. Selectors that touch Streamlit's own markup (data-testid) are
# here only, so a Streamlit upgrade means one fix.
_LAYOUT_CSS = """
<style>
[data-testid="stMainBlockContainer"] { padding: 4.4rem 1.6rem 1.5rem; max-width: none; }
[data-testid="stVerticalBlock"] { gap: 0.65rem; }
[data-testid="stMainBlockContainer"] h4 { padding: 0.1rem 0; margin: 0; }
[data-testid="stMetricValue"] { font-size: 1.6rem; }
[data-testid="stElementContainer"]:has(.dq-css) { display: none; }
/* Scrolling panes are sized from the viewport, so a short laptop screen doesn't scroll the page.
   Streamlit puts a container's fixed height on its layout wrapper (as height and flex-basis), so
   that is what we override. */
[data-testid="stLayoutWrapper"]:has(> .st-key-rules_detail),
[data-testid="stLayoutWrapper"]:has(> .st-key-findings_detail) {
  height: calc(100vh - 12.9rem) !important; flex-basis: auto !important; min-height: 320px; }
[data-testid="stLayoutWrapper"]:has(> .st-key-rules_list),
[data-testid="stLayoutWrapper"]:has(> .st-key-findings_list) {
  height: calc(100vh - 14.9rem) !important; flex-basis: auto !important; min-height: 280px; }
/* The Findings chip row, when shown, takes a row of height from the panes below it. */
[data-testid="stMainBlockContainer"]:has(.st-key-findings_chips)
  [data-testid="stLayoutWrapper"]:has(> .st-key-findings_detail) {
  height: calc(100vh - 15.9rem) !important; }
[data-testid="stMainBlockContainer"]:has(.st-key-findings_chips)
  [data-testid="stLayoutWrapper"]:has(> .st-key-findings_list) {
  height: calc(100vh - 17.9rem) !important; }
/* To-do links wrap instead of cutting long rule names off. */
.st-key-overview_todo [data-testid="stPageLink-NavLink"] { height: auto; align-items: flex-start; }
.st-key-overview_todo [data-testid="stPageLink-NavLink"] p { white-space: normal !important;
  overflow: visible !important; text-overflow: clip !important; }
/* Children must not shrink to fit: when content is taller than the pane it scrolls instead. */
.st-key-rules_detail > *, .st-key-rules_list > *,
.st-key-findings_detail > *, .st-key-findings_list > * { flex-shrink: 0; }
.st-key-rules_list button { justify-content: flex-start; height: auto; padding: 0.4rem 0.7rem;
  border: 1px solid transparent; }
.st-key-rules_list button > div, .st-key-rules_list button > div > span { width: 100%;
  justify-content: flex-start; }
.st-key-rules_list button [data-testid="stMarkdownContainer"] { width: 100%; text-align: left; }
.st-key-rules_list button p { text-align: left; line-height: 1.35; }
.st-key-rules_list button[data-testid="stBaseButton-primary"] { background: #dbeafe;
  border-color: #93c5fd; }
.st-key-rules_list button[data-testid="stBaseButton-primary"] p { color: #1e3a8a; }
.dq-steps { display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem; }
.dq-step { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.82rem;
  padding: 0.12rem 0.55rem; border-radius: 999px; border: 1px solid #9ca3af; color: #374151;
  background: #fff; white-space: nowrap; }
.dq-step.done { color: #166534; border-color: #86efac; background: #f0fdf4; }
.dq-step.current { color: #fff; font-weight: 600; }
.dq-sep { width: 0.6rem; height: 2px; background: #d1d5db; }
.dq-retired { padding: 0.3rem 0.8rem; border-radius: 6px; background: #fef2f2;
  border: 1px solid #fca5a5; color: #991b1b; font-weight: 600; font-size: 0.85rem; }
</style>
<span class="dq-css"></span>
"""


def apply_layout_css() -> None:
    st.markdown(_LAYOUT_CSS, unsafe_allow_html=True)


def render_lifecycle_strip(status: str) -> None:
    """The lifecycle as one slim line of steps: done, current, to do. Uses
    dark text and borders, not opacity, so every step stays readable."""
    view = lifecycle_view(status)
    if view.retired:
        st.markdown(
            '<div class="dq-retired">Retired: excluded from future runs</div>',
            unsafe_allow_html=True,
        )
        return

    current_color = _STATUS_STYLE.get(status, _STATUS_STYLE["DRAFT"])[3]
    parts: list[str] = []
    for i, step in enumerate(view.steps):
        if i:
            parts.append('<span class="dq-sep"></span>')
        if step.state == "done":
            parts.append(f'<span class="dq-step done">✓ {step.label}</span>')
        elif step.state == "current":
            label = f"{step.label} · paused" if view.paused else step.label
            parts.append(
                f'<span class="dq-step current" style="background:{current_color};'
                f'border-color:{current_color};">{label}</span>'
            )
        else:
            parts.append(f'<span class="dq-step">{step.label}</span>')
    st.markdown(f'<div class="dq-steps">{"".join(parts)}</div>', unsafe_allow_html=True)


_OWNER_WIDGET_KEY = "acting_as_widget"
_OWNER_REMEMBER_KEY = "acting_as_email"


def current_owner(store: GovernanceStore) -> dict | None:
    """The owner being acted as. Streamlit drops a widget's own state when you
    change page, so the choice is also kept under a plain session key; the
    selector's state wins when it exists (it is set before a rerun, so this is
    right even before the selector is drawn in a run). Falls back to the first owner."""
    owners = store.list_owners()
    if not owners:
        return None
    emails = {o["OWNER_EMAIL"] for o in owners}
    chosen = st.session_state.get(_OWNER_WIDGET_KEY)
    if chosen in emails:
        st.session_state[_OWNER_REMEMBER_KEY] = chosen
    else:
        chosen = st.session_state.get(_OWNER_REMEMBER_KEY)
    for owner in owners:
        if owner["OWNER_EMAIL"] == chosen:
            return owner
    return owners[0]


def owner_selector(store: GovernanceStore) -> dict | None:
    """The "Acting as" selector (brief §10.6: a visible facilitated-session
    banner), drawn compactly in the page header. With one owner it is a label."""
    owners = store.list_owners()
    if not owners:
        st.warning("No active owners configured in DQ_OWNER.")
        return None
    owner = current_owner(store)
    if len(owners) == 1:
        st.caption(f"Facilitated session · acting as **{owner['DISPLAY_NAME']}**")
        return owner
    names = {o["OWNER_EMAIL"]: o["DISPLAY_NAME"] for o in owners}
    emails = list(names)
    st.selectbox(
        "Acting as",
        emails,
        index=emails.index(owner["OWNER_EMAIL"]),
        format_func=lambda email: f"Acting as: {names[email]}",
        key=_OWNER_WIDGET_KEY,
        label_visibility="collapsed",
        help="Facilitated session: every action is recorded against this owner.",
    )
    return current_owner(store)


def key_values(pairs: list[tuple[str, str]], *, columns: int = 4) -> None:
    """A compact grid of small labels over bold values, instead of a block of
    disabled input widgets."""
    cells = st.columns(columns)
    for i, (label, value) in enumerate(pairs):
        cells[i % columns].markdown(
            f'<span style="font-size:0.78rem;color:#4b5563;">{label}</span><br>**{value or "—"}**',
            unsafe_allow_html=True,
        )


def page_header(
    store: GovernanceStore, title: str, *, icon: str, subtitle: str | None = None
) -> dict | None:
    """One compact header row: the page title (matching its navigation label
    and icon), a one-line context, and the owner selector on the right."""
    left, right = st.columns([5, 2], vertical_alignment="center")
    with left:
        st.markdown(f"#### :material/{icon}: {title}")
        if subtitle:
            st.caption(subtitle)
    with right:
        return owner_selector(store)


def select_index(options: list[str], value: str | None, default: int = 0) -> int:
    """Index of `value` in `options`, or `default` if it's not there (e.g.
    an older rule using a value not in the current dropdown list)."""
    try:
        return options.index(value) if value is not None else default
    except ValueError:
        return default


# --- Display helpers (pure; timestamps are stored UTC, shown in NZ time) ---------------


def format_nz(value: datetime | None) -> str:
    """A date in Pacific/Auckland, e.g. "15 Oct 2026"."""
    if value is None:
        return "—"
    return value.astimezone(NZ_TZ).strftime("%d %b %Y").lstrip("0")


def format_nz_datetime(value: datetime | None) -> str:
    """A date and 12-hour time in Pacific/Auckland, e.g. "15 Oct 2026, 12:30 pm"."""
    if value is None:
        return "—"
    local = value.astimezone(NZ_TZ)
    clock = local.strftime("%I:%M %p").lstrip("0").lower()
    return f"{local.strftime('%d %b %Y').lstrip('0')}, {clock}"


_RESOLUTION_LABELS = {
    "FIXED": "fixed",
    "OUT_OF_SCOPE": "out of scope",
    "RULE_RETIRED": "rule retired",
}


def finding_status_label(finding: dict, *, now: datetime | None = None) -> str:
    """Plain-words status for a finding row -- an accepted exception carries
    its expiry, since that's what an owner needs to see at a glance."""
    status = finding["STATUS"]
    if status == "OPEN":
        return "Open"
    if status == "FALSE_POSITIVE":
        return "False positive"
    if status == "RESOLVED":
        reason = _RESOLUTION_LABELS.get(finding.get("RESOLUTION_REASON") or "")
        return f"Resolved ({reason})" if reason else "Resolved"
    if status == "ACCEPTED_EXCEPTION":
        expires_at = finding.get("DISPOSITION_EXPIRES_AT")
        if expires_at is None:
            return "Accepted exception"
        if expires_at <= (now or datetime.now(UTC)):
            return "Accepted — lapsed, reopens next run"
        return f"Accepted until {format_nz(expires_at)}"
    return status


# --- Run results helpers (pure) ----------------------------------------------------------


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def format_age(value: datetime | None, *, now: datetime | None = None) -> str:
    """ "5 hours ago" -- for how fresh a source sync or a run is."""
    if value is None:
        return "never"
    seconds = int(((now or datetime.now(UTC)) - value).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{_plural(seconds // 60, 'minute')} ago"
    if seconds < 86400:
        return f"{_plural(seconds // 3600, 'hour')} ago"
    return f"{_plural(seconds // 86400, 'day')} ago"


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "—"
    seconds = int((end - start).total_seconds())
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60} s"


def change_label(previous: int | None, current: int) -> str:
    """Change in a count since the previous run. Down is the good direction
    for exceptions, but this only reports it -- colour is the caller's call."""
    if previous is None:
        return "first run"
    if current < previous:
        return f"▼ {previous - current}"
    if current > previous:
        return f"▲ {current - previous}"
    return "no change"
