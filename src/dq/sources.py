"""sources.yml loader, the {{ src.NAME }} renderer, and the pre-render SQL lint.

Snowflake-free: this only manipulates SQL text and parses it with sqlglot.
Nothing here opens a network connection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

# sqlglot logs a warning whenever it falls back to parsing a statement it
# doesn't fully model (e.g. CALL) as a generic Command. That's expected here
# -- an unmodelled statement is rejected by lint() anyway -- so keep it quiet.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_DIALECT = "snowflake"

_PLACEHOLDER_RE = re.compile(r"\{\{\s*src\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class UnknownSourceError(Exception):
    """Raised when rule SQL references a {{ src.NAME }} not in sources.yml."""


class LintError(Exception):
    """Raised when rule SQL fails the pre-render lint (brief §8)."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("; ".join(violations))


@dataclass(frozen=True)
class Source:
    table: str
    sf_object: str
    record_label: str
    description: str
    columns: list[str]
    filters: list[str]
    item_label: str | None = None
    apply_default_filters: bool = True


@dataclass(frozen=True)
class SourcesConfig:
    database: str
    schema: str
    default_filters: list[str]
    freshness_column: str
    sources: dict[str, Source]


def load_sources(path: Path) -> SourcesConfig:
    """Load config/sources.yml into typed structures."""
    with Path(path).open() as f:
        raw = yaml.safe_load(f)

    snowflake = raw["snowflake"]
    sources = {
        name: Source(
            table=entry["table"],
            sf_object=entry["sf_object"],
            record_label=entry["record_label"],
            description=entry.get("description", ""),
            columns=entry.get("columns", ["*"]),
            filters=entry.get("filters", []),
            item_label=entry.get("item_label"),
            apply_default_filters=entry.get("apply_default_filters", True),
        )
        for name, entry in raw["sources"].items()
    }

    return SourcesConfig(
        database=snowflake["database"],
        schema=snowflake["schema"],
        default_filters=raw.get("default_filters", []),
        freshness_column=raw["freshness_column"],
        sources=sources,
    )


def _get_source(name: str, config: SourcesConfig) -> Source:
    try:
        return config.sources[name]
    except KeyError:
        raise UnknownSourceError(
            f"Unknown source '{name}' referenced as {{{{ src.{name} }}}}"
        ) from None


def _render_source(name: str, config: SourcesConfig) -> str:
    source = _get_source(name, config)
    columns_sql = "*" if source.columns == ["*"] else ", ".join(source.columns)

    conditions = list(config.default_filters) if source.apply_default_filters else []
    conditions += source.filters

    where_sql = ""
    if conditions:
        where_sql = " WHERE " + " AND ".join(f"({c})" for c in conditions)

    table_ref = f"{config.database}.{config.schema}.{source.table}"
    return f"(SELECT {columns_sql} FROM {table_ref}{where_sql})"


def render(sql: str, config: SourcesConfig) -> str:
    """Replace every {{ src.NAME }} with its filtered subquery."""

    def _substitute(match: re.Match[str]) -> str:
        return _render_source(match.group(1), config)

    return _PLACEHOLDER_RE.sub(_substitute, sql)


def _stub_placeholders(sql: str) -> str:
    """Swap {{ src.NAME }} for a plain identifier so the SQL parses.

    Everything else in the raw text -- including any bypass attempt -- is
    left untouched, so lint() below still sees it.
    """
    return _PLACEHOLDER_RE.sub(lambda m: f"__SRC_{m.group(1)}__", sql)


def lint(sql: str, config: SourcesConfig) -> None:
    """Lint unrendered rule SQL per brief §8. Raises LintError on any violation."""
    violations: list[str] = []
    substituted = _stub_placeholders(sql)

    try:
        tokens = list(sqlglot.tokenize(substituted, read=_DIALECT))
    except Exception as exc:  # noqa: BLE001 - any tokenize failure is a lint failure
        raise LintError([f"could not tokenize rule SQL: {exc}"]) from exc

    semicolon_count = sum(1 for t in tokens if t.token_type.name == "SEMICOLON")

    try:
        statements = [s for s in sqlglot.parse(substituted, read=_DIALECT) if s is not None]
    except Exception as exc:  # noqa: BLE001 - any parse failure is a lint failure
        raise LintError([f"could not parse rule SQL: {exc}"]) from exc

    if len(statements) > 1:
        violations.append(
            f"multiple statements are not allowed (found {len(statements)}); use a single SELECT"
        )
    elif semicolon_count > 0:
        violations.append(
            "a trailing semicolon is not allowed; use a single SELECT with no statement terminator"
        )

    for stmt in statements:
        if not isinstance(stmt, exp.Select):
            violations.append(
                f"disallowed statement type '{stmt.__class__.__name__}'; "
                "only a single SELECT (CTEs allowed) is permitted"
            )
            continue

        for table in stmt.find_all(exp.Table):
            if table.catalog:
                violations.append(
                    f"three-part identifier '{table.sql()}' is not allowed; "
                    "reference sources via {{ src.NAME }}"
                )
            elif table.db and table.db.upper() in (config.database.upper(), config.schema.upper()):
                violations.append(
                    f"'{table.sql()}' references the database or schema directly; "
                    "use {{ src.NAME }} instead"
                )

        if any(stmt.find_all(exp.TableSample)):
            violations.append("SAMPLE / TABLESAMPLE is not allowed")

        if any(stmt.find_all(exp.Rand)):
            violations.append("RANDOM() is not allowed")

        if stmt.args.get("limit") is not None:
            violations.append(
                "a top-level LIMIT is not allowed; "
                "the check must return the full applicable population"
            )

    # Defence in depth: the role is read-only anyway, but also reject the raw
    # database/schema name appearing anywhere in the text (string literals,
    # dynamic identifiers) even when it isn't a structured table reference.
    for raw_name in (config.database, config.schema):
        if re.search(rf"\b{re.escape(raw_name)}\b", substituted, re.IGNORECASE):
            violations.append(
                f"'{raw_name}' references the database or schema directly; "
                "it must not appear in rule SQL"
            )

    if violations:
        raise LintError(violations)
