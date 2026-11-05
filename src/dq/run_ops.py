"""The in-app "Run checks now" (brief §10.6 page 5).

The same engine run as `dq run --manual`, wrapped for the Streamlit app:
refuses to start while another run is in progress, and turns a Snowflake
connection failure into a clean error before any run row is written. The
app supplies the real executor and completer; tests supply fakes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from dq import engine
from dq.engine import EngineConfig, RuleExecutor, RunSummary
from dq.narratives import NarrativeCompleter
from dq.sources import SourcesConfig
from dq.store.base import GovernanceStore

# A RUNNING row older than this is treated as a crashed run, not a live one.
RUN_IN_PROGRESS_WINDOW = timedelta(minutes=30)


class RunOpsError(Exception):
    """A run couldn't be started: another is in progress, or Snowflake
    couldn't be reached."""


def run_checks_now(
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    config: EngineConfig,
    *,
    triggered_by: str,
    narrator: NarrativeCompleter | None = None,
    now: datetime | None = None,
) -> RunSummary:
    now = now or datetime.now(UTC)
    latest = store.list_runs(1)
    if (
        latest
        and latest[0]["STATUS"] == "RUNNING"
        and now - latest[0]["STARTED_AT"] < RUN_IN_PROGRESS_WINDOW
    ):
        raise RunOpsError(
            f"A run is already in progress (started {latest[0]['STARTED_AT']:%H:%M}) -- "
            "wait for it to finish."
        )

    run_id = str(uuid.uuid4())
    try:
        freshness = engine.collect_source_freshness(
            executor, sources_config, query_tag=f"dq_poc:run:{run_id}"
        )
    except Exception as exc:
        raise RunOpsError(f"Couldn't read source freshness from Snowflake: {exc}") from exc

    return engine.run(
        store=store,
        executor=executor,
        sources_config=sources_config,
        config=config,
        run_id=run_id,
        run_type="MANUAL",
        triggered_by=triggered_by,
        started_at=now,
        source_freshness=freshness,
        narrator=narrator,
    )
