"""Overview (brief §10.6, "Run and results"): the landing page, for the week-to-week
questions. Did the last run work? What changed? Which rules need attention, and
what is waiting on me? Everything else (site scores, narratives, per-object
cards) is left out on purpose; the data is still stored.
"""

from __future__ import annotations

import getpass
from datetime import UTC, datetime, timedelta

import streamlit as st

from dq import finding_ops, run_ops
from dq.app import context, ui

settings, store, sources_config = context.load_context()
owner = ui.page_header(store, "Overview", icon="dashboard")
if owner is None:
    st.stop()

is_approver = bool(owner.get("IS_APPROVER"))
owner_email = owner["OWNER_EMAIL"]
now = datetime.now(UTC)
STALE_AFTER = timedelta(hours=48)

flash = st.session_state.pop("_flash_run", None)
if flash:
    st.toast(flash, icon=":material/check_circle:")


# --- Run checks now (dialog) -----------------------------------------------------------


@st.dialog("Run checks now")
def _run_dialog() -> None:
    st.write(
        "This runs every active rule against Snowflake, usually in a few minutes, and writes "
        "a new run and updated findings to the governance store. **Stay on this page until "
        "it finishes.**"
    )
    go_col, cancel_col = st.columns(2)
    with go_col:
        go = st.button("Run now", type="primary", icon=":material/play_arrow:", width="stretch")
    with cancel_col:
        if st.button("Cancel", width="stretch"):
            st.rerun()
    if not go:
        return

    try:
        with st.spinner("Running checks against Snowflake. This can take a few minutes…"):
            summary = run_ops.run_checks_now(
                store,
                context.make_executor(settings),
                sources_config,
                context.engine_config(store),
                triggered_by=getpass.getuser(),
            )
    except run_ops.RunOpsError as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 -- surface any connection/credential failure to the owner
        st.error(f"The run couldn't complete: {exc}")
        return

    st.session_state["_flash_run"] = (
        f"Run finished: {summary.status.lower()}. {summary.rules_succeeded} of "
        f"{summary.rules_attempted} rules succeeded"
        + (f", {summary.rules_errored} errored." if summary.rules_errored else ".")
    )
    st.rerun()


# --- Status line: did the last run work? -----------------------------------------------

runs = store.list_runs(10)
if not runs:
    st.info(
        "No runs yet. Use **Run checks now**, or run `dq run` in a terminal. Results and "
        "findings appear here afterwards."
    )
    if st.button("Run checks now", type="primary", icon=":material/play_arrow:"):
        _run_dialog()
    st.stop()

latest_any = runs[0]
latest_result = store.get_latest_run()

status_col, scope_col, run_col = st.columns([7, 2, 2], vertical_alignment="center")
with status_col:
    attempted = latest_any["RULES_ATTEMPTED"]
    rules_text = (
        f" · {latest_any['RULES_SUCCEEDED']} of {attempted} rules succeeded"
        if attempted is not None
        else ""
    )
    st.markdown(
        f"{ui.run_status_badge(latest_any['STATUS'])} **Latest run** "
        f"{ui.format_nz_datetime(latest_any['STARTED_AT'])} · "
        f"{ui.format_age(latest_any['STARTED_AT'], now=now)}{rules_text}"
    )
with scope_col:
    show_all = bool(is_approver and st.toggle("All owners", key="overview_all_owners"))
with run_col:
    if st.button("Run checks now", type="primary", icon=":material/play_arrow:", width="stretch"):
        _run_dialog()

# Only what needs attention is shown as an alert.
if latest_any["STATUS"] == "RUNNING":
    st.info("A run is in progress. Refresh this page when it finishes.")
elif latest_any["STATUS"] == "FAILED":
    st.error(f"The latest run failed: {latest_any['ERROR_MESSAGE'] or 'no error message recorded'}")
    if latest_result:
        st.caption(
            "The figures below are from the last completed run on "
            f"{ui.format_nz_datetime(latest_result['STARTED_AT'])}."
        )

if latest_result is None:
    st.info("No completed run yet. Results appear here once a run has finished.")
    st.stop()

errored = store.get_run_rule_errors(latest_result["RUN_ID"])
if errored:
    lines = "\n".join(f"- **{e['RULE_CODE']}**: {e['ERROR_MESSAGE']}" for e in errored[:5])
    more = f"\n- and {len(errored) - 5} more" if len(errored) > 5 else ""
    noun = "rule errored" if len(errored) == 1 else "rules errored"
    st.warning(
        f"**{len(errored)} {noun}** in the latest completed run, so their findings are "
        f"unchanged:\n{lines}{more}",
        icon=":material/error:",
    )

stale = [
    row
    for row in store.get_source_freshness_latest()
    if row["LAST_SYNCED_AT"] is None or now - row["LAST_SYNCED_AT"] > STALE_AFTER
]
if stale:
    names = ", ".join(
        f"**{row['SOURCE_NAME']}** ({ui.format_age(row['LAST_SYNCED_AT'], now=now)})"
        for row in stale
    )
    st.warning(f"Source data may be out of date: {names}.", icon=":material/schedule:")

