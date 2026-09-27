"""dq validate / dq rule set-sql / dq rule clarify / rule editing / preview
(brief §7, §10.5, §10.6).

Ties sources.py (render/lint), contract.py (validate_result) and the store
together for the rule-authoring commands. The CLI-facing functions here
never return rows (brief §6's hard rule for Claude Code); `preview_rule`
does return sample rows and is for the Streamlit app only, run by the
owner themselves, never called by Claude Code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from dq.contract import ValidationResult, validate_result
from dq.engine import EngineConfig, RuleExecutor
from dq.lifecycle import build_record_url
from dq.sources import LintError, SourcesConfig, lint, render
from dq.store.base import GovernanceStore

_RULE_CODE_PREFIX_BY_OBJECT = {"SITE": "SITE", "PROJECT": "PRJ", "MILESTONE": "MS"}


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


# --- Streamlit page 1: My rules (view, edit, add) ----------------------------------


def create_rule(
    store: GovernanceStore,
    *,
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
    """A brand-new DRAFT rule, code auto-generated for the target object
    (brief §10.6 page 1: "Add rule" generates the next code)."""
    prefix = _RULE_CODE_PREFIX_BY_OBJECT.get(target_object)
    if prefix is None:
        raise RuleOpsError(
            f"No rule-code prefix configured for target object '{target_object}' "
            f"(known: {sorted(_RULE_CODE_PREFIX_BY_OBJECT)})"
        )
    return store.create_rule(
        prefix=prefix,
        rule_name=rule_name,
        statement=statement,
        domain=domain,
        target_object=target_object,
        scope=scope,
        dimension=dimension,
        severity=severity,
        owner_email=owner_email,
        record_label=record_label,
        item_label=item_label,
        created_by=created_by,
        facilitated_by=facilitated_by,
    )


def update_rule(
    store: GovernanceStore,
    rule_code: str,
    *,
    fields: dict[str, Any],
    changed_by: str,
    facilitated_by: str | None = None,
) -> dict[str, Any]:
    """Edit a rule's statement or metadata. Editing RULE_STATEMENT resets a
    compiled-or-later rule to DRAFT and clears everything downstream of it
    (brief §10.6 page 1)."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    return store.update_rule(
        rule_code, fields=fields, changed_by=changed_by, facilitated_by=facilitated_by
    )


# --- Streamlit page 2: Rule detail and preview -------------------------------------


def answer_clarification(
    store: GovernanceStore,
    rule_code: str,
    answer: str,
    *,
    changed_by: str,
    facilitated_by: str | None = None,
) -> dict[str, Any]:
    """The owner answers a clarification question (brief §10.6 page 2)."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    return store.answer_clarification(
        rule_code, answer, changed_by=changed_by, facilitated_by=facilitated_by
    )


def submit_rule(
    store: GovernanceStore,
    rule_code: str,
    *,
    changed_by: str,
    facilitated_by: str | None = None,
) -> dict[str, Any]:
    """DRAFT -> READY_FOR_COMPILE: the owner submits a finished statement
    (brief §10.6 page 1's "Submit for compilation")."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    if rule["STATUS"] != "DRAFT":
        raise RuleOpsError(f"{rule_code} is {rule['STATUS']}, not DRAFT -- can't submit")
    return store.submit_rule(rule_code, changed_by=changed_by, facilitated_by=facilitated_by)


def reject_confirmation(
    store: GovernanceStore,
    rule_code: str,
    comment: str,
    *,
    changed_by: str,
    facilitated_by: str | None = None,
) -> dict[str, Any]:
    """"Not right" -- returns the rule to DRAFT with a required comment
    (brief §10.6 page 2)."""
    if not comment or not comment.strip():
        raise RuleOpsError("A comment is required to mark a rule 'Not right'")
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    return store.reject_confirmation(
        rule_code, comment, changed_by=changed_by, facilitated_by=facilitated_by
    )


def preview_rule(
    store: GovernanceStore,
    executor: RuleExecutor,
    sources_config: SourcesConfig,
    rule_code: str,
    *,
    config: EngineConfig,
    query_tag: str,
    sample_size: int = 10,
    changed_by: str = "claude-code",
) -> dict[str, Any]:
    """dq preview CODE (brief §10.5): validate, then return up to
    sample_size failing rows with deep links. This is for the Streamlit
    app only -- it returns row data, so Claude Code never calls it."""
    rule = store.get_rule(rule_code)
    if rule is None:
        raise RuleOpsError(f"Unknown rule code: {rule_code}")
    check_sql = rule.get("CHECK_SQL")
    if not check_sql:
        raise RuleOpsError(f"{rule_code} has no CHECK_SQL yet -- run set-sql first")

    try:
        lint(check_sql, sources_config)
    except LintError as exc:
        return {"ok": False, "errors": exc.violations, "sample": []}

    rendered_sql = render(check_sql, sources_config)
    exec_result = executor.execute_rule(
        rule_code, rendered_sql, query_tag=query_tag, timeout_seconds=config.rule_timeout_seconds
    )
    validation = validate_result(exec_result.table, scope=rule["SCOPE"])

    sf_object = sources_config.sources[rule["TARGET_OBJECT"]].sf_object
    sample = _sample_failing_rows(
        exec_result.table,
        sample_size=sample_size,
        sf_base_url=config.sf_base_url,
        sf_object=sf_object,
    )

    store.record_preview(
        rule_code,
        evaluated=validation.records_evaluated,
        failed=validation.records_failed,
        changed_by=changed_by,
    )

    return {
        "ok": validation.ok,
        "errors": validation.errors,
        "records_evaluated": validation.records_evaluated,
        "records_failed": validation.records_failed,
        "items_evaluated": validation.items_evaluated,
        "items_failed": validation.items_failed,
        "sample": sample,
    }


def _sample_failing_rows(
    table: pa.Table, *, sample_size: int, sf_base_url: str, sf_object: str
) -> list[dict[str, Any]]:
    record_ids = table.column("RECORD_ID").to_pylist()
    record_names = table.column("RECORD_NAME").to_pylist()
    site_names = table.column("SITE_NAME").to_pylist()
    is_valid = table.column("IS_VALID").to_pylist()
    details = table.column("DETAIL").to_pylist()

    rows: list[dict[str, Any]] = []
    for record_id, record_name, site_name, valid, detail in zip(
        record_ids, record_names, site_names, is_valid, details, strict=True
    ):
        if valid:
            continue
        rows.append(
            {
                "record_id": record_id,
                "record_name": record_name,
                "site_name": site_name,
                "detail": detail,
                "record_url": build_record_url(sf_base_url, sf_object, record_id),
            }
        )
        if len(rows) >= sample_size:
            break
    return rows
