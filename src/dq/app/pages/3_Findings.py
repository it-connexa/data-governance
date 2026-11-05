"""Page 3 -- Findings (brief §10.6, page 4 "My findings"): every finding the
rules have raised, filterable, with accept-as-exception / false-positive /
revoke applied to one finding or a whole selection at once. Marking a
false positive offers to flag the rule for review.
"""

from __future__ import annotations

import getpass
import hashlib
import json
from datetime import datetime, time, timedelta

import streamlit as st

from dq import finding_ops
from dq.app import context, ui

st.set_page_config(page_title="Findings", layout="wide")

settings, store, sources_config = context.load_context()
owner = ui.render_sidebar(store)
st.title("Findings")

if owner is None:
    st.stop()

is_approver = bool(owner.get("IS_APPROVER"))
facilitated_by = getpass.getuser()


def _finish_action(message: str) -> None:
    """After any change to a finding: flash a message, then give the table
    a fresh selection widget (its key carries this epoch) so rows that just
    left the current filter don't leave a phantom tick behind -- the same
    reasoning as the Rule Catalogue's table, simplified because a
    multi-selection is simply cleared rather than re-seeded."""
    st.session_state["_flash_finding"] = message
    st.session_state["_findings_epoch"] = st.session_state.get("_findings_epoch", 0) + 1
    st.rerun()


flash = st.session_state.pop("_flash_finding", None)
if flash:
    st.success(flash)

# --- "Flag this rule for review?" prompt, offered after a false positive ----------------

flag_prompt: dict[str, str] | None = st.session_state.get("_findings_flag_prompt")
if flag_prompt:
    with st.container(border=True):
        st.markdown(
            "**A false positive can mean the rule itself is wrong.** Flag it for review so "
            "it shows up in the Rule Catalogue for whoever looks after the rule?"
        )
        for code, default_reason in list(flag_prompt.items()):
            reason_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
            with reason_col:
                flag_reason = st.text_input(
                    f"Why should {code} be reviewed?",
                    value=default_reason,
                    key=f"flag_reason_{code}",
                )
            with button_col:
                flag_clicked = st.button("Flag for review", key=f"flag_btn_{code}")
            if flag_clicked:
                try:
                    finding_ops.flag_rule_for_review(
                        store, code, flag_reason, owner=owner, facilitated_by=facilitated_by
                    )
                except finding_ops.FindingOpsError as exc:
                    st.error(str(exc))
                else:
                    remaining = {c: r for c, r in flag_prompt.items() if c != code}
                    if remaining:
                        st.session_state["_findings_flag_prompt"] = remaining
                    else:
                        st.session_state.pop("_findings_flag_prompt", None)
                    st.session_state["_flash_finding"] = f"Flagged {code} for review."
                    st.rerun()
        if st.button("No thanks", key="flag_dismiss"):
            st.session_state.pop("_findings_flag_prompt", None)
            st.rerun()

# --- Scope and header --------------------------------------------------------------------

show_all = False
if is_approver:
    show_all = st.checkbox("Include other owners' findings", key="findings_all_owners")
all_findings = store.list_findings(None if show_all else owner["OWNER_EMAIL"])

if not all_findings:
    st.info(
        "No findings yet. They appear after the first run of your active rules — "
        "run `dq run`, or use **Run checks now** on the Run results page."
    )
    st.stop()

summary = finding_ops.summarise(all_findings)
metric_cols = st.columns(4)
metric_cols[0].metric("Open", f"{summary.open:,}")
metric_cols[1].metric("Accepted exceptions", f"{summary.accepted:,}")
metric_cols[2].metric("False positives", f"{summary.false_positive:,}")
metric_cols[3].metric("Resolved", f"{summary.resolved:,}")
if summary.expired or summary.expiring_soon:
    parts = []
    if summary.expiring_soon:
        parts.append(
            f"{summary.expiring_soon} accepted exception(s) lapse within "
            f"{finding_ops.EXPIRING_SOON_DAYS} days"
        )
    if summary.expired:
        parts.append(f"{summary.expired} already lapsed and will reopen at the next run")
    st.warning("⏳ " + "; ".join(parts) + ".")
