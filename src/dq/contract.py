"""The check SQL contract (brief §9) and result validation (brief §10.5).

Snowflake-free: operates purely on a pyarrow.Table already fetched by the
engine (or, in tests, a FakeExecutor). Never opens a connection.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.types as pat

# DETAIL arrives as TO_JSON(DETAIL) per brief §9's engine execution SQL, so it
# is a string column by the time it reaches this validator, not an object.
_STRING_COLUMNS = ("RECORD_ID", "ITEM_KEY", "RECORD_NAME", "SITE_ID", "SITE_NAME", "DETAIL")
_REQUIRED_COLUMNS = (
    "RECORD_ID",
    "ITEM_KEY",
    "RECORD_NAME",
    "SITE_ID",
    "SITE_NAME",
    "IS_VALID",
    "DETAIL",
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]
    columns: list[tuple[str, str]]
    records_evaluated: int
    records_failed: int
    items_evaluated: int
    items_failed: int
    duplicate_record_items: list[tuple[str, str | None]]
    null_record_ids: int
    null_is_valid: int
    null_site_ids: int
    item_key_used: bool


def _is_compatible_string(field_: pa.Field) -> bool:
    return pat.is_string(field_.type) or pat.is_large_string(field_.type)


def _is_compatible_bool(field_: pa.Field) -> bool:
    return pat.is_boolean(field_.type)


_COLUMN_CHECK = {name: _is_compatible_string for name in _STRING_COLUMNS} | {
    "IS_VALID": _is_compatible_bool
}


def validate_result(table: pa.Table, *, scope: str) -> ValidationResult:
    """Validate a rendered check's result against the contract in brief §9.

    `scope` is the rule's SCOPE ('SITE' or 'OBJECT', etc.); SITE_ID is only
    required to be non-null when scope == 'SITE'.
    """
    errors: list[str] = []
    columns = [(f.name, str(f.type)) for f in table.schema]
    present = set(table.schema.names)

    for name in _REQUIRED_COLUMNS:
        if name not in present:
            errors.append(f"missing required column '{name}'")

    usable_columns = {
        name
        for name in _REQUIRED_COLUMNS
        if name in present and _COLUMN_CHECK[name](table.schema.field(name))
    }
    for name in _REQUIRED_COLUMNS:
        if name in present and name not in usable_columns:
            field_type = table.schema.field(name).type
            errors.append(f"column '{name}' has an incompatible type: {field_type}")

    if {"RECORD_ID", "IS_VALID"} - usable_columns:
        # Can't safely compute anything else without these two columns.
        return ValidationResult(
            ok=False,
            errors=errors,
            columns=columns,
            records_evaluated=0,
            records_failed=0,
            items_evaluated=0,
            items_failed=0,
            duplicate_record_items=[],
            null_record_ids=0,
            null_is_valid=0,
            null_site_ids=0,
            item_key_used=False,
        )

    record_ids = table.column("RECORD_ID").to_pylist()
    is_valid = table.column("IS_VALID").to_pylist()
    n = len(record_ids)
    item_keys = table.column("ITEM_KEY").to_pylist() if "ITEM_KEY" in usable_columns else [None] * n
    site_ids = table.column("SITE_ID").to_pylist() if "SITE_ID" in usable_columns else [None] * n

    null_record_ids = sum(1 for v in record_ids if v is None)
    null_is_valid = sum(1 for v in is_valid if v is None)
    null_site_ids = sum(1 for v in site_ids if v is None)

    if null_record_ids:
        errors.append(f"{null_record_ids} row(s) have a null RECORD_ID")
    if null_is_valid:
        errors.append(f"{null_is_valid} row(s) have a null IS_VALID")
    if scope == "SITE" and null_site_ids:
        errors.append(f"{null_site_ids} row(s) have a null SITE_ID, required for SCOPE = 'SITE'")

    pair_counts = Counter(zip(record_ids, item_keys, strict=True))
    duplicate_record_items = sorted(pair for pair, count in pair_counts.items() if count > 1)
    if duplicate_record_items:
        errors.append(
            f"{len(duplicate_record_items)} duplicate (RECORD_ID, ITEM_KEY) pair(s): "
            f"{duplicate_record_items}"
        )

    distinct_records = {r for r in record_ids if r is not None}
    failed_records = {
        r for r, valid in zip(record_ids, is_valid, strict=True) if r is not None and valid is False
    }
    items_failed = sum(1 for valid in is_valid if valid is False)
    item_key_used = any(k is not None for k in item_keys)

    return ValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        columns=columns,
        records_evaluated=len(distinct_records),
        records_failed=len(failed_records),
        items_evaluated=len(record_ids),
        items_failed=items_failed,
        duplicate_record_items=duplicate_record_items,
        null_record_ids=null_record_ids,
        null_is_valid=null_is_valid,
        null_site_ids=null_site_ids,
        item_key_used=item_key_used,
    )
