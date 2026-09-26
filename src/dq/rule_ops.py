"""dq validate / dq rule set-sql / dq rule clarify (brief §7, §10.5).

Ties sources.py (render/lint), contract.py (validate_result) and the store
together for the rule-authoring commands. Never returns rows -- only
counts and column metadata, per brief §6's hard rule for Claude Code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dq.contract import ValidationResult, validate_result
from dq.engine import EngineConfig, RuleExecutor
from dq.sources import LintError, SourcesConfig, lint, render
from dq.store.base import GovernanceStore


class RuleOpsError(Exception):
    """Raised for conditions the caller can't retry its way out of:
    an unknown rule code, or a missing file."""


def _validation_dict(
    validation: ValidationResult, *, duration_ms: int | None, query_id: str | None
) -> dict[str, Any]:
    return {
        "ok": validation.ok,
        "errors": validation.errors,
        "columns": [{"name": name, "type": type_} for name, type_ in validation.columns],
        "records_evaluated": validation.records_evaluated,
        "records_failed": validation.records_failed,
        "items_evaluated": validation.items_evaluated,
        "items_failed": validation.items_failed,
        "duplicate_record_items": validation.duplicate_record_items,
        "null_record_ids": validation.null_record_ids,
        "null_is_valid": validation.null_is_valid,
        "null_site_ids": validation.null_site_ids,
        "item_key_used": validation.item_key_used,
        "duration_ms": duration_ms,
        "query_id": query_id,
    }


def _lint_error_dict(exc: LintError) -> dict[str, Any]:
    return {
        "ok": False,
        "errors": exc.violations,
        "columns": [],
        "records_evaluated": None,
        "records_failed": None,
        "items_evaluated": None,
        "items_failed": None,
        "duplicate_record_items": [],
        "null_record_ids": None,
        "null_is_valid": None,
        "null_site_ids": None,
        "item_key_used": None,
        "duration_ms": None,
        "query_id": None,
    }


def validate_rule(
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    rule_code: str,
    *,
    config: EngineConfig,
    query_tag: str,
    changed_by: str = "claude-code",
) -> dict[str, Any]:
    """dq validate CODE: render, lint, execute, validate against the
    contract. Always persists the result to DQ_RULE.VALIDATION_RESULT and a
    VALIDATED history row (brief §10.5), whether it passed or not."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    check_sql = rule.get("CHECK_SQL")
    if not check_sql:
        raise RuleOpsError(f"{rule_code} has no CHECK_SQL yet -- run `dq rule set-sql` first")

    try:
        lint(check_sql, sources_config)
    except LintError as exc:
        result = _lint_error_dict(exc)
        store.record_validation(rule_code, result, changed_by=changed_by)
        return result

    rendered_sql = render(check_sql, sources_config)
    exec_result = executor.execute_rule(
        rule_code, rendered_sql, query_tag=query_tag, timeout_seconds=config.rule_timeout_seconds
    )
    validation = validate_result(exec_result.table, scope=rule["SCOPE"])
    result = _validation_dict(
        validation, duration_ms=exec_result.duration_ms, query_id=exec_result.query_id
    )
    store.record_validation(rule_code, result, changed_by=changed_by)
    return result


def set_rule_sql(
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    rule_code: str,
    sql_path: Path | str,
    *,
    config: EngineConfig,
    query_tag: str,
    changed_by: str = "claude-code",
) -> dict[str, Any]:
    """dq rule set-sql CODE --file PATH: write CHECK_SQL, run lint +
    validate, set COMPILED on success (brief §7). Nothing is written to the
    store if lint or validation fails -- the caller (the /compile-rules
    workflow) is expected to fix and retry."""
    path = Path(sql_path)
    if not path.exists():
        raise RuleOpsError(f"SQL file not found: {path}")
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")

    check_sql = path.read_text()

    try:
        lint(check_sql, sources_config)
    except LintError as exc:
        return {"ok": False, "stage": "lint", "errors": exc.violations}

    rendered_sql = render(check_sql, sources_config)
    exec_result = executor.execute_rule(
        rule_code, rendered_sql, query_tag=query_tag, timeout_seconds=config.rule_timeout_seconds
    )
    validation = validate_result(exec_result.table, scope=rule["SCOPE"])
    validation_dict = _validation_dict(
        validation, duration_ms=exec_result.duration_ms, query_id=exec_result.query_id
    )
    if not validation.ok:
        return {
            "ok": False,
            "stage": "validate",
            "errors": validation.errors,
            "validation": validation_dict,
        }

    updated = store.compile_rule(
        rule_code,
        check_sql=check_sql,
        validation_result=validation_dict,
        changed_by=changed_by,
        generated_at=datetime.now(UTC),
    )
    return {"ok": True, "rule": updated, "validation": validation_dict}


def clarify_rule(
    store: GovernanceStore, rule_code: str, question: str, *, changed_by: str = "claude-code"
) -> dict[str, Any]:
    """dq rule clarify CODE --question TEXT: set NEEDS_CLARIFICATION (brief §7)."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    return store.set_rule_clarification(rule_code, question, changed_by=changed_by)


def confirm_rule(store: GovernanceStore, rule_code: str, *, confirmed_by: str) -> dict[str, Any]:
    """COMPILED -> OWNER_CONFIRMED: the owner has looked at a preview of the
    actual failing records and agrees the rule means what they intended
    (brief §10.2, Streamlit page 2's "Yes, this is what I meant")."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    if rule["STATUS"] != "COMPILED":
        raise RuleOpsError(f"{rule_code} is {rule['STATUS']}, not COMPILED -- can't confirm")
    return store.confirm_rule(rule_code, confirmed_by=confirmed_by)


def approve_rule(store: GovernanceStore, rule_code: str, *, approved_by: str) -> dict[str, Any]:
    """OWNER_CONFIRMED -> ACTIVE: an approver has reviewed the SQL itself
    (brief §10.2, Streamlit page 3, approvers only)."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    if rule["STATUS"] != "OWNER_CONFIRMED":
        raise RuleOpsError(f"{rule_code} is {rule['STATUS']}, not OWNER_CONFIRMED -- can't approve")

    owner = store.get_owner(approved_by)
    if owner is None or not owner["IS_APPROVER"]:
        raise RuleOpsError(f"{approved_by} is not an approver")

    return store.approve_rule(rule_code, approved_by=approved_by)