st.caption(
    "Changes here apply straight away. The counts on the Run results page update at the next run."
)

# --- Filters ------------------------------------------------------------------------------

STATUS_GROUPS = {
    "Open": ["OPEN"],
    "Accepted exceptions": ["ACCEPTED_EXCEPTION"],
    "False positives": ["FALSE_POSITIVE"],
    "Resolved": ["RESOLVED"],
}
rule_options = sorted({f["RULE_CODE"] for f in all_findings})

st.session_state.setdefault("findings_status", ["Open"])
jump_rule = st.session_state.pop("_findings_jump_rule", None)
if jump_rule in rule_options:
    st.session_state["findings_rule"] = [jump_rule]
    st.session_state["findings_status"] = ["Open"]

status_filter = st.pills("Show", list(STATUS_GROUPS), selection_mode="multi", key="findings_status")
filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
with filter_col1:
    rule_filter = st.multiselect("Rule", rule_options, key="findings_rule")
with filter_col2:
    severity_filter = st.multiselect("Severity", ui.SEVERITIES, key="findings_severity")
with filter_col3:
    search = st.text_input("Search record / site", key="findings_search")
with filter_col4:
    min_age = st.number_input("Open at least (days)", min_value=0, value=0, key="findings_min_age")

allowed_statuses = {s for label in (status_filter or STATUS_GROUPS) for s in STATUS_GROUPS[label]}


def _matches(f: dict) -> bool:
    if f["STATUS"] not in allowed_statuses:
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
st.caption(f"{len(filtered):,} of {len(all_findings):,} findings")

if not filtered:
    st.info("No findings match the current filters.")
    st.stop()