# --- Data: the last run's results for the owner's rules --------------------------------

rule_rows = store.list_rule_summaries_latest(None if show_all else owner_email)
rule_trend: dict[str, list[dict]] = {}
for row in store.get_rule_trend(runs=12):
    rule_trend.setdefault(row["RULE_CODE"], []).append(row)
totals = ui.overview_totals(rule_rows, rule_trend)

findings = store.list_findings(None if show_all else owner_email)
change = finding_ops.summarise_run(findings, latest_result["RUN_ID"])
lapsing = sum(1 for f in findings if finding_ops.is_lapsing(f))

# --- The numbers ------------------------------------------------------------------------

open_delta = (
    totals["open"] - totals["previous_open"] if totals["previous_open"] is not None else None
)
metric_cols = st.columns(4)
metric_cols[0].metric(
    "Open findings",
    f"{totals['open']:,}",
    delta=f"{open_delta:+,} since the run before" if open_delta else None,
    delta_color="inverse",
    help="Findings still failing and not accepted, across your rules, as at the last run.",
)
metric_cols[1].metric("New this run", f"{change.new:,}", help="First raised in the last run.")
metric_cols[2].metric(
    "Resolved this run", f"{change.resolved:,}", help="Found fixed by the last run."
)
metric_cols[3].metric("Critical open", f"{totals['critical_open']:,}")

# --- Rules (left) and to do (right) -----------------------------------------------------

rules_col, todo_col = st.columns([7, 3], gap="medium")

with rules_col:
    st.markdown("**Rules**")
    if not rule_rows:
        st.info("No rule results to show.")
    else:
        table_rows = []
        for row in rule_rows:
            history = rule_trend.get(row["RULE_CODE"], [])
            position = next(
                (i for i, h in enumerate(history) if h["RUN_ID"] == row["RUN_ID"]), None
            )
            previous = history[position - 1] if position else None
            open_now = row["ITEMS_FAILED_OPEN"] or 0
            table_rows.append(
                {
                    "Rule": row["RULE_CODE"],
                    "Name": row["RULE_NAME"],
                    "Severity": ui.severity_cell(row["SEVERITY"]),
                    "Open": open_now,
                    "Since last run": ui.change_label(
                        (previous["ITEMS_FAILED_OPEN"] or 0) if previous else None, open_now
                    ),
                    "Trend": [h["ITEMS_FAILED_OPEN"] or 0 for h in history],
                }
            )

        event = st.dataframe(
            table_rows,
            width="stretch",
            height=min(430, 36 * (len(table_rows) + 1) + 4),
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"overview_rules_{show_all}",
            column_config={
                "Rule": st.column_config.TextColumn(width="small"),
                "Severity": st.column_config.TextColumn(width="small"),
                "Open": st.column_config.NumberColumn(format="%d", width="small"),
                "Since last run": st.column_config.TextColumn(width="small"),
                "Trend": st.column_config.LineChartColumn("Trend", y_min=0, width="small"),
            },
        )
        picked_rows = event.selection["rows"]
        if picked_rows:
            picked = table_rows[picked_rows[0]]["Rule"]
            if st.button(
                f"View open findings for {picked}",
                icon=":material/arrow_forward:",
                key=f"overview_view_{picked}",
            ):
                st.switch_page(ui.PAGE_FINDINGS, query_params={"rule": picked})
        else:
            st.caption("Tick a rule to jump to its open findings. Counts are as at the last run.")

with todo_col:
    st.markdown("**To do**")
    todo_rules = [
        r
        for r in store.list_rules()
        if r["OWNER_EMAIL"] == owner_email or (is_approver and r["STATUS"] == "OWNER_CONFIRMED")
    ]
    items = ui.todo_items(
        todo_rules,
        flagged_codes={code for code in store.list_rules_flagged_for_review()},
        is_approver=is_approver,
        lapsing=lapsing,
    )
    with st.container(border=True, key="overview_todo"):
        if not items:
            st.caption("Nothing needs you right now.")
        for item in items[:6]:
            st.page_link(
                item["page"],
                label=item["label"],
                icon=":material/arrow_forward:",
                query_params=item["params"],
            )
        if len(items) > 6:
            st.page_link(
                ui.PAGE_RULES, label=f"and {len(items) - 6} more on the Rules page", icon=None
            )

# --- Run history ----------------------------------------------------------------------------

with st.expander("Run history"):
    st.dataframe(
        [
            {
                "Started": ui.format_nz_datetime(r["STARTED_AT"]),
                "Type": r["RUN_TYPE"].title(),
                "By": r["TRIGGERED_BY"],
                "Status": r["STATUS"].title(),
                "Rules succeeded": r["RULES_SUCCEEDED"],
                "Rules errored": r["RULES_ERRORED"],
                "Took": ui.format_duration(r["STARTED_AT"], r["FINISHED_AT"]),
            }
            for r in runs
        ],
        width="stretch",
        hide_index=True,
    )
