"""dq catalogue refresh / show / load-labels (brief §7).

Pulls column metadata from Snowflake INFORMATION_SCHEMA into the local
SOURCE_COLUMN table, loads field labels from a CSV, and reads both back.
Claude Code compiles rules from SOURCE_COLUMN, never from Snowflake
directly (brief §6) -- `refresh` and `load_labels` are developer-only.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from dq.sources import SourcesConfig
from dq.store.base import GovernanceStore

_REQUIRED_CSV_COLUMNS = {"source", "column_name", "label"}


class CatalogueError(Exception):
    """Raised when catalogue refresh or field-label loading can't proceed."""


def refresh(
    store: GovernanceStore,
    snowflake_client: Any,
    sources_config: SourcesConfig,
    *,
    query_tag: str,
) -> dict[str, int]:
    """Pull column metadata for every source in sources_config from
    Snowflake INFORMATION_SCHEMA.COLUMNS into the local SOURCE_COLUMN table.

    Existing COLUMN_LABEL values (from load_labels) are preserved across a
    refresh; columns no longer present in Snowflake are removed.
    """
    refreshed_at = datetime.now(UTC)
    counts: dict[str, int] = {}

    for name, source in sources_config.sources.items():
        sql = (
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, ORDINAL_POSITION, COMMENT "
            f"FROM {sources_config.database}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{sources_config.schema}' AND TABLE_NAME = '{source.table}' "
            "ORDER BY ORDINAL_POSITION"
        )
        result = snowflake_client.execute(sql, query_tag=query_tag)
        columns = _table_to_columns(result.table)
        if not columns:
            raise CatalogueError(
                f"No columns found for source '{name}' "
                f"({sources_config.database}.{sources_config.schema}.{source.table}); "
                "check the table name in sources.yml"
            )
        store.upsert_source_columns(name, columns, refreshed_at)
        counts[name] = len(columns)

    return counts


def load_labels(store: GovernanceStore, csv_path: Path | str) -> int:
    """Load a field_labels.csv (columns: source, column_name, label) into
    SOURCE_COLUMN.COLUMN_LABEL."""
    path = Path(csv_path)
    if not path.exists():
        raise CatalogueError(f"Field labels file not found: {path}")

    labels: list[tuple[str, str, str]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        if not _REQUIRED_CSV_COLUMNS.issubset(fieldnames):
            raise CatalogueError(
                f"field_labels.csv must have columns {sorted(_REQUIRED_CSV_COLUMNS)}, "
                f"found {reader.fieldnames}"
            )
        for row in reader:
            labels.append((row["source"], row["column_name"].upper(), row["label"]))

    return store.update_column_labels(labels)


def show(
    store: GovernanceStore, source_name: str | None = None, search: str | None = None
) -> list[dict[str, Any]]:
    return store.get_source_columns(source_name=source_name, search=search)


def _table_to_columns(table: pa.Table) -> list[dict[str, Any]]:
    names = table.column("COLUMN_NAME").to_pylist()
    types = table.column("DATA_TYPE").to_pylist()
    nullable = table.column("IS_NULLABLE").to_pylist()
    positions = table.column("ORDINAL_POSITION").to_pylist()
    comments = table.column("COMMENT").to_pylist()

    return [
        {
            "column_name": name,
            "data_type": data_type,
            "is_nullable": nullable_flag == "YES",
            "ordinal_position": position,
            "column_comment": comment,
        }
        for name, data_type, nullable_flag, position, comment in zip(
            names, types, nullable, positions, comments, strict=True
        )
    ]
