"""The `dq` CLI (brief §7). The only interface Claude Code uses.

Every command here prints JSON and nothing else -- no row data, per
CLAUDE.md's hard rule. Commands that need Snowflake or the governance
store build their dependencies from `.dq/settings.toml` and DQ_CONFIG;
nothing here hardcodes a path or a credential.
"""

from __future__ import annotations

import getpass
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from dq import catalogue, engine, rule_ops
from dq.engine import EngineConfig
from dq.settings import SettingsError, load_settings
from dq.snowflake_client import SnowflakeClient, SnowflakeExecutor
from dq.sources import SourcesConfig, load_sources
from dq.store.base import GovernanceStore
from dq.store.duckdb_store import DuckDBStore

app = typer.Typer(help="Data governance PoC — compile plain-English rules to deterministic checks.")
catalogue_app = typer.Typer(
    help="Local column catalogue, refreshed from Snowflake INFORMATION_SCHEMA."
)
rule_app = typer.Typer(help="The rule registry: list, show, compile, clarify.")
app.add_typer(catalogue_app, name="catalogue")
app.add_typer(rule_app, name="rule")


def _print_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _load_store_and_sources() -> tuple[GovernanceStore, SourcesConfig]:
    try:
        settings = load_settings()
        store = DuckDBStore(settings.paths.duckdb)
        sources_config = load_sources(settings.paths.sources)
    except SettingsError as exc:
        _fail(str(exc))
    return store, sources_config


def _make_executor() -> SnowflakeExecutor:
    try:
        settings = load_settings()
    except SettingsError as exc:
        _fail(str(exc))
    return SnowflakeExecutor(SnowflakeClient(settings))


def _engine_config(store: GovernanceStore) -> EngineConfig:
    sf_base_url = store.get_config_value("SF_BASE_URL") or ""
    timeout = store.get_config_value("RULE_TIMEOUT_SECONDS")
    return EngineConfig(
        sf_base_url=sf_base_url, rule_timeout_seconds=int(timeout) if timeout else 120
    )


def _query_tag(command: str) -> str:
    return f"dq_poc:{command}:{uuid.uuid4()}"


def _fail(message: str) -> NoReturn:
    _print_json({"error": message})
    raise typer.Exit(code=1)


# --- dq catalogue ----------------------------------------------------------------


@catalogue_app.command("refresh")
def catalogue_refresh() -> None:
    """Pull column metadata for every source in sources.yml from Snowflake
    INFORMATION_SCHEMA into the local SOURCE_COLUMN table. Developer-only."""
    store, sources_config = _load_store_and_sources()
    settings = load_settings()
    client = SnowflakeClient(settings)
    try:
        counts = catalogue.refresh(
            store, client, sources_config, query_tag=_query_tag("catalogue_refresh")
        )
    except catalogue.CatalogueError as exc:
        _fail(str(exc))
    _print_json(counts)


@catalogue_app.command("load-labels")
def catalogue_load_labels(
    file: Annotated[Path, typer.Option("--file", help="CSV: source,column_name,label")],
) -> None:
    """Load Salesforce field labels into SOURCE_COLUMN.COLUMN_LABEL. Developer-only."""
    store, _ = _load_store_and_sources()
    try:
        count = catalogue.load_labels(store, file)
    except catalogue.CatalogueError as exc:
        _fail(str(exc))
    _print_json({"labels_loaded": count})


@catalogue_app.command("show")
def catalogue_show(
    source: Annotated[str | None, typer.Argument(help="Logical source name, e.g. SITE")] = None,
    search: Annotated[
        str | None, typer.Option("--search", help="Match on label or API name")
    ] = None,
) -> None:
    """Print columns, labels and types from the local catalogue."""
    store, _ = _load_store_and_sources()
    rows = catalogue.show(store, source_name=source, search=search)
    _print_json(rows)


# --- dq rule ---------------------------------------------------------------------


@rule_app.command("list")
def rule_list(status: Annotated[str | None, typer.Option("--status")] = None) -> None:
    store, _ = _load_store_and_sources()
    _print_json(store.list_rules(status=status))


@rule_app.command("show")
def rule_show(code: str) -> None:
    store, _ = _load_store_and_sources()
    rule = store.get_rule(code)
    if rule is None:
        _fail(f"Unknown rule code: {code}")
    _print_json(rule)


