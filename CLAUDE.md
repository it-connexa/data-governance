# dq-governance — Data Governance PoC

Local-first PoC that turns plain-English data governance rules into deterministic Snowflake checks over Sitetracker data (via Fivetran), tracks findings in a local DuckDB store, and reports weekly to data owners.

**Read `docs/BRIEF.md` before doing anything.** It is the specification. `docs/SETUP.md` lists the human setup actions (Snowflake access, keys, DuckDB store, SharePoint).

## Hard rules

- **Never access production row data.** Interact with Snowflake and the governance store only through `dq` CLI commands that return metadata or counts (brief §6–7). Never run `dq preview`, `dq run` or `dq export`, and never run ad-hoc Snowflake or DuckDB queries against real data.
- **Never read or handle credentials.** Don't read `.dq/` (project-local, gitignored — see `docs/adr/0001-local-config-location.md`), `~/dq-data/`, `~/.snowflake/` or Keychain entries. Never ask the user to paste keys, tokens or connection secrets into this session. For setup steps involving keys (SETUP.md step 3), tell the user to run them in a separate terminal.
- Use `DQ_TEST` data, synthetic fixtures and the `FakeExecutor` for anything that needs rows.
- If a task seems to need production rows or credentials, stop and ask.

## Known gaps

- `.claude/settings.json` denies `Bash(uv run dq app:*)`, but not the underlying `streamlit run`/`uv run streamlit` command directly. Left open deliberately so Claude Code can verify Streamlit UI changes itself (always against a disposable temp store via `DQ_SETTINGS_PATH`, never real settings/`~/dq-data`) — see `docs/adr/0002-streamlit-verification-path.md`. Revisit before handover to the AI Solutions Specialist: either close the gap with an explicit deny, or confirm it's acceptable long-term.

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

- [x] Step 1 — Scaffold, settings, `secrets.py` (Keychain via `keyring`, mocked in tests) and `snowflake_client.py` (injectable connector, `SnowflakeExecutor` adapter for `engine.RuleExecutor`) all done. Connectivity proven live via `.dq/verify.py` (account `CONNEXA-DATA`, role/warehouse/read/write-blocked/Cortex all confirmed by Kevin)
- [x] Step 2 — Renderer + linter (`src/dq/sources.py`, sqlglot-based) and contract/result validator (`src/dq/contract.py`) done, test-first
- [x] Step 3 — `src/dq/catalogue.py`, `src/dq/rule_ops.py` (validate/set-sql/clarify/confirm/approve), full `dq` CLI wiring, `.claude/settings.json` allow/deny per brief §7, `.claude/commands/compile-rules.md`. `config/sources.yml` has real SITE/PROJECT tables. **SITE-004 and PRJ-002 are `ACTIVE`** — compiled and validated against live Snowflake via `dq rule set-sql` (3,346 sites/76 flagged; 579 projects/4 flagged), then `dq rule confirm`/`dq rule approve` (both run as `kevin.ross@connexa.co.nz`, without an actual owner preview of the failing rows first — a deliberate shortcut Kevin asked for, not the full brief §10.6 page-2 flow)
- [x] Step 4 — Lifecycle (`src/dq/lifecycle.py`), `src/dq/store/` (`GovernanceStore` protocol + DuckDB implementation), and `src/dq/engine.py` (run orchestration behind a `RuleExecutor` protocol, FakeExecutor-tested per brief §11) all done, test-first. `dq run` CLI wrapper built (`src/dq/cli.py`), deny-listed for Claude Code as brief requires — Kevin runs it himself
- [x] Step 5 — Totals, rollups, scoring (`src/dq/scoring.py`), wired into the engine's per-run object rollups and site scores. All Snowflake-free work so far: 187 passed, `uv run ruff check` clean
- [~] Step 6 — Streamlit app, down to two pages: **Rule Catalogue** (`pages/1_Rule_Catalogue.py`, renamed from "My rules") and **Add rule**. Rule Catalogue: filters (object-type pills, per-column multiselects for status/domain/scope/dimension/severity, search) over a row-click-to-select table, feeding one detail panel per rule covering every status including ACTIVE/RETIRED. That panel is now organised as **Manage this rule** (every lifecycle action together, high up: submit, clarify, confirm/reject, approve, retire with a required reason, delete a never-run DRAFT with a two-step confirm) → **Rule details** (read-only/editable fields) → a divider → **Preview** → **SQL** (Snowflake-executable only; the raw placeholder view and the separate Rule Detail page are both gone, folded in here). New backend: `retire_rule`/`delete_rule`/`has_rule_run_history` alongside the existing `create_rule`/`update_rule`/`submit_rule`/`answer_clarification`/`reject_confirmation`/`preview_rule` (`src/dq/rule_ops.py` + store) — `delete_rule` refuses if `DQ_RULE_RESULT` has any row for that rule (a real run happened at some point), pointing to retire instead. `READY_FOR_COMPILE` sits between `DRAFT` and `COMPILED` so the compile queue is explicit; that and the earlier retire/delete additions needed no new migration (`RETIRED`/`DRAFT` and history change-types were already valid) except the one already-run `sql/migrations/0001_add_ready_for_compile_status.sql`. `dq app` stays deny-listed for Claude Code; raw `streamlit run` isn't blocked the same way (flagged to Kevin, not yet resolved). Everything verified live in the browser against disposable temp stores, never `~/dq-data` — including catching and fixing two real bugs: pre-seeding the table widget's selection state on every rerun silently swallowed fresh row clicks, and deleting a rule left the table's selection index pointing at whatever rule shifted into that row. Pages 5 (run results/narratives) and 4 (findings/dispositions) still to do; page 3 (approvals) stays folded into Rule Catalogue rather than becoming its own page
- [ ] Step 7 — Export + SharePoint
- [ ] Step 8 — Narratives
- [ ] Step 9 — Owner sessions + weekly runs
- [ ] Step 10 — Measure and write up
