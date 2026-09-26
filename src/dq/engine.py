"""Run orchestration (brief §10.1).

Renders, lints and executes each active rule independently, validates the
result against the contract, applies the finding lifecycle, then scores
sites and writes object rollups. The Snowflake connection sits behind the
RuleExecutor protocol so tests substitute a FakeExecutor -- nothing here
talks to Snowflake or DuckDB directly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import pyarrow as pa

from dq import scoring
from dq.contract import validate_result
from dq.scoring import EvaluatedRow, SiteRuleRows
from dq.sources import SourcesConfig, lint, render
from dq.store.base import FindingInput, GovernanceStore

_ACTIVE_DISPOSITION_STATUSES = ("ACCEPTED_EXCEPTION", "FALSE_POSITIVE")


@dataclass(frozen=True)
class ExecutionResult:
    table: pa.Table
    query_id: str | None
    duration_ms: int


class RuleExecutor(Protocol):
    def execute_rule(
        self, rule_code: str, sql: str, *, query_tag: str, timeout_seconds: int
    ) -> ExecutionResult: ...

    def count_object(self, target_object: str, sql: str, *, query_tag: str) -> int: ...


class RuleRunError(Exception):
    """Raised internally when a rule fails to render, lint, execute or
    validate; caught per-rule so it never affects other rules (brief §10.1)."""


@dataclass(frozen=True)
class EngineConfig:
    sf_base_url: str
    rule_timeout_seconds: int = 120
    engine_version: str = "0.1.0"


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: str  # SUCCEEDED | PARTIAL | FAILED
    rules_attempted: int
    rules_succeeded: int
    rules_errored: int
    errors: dict[str, str]


def run(
    *,
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    config: EngineConfig,
    run_id: str,
    run_type: str,
    triggered_by: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    source_freshness: dict[str, datetime | None] | None = None,
) -> RunSummary:
    store.start_run(run_id, run_type, triggered_by, started_at)

    try:
        summary = _run_body(
            store=store,
            executor=executor,
            sources_config=sources_config,
            config=config,
            run_id=run_id,
            started_at=started_at,
            source_freshness=source_freshness,
        )
    except Exception as exc:
        store.finish_run(
            run_id,
            status="FAILED",
            finished_at=finished_at or started_at,
            rules_attempted=0,
            rules_succeeded=0,
            rules_errored=0,
            engine_version=config.engine_version,
            error_message=str(exc),
        )
        return RunSummary(
            run_id=run_id,
            status="FAILED",
            rules_attempted=0,
            rules_succeeded=0,
            rules_errored=0,
            errors={"__run__": str(exc)},
        )

    store.finish_run(
        run_id,
        status=summary.status,
        finished_at=finished_at or started_at,
        rules_attempted=summary.rules_attempted,
        rules_succeeded=summary.rules_succeeded,
        rules_errored=summary.rules_errored,
        engine_version=config.engine_version,
        snowflake_query_tag=f"dq_poc:run:{run_id}",
    )
    return summary


def _run_body(
    *,
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    config: EngineConfig,
    run_id: str,
    started_at: datetime,
    source_freshness: dict[str, datetime | None] | None,
) -> RunSummary:
    if source_freshness:
        for source_name, synced_at in source_freshness.items():
            store.record_source_freshness(run_id, source_name, synced_at)

    rules = store.get_active_rules()
    severity_weights = store.get_severity_weights()
    query_tag = f"dq_poc:run:{run_id}"

    target_objects = sorted({rule["TARGET_OBJECT"] for rule in rules})
    object_totals = {
        target: _count_object(executor, sources_config, target, query_tag)
        for target in target_objects
    }

    errors: dict[str, str] = {}
    rows_by_object: dict[str, list[list[EvaluatedRow]]] = defaultdict(list)
    succeeded_by_object: dict[str, int] = defaultdict(int)
    site_rule_rows: list[SiteRuleRows] = []

    for rule in rules:
        rule_code = rule["RULE_CODE"]
        target = rule["TARGET_OBJECT"]
        try:
            evaluated_rows = _run_one_rule(
                store=store,
                executor=executor,
                sources_config=sources_config,
                config=config,
                rule=rule,
                run_id=run_id,
                run_at=started_at,
                query_tag=query_tag,
                object_total=object_totals.get(target),
            )
        except RuleRunError as exc:
            errors[rule_code] = str(exc)
            store.write_rule_result(
                run_id,
                rule["RULE_ID"],
                rule_code,
                rule["VERSION"],
                {
                    "status": "ERRORED",
                    "object_total": object_totals.get(target),
                    "error_message": str(exc),
                },
            )
            continue

        rows_by_object[target].append(evaluated_rows)
        succeeded_by_object[target] += 1
        if rule["SCOPE"] == "SITE":
            site_rule_rows.append(SiteRuleRows(rule_code, rule["SEVERITY"], evaluated_rows))

    store.close_retired_rule_findings(run_id, started_at, store.get_retired_rule_ids())
    _write_object_rollups(
        store, run_id, rules, sources_config, object_totals, succeeded_by_object, rows_by_object
    )
    _write_site_scores(store, run_id, started_at, severity_weights, site_rule_rows)

    rules_attempted = len(rules)
    rules_errored = len(errors)
    rules_succeeded = rules_attempted - rules_errored
    status = "SUCCEEDED" if rules_errored == 0 else "PARTIAL"

    return RunSummary(
        run_id=run_id,
        status=status,
        rules_attempted=rules_attempted,
        rules_succeeded=rules_succeeded,
        rules_errored=rules_errored,
        errors=errors,
    )


def _count_object(
    executor: RuleExecutor, sources_config: SourcesConfig, target: str, query_tag: str
) -> int:
    sql = render(f"SELECT COUNT(*) FROM {{{{ src.{target} }}}}", sources_config)
    return executor.count_object(target, sql, query_tag=query_tag)


def _run_one_rule(
    *,
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    config: EngineConfig,
    rule: dict[str, Any],
    run_id: str,
    run_at: datetime,
    query_tag: str,
    object_total: int | None,
) -> list[EvaluatedRow]:
    rule_code = rule["RULE_CODE"]
    target = rule["TARGET_OBJECT"]
    check_sql = rule["CHECK_SQL"]
    if not check_sql:
        raise RuleRunError("CHECK_SQL is empty")

    try:
        lint(check_sql, sources_config)
        rendered_sql = render(check_sql, sources_config)
    except Exception as exc:
        raise RuleRunError(f"render/lint failed: {exc}") from exc

    try:
        exec_result = executor.execute_rule(
            rule_code,
            rendered_sql,
            query_tag=query_tag,
            timeout_seconds=config.rule_timeout_seconds,
        )
    except Exception as exc:
        raise RuleRunError(f"execution failed: {exc}") from exc

    validation = validate_result(exec_result.table, scope=rule["SCOPE"])
    if not validation.ok:
        raise RuleRunError("; ".join(validation.errors))

    finding_inputs = _table_to_finding_inputs(exec_result.table)
    sf_object = sources_config.sources[target].sf_object

    apply_result = store.apply_findings(
        run_id=run_id,
        run_at=run_at,
        rule_id=rule["RULE_ID"],
        rule_code=rule_code,
        rule_version=rule["VERSION"],
        source_object=target,
        sf_object=sf_object,
        sf_base_url=config.sf_base_url,
        rows=finding_inputs,
    )
    evaluated_rows = _build_evaluated_rows(finding_inputs, apply_result.row_statuses)
    totals = scoring.rule_totals(evaluated_rows)

    store.write_rule_result(
        run_id,
        rule["RULE_ID"],
        rule_code,
        rule["VERSION"],
        {
            "status": "SUCCEEDED",
            "object_total": object_total,
            "records_evaluated": totals.records_evaluated,
            "records_failed": totals.records_failed,
            "records_failed_open": totals.records_failed_open,
            "pass_rate": totals.pass_rate,
            "items_evaluated": totals.items_evaluated,
            "items_failed": totals.items_failed,
            "items_failed_open": totals.items_failed_open,
            "item_pass_rate": totals.item_pass_rate,
            "duration_ms": exec_result.duration_ms,
            "snowflake_query_id": exec_result.query_id,
        },
    )
    return evaluated_rows


def _table_to_finding_inputs(table: pa.Table) -> list[FindingInput]:
    record_ids = table.column("RECORD_ID").to_pylist()
    item_keys = table.column("ITEM_KEY").to_pylist()
    record_names = table.column("RECORD_NAME").to_pylist()
    site_ids = table.column("SITE_ID").to_pylist()
    site_names = table.column("SITE_NAME").to_pylist()
    is_valid = table.column("IS_VALID").to_pylist()
    details = table.column("DETAIL").to_pylist()

    return [
        FindingInput(
            record_id=record_id,
            item_key=item_key,
            record_name=record_name,
            site_id=site_id,
            site_name=site_name,
            is_valid=valid,
            detail=detail,
        )
        for record_id, item_key, record_name, site_id, site_name, valid, detail in zip(
            record_ids,
            item_keys,
            record_names,
            site_ids,
            site_names,
            is_valid,
            details,
            strict=True,
        )
    ]


def _build_evaluated_rows(
    rows: list[FindingInput], row_statuses: list[str | None]
) -> list[EvaluatedRow]:
    return [
        EvaluatedRow(
            record_id=row.record_id,
            item_key=row.item_key,
            site_id=row.site_id,
            is_valid=row.is_valid,
            has_active_disposition=status in _ACTIVE_DISPOSITION_STATUSES,
        )
        for row, status in zip(rows, row_statuses, strict=True)
    ]


def _write_object_rollups(
    store: GovernanceStore,
    run_id: str,
    rules: list[dict[str, Any]],
    sources_config: SourcesConfig,
    object_totals: dict[str, int],
    succeeded_by_object: dict[str, int],
    rows_by_object: dict[str, list[list[EvaluatedRow]]],
) -> None:
    rules_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rule in rules:
        rules_by_target[rule["TARGET_OBJECT"]].append(rule)

    for target, target_rules in rules_by_target.items():
        result = scoring.object_rollup(
            target_object=target,
            record_label=sources_config.sources[target].record_label,
            object_total=object_totals.get(target),
            active_rules=len(target_rules),
            rules_succeeded=succeeded_by_object.get(target, 0),
            rows_by_rule=rows_by_object.get(target, []),
        )
        store.write_object_result(
            run_id,
            {
                "target_object": result.target_object,
                "record_label": result.record_label,
                "object_total": result.object_total,
                "active_rules": result.active_rules,
                "rules_succeeded": result.rules_succeeded,
                "records_evaluated": result.records_evaluated,
                "records_with_exception": result.records_with_exception,
                "open_findings": result.open_findings,
                "exception_rate": result.exception_rate,
            },
        )


def _write_site_scores(
    store: GovernanceStore,
    run_id: str,
    scored_at: datetime,
    severity_weights: dict[str, int],
    site_rule_rows: list[SiteRuleRows],
) -> None:
    if not site_rule_rows:
        return
    scores = scoring.site_scores(severity_weights, site_rule_rows)
    if not scores:
        return
    store.write_site_scores(
        run_id,
        scored_at,
        [
            {
                "site_id": score.site_id,
                "rules_applicable": score.rules_applicable,
                "rules_failed": score.rules_failed,
                "open_findings": score.open_findings,
                "critical_open": score.critical_open,
                "weighted_score": score.weighted_score,
            }
            for score in scores.values()
        ],
    )
