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

    def get_config_value(self, key: str) -> str | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT CONFIG_VALUE FROM DQ_CONFIG WHERE CONFIG_KEY = ?", [key]
            ).fetchone()
            return row[0] if row else None

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
                    json.dumps(snapshot, default=str),
                    changed_by,
                    facilitated_by,
                    comment,
                ],
            )

    # --- catalogue (brief §7: dq catalogue refresh/show/load-labels) ---------

    def upsert_source_columns(
        self, source_name: str, columns: list[dict[str, Any]], refreshed_at: datetime
    ) -> None:
        with self._connect() as con:
            incoming_names = [c["column_name"] for c in columns]
            if incoming_names:
                placeholders = ", ".join("?" * len(incoming_names))
                con.execute(
                    f"DELETE FROM SOURCE_COLUMN WHERE SOURCE_NAME = ? "
                    f"AND COLUMN_NAME NOT IN ({placeholders})",
                    [source_name, *incoming_names],
                )
            else:
                con.execute("DELETE FROM SOURCE_COLUMN WHERE SOURCE_NAME = ?", [source_name])

            for c in columns:
                con.execute(
                    """
                    MERGE INTO SOURCE_COLUMN AS tgt
                    USING (
                        SELECT ? AS SOURCE_NAME, ? AS COLUMN_NAME, ? AS DATA_TYPE,
                               ? AS IS_NULLABLE, ? AS ORDINAL_POSITION,
                               ? AS COLUMN_COMMENT, ? AS REFRESHED_AT
                    ) AS src
                    ON tgt.SOURCE_NAME = src.SOURCE_NAME AND tgt.COLUMN_NAME = src.COLUMN_NAME
                    WHEN MATCHED THEN UPDATE SET
                        DATA_TYPE = src.DATA_TYPE, IS_NULLABLE = src.IS_NULLABLE,
                        ORDINAL_POSITION = src.ORDINAL_POSITION,
                        COLUMN_COMMENT = src.COLUMN_COMMENT, REFRESHED_AT = src.REFRESHED_AT
                    WHEN NOT MATCHED THEN INSERT (
                        SOURCE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE,
                        ORDINAL_POSITION, COLUMN_COMMENT, REFRESHED_AT
                    )
                    VALUES (
                        src.SOURCE_NAME, src.COLUMN_NAME, src.DATA_TYPE, src.IS_NULLABLE,
                        src.ORDINAL_POSITION, src.COLUMN_COMMENT, src.REFRESHED_AT
                    )
                    """,
                    [
                        source_name,
                        c["column_name"],
                        c["data_type"],
                        c.get("is_nullable"),
                        c.get("ordinal_position"),
                        c.get("column_comment"),
                        refreshed_at,
                    ],
                )

    def update_column_labels(self, labels: list[tuple[str, str, str]]) -> int:
        with self._connect() as con:
            for source_name, column_name, label in labels:
                con.execute(
                    "UPDATE SOURCE_COLUMN SET COLUMN_LABEL = ? "
                    "WHERE SOURCE_NAME = ? AND COLUMN_NAME = ?",
                    [label, source_name, column_name],
                )
        return len(labels)

    def get_source_columns(
        self, source_name: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM SOURCE_COLUMN WHERE 1=1"
        params: list[Any] = []
        if source_name:
            query += " AND SOURCE_NAME = ?"
            params.append(source_name)
        if search:
            query += " AND (COLUMN_LABEL ILIKE ? OR COLUMN_NAME ILIKE ?)"
            params += [f"%{search}%", f"%{search}%"]
        query += " ORDER BY SOURCE_NAME, ORDINAL_POSITION"

        with self._connect() as con:
            cur = con.execute(query, params)
            return _rows_to_dicts(cur, cur.fetchall())

    # --- rule registry (brief §7: dq rule list/show/set-sql/clarify) ---------

    def list_rules(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM DQ_RULE"
        params: list[Any] = []
        if status:
            query += " WHERE STATUS = ?"
            params.append(status)
        query += " ORDER BY RULE_CODE"

        with self._connect() as con:
            cur = con.execute(query, params)
            return _rows_to_dicts(cur, cur.fetchall())

    def get_rule(self, rule_code: str) -> dict[str, Any] | None:
        with self._connect() as con:
            cur = con.execute("SELECT * FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code])
            row = cur.fetchone()
            if row is None:
                return None
            return _rows_to_dicts(cur, [row])[0]

    def compile_rule(
        self,
        rule_code: str,
        *,
        check_sql: str,
        validation_result: dict[str, Any],
        changed_by: str,
        generated_at: datetime,
        generated_by: str = "claude-code",
    ) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET CHECK_SQL = ?, CHECK_SQL_GENERATED_BY = ?, CHECK_SQL_GENERATED_AT = ?,
                    VALIDATION_RESULT = ?, STATUS = 'COMPILED', VERSION = VERSION + 1,
                    OWNER_CONFIRMED_BY = NULL, OWNER_CONFIRMED_AT = NULL,
                    SQL_APPROVED_BY = NULL, SQL_APPROVED_AT = NULL,
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [
                    check_sql,
                    generated_by,
                    generated_at,
                    json.dumps(validation_result, default=str),
                    changed_by,
                    rule_code,
                ],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="SQL_COMPILED",
            status_after="COMPILED",
            snapshot=updated,
            changed_by=changed_by,
        )
        return updated

    def record_validation(
        self, rule_code: str, validation_result: dict[str, Any], *, changed_by: str
    ) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET VALIDATION_RESULT = ?, UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [json.dumps(validation_result, default=str), changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="VALIDATED",
            status_after=updated["STATUS"],
            snapshot=updated,
            changed_by=changed_by,
        )
        return updated

    def set_rule_clarification(
        self, rule_code: str, question: str, *, changed_by: str
    ) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET STATUS = 'NEEDS_CLARIFICATION', CLARIFICATION_QUESTION = ?,
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [question, changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="CLARIFICATION",
            status_after="NEEDS_CLARIFICATION",
            snapshot=updated,
            changed_by=changed_by,
        )
        return updated

    def confirm_rule(self, rule_code: str, *, confirmed_by: str) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET STATUS = 'OWNER_CONFIRMED', OWNER_CONFIRMED_BY = ?,
                    OWNER_CONFIRMED_AT = current_timestamp,
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [confirmed_by, confirmed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="OWNER_CONFIRMED",
            status_after="OWNER_CONFIRMED",
            snapshot=updated,
            changed_by=confirmed_by,
        )
        return updated

    def approve_rule(self, rule_code: str, *, approved_by: str) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET STATUS = 'ACTIVE', SQL_APPROVED_BY = ?, SQL_APPROVED_AT = current_timestamp,
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [approved_by, approved_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="APPROVED",
            status_after="ACTIVE",
            snapshot=updated,
            changed_by=approved_by,
        )
        return updated

    def retire_rule(
        self, rule_code: str, reason: str, *, changed_by: str, facilitated_by: str | None = None
    ) -> dict[str, Any]:
        """Any compiled-or-later status -> RETIRED. Status/approver checks
        live in rule_ops.retire_rule, matching confirm_rule/approve_rule."""
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET STATUS = 'RETIRED', RETIRED_REASON = ?,
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [reason, changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="RETIRED",
            status_after="RETIRED",
            snapshot=updated,
            changed_by=changed_by,
            facilitated_by=facilitated_by,
            comment=reason,
        )
        return updated

    def has_rule_run_history(self, rule_code: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM DQ_RULE_RESULT WHERE RULE_CODE = ? LIMIT 1", [rule_code]
            ).fetchone()
            return row is not None

    def delete_rule(self, rule_code: str) -> None:
        """Hard-delete -- for an abandoned DRAFT idea only (enforced by
        rule_ops.delete_rule), so there's nothing worth keeping an audit
        trail of. Removes its own history rows too, not just the rule."""
        with self._connect() as con:
            con.execute("DELETE FROM DQ_RULE_HISTORY WHERE RULE_CODE = ?", [rule_code])
            con.execute("DELETE FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code])

    def get_owner(self, owner_email: str) -> dict[str, Any] | None:
        with self._connect() as con:
            cur = con.execute("SELECT * FROM DQ_OWNER WHERE OWNER_EMAIL = ?", [owner_email])
            row = cur.fetchone()
            if row is None:
                return None
            return _rows_to_dicts(cur, [row])[0]

    def list_owners(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            cur = con.execute("SELECT * FROM DQ_OWNER WHERE IS_ACTIVE ORDER BY DISPLAY_NAME")
            return _rows_to_dicts(cur, cur.fetchall())

    def create_rule(
        self,
        *,
        prefix: str,
        rule_name: str,
        statement: str,
        domain: str,
        target_object: str,
        scope: str,
        dimension: str,
        severity: str,
        owner_email: str,
        record_label: str = "records",
        item_label: str | None = None,
        created_by: str,
        facilitated_by: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute(
                "SELECT RULE_CODE FROM DQ_RULE WHERE RULE_CODE LIKE ? "
                "ORDER BY RULE_CODE DESC LIMIT 1",
                [f"{prefix}-%"],
            ).fetchone()
            next_n = 1
            if row:
                try:
                    next_n = int(row[0].rsplit("-", 1)[-1]) + 1
                except ValueError:
                    next_n = 1
            rule_code = f"{prefix}-{next_n:03d}"

            con.execute(
                """
                INSERT INTO DQ_RULE (
                    RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                    RECORD_LABEL, ITEM_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL,
                    STATUS, CREATED_BY
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?)
                """,
                [
                    rule_code,
                    rule_name,
                    statement,
                    domain,
                    target_object,
                    record_label,
                    item_label,
                    scope,
                    dimension,
                    severity,
                    owner_email,
                    created_by,
                ],
            )

        created = self.get_rule(rule_code)
        if created is None:
            raise ValueError(f"Failed to create rule {rule_code}")

        self.add_history(
            rule_id=created["RULE_ID"],
            rule_code=rule_code,
            version=created["VERSION"],
            change_type="CREATED",
            status_after="DRAFT",
            snapshot=created,
            changed_by=created_by,
            facilitated_by=facilitated_by,
        )
        return created

    def update_rule(
        self,
        rule_code: str,
        *,
        fields: dict[str, Any],
        changed_by: str,
        facilitated_by: str | None = None,
    ) -> dict[str, Any]:
        set_clauses: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)

        if "RULE_STATEMENT" in fields:
            # Brief §10.6: editing the statement resets a compiled-or-later
            # rule back to DRAFT and clears everything downstream of it.
            reset_fields = {
                "STATUS": "DRAFT",
                "CHECK_SQL": None,
                "CHECK_SQL_GENERATED_BY": None,
                "CHECK_SQL_GENERATED_AT": None,
                "VALIDATION_RESULT": None,
                "CLARIFICATION_QUESTION": None,
                "CLARIFICATION_ANSWER": None,
                "OWNER_CONFIRMED_BY": None,
                "OWNER_CONFIRMED_AT": None,
                "SQL_APPROVED_BY": None,
                "SQL_APPROVED_AT": None,
            }
            for key, value in reset_fields.items():
                if key not in fields:
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
            set_clauses.append("VERSION = VERSION + 1")

        set_clauses += ["UPDATED_BY = ?", "UPDATED_AT = current_timestamp"]
        params.append(changed_by)
        params.append(rule_code)

        with self._connect() as con:
            con.execute(f"UPDATE DQ_RULE SET {', '.join(set_clauses)} WHERE RULE_CODE = ?", params)

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="STATEMENT_EDITED",
            status_after=updated["STATUS"],
            snapshot=updated,
            changed_by=changed_by,
            facilitated_by=facilitated_by,
        )
        return updated

    def answer_clarification(
        self, rule_code: str, answer: str, *, changed_by: str, facilitated_by: str | None = None
    ) -> dict[str, Any]:
        """Answering re-queues the rule for compilation directly -- it's
        the owner's signal that this is ready to try again, same as
        `submit_rule` for a fresh DRAFT."""
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET CLARIFICATION_ANSWER = ?, STATUS = 'READY_FOR_COMPILE',
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [answer, changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="CLARIFICATION",
            status_after="READY_FOR_COMPILE",
            snapshot=updated,
            changed_by=changed_by,
            facilitated_by=facilitated_by,
        )
        return updated

    def submit_rule(
        self, rule_code: str, *, changed_by: str, facilitated_by: str | None = None
    ) -> dict[str, Any]:
        """DRAFT -> READY_FOR_COMPILE: the owner is done editing and wants
        this picked up. Status is validated by the rule_ops caller, not
        here (matching confirm_rule/approve_rule)."""
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET STATUS = 'READY_FOR_COMPILE', UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="SUBMITTED",
            status_after="READY_FOR_COMPILE",
            snapshot=updated,
            changed_by=changed_by,
            facilitated_by=facilitated_by,
        )
        return updated

    def reject_confirmation(
        self, rule_code: str, comment: str, *, changed_by: str, facilitated_by: str | None = None
    ) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET STATUS = 'DRAFT', UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="OWNER_REJECTED",
            status_after="DRAFT",
            snapshot=updated,
            changed_by=changed_by,
            facilitated_by=facilitated_by,
            comment=comment,
        )
        return updated

    def record_preview(
        self,
        rule_code: str,
        *,
        evaluated: int,
        failed: int,
        changed_by: str,
        facilitated_by: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as con:
            con.execute(
                """
                UPDATE DQ_RULE
                SET PREVIEW_EVALUATED_COUNT = ?, PREVIEW_FAILED_COUNT = ?,
                    PREVIEW_AT = current_timestamp,
                    UPDATED_BY = ?, UPDATED_AT = current_timestamp
                WHERE RULE_CODE = ?
                """,
                [evaluated, failed, changed_by, rule_code],
            )

        updated = self.get_rule(rule_code)
        if updated is None:
            raise ValueError(f"Unknown rule code: {rule_code}")

        self.add_history(
            rule_id=updated["RULE_ID"],
            rule_code=rule_code,
            version=updated["VERSION"],
            change_type="PREVIEWED",
            status_after=updated["STATUS"],
            snapshot=updated,
            changed_by=changed_by,
            facilitated_by=facilitated_by,
        )
        return updated
