"""Findings (brief §10.6, "My findings"): the findings on the left, and on the
right whatever you need to decide about them -- always starting with what the
rule is for, so you never switch pages to remember. Accept as exception, mark
false positive or revoke, for one finding or a whole selection at once.
"""

from __future__ import annotations

import getpass
import hashlib
import json
from datetime import datetime, time, timedelta

import streamlit as st

from dq import finding_ops
from dq.app import context, ui

settings, store, sources_config = context.load_context()
owner = ui.page_header(store, "Findings", icon="fact_check")
if owner is None:
    st.stop()

is_approver = bool(owner.get("IS_APPROVER"))
facilitated_by = getpass.getuser()

_PILL_LABELS = {
    "Open": "Open",
    "Accepted": "Accepted exceptions",
    "False positive": "False positives",
    "Resolved": "Resolved",
    "Lapsing": "Lapsing soon",
}


def _finish_action(message: str) -> None:
    """After any change to a finding: flash a toast on the next run, then give
    the table a fresh selection widget (its key carries this epoch) so rows
    that just left the current filter don't leave a phantom tick behind."""
    st.session_state["_flash_finding"] = message
    st.session_state["_findings_epoch"] = st.session_state.get("_findings_epoch", 0) + 1
    st.rerun()


flash = st.session_state.pop("_flash_finding", None)
if flash:
    st.toast(flash, icon=":material/check_circle:")

# --- Scope ------------------------------------------------------------------------------

show_all = bool(is_approver and st.session_state.get("findings_all_owners"))
all_findings = store.list_findings(None if show_all else owner["OWNER_EMAIL"])

if not all_findings:
    st.info(
        "No findings yet. They appear after the first run of your active rules. "
        "Run `dq run`, or use **Run checks now** on the Overview page."
    )
    st.page_link(ui.PAGE_OVERVIEW, label="Go to Overview", icon=":material/dashboard:")
    st.stop()

rule_names = {f["RULE_CODE"]: f["RULE_NAME"] for f in all_findings}
rule_options = sorted(rule_names)

# --- Arrival from another page (Rules, Overview): show one rule's open findings ----------

st.session_state.setdefault("findings_status", ["Open"])
jump_rule = st.query_params.get("rule")
if jump_rule:
    st.query_params.clear()
    if jump_rule in rule_options:
        st.session_state["findings_rule"] = [jump_rule]
        st.session_state["findings_status"] = ["Open"]
        st.session_state["_findings_from"] = jump_rule

# --- Header: status pills (they double as the counts), search, filters -----------------

pill_counts = ui.finding_pill_counts(all_findings)

pills_col, search_col, filters_col = st.columns([6, 3, 1.5], vertical_alignment="center")
with pills_col:
    pill_filter = st.pills(
        "Show",
        ui.FINDING_PILLS,
        selection_mode="multi",
        format_func=lambda pill: f"{_PILL_LABELS[pill]} · {pill_counts[pill]:,}",
        key="findings_status",
        label_visibility="collapsed",
    )
with search_col:
    search = st.text_input(
        "Search",
        placeholder="Search record or site",
        key="findings_search",
        label_visibility="collapsed",
        icon=":material/search:",
    )
with filters_col:
    with st.popover("Filters", icon=":material/filter_list:", width="stretch"):
        rule_filter = st.multiselect(
            "Rule",
            rule_options,
            format_func=lambda code: f"{code} · {rule_names[code]}",
            key="findings_rule",
        )
        severity_filter = st.multiselect(
            "Severity", ui.SEVERITIES, format_func=ui.enum_label, key="findings_severity"
        )
        min_age = st.number_input(
            "Open at least (days)", min_value=0, value=0, key="findings_min_age"
        )
        if is_approver:
            st.checkbox("Include other owners' findings", key="findings_all_owners")


# Active filters, shown as removable chips so they are never hidden inside the popover.
def _drop_rule(code: str) -> None:
    st.session_state["findings_rule"] = [
        c for c in st.session_state.get("findings_rule", []) if c != code
    ]


def _drop_severity(severity: str) -> None:
    st.session_state["findings_severity"] = [
        s for s in st.session_state.get("findings_severity", []) if s != severity
    ]


def _drop_age() -> None:
    st.session_state["findings_min_age"] = 0


chips = [
    *[(f"Rule: {c}", _drop_rule, (c,)) for c in rule_filter],
    *[(f"Severity: {ui.enum_label(s)}", _drop_severity, (s,)) for s in severity_filter],
    *([(f"Open ≥ {min_age} days", _drop_age, ())] if min_age else []),
]
came_from = st.session_state.get("_findings_from")
if came_from and rule_filter != [came_from]:
    st.session_state.pop("_findings_from", None)
    came_from = None
