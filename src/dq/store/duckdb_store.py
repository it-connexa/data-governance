"""DuckDB implementation of GovernanceStore (brief §12).

The only module outside tests allowed to `import duckdb`. Opens a
short-lived connection per operation (brief §5) rather than holding one
across user interaction.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from dq.lifecycle import (
    Action,
    DispositionType,
    FindingStatus,
    LifecycleDecision,
    Verdict,
    build_record_url,
    compute_finding_id,
    decide,
    decide_retired,
)
from dq.store.base import ApplyFindingsResult, FindingInput


def _rows_to_dicts(cursor: duckdb.DuckDBPyConnection, rows: list[tuple]) -> list[dict[str, Any]]:
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]


class DuckDBStore:
    def __init__(self, path: Path | str):
        self._path = Path(path)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self._path))

    # --- rules and runs ------------------------------------------------------

    def get_active_rules(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            cur = con.execute("SELECT * FROM DQ_RULE WHERE STATUS = 'ACTIVE' ORDER BY RULE_CODE")
            return _rows_to_dicts(cur, cur.fetchall())

    def get_retired_rule_ids(self) -> list[int]:
        with self._connect() as con:
            rows = con.execute("SELECT RULE_ID FROM DQ_RULE WHERE STATUS = 'RETIRED'").fetchall()
            return [row[0] for row in rows]

    def get_severity_weights(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute("SELECT SEVERITY, WEIGHT FROM DQ_SEVERITY").fetchall()
            return dict(rows)

    def start_run(
        self, run_id: str, run_type: str, triggered_by: str, started_at: datetime
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO DQ_RUN (RUN_ID, RUN_TYPE, TRIGGERED_BY, STARTED_AT, STATUS)
                VALUES (?, ?, ?, ?, 'RUNNING')
                """,
                [run_id, run_type, triggered_by, started_at],
            )

    def record_source_freshness(
        self, run_id: str, source_name: str, last_synced_at: datetime | None
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO DQ_RUN_SOURCE_FRESHNESS (RUN_ID, SOURCE_NAME, LAST_SYNCED_AT) "
                "VALUES (?, ?, ?)",
                [run_id, source_name, last_synced_at],
            )

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
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RUN
                SET FINISHED_AT = ?, STATUS = ?, RULES_ATTEMPTED = ?,
                    RULES_SUCCEEDED = ?, RULES_ERRORED = ?, ENGINE_VERSION = ?,
                    SNOWFLAKE_QUERY_TAG = ?, ERROR_MESSAGE = ?
                WHERE RUN_ID = ?
                """,
                [
                    finished_at,
                    status,
                    rules_attempted,
                    rules_succeeded,
                    rules_errored,
                    engine_version,
                    snowflake_query_tag,
                    error_message,
                    run_id,
                ],
            )

    # --- findings and dispositions --------------------------------------------

    def get_active_disposition(
        self, finding_id: str, *, as_of: datetime | None = None
    ) -> dict[str, Any] | None:
        with self._connect() as con:
            return self._get_active_disposition(con, finding_id, as_of or datetime.now(UTC))

    @staticmethod
    def _get_active_disposition(
        con: duckdb.DuckDBPyConnection, finding_id: str, as_of: datetime
    ) -> dict[str, Any] | None:
        cur = con.execute(
            """
            SELECT * FROM DQ_FINDING_DISPOSITION
            WHERE FINDING_ID = ? AND DISPOSITION <> 'REVOKED'
              AND (EXPIRES_AT IS NULL OR EXPIRES_AT > ?)
            ORDER BY SET_AT DESC
            LIMIT 1
            """,
            [finding_id, as_of],
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _rows_to_dicts(cur, [row])[0]

    @staticmethod
    def _get_finding_status(
        con: duckdb.DuckDBPyConnection, finding_id: str
    ) -> FindingStatus | None:
        row = con.execute(
            "SELECT STATUS FROM DQ_FINDING WHERE FINDING_ID = ?", [finding_id]
        ).fetchone()
        return FindingStatus(row[0]) if row else None

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
    ) -> ApplyFindingsResult:
        counts: dict[Action, int] = {}
        row_statuses: list[str | None] = []
        with self._connect() as con:
            evaluated_finding_ids: list[str] = []

            for row in rows:
                finding_id = compute_finding_id(rule_code, row.record_id, row.item_key)
                evaluated_finding_ids.append(finding_id)

                existing_status = self._get_finding_status(con, finding_id)
                active_disposition_row = self._get_active_disposition(con, finding_id, run_at)
                active_disposition = (
                    DispositionType(active_disposition_row["DISPOSITION"])
                    if active_disposition_row
                    else None
                )
                verdict = Verdict.PASSED if row.is_valid else Verdict.FAILED
                decision = decide(existing_status, active_disposition, verdict)
                counts[decision.action] = counts.get(decision.action, 0) + 1
                final_status = decision.new_status or existing_status
                row_statuses.append(final_status.value if final_status else None)

                self._apply_decision(
                    con,
                    decision,
                    finding_id=finding_id,
                    run_id=run_id,
                    run_at=run_at,
                    rule_id=rule_id,
                    rule_code=rule_code,
                    rule_version=rule_version,
                    source_object=source_object,
                    sf_object=sf_object,
                    sf_base_url=sf_base_url,
                    row=row,
                )

            self._resolve_out_of_scope(con, rule_id, evaluated_finding_ids, counts, run_id, run_at)

        return ApplyFindingsResult(
            inserted=counts.get(Action.INSERT_OPEN, 0),
            updated=counts.get(Action.UPDATE_LAST_SEEN, 0),
            reopened=counts.get(Action.REOPEN, 0),
            disposition_applied=counts.get(Action.APPLY_DISPOSITION, 0),
            reactivated=counts.get(Action.REACTIVATE, 0),
            resolved_fixed=counts.get(Action.RESOLVE_FIXED, 0),
            resolved_out_of_scope=counts.get(Action.RESOLVE_OUT_OF_SCOPE, 0),
            no_change=counts.get(Action.NO_CHANGE, 0),
            row_statuses=row_statuses,
        )

    def _apply_decision(
        self,
        con: duckdb.DuckDBPyConnection,
        decision: LifecycleDecision,
        *,
        finding_id: str,
        run_id: str,
        run_at: datetime,
        rule_id: int,
        rule_code: str,
        rule_version: int,
        source_object: str,
        sf_object: str,
        sf_base_url: str,
        row: FindingInput,
    ) -> None:
        if decision.action == Action.NO_CHANGE:
            return

        if decision.action == Action.INSERT_OPEN:
            record_url = build_record_url(sf_base_url, sf_object, row.record_id)
            con.execute(
                """
                INSERT INTO DQ_FINDING (
                    FINDING_ID, RULE_ID, RULE_CODE, LAST_RULE_VERSION, SOURCE_OBJECT,
                    RECORD_ID, ITEM_KEY, RECORD_NAME, SITE_ID, SITE_NAME, RECORD_URL, DETAIL,
                    STATUS, FIRST_SEEN_RUN_ID, FIRST_SEEN_AT, LAST_SEEN_RUN_ID, LAST_SEEN_AT,
                    TIMES_SEEN, REOPENED_COUNT
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, 1, 0)
                """,
                [
                    finding_id,
                    rule_id,
                    rule_code,
                    rule_version,
                    source_object,
                    row.record_id,
                    row.item_key,
                    row.record_name,
                    row.site_id,
                    row.site_name,
                    record_url,
                    row.detail,
                    run_id,
                    run_at,
                    run_id,
                    run_at,
                ],
            )
            return

        # Every remaining action updates an existing DQ_FINDING row.
        set_clauses = [
            "LAST_RULE_VERSION = ?",
            "LAST_SEEN_RUN_ID = ?",
            "LAST_SEEN_AT = ?",
            "DETAIL = ?",
        ]
        params: list[Any] = [rule_version, run_id, run_at, row.detail]

        if decision.new_status is not None:
            set_clauses.append("STATUS = ?")
            params.append(decision.new_status.value)

        if decision.increment_times_seen:
            set_clauses.append("TIMES_SEEN = TIMES_SEEN + 1")

        if decision.increment_reopened_count:
            set_clauses.append("REOPENED_COUNT = REOPENED_COUNT + 1")

        if decision.clear_resolved_fields:
            set_clauses += [
                "RESOLVED_RUN_ID = NULL",
                "RESOLVED_AT = NULL",
                "RESOLUTION_REASON = NULL",
            ]

        if decision.action == Action.RESOLVE_FIXED:
            set_clauses += ["RESOLUTION_REASON = ?", "RESOLVED_RUN_ID = ?", "RESOLVED_AT = ?"]
            params += [decision.resolution_reason.value, run_id, run_at]

        params.append(finding_id)
        con.execute(f"UPDATE DQ_FINDING SET {', '.join(set_clauses)} WHERE FINDING_ID = ?", params)

    def _resolve_out_of_scope(
        self,
        con: duckdb.DuckDBPyConnection,
        rule_id: int,
        evaluated_finding_ids: list[str],
        counts: dict[Action, int],
        run_id: str,
        run_at: datetime,
    ) -> None:
        if evaluated_finding_ids:
            placeholders = ", ".join("?" * len(evaluated_finding_ids))
            cur = con.execute(
                f"""
                SELECT FINDING_ID, STATUS FROM DQ_FINDING
                WHERE RULE_ID = ? AND STATUS <> 'RESOLVED' AND FINDING_ID NOT IN ({placeholders})
                """,
                [rule_id, *evaluated_finding_ids],
            )
        else:
            # The rule returned no rows at all this run: every one of its
            # non-resolved findings is now out of scope.
            cur = con.execute(
                "SELECT FINDING_ID, STATUS FROM DQ_FINDING "
                "WHERE RULE_ID = ? AND STATUS <> 'RESOLVED'",
                [rule_id],
            )
        stale = cur.fetchall()

        for finding_id, status in stale:
            decision = decide(FindingStatus(status), None, Verdict.NOT_EVALUATED)
            counts[decision.action] = counts.get(decision.action, 0) + 1
            if decision.action == Action.RESOLVE_OUT_OF_SCOPE:
                con.execute(
                    """
                    UPDATE DQ_FINDING
                    SET STATUS = 'RESOLVED', RESOLUTION_REASON = 'OUT_OF_SCOPE',
                        RESOLVED_RUN_ID = ?, RESOLVED_AT = ?
                    WHERE FINDING_ID = ?
                    """,
                    [run_id, run_at, finding_id],
                )

    def close_retired_rule_findings(
        self, run_id: str, run_at: datetime, rule_ids: list[int]
    ) -> int:
        if not rule_ids:
            return 0
        closed = 0
        with self._connect() as con:
            placeholders = ", ".join("?" * len(rule_ids))
            cur = con.execute(
                f"""
                SELECT FINDING_ID, STATUS FROM DQ_FINDING
                WHERE RULE_ID IN ({placeholders}) AND STATUS <> 'RESOLVED'
                """,
                rule_ids,
            )
            for finding_id, status in cur.fetchall():
                decision = decide_retired(FindingStatus(status))
                if decision.action == Action.CLOSE_RULE_RETIRED:
                    con.execute(
                        """
                        UPDATE DQ_FINDING
                        SET STATUS = 'RESOLVED', RESOLUTION_REASON = 'RULE_RETIRED',
                            RESOLVED_RUN_ID = ?, RESOLVED_AT = ?
                        WHERE FINDING_ID = ?
                        """,
                        [run_id, run_at, finding_id],
                    )
                    closed += 1
        return closed

    # --- results, scores and history -------------------------------------------

    def write_rule_result(
        self, run_id: str, rule_id: int, rule_code: str, rule_version: int, result: dict[str, Any]
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO DQ_RULE_RESULT (
                    RUN_ID, RULE_ID, RULE_CODE, RULE_VERSION, STATUS, OBJECT_TOTAL,
                    RECORDS_EVALUATED, RECORDS_FAILED, RECORDS_FAILED_OPEN, PASS_RATE,
                    ITEMS_EVALUATED, ITEMS_FAILED, ITEMS_FAILED_OPEN, ITEM_PASS_RATE,
                    DURATION_MS, SNOWFLAKE_QUERY_ID, ERROR_MESSAGE
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    rule_id,
                    rule_code,
                    rule_version,
                    result.get("status", "SUCCEEDED"),
                    result.get("object_total"),
                    result.get("records_evaluated"),
                    result.get("records_failed"),
                    result.get("records_failed_open"),
                    result.get("pass_rate"),
                    result.get("items_evaluated"),
                    result.get("items_failed"),
                    result.get("items_failed_open"),
                    result.get("item_pass_rate"),
                    result.get("duration_ms"),
                    result.get("snowflake_query_id"),
                    result.get("error_message"),
                ],
            )

    def write_object_result(self, run_id: str, result: dict[str, Any]) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO DQ_OBJECT_RESULT (
                    RUN_ID, TARGET_OBJECT, RECORD_LABEL, OBJECT_TOTAL, ACTIVE_RULES,
                    RULES_SUCCEEDED, RECORDS_EVALUATED, RECORDS_WITH_EXCEPTION,
                    OPEN_FINDINGS, EXCEPTION_RATE
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    result["target_object"],
                    result["record_label"],
                    result.get("object_total"),
                    result["active_rules"],
                    result["rules_succeeded"],
                    result["records_evaluated"],
                    result["records_with_exception"],
                    result["open_findings"],
                    result.get("exception_rate"),
                ],
            )

    def write_site_scores(
        self, run_id: str, scored_at: datetime, scores: list[dict[str, Any]]
    ) -> None:
        with self._connect() as con:
            for score in scores:
                con.execute(
                    """
                    INSERT INTO DQ_SITE_SCORE (
                        RUN_ID, SITE_ID, SITE_NAME, RULES_APPLICABLE, RULES_FAILED,
                        OPEN_FINDINGS, CRITICAL_OPEN, WEIGHTED_SCORE, SCORED_AT
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id,
                        score["site_id"],
                        score.get("site_name"),
                        score["rules_applicable"],
                        score["rules_failed"],
                        score["open_findings"],
                        score["critical_open"],
                        score.get("weighted_score"),
                        scored_at,
                    ],
                )

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
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO DQ_RULE_HISTORY (
                    RULE_ID, RULE_CODE, VERSION, CHANGE_TYPE, STATUS_AFTER, SNAPSHOT,
                    CHANGED_BY, FACILITATED_BY, COMMENT
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rule_id,
                    rule_code,
                    version,
                    change_type,
                    status_after,
                    json.dumps(snapshot),
                    changed_by,
                    facilitated_by,
                    comment,
                ],
            )
