# dq-governance

A local-first proof of concept that turns plain-English data governance rules into deterministic Snowflake checks over Sitetracker data, tracks the findings in a local DuckDB store, and reports weekly to data owners.

The specification is [docs/BRIEF.md](docs/BRIEF.md). Decisions are recorded in [docs/adr/](docs/adr/).

## Before you start

Complete the one-off setup in [docs/SETUP.md](docs/SETUP.md): Snowflake read-only access, the key in Keychain, the DuckDB store at `~/dq-data/governance.duckdb`, and `.dq/settings.toml`. Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Run the app

```bash
uv run streamlit run src/dq/app/streamlit_app.py
```

It opens at http://localhost:8501. Pick who you're acting as in the sidebar, then:

| Page | Use it to |
|---|---|
| **Rule Catalogue** | browse rules and manage each one: edit, submit, clarify, confirm, approve, retire, delete |
| **Add rule** | write a new rule in plain English |
| **Findings** | accept exceptions, mark false positives or revoke, singly or in bulk |
| **Run results** | see the latest run, trends, site scores and your narrative, or **Run checks now** |

## Run from the command line

Every `dq` command prints JSON only, never row data.

```bash
uv run dq catalogue refresh          # refresh the column catalogue from Snowflake
uv run dq rule list --status READY_FOR_COMPILE
uv run dq rule set-sql SITE-001 --file rules/sql/SITE-001.sql   # lint, validate, compile
uv run dq rule confirm SITE-001 --as <owner email>
uv run dq rule approve SITE-001 --as <approver email>
uv run dq run --manual               # weekly run; add --no-narratives to skip Cortex
```

Run `uv run dq --help` for the full list.

## Test

```bash
uv run pytest
uv run ruff check
```

Tests use a temporary DuckDB store and fake Snowflake/Cortex clients. They never touch real data.

## Upgrading the store

When a release adds a file under [sql/migrations/](sql/migrations/), apply it once, in order, after taking a backup:

```bash
cp ~/dq-data/governance.duckdb ~/dq-data/governance.duckdb.bak-$(date +%Y%m%d)
duckdb ~/dq-data/governance.duckdb < sql/migrations/0002_add_review_flag_change_types.sql
```

Migrations already applied: 0001. Next: 0002.

## Working with Claude Code

Read [CLAUDE.md](CLAUDE.md) first. Claude Code never reads production rows or credentials, and `dq run`, `dq preview`, `dq export` and `dq app` are denied to it. You run those yourself.
