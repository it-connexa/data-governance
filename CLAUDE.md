# dq-governance — Data Governance PoC

Local-first PoC that turns plain-English data governance rules into deterministic Snowflake checks over Sitetracker data (via Fivetran), tracks findings in a local DuckDB store, and reports weekly to data owners.

**Read `docs/BRIEF.md` before doing anything.** It is the specification. `docs/SETUP.md` lists the human setup actions (Snowflake access, keys, DuckDB store, SharePoint).

## Hard rules

- **Never access production row data.** Interact with Snowflake and the governance store only through `dq` CLI commands that return metadata or counts (brief §6–7). Never run `dq preview`, `dq run` or `dq export`, and never run ad-hoc Snowflake or DuckDB queries against real data.
- **Never read or handle credentials.** Don't read `~/.dq/`, `~/dq-data/`, `~/.snowflake/` or Keychain entries. Never ask the user to paste keys, tokens or connection secrets into this session. For setup steps involving keys (SETUP.md step 3), tell the user to run them in a separate terminal.
- Use `DQ_TEST` data, synthetic fixtures and the `FakeExecutor` for anything that needs rows.
- If a task seems to need production rows or credentials, stop and ask.

## Working agreement

- Work through brief §15 one step at a time. Propose a plan, wait for approval, implement test-first, run `uv run pytest` and `uv run ruff check`, then summarise and **stop for review** before the next step.
- Snowflake-free work first (renderer, linter, contract, lifecycle, scoring, store), while the user completes SETUP.md in parallel.
- Keep business logic in plain modules. Nothing outside `src/dq/store/` imports `duckdb` (brief §12).
- When a new `dq` command is built, propose the matching allow/deny entry for `.claude/settings.json` per the table in brief §7.
- Record significant decisions as short ADRs in `docs/adr/`.
- Update the status section below at the end of each step.

## Conventions

- Python 3.11+, `uv`, type hints, `ruff`, `pytest`.
- New Zealand English in all user-facing text and docs.
- Timestamps are `TIMESTAMPTZ`, displayed in `Pacific/Auckland`.
- Rule SQL is Snowflake dialect using `{{ src.NAME }}` placeholders (brief §8–9).

## Status

- [~] Step 1 — Scaffold + settings loader done (`pyproject.toml`, `src/dq/settings.py`); `secrets.py`/`snowflake_client.py` and connectivity still pending SETUP.md
- [~] Step 2 — Renderer + linter (`src/dq/sources.py`, sqlglot-based) and contract/result validator (`src/dq/contract.py`) done, test-first, `uv run pytest` (54 passed) and `uv run ruff check` clean
- [ ] Step 3 — Catalogue labels, validate, rule set-sql / clarify, `/compile-rules`
- [ ] Step 4 — Lifecycle + engine (FakeExecutor tests)
- [ ] Step 5 — Totals, rollups, scoring
- [ ] Step 6 — Streamlit app
- [ ] Step 7 — Export + SharePoint
- [ ] Step 8 — Narratives
- [ ] Step 9 — Owner sessions + weekly runs
- [ ] Step 10 — Measure and write up
