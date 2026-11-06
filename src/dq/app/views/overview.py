"""Page 4 -- Run results (brief §10.6, page 5 "Run and results"): the latest
run at a glance, object and rule headlines with week-on-week trends, site
scores and the owner's narrative -- plus "Run checks now", so an owner can
see the whole loop (rules, run, findings, summary) in one session.
"""

from __future__ import annotations

import getpass
import json
from datetime import UTC, datetime, timedelta

import streamlit as st

from dq import narratives, run_ops
from dq.app import context, ui

settings, store, sources_config = context.load_context()
owner = ui.page_header(store, "Overview", icon="dashboard")

if owner is None:
    st.stop()

now = datetime.now(UTC)
STALE_AFTER = timedelta(hours=48)

flash = st.session_state.pop("_flash_run", None)
if flash:
    st.success(flash)


def _num(value) -> float:
    return float(value) if value is not None else 0.0


# --- Run checks now -------------------------------------------------------------------


def _render_run_controls() -> None:
    if not st.session_state.get("_confirming_run"):
        if st.button("▶ Run checks now", type="primary"):
            st.session_state["_confirming_run"] = True
            st.rerun()
        return

    st.warning(
        "This runs every active rule against Snowflake — usually a few minutes — and writes "
        "a new run and updated findings to the governance store. **Stay on this page until "
        "it finishes.**"
    )
    with_narratives = st.checkbox(
        "Also write each owner's narrative (uses Snowflake Cortex)",
        value=True,
        key="run_with_narratives",
    )
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        go = st.button("Yes, run now", type="primary")
    with cancel_col:
        if st.button("Cancel"):
            st.session_state["_confirming_run"] = False
            st.rerun()
    if not go:
        return

    try:
        with st.spinner("Running checks against Snowflake — this can take a few minutes…"):
            summary = run_ops.run_checks_now(
                store,
                context.make_executor(settings),
                sources_config,
                context.engine_config(store),
                triggered_by=getpass.getuser(),
                narrator=context.make_completer(settings) if with_narratives else None,
            )
    except run_ops.RunOpsError as exc:
        st.session_state["_confirming_run"] = False
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 -- surface any connection/credential failure to the owner
        st.session_state["_confirming_run"] = False
        st.error(f"The run couldn't complete: {exc}")
        return

    st.session_state["_confirming_run"] = False
    st.session_state["_flash_run"] = (
        f"Run finished: {summary.status.lower()} — {summary.rules_succeeded} of "
        f"{summary.rules_attempted} rules succeeded"
        + (f", {summary.rules_errored} errored." if summary.rules_errored else ".")
    )
    st.rerun()


# --- Latest run band ----------------------------------------------------------------------

runs = store.list_runs(10)
if not runs:
    st.info(
        "No runs yet. Use **Run checks now** below, or run `dq run` in a terminal — "
        "results, findings and narratives appear here afterwards."
    )
    _render_run_controls()
    st.stop()

latest_any = runs[0]
latest_result = store.get_latest_run()

band_cols = st.columns(4)
with band_cols[0]:
    st.markdown(
        f"**Latest run**  \n{ui.run_status_badge(latest_any['STATUS'])}",
        unsafe_allow_html=True,
    )
with band_cols[1]:
    st.markdown(
        f"**Started**  \n{ui.format_nz_datetime(latest_any['STARTED_AT'])}  \n"
        f"{ui.format_age(latest_any['STARTED_AT'], now=now)}"
    )
with band_cols[2]:
    st.markdown(
        f"**Took**  \n{ui.format_duration(latest_any['STARTED_AT'], latest_any['FINISHED_AT'])}  \n"
        f"{latest_any['RUN_TYPE'].title()} · {latest_any['TRIGGERED_BY']}"
    )
with band_cols[3]:
    attempted = latest_any["RULES_ATTEMPTED"]
    if attempted is None:
        st.markdown("**Rules**  \n—")
    else:
        st.markdown(
            f"**Rules**  \n{latest_any['RULES_SUCCEEDED']} of {attempted} succeeded"
            + (f"  \n{latest_any['RULES_ERRORED']} errored" if latest_any["RULES_ERRORED"] else "")
        )