@rule_app.command("set-sql")
def rule_set_sql(
    code: str,
    file: Annotated[Path, typer.Option("--file", help="Path to the rule's CHECK_SQL")],
) -> None:
    """Write CHECK_SQL, run lint + validate, set COMPILED on success (brief §7)."""
    store, sources_config = _load_store_and_sources()
    executor = _make_executor()
    config = _engine_config(store)
    try:
        result = rule_ops.set_rule_sql(
            store,
            executor,
            sources_config,
            code,
            file,
            config=config,
            query_tag=_query_tag("rule_set_sql"),
        )
    except rule_ops.RuleOpsError as exc:
        _fail(str(exc))
    _print_json(result)
    if not result["ok"]:
        raise typer.Exit(code=1)


@rule_app.command("clarify")
def rule_clarify(
    code: str,
    question: Annotated[str, typer.Option("--question", help="What's ambiguous, specifically")],
) -> None:
    store, _ = _load_store_and_sources()
    try:
        updated = rule_ops.clarify_rule(store, code, question)
    except rule_ops.RuleOpsError as exc:
        _fail(str(exc))
    _print_json(updated)


@rule_app.command("confirm")
def rule_confirm(
    code: str,
    as_: Annotated[str, typer.Option("--as", help="Owner email confirming this rule")],
) -> None:
    """COMPILED -> OWNER_CONFIRMED: the owner has previewed the actual
    failing records and agrees the rule means what they intended."""
    store, _ = _load_store_and_sources()
    try:
        updated = rule_ops.confirm_rule(store, code, confirmed_by=as_)
    except rule_ops.RuleOpsError as exc:
        _fail(str(exc))
    _print_json(updated)


@rule_app.command("approve")
def rule_approve(
    code: str,
    as_: Annotated[str, typer.Option("--as", help="Approver email (must have IS_APPROVER)")],
) -> None:
    """OWNER_CONFIRMED -> ACTIVE: an approver has reviewed the SQL itself."""
    store, _ = _load_store_and_sources()
    try:
        updated = rule_ops.approve_rule(store, code, approved_by=as_)
    except rule_ops.RuleOpsError as exc:
        _fail(str(exc))
    _print_json(updated)


# --- dq validate -------------------------------------------------------------------


@app.command("validate")
def validate(code: str) -> None:
    """Render, lint, execute, and return counts + column metadata. Never rows."""
    store, sources_config = _load_store_and_sources()
    executor = _make_executor()
    config = _engine_config(store)
    try:
        result = rule_ops.validate_rule(
            store, executor, sources_config, code, config=config, query_tag=_query_tag("validate")
        )
    except rule_ops.RuleOpsError as exc:
        _fail(str(exc))
    _print_json(result)
    if not result["ok"]:
        raise typer.Exit(code=1)


# --- dq run ------------------------------------------------------------------------


@app.command("run")
def run(
    manual: Annotated[
        bool, typer.Option("--manual", help="Mark this as a manually triggered run")
    ] = False,
) -> None:
    """Full engine run (brief §10.1): render, lint, execute and validate
    every ACTIVE rule, apply the finding lifecycle, score sites and write
    object rollups. Writes the governance store; never prints row data."""
    store, sources_config = _load_store_and_sources()
    executor = _make_executor()
    config = _engine_config(store)
    query_tag = _query_tag("run")

    freshness = engine.collect_source_freshness(executor, sources_config, query_tag=query_tag)

    summary = engine.run(
        store=store,
        executor=executor,
        sources_config=sources_config,
        config=config,
        run_id=str(uuid.uuid4()),
        run_type="MANUAL" if manual else "SCHEDULED",
        triggered_by=getpass.getuser(),
        started_at=datetime.now(UTC),
        source_freshness=freshness,
    )

    _print_json(
        {
            "run_id": summary.run_id,
            "status": summary.status,
            "rules_attempted": summary.rules_attempted,
            "rules_succeeded": summary.rules_succeeded,
            "rules_errored": summary.rules_errored,
            "errors": summary.errors,
        }
    )
    if summary.status != "SUCCEEDED":
        raise typer.Exit(code=1)