# A changed filter invalidates the table's selection the same way an action does.
st.session_state.setdefault("_findings_epoch", 0)
filter_signature = (
    tuple(sorted(allowed_statuses)),
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

event = st.dataframe(
    [
        {
            "Rule": f["RULE_CODE"],
            "Severity": f["SEVERITY"],
            "Record": f["RECORD_NAME"] or f["RECORD_ID"],
            "Site": f["SITE_NAME"],
            "Item": f["ITEM_KEY"],
            "Age (days)": f["AGE_DAYS"],
            "Seen": f["TIMES_SEEN"],
            "Reopened": f["REOPENED_COUNT"],
            "Status": ui.finding_status_label(f),
            "Salesforce": f["RECORD_URL"],
            **({"Owner": f["OWNER_EMAIL"]} if show_all else {}),
        }
        for f in filtered
    ],
    width="stretch",
    hide_index=True,
    on_select="rerun",
    selection_mode="multi-row",
    key=f"findings_table_{epoch}",
    column_config={
        "Salesforce": st.column_config.LinkColumn("Salesforce", display_text="Open ↗"),
        "Age (days)": st.column_config.NumberColumn(format="%d"),
    },
)
selected = [filtered[i] for i in event.selection["rows"]]

if not selected:
    st.caption(
        "Tick one or more rows to accept, dismiss or revoke them — or tick the box in the "
        "header to select every row shown."
    )
    st.stop()

# --- Action panel -------------------------------------------------------------------------

st.divider()
count = len(selected)
noun = "finding" if count == 1 else "findings"
st.subheader(f"{count:,} {noun} selected")

# Reason/expiry widgets are keyed by the selection, so text typed for one
# selection can never carry over onto a different one.
selection_sig = hashlib.sha1(
    "|".join(sorted(f["FINDING_ID"] for f in selected)).encode()
).hexdigest()[:8]


def _key(name: str) -> str:
    return f"{name}_{epoch}_{selection_sig}"


if any(f["STATUS"] == "RESOLVED" for f in selected):
    st.info(
        "Resolved findings are closed — there's nothing left to accept or dismiss. "
        "Untick them to take action on the rest."
    )
else:
    all_disposed = all(f["STATUS"] in ("ACCEPTED_EXCEPTION", "FALSE_POSITIVE") for f in selected)
    tab_labels = ["Accept as exception", "Mark false positive"]
    if all_disposed:
        tab_labels.append("Revoke")
    tabs = st.tabs(tab_labels)

    with tabs[0]:
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
            key=_key("accept_until"),
        )
        accept_reason = st.text_area(
            "Reason (required)",
            key=_key("accept_reason"),
            placeholder="Why is this acceptable for now?",
        )
        st.caption(
            f"Accept {count:,} {noun} as {'an exception' if count == 1 else 'exceptions'} until "
            f"{ui.format_nz(datetime.combine(expiry_date, time(12), tzinfo=ui.NZ_TZ))}? "
            f"{'It reopens by itself' if count == 1 else 'They reopen by themselves'} at the "
            "next run after that date if still failing."
        )
        if st.button(f"Accept {count:,} {noun}", type="primary", key=_key("accept_btn")):
            try:
                finding_ops.accept_exception(
                    store,
                    [f["FINDING_ID"] for f in selected],
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
                    f"{'an exception' if count == 1 else 'exceptions'} until "
                    f"{ui.format_nz(datetime.combine(expiry_date, time(12), tzinfo=ui.NZ_TZ))}."
                )

    with tabs[1]:
        fp_reason = st.text_area(
            "Reason (required)",
            key=_key("fp_reason"),
            placeholder="Why is the rule wrong about this record?",
        )
        st.caption(
            f"Mark {count:,} {noun} as false positive? "
            f"{'It stays' if count == 1 else 'They stay'} hidden for good, unless you revoke "
            "this later."
        )
        if st.button(f"Mark {count:,} as false positive", type="primary", key=_key("fp_btn")):
            try:
                finding_ops.mark_false_positive(
                    store,
                    [f["FINDING_ID"] for f in selected],
                    fp_reason,
                    owner=owner,
                    facilitated_by=facilitated_by,
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

    if all_disposed:
        with tabs[2]:
            revoke_reason = st.text_area(
                "Reason (required)",
                key=_key("revoke_reason"),
                placeholder="Why is this no longer an exception / false positive?",
            )
            st.caption(
                f"Revoke the disposition on {count:,} {noun}? "
                f"{'It goes' if count == 1 else 'They go'} back to Open."
            )
            if st.button(f"Revoke {count:,} {noun}", type="primary", key=_key("revoke_btn")):
                try:
                    finding_ops.revoke_disposition(
                        store,
                        [f["FINDING_ID"] for f in selected],
                        revoke_reason,
                        owner=owner,
                        facilitated_by=facilitated_by,
                    )
                except finding_ops.FindingOpsError as exc:
                    st.error(str(exc))
                else:
                    _finish_action(f"Revoked {count:,} {noun} — back to Open.")

# --- Detail -------------------------------------------------------------------------------

if count > 1:
    per_rule_counts: dict[str, int] = {}
    for f in selected:
        per_rule_counts[f["RULE_CODE"]] = per_rule_counts.get(f["RULE_CODE"], 0) + 1
    st.caption("Selection by rule: " + ", ".join(f"{c} ({n})" for c, n in per_rule_counts.items()))
    st.stop()

finding = selected[0]
st.subheader("Finding detail")
detail_col1, detail_col2, detail_col3 = st.columns(3)
detail_col1.markdown(f"**Record**  \n{finding['RECORD_NAME'] or finding['RECORD_ID']}")
detail_col2.markdown(f"**Rule**  \n{finding['RULE_CODE']} — {finding['RULE_NAME']}")
detail_col3.markdown(f"**Status**  \n{ui.finding_status_label(finding)}")
detail_col1.markdown(f"**First seen**  \n{ui.format_nz(finding['FIRST_SEEN_AT'])}")
detail_col2.markdown(f"**Last seen**  \n{ui.format_nz(finding['LAST_SEEN_AT'])}")
detail_col3.markdown(
    f"**Seen {finding['TIMES_SEEN']} time(s), reopened {finding['REOPENED_COUNT']} time(s)**"
)
if finding["RECORD_URL"]:
    st.link_button("Open in Salesforce ↗", finding["RECORD_URL"])

if finding["DETAIL"]:
    st.markdown("**Why it failed**")
    try:
        st.json(json.loads(finding["DETAIL"]))
    except ValueError:
        st.code(finding["DETAIL"])

history = store.list_dispositions(finding["FINDING_ID"])
if history:
    st.markdown("**Disposition history**")
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