if latest_any["STATUS"] == "RUNNING":
    st.info("A run is in progress. Refresh this page when it finishes.")
elif latest_any["STATUS"] == "FAILED":
    st.error(f"The latest run failed: {latest_any['ERROR_MESSAGE'] or 'no error message recorded'}")
    if latest_result:
        st.caption(
            "The figures below are from the last completed run on "
            f"{ui.format_nz_datetime(latest_result['STARTED_AT'])}."
        )

if latest_result:
    errored = store.get_run_rule_errors(latest_result["RUN_ID"])
    if errored:
        with st.expander(
            f"⚠️ {len(errored)} rule(s) errored in the latest completed run — "
            "their findings are unchanged",
            expanded=True,
        ):
            for entry in errored:
                st.markdown(f"**{entry['RULE_CODE']}** — {entry['ERROR_MESSAGE']}")

    freshness = store.get_source_freshness_latest()
    if freshness:
        parts = []
        for row in freshness:
            synced = row["LAST_SYNCED_AT"]
            stale = synced is None or now - synced > STALE_AFTER
            parts.append(
                f"{'⚠️ ' if stale else ''}**{row['SOURCE_NAME']}** synced "
                f"{ui.format_age(synced, now=now)}"
            )
        st.caption("Source data: " + " · ".join(parts))

_render_run_controls()

if latest_result is None:
    st.info("No completed run yet — results appear here once a run has finished.")
    st.stop()

st.caption(
    f"Results below are from the run on {ui.format_nz_datetime(latest_result['STARTED_AT'])}."
)
st.divider()

# --- Object headlines ---------------------------------------------------------------------

st.subheader("Object headlines")
object_rows = store.list_object_summaries_latest()
object_trend: dict[str, list[dict]] = {}
for row in store.get_object_trend(runs=12):
    object_trend.setdefault(row["TARGET_OBJECT"], []).append(row)

if not object_rows:
    st.caption("No object results in this run.")
else:
    for col, row in zip(st.columns(len(object_rows)), object_rows, strict=True):
        history = object_trend.get(row["TARGET_OBJECT"], [])
        position = next((i for i, h in enumerate(history) if h["RUN_ID"] == row["RUN_ID"]), None)
        previous = history[position - 1] if position else None
        rate = _num(row["EXCEPTION_RATE"])
        with col, st.container(border=True):
            st.markdown(f"**{row['TARGET_OBJECT'].title()}**")
            st.metric(
                "Records with an exception",
                f"{rate:.1f}%",
                delta=f"{rate - _num(previous['EXCEPTION_RATE']):+.1f} pts" if previous else None,
                delta_color="inverse",
            )
            st.caption(row["SUMMARY_TEXT"])

# --- Rule results -------------------------------------------------------------------------

st.subheader("Rule results")
toggle_cols = st.columns(2)
with toggle_cols[0]:
    scope = (
        st.segmented_control(
            "Rules", ["My rules", "All rules"], default="My rules", key="results_scope"
        )
        or "My rules"
    )
with toggle_cols[1]:
    level = (
        st.segmented_control(
            "Count by", ["Records", "Items"], default="Records", key="results_level"
        )
        or "Records"
    )

rule_rows = store.list_rule_summaries_latest(owner["OWNER_EMAIL"] if scope == "My rules" else None)
rule_trend: dict[str, list[dict]] = {}
for row in store.get_rule_trend(runs=12):
    rule_trend.setdefault(row["RULE_CODE"], []).append(row)

if not rule_rows:
    st.info("No rule results to show for this selection.")
