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

It opens at http://localhost:8501. Pick who you're acting as at the top right, then use the three pages in the bar across the top:

| Page | Use it to |
|---|---|
| **Overview** | see whether the last run worked, what changed, your rules at a glance with their trend, and what is waiting on you; or **Run checks now** |
| **Findings** | see what each rule is for beside its findings, then accept exceptions, mark false positives or revoke, singly or in bulk |
| **Rules** | pick a rule on the left; on the right see its statement, SQL and history, move it through its lifecycle with one button, or add a **New rule** |

## Run from the command line

Every `dq` command prints JSON only, never row data.

```bash
uv run dq catalogue refresh          # refresh the column catalogue from Snowflake
uv run dq catalogue sources          # which tables are registered, and what each is for
uv run dq rule list --status READY_FOR_COMPILE
uv run dq validate SITE-001 --file rules/sql/SITE-001.sql       # dry run: counts only, writes nothing
uv run dq rule set-sql SITE-001 --file rules/sql/SITE-001.sql   # lint, validate, compile
uv run dq rule confirm SITE-001 --as <owner email>
uv run dq rule approve SITE-001 --as <approver email>
uv run dq run --manual               # weekly run; add --narratives to also write Cortex narratives
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