if chips or came_from:
    with st.container(horizontal=True, vertical_alignment="center", key="findings_chips"):
        if came_from:
            st.page_link(
                ui.PAGE_RULES,
                label="Back to the rule",
                icon=":material/arrow_back:",
                query_params={"rule": came_from},
            )
        for label, callback, args in chips:
            st.button(
                label,
                icon=":material/close:",
                type="secondary",
                key=f"chip_{label}",
                on_click=callback,
                args=args,
            )

# --- Filtering --------------------------------------------------------------------------


def _matches(f: dict) -> bool:
    if pill_filter and not any(ui.finding_in_pill(f, pill) for pill in pill_filter):
        return False
    if rule_filter and f["RULE_CODE"] not in rule_filter:
        return False
    if severity_filter and f["SEVERITY"] not in severity_filter:
        return False
    if min_age and (f["AGE_DAYS"] or 0) < min_age:
        return False
    if search:
        needle = search.lower()
        haystack = " ".join(
            str(f.get(k) or "") for k in ("RECORD_NAME", "SITE_NAME", "RECORD_ID", "ITEM_KEY")
        ).lower()
        if needle not in haystack:
            return False
    return True


filtered = [f for f in all_findings if _matches(f)]

# A changed filter invalidates the table's selection the same way an action does.
st.session_state.setdefault("_findings_epoch", 0)
filter_signature = (
    tuple(sorted(pill_filter or [])),
    tuple(sorted(rule_filter)),
    tuple(sorted(severity_filter)),
    search,
    min_age,
    show_all,
)
if st.session_state.get("_findings_signature") != filter_signature:
    st.session_state["_findings_signature"] = filter_signature
    st.session_state["_findings_epoch"] += 1
epoch = st.session_state["_findings_epoch"]

list_col, detail_col = st.columns([3, 2], gap="medium")

# --- Left: the findings -------------------------------------------------------------------

selected: list[dict] = []
with list_col:
    st.caption(
        f"{len(filtered):,} of {len(all_findings):,} findings · "
        "counts on Overview update at the next run"
    )
    if not filtered:
        st.info("No findings match the current filters.")
    else:
        with st.container(height=ui.PANE_HEIGHT - 36, key="findings_list"):
            event = st.dataframe(
                [
                    {
                        "Rule": f["RULE_CODE"],
                        "Severity": ui.severity_cell(f["SEVERITY"]),
                        "Record": f["RECORD_NAME"] or f["RECORD_ID"],
                        "Site": f["SITE_NAME"],
                        "Item": f["ITEM_KEY"],
                        "Age (days)": f["AGE_DAYS"],
                        "Status": ui.finding_status_label(f),
                        **({"Owner": f["OWNER_EMAIL"]} if show_all else {}),
                    }
                    for f in filtered
                ],
                width="stretch",
                height="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="multi-row",
                key=f"findings_table_{epoch}",
                column_config={
                    "Age (days)": st.column_config.NumberColumn(format="%d", width="small"),
                    "Rule": st.column_config.TextColumn(width="small"),
                    "Severity": st.column_config.TextColumn(width="small"),
                },
            )
        selected = [filtered[i] for i in event.selection["rows"]]


# --- Right: what the rule is for, why it failed, and what to do -------------------------------


def _rule_card(rule: dict, *, expanded: bool = True, show_count: bool = True) -> None:
    """A rule at a glance: its name, severity, its plain-English statement and, when
    asked, how many findings it raised in this view."""
    heading = f"{rule['code']} · {rule['name']}"
    with st.expander(heading, expanded=expanded, icon=":material/rule:"):
        severity = ui.severity_cell(rule["severity"])
        if show_count:
            noun = "finding" if rule["findings"] == 1 else "findings"
            st.markdown(f"{severity} · {rule['findings']:,} {noun} here")
        else:
            st.markdown(severity)
        if rule["statement"]:
            st.markdown(rule["statement"])
        else:
            st.caption("No statement recorded for this rule.")
        st.page_link(
            ui.PAGE_RULES,
            label="View rule",
            icon=":material/arrow_forward:",
            query_params={"rule": rule["code"]},
        )


