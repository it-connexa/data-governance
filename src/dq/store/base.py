"""The GovernanceStore protocol (brief §12).

Nothing outside dq.store may import duckdb. Business logic (lifecycle,
scoring, the engine) talks to a GovernanceStore, never to DuckDB directly,
so the store can be swapped for a SnowflakeStore if the PoC succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class FindingInput:
    """One evaluated row for a rule, ready to run through the lifecycle."""

    record_id: str
    item_key: str | None
    record_name: str | None
    site_id: str | None
    site_name: str | None
    is_valid: bool
    detail: str | None  # JSON text (already TO_JSON'd upstream, brief §9)


@dataclass(frozen=True)
class ApplyFindingsResult:
    """Counts of each lifecycle action taken by one apply_findings() call.

    `row_statuses` gives each input row's resulting FINDING STATUS, in the
    same order as the `rows` passed in -- for a failing row this is one of
    OPEN / ACCEPTED_EXCEPTION / FALSE_POSITIVE, telling the caller whether
    it should count as an *open* exception for scoring (brief §10.3).
    """

    inserted: int = 0
    updated: int = 0
    reopened: int = 0
    disposition_applied: int = 0
    reactivated: int = 0
    resolved_fixed: int = 0
    resolved_out_of_scope: int = 0
    no_change: int = 0
    row_statuses: list[str | None] = field(default_factory=list)


class GovernanceStore(Protocol):
    def get_active_rules(self) -> list[dict[str, Any]]: ...

    def get_retired_rule_ids(self) -> list[int]: ...

    def get_severity_weights(self) -> dict[str, int]: ...

    def get_config_value(self, key: str) -> str | None: ...

    def start_run(
        self, run_id: str, run_type: str, triggered_by: str, started_at: datetime
    ) -> None: ...

    def record_source_freshness(
        self, run_id: str, source_name: str, last_synced_at: datetime | None
    ) -> None: ...

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime,
        rules_attempted: int,
        rules_succeeded: int,
        rules_errored: int,
        engine_version: str | None = None,
        snowflake_query_tag: str | None = None,
        error_message: str | None = None,
    ) -> None: ...

    def get_active_disposition(
        self, finding_id: str, *, as_of: datetime | None = None
    ) -> dict[str, Any] | None: ...

    def apply_findings(
        self,
        *,
        run_id: str,
        run_at: datetime,
        rule_id: int,
        rule_code: str,
        rule_version: int,
        source_object: str,
        sf_object: str,
        sf_base_url: str,
        rows: list[FindingInput],
    ) -> ApplyFindingsResult: ...

    def close_retired_rule_findings(
        self, run_id: str, run_at: datetime, rule_ids: list[int]
    ) -> int: ...

    def write_rule_result(
        self, run_id: str, rule_id: int, rule_code: str, rule_version: int, result: dict[str, Any]
    ) -> None: ...

    def write_object_result(self, run_id: str, result: dict[str, Any]) -> None: ...

    def write_site_scores(
        self, run_id: str, scored_at: datetime, scores: list[dict[str, Any]]
    ) -> None: ...

    def add_history(
        self,
        *,
        rule_id: int,
        rule_code: str,
        version: int,
        change_type: str,
        status_after: str,
        snapshot: dict[str, Any],
        changed_by: str,
        facilitated_by: str | None = None,
        comment: str | None = None,
    ) -> None: ...

    # --- catalogue (brief §7: dq catalogue refresh/show/load-labels) ---------

    def upsert_source_columns(
        self, source_name: str, columns: list[dict[str, Any]], refreshed_at: datetime
    ) -> None: ...

    def update_column_labels(self, labels: list[tuple[str, str, str]]) -> int: ...

    def get_source_columns(
        self, source_name: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]: ...

    # --- rule registry (brief §7: dq rule list/show/set-sql/clarify) ---------

    def list_rules(self, status: str | None = None) -> list[dict[str, Any]]: ...

    def get_rule(self, rule_code: str) -> dict[str, Any] | None: ...

    def compile_rule(
        self,
        rule_code: str,
        *,
        check_sql: str,
        validation_result: dict[str, Any],
        changed_by: str,
        generated_at: datetime,
        generated_by: str = "claude-code",
    ) -> dict[str, Any]: ...

    def record_validation(
        self, rule_code: str, validation_result: dict[str, Any], *, changed_by: str
    ) -> dict[str, Any]: ...

    def set_rule_clarification(
        self, rule_code: str, question: str, *, changed_by: str
    ) -> dict[str, Any]: ...