else:
    by_items = level == "Items"
    evaluated_key = "ITEMS_EVALUATED" if by_items else "RECORDS_EVALUATED"
    failed_key = "ITEMS_FAILED_OPEN" if by_items else "RECORDS_FAILED_OPEN"
    rate_key = "ITEM_EXCEPTION_RATE" if by_items else "EXCEPTION_RATE"

    table_rows = []
    for row in rule_rows:
        history = rule_trend.get(row["RULE_CODE"], [])
        position = next((i for i, h in enumerate(history) if h["RUN_ID"] == row["RUN_ID"]), None)
        previous = history[position - 1] if position else None
        table_rows.append(
            {
                "Rule": row["RULE_CODE"],
                "Name": row["RULE_NAME"],
                "Severity": row["SEVERITY"],
                "Evaluated": row[evaluated_key],
                "Exceptions": row[failed_key],
                "Rate": _num(row[rate_key]),
                "Trend": [_num(h[rate_key]) for h in history],
                "Since last run": ui.change_label(
                    previous[failed_key] if previous else None, row[failed_key]
                ),
            }
        )

    event = st.dataframe(
        table_rows,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"results_rules_{scope}_{level}",
        column_config={
            "Evaluated": st.column_config.NumberColumn(format="%d"),
            "Exceptions": st.column_config.NumberColumn(format="%d"),
            "Rate": st.column_config.NumberColumn("Rate", format="%.1f%%"),
            "Trend": st.column_config.LineChartColumn("Trend", y_min=0),
        },
    )
    with st.expander("Headline sentences", expanded=True):
        for row in rule_rows:
            st.markdown(f"- {row['SUMMARY_TEXT']}")

    if event.selection["rows"]:
        picked = table_rows[event.selection["rows"][0]]["Rule"]
        if st.button(f"View open findings for {picked}", key=f"results_view_{picked}"):
            st.switch_page(ui.PAGE_FINDINGS, query_params={"rule": picked})
    else:
        st.caption("Tick a rule to jump to its open findings.")

# --- Site scores --------------------------------------------------------------------------

st.subheader("Site scores")
site_scores = store.list_site_scores_latest()
if not site_scores:
    st.caption("No site scores in this run (no site-level rules succeeded).")
else:
    show = (
        st.segmented_control(
            "Show", ["Worst 25", "Worst 100", "All"], default="Worst 25", key="results_sites_show"
        )
        or "Worst 25"
    )
    limit = {"Worst 25": 25, "Worst 100": 100}.get(show)
    shown = site_scores[:limit] if limit else site_scores
    st.caption(f"Showing {len(shown):,} of {len(site_scores):,} sites — lowest score first.")
    st.dataframe(
        [
            {
                "Site": s["SITE_NAME"] or s["SITE_ID"],
                "Score": _num(s["WEIGHTED_SCORE"]) if s["WEIGHTED_SCORE"] is not None else None,
                "Open findings": s["OPEN_FINDINGS"],
                "Critical open": s["CRITICAL_OPEN"],
                "Rules failed": f"{s['RULES_FAILED']} of {s['RULES_APPLICABLE']}",
            }
            for s in shown
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.0f"
            ),
        },
    )

# --- Narrative ----------------------------------------------------------------------------

st.subheader("Your narrative")
narrative = store.get_latest_narrative(owner["OWNER_EMAIL"])
if narrative is None:
    st.caption("No narrative has been written for you yet.")
elif narrative["STATUS"] == "ERRORED":
    st.warning(f"The last narrative couldn't be written: {narrative['ERROR_MESSAGE']}")
else:
    with st.container(border=True):
        st.write(narrative["NARRATIVE"])
    caption = (
        f"Written {ui.format_nz_datetime(narrative['GENERATED_AT'])} · {narrative['MODEL']} · "
        f"prompt {narrative['PROMPT_VERSION']}"
    )
    if narrative["RUN_ID"] != latest_result["RUN_ID"]:
        caption += " · **from an earlier run**"
    st.caption(caption)
    if narrative["INPUT_SUMMARY"]:
        with st.expander("What was sent to Cortex"):
            st.caption(
                "Only these aggregated figures are sent — never finding details, record names "
                "or any text from source records."
            )
            st.json(json.loads(narrative["INPUT_SUMMARY"]))

if st.button("Regenerate narrative" if narrative else "Generate narrative", key="gen_narrative"):
    with st.spinner("Asking Cortex…"):
        outcome = narratives.generate(
            store,
            context.make_completer(settings),
            owner["OWNER_EMAIL"],
            run_id=latest_result["RUN_ID"],
        )
    if outcome.status == "SUCCEEDED":
        st.rerun()
    st.error(f"Couldn't write the narrative: {outcome.error}")

# --- Run history --------------------------------------------------------------------------

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