def _render_flag_prompt() -> None:
    """Offered after a false positive: the rule itself may be wrong."""
    prompt: dict[str, str] | None = st.session_state.get("_findings_flag_prompt")
    if not prompt:
        return
    with st.container(border=True):
        st.markdown(
            "**A false positive can mean the rule itself is wrong.** Flag it for review so it "
            "shows up on the Rules page for whoever looks after it?"
        )
        for code, default_reason in list(prompt.items()):
            reason = st.text_input(
                f"Why should {code} be reviewed?", value=default_reason, key=f"flag_reason_{code}"
            )
            if st.button("Flag for review", icon=":material/flag:", key=f"flag_btn_{code}"):
                try:
                    finding_ops.flag_rule_for_review(
                        store, code, reason, owner=owner, facilitated_by=facilitated_by
                    )
                except finding_ops.FindingOpsError as exc:
                    st.error(str(exc))
                else:
                    remaining = {c: r for c, r in prompt.items() if c != code}
                    if remaining:
                        st.session_state["_findings_flag_prompt"] = remaining
                    else:
                        st.session_state.pop("_findings_flag_prompt", None)
                    st.session_state["_flash_finding"] = f"Flagged {code} for review."
                    st.rerun()
        if st.button("No thanks", key="flag_dismiss"):
            st.session_state.pop("_findings_flag_prompt", None)
            st.rerun()


def _render_why_it_failed(finding: dict) -> None:
    if not finding["DETAIL"]:
        return
    try:
        detail = json.loads(finding["DETAIL"])
    except ValueError:
        st.markdown("**Why it failed**")
        st.code(finding["DETAIL"])
        return
    flat = isinstance(detail, dict) and all(
        not isinstance(v, (dict, list)) for v in detail.values()
    )
    if flat and len(detail) <= 4 and all(len(str(v)) <= 40 for v in detail.values()):
        facts = " · ".join(
            f"**{key.replace('_', ' ').capitalize()}:** {value}" for key, value in detail.items()
        )
        st.markdown(f"**Why it failed**  \n{facts}")
    elif flat:
        st.markdown("**Why it failed**")
        ui.key_values(
            [(key.replace("_", " ").capitalize(), str(value)) for key, value in detail.items()],
            columns=2,
        )
    else:
        st.markdown("**Why it failed**")
        st.json(detail)


def _render_finding_facts(finding: dict) -> None:
    name = finding["RECORD_NAME"] or finding["RECORD_ID"]
    if finding["RECORD_URL"]:
        st.markdown(f"##### [{name}]({finding['RECORD_URL']})")
    else:
        st.markdown(f"##### {name}")
    where = " · ".join(part for part in (finding["SITE_NAME"], finding["ITEM_KEY"]) if part)
    st.caption(
        f"{where + ' · ' if where else ''}{ui.finding_status_label(finding)} · "
        f"open {finding['AGE_DAYS']} days · seen {finding['TIMES_SEEN']}×, "
        f"reopened {finding['REOPENED_COUNT']}× · first seen "
        f"{ui.format_nz(finding['FIRST_SEEN_AT'])}"
    )


def _render_actions(selected: list[dict]) -> None:
    count = len(selected)
    noun = "finding" if count == 1 else "findings"
    # Reason and expiry widgets are keyed by the selection, so text typed for one
    # selection can never carry over onto a different one.
    selection_sig = hashlib.sha1(
        "|".join(sorted(f["FINDING_ID"] for f in selected)).encode()
    ).hexdigest()[:8]

    def key(name: str) -> str:
        return f"{name}_{epoch}_{selection_sig}"

    if any(f["STATUS"] == "RESOLVED" for f in selected):
        st.info(
            "Resolved findings are closed, so there is nothing left to accept or dismiss. "
            "Untick them to act on the rest."
        )
        return

    all_disposed = all(f["STATUS"] in ("ACCEPTED_EXCEPTION", "FALSE_POSITIVE") for f in selected)
    options = ["Accept exception", "False positive"] + (["Revoke"] if all_disposed else [])
    st.markdown(f"**{count:,} {noun} selected**")
    action = (
        st.segmented_control(
            "Action",
            options,
            default="Revoke" if all_disposed else options[0],
            key=key("action"),
            label_visibility="collapsed",
        )
        or options[0]
    )
    ids = [f["FINDING_ID"] for f in selected]

    if action == "Accept exception":
        configured_days = store.get_config_value("EXCEPTION_DEFAULT_DAYS")
        try:
            default_days = int(configured_days) if configured_days else 365
        except ValueError:
            default_days = 365
        today_nz = datetime.now(ui.NZ_TZ).date()
        expiry_date = st.date_input(
            "Accepted until",
            value=today_nz + timedelta(days=default_days),
            min_value=today_nz + timedelta(days=1),
            key=key("accept_until"),
        )
        accept_reason = st.text_area(
            "Reason (required)",
            key=key("accept_reason"),
            placeholder="Why is this acceptable for now?",
            height=80,
        )
        until = ui.format_nz(datetime.combine(expiry_date, time(12), tzinfo=ui.NZ_TZ))
        st.caption(
            f"{'It reopens' if count == 1 else 'They reopen'} at the next run after {until} "
            "if still failing."
        )
        if st.button(
            f"Accept {count:,} {noun}",
            type="primary",
            icon=":material/check:",
            key=key("accept_btn"),
        ):
            try:
                finding_ops.accept_exception(
                    store,
                    ids,
                    accept_reason,
                    owner=owner,
                    facilitated_by=facilitated_by,
                    expires_at=datetime.combine(expiry_date, time(23, 59, 59), tzinfo=ui.NZ_TZ),
                )
            except finding_ops.FindingOpsError as exc:
                st.error(str(exc))
            else:
                _finish_action(
                    f"Accepted {count:,} {noun} as "
                    f"{'an exception' if count == 1 else 'exceptions'} until {until}."
                )

    elif action == "False positive":
        fp_reason = st.text_area(
            "Reason (required)",
            key=key("fp_reason"),
            placeholder="Why is the rule wrong about this record?",
            height=80,
        )
        st.caption(
            f"{'It stays' if count == 1 else 'They stay'} hidden for good unless you revoke this."
        )
        if st.button(
            f"Mark {count:,} as false positive",
            type="primary",
            icon=":material/block:",
            key=key("fp_btn"),
        ):
            try:
                finding_ops.mark_false_positive(
                    store, ids, fp_reason, owner=owner, facilitated_by=facilitated_by
                )
            except finding_ops.FindingOpsError as exc:
                st.error(str(exc))
            else:
                per_rule: dict[str, int] = {}
                for f in selected:
                    per_rule[f["RULE_CODE"]] = per_rule.get(f["RULE_CODE"], 0) + 1
                targets = {
                    code: f"{n} finding(s) marked as false positive: {fp_reason.strip()}"
                    for code, n in sorted(per_rule.items())
                    if store.get_rule_review_flag(code) is None
                }
                if targets:
                    st.session_state["_findings_flag_prompt"] = targets
                _finish_action(f"Marked {count:,} {noun} as false positive.")

    else:  # Revoke
        revoke_reason = st.text_area(
            "Reason (required)",
            key=key("revoke_reason"),
            placeholder="Why is this no longer an exception or false positive?",
            height=80,
        )
        st.caption(f"{'It goes' if count == 1 else 'They go'} back to Open.")
        if st.button(
            f"Revoke {count:,} {noun}",
            type="primary",
            icon=":material/undo:",
            key=key("revoke_btn"),
        ):
            try:
                finding_ops.revoke_disposition(
                    store, ids, revoke_reason, owner=owner, facilitated_by=facilitated_by
                )
            except finding_ops.FindingOpsError as exc:
                st.error(str(exc))
            else:
                _finish_action(f"Revoked {count:,} {noun}. Back to Open.")


def _render_history(finding: dict) -> None:
    history = store.list_dispositions(finding["FINDING_ID"])
    if not history:
        return
    with st.expander("Disposition history"):
        st.dataframe(
            [
                {
                    "When": ui.format_nz_datetime(h["SET_AT"]),
                    "By": h["SET_BY"],
                    "Action": h["DISPOSITION"].replace("_", " ").capitalize(),
                    "Reason": h["REASON"],
                    "Until": ui.format_nz(h["EXPIRES_AT"]) if h["EXPIRES_AT"] else "—",
                }
                for h in history
            ],
            hide_index=True,
            width="stretch",
        )


with detail_col:
    with st.container(height=ui.PANE_HEIGHT, border=True, key="findings_detail"):
        _render_flag_prompt()
        if not filtered:
            st.caption("Nothing to show. Adjust the filters on the left.")
        elif not selected:
            # No selection: tell me what these findings are about.
            in_view = ui.rules_in_view(filtered)
            noun = "rule" if len(in_view) == 1 else "rules"
            st.markdown(f"**{len(in_view)} {noun} behind these findings**")
            st.caption("Tick rows on the left to accept, dismiss or revoke them.")
            for rule in in_view:
                _rule_card(rule, expanded=len(in_view) <= 4)
        else:
            involved = ui.rules_in_view(selected)
            for rule in involved:
                _rule_card(rule, expanded=len(involved) == 1, show_count=len(selected) > 1)
            if len(selected) == 1:
                _render_finding_facts(selected[0])
                _render_why_it_failed(selected[0])
            _render_actions(selected)
            if len(selected) == 1:
                _render_history(selected[0])
