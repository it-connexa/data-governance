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
- [x] Step 6 — Streamlit app, four pages plus a home page. **Rule Catalogue** and **Add rule** as before (filters, row-click table, one panel to manage a rule's whole lifecycle; epoch-keyed table so a row that leaves the view loses its tick). **Findings** (`pages/3_Findings.py`): filter by status/rule/severity/record/age, select one or many, accept as exception (expiry defaults from `EXCEPTION_DEFAULT_DAYS`), mark false positive (then offers to flag the rule for review) or revoke, with disposition history; the Catalogue shows the review flag, an open-findings count and a jump to Findings. **Run results** (`pages/4_Run_Results.py`): latest run band with source freshness and errored rules, object headlines with week-on-week deltas, per-rule results (record/item toggle, sparklines), site scores (worst first), the owner's narrative with an audit view of exactly what was sent to Cortex, run history, and **Run checks now** behind a two-step confirm (`src/dq/run_ops.py`, refuses while another run is in progress). Logic lives in `src/dq/finding_ops.py` and the store, all tested. **Migration 0002** (`sql/migrations/0002_add_review_flag_change_types.sql`) widens `DQ_RULE_HISTORY.CHANGE_TYPE` for the review flag: Kevin runs it against `~/dq-data` (flagging a rule fails on the real store until then). `dq app` stays deny-listed for Claude Code; raw `streamlit run` isn't blocked (see Known gaps). Everything verified live in the browser against disposable temp stores, never `~/dq-data` — the Run now / Generate narrative buttons only against a fake executor/completer injected from a scratchpad `sitecustomize.py`, so **Kevin still needs to try both against real Snowflake/Cortex**. Bugs found and fixed on the way: a revoked disposition was ignored on the next run (latest disposition row now decides), and `DQ_SITE_SCORE.SITE_NAME` was never written (site scores showed Salesforce IDs; names appear from the next run). Page 3 (approvals) stays folded into the Rule Catalogue
- [ ] Step 7 — Export + SharePoint
- [~] Step 8 — Narratives: `src/dq/narratives.py` + `prompts/narrative_v1.md` done test-first against a fake completer (aggregated input only, no `DETAIL`/record names, enforced by tests; model and prompt passed as bound parameters via `SnowflakeCompleter`). `dq run` writes a narrative per owner unless `--no-narratives`; the Run results page can regenerate one. **Not yet tried against real Cortex** — needs `CORTEX_MODEL` set in `DQ_CONFIG` and Kevin running it once to check cost, model availability and the wording of the output
- [ ] Step 9 — Owner sessions + weekly runs
- [ ] Step 10 — Measure and write up
- [x] Reporting-layer move (2026-11-06) — milestones and project scope now come from `PROD.REPORTING` views; `SITE`, `PROJECT` and `PROJECT_TEMPLATE` stay on `RAW.TMP` for the PoC (Kevin's call). `sources.yml` sources can set their own `database`/`schema`/`freshness_column`/`check_freshness` and carry registry fields `grain`/`not_for` (`dq catalogue sources` prints them); the linter covers every configured database/schema and ignores comments. `MILESTONES` = `RPT_PROJECT_UNPIVOT_MILESTONES` (one row per project per milestone, front-end milestone names; `PROJECT_TRAN_ID` is the Salesforce project ID, `PROJECT_ID` is the project name) and `WIP_PROJECT` = `RPT_WIP_PROJECT_DETAIL` (the business's reporting scope: one row per project, no Salesforce ID, join to `PROJECT` on name; its `MILESTONE_nX` columns are report layout, not for rules). `MILESTONE_EXT`/`MILESTONE_EXT_2` retired as sources (`DQ_POC_READER` no longer needs them). **MS-001 v4** (milestones view) and **PRJ-002 v3** (WIP scope) are `ACTIVE`, confirmed and approved by Kevin. Checking a candidate SQL file without recompiling: `dq validate CODE --file PATH` (dry run, counts only, writes nothing); a check that returns no rows is now a validation error. **Open with the Data Lead** (`docs/reporting-view-followups.md`): the milestones view appears to map Equipment Live and Site Confirmation Notice Signed to the wrong raw fields (so some MS-001 findings may be false positives), it has no rows for 7 older plus 29 recent active New Site projects, and one project is missing from the WIP view. `.claude/commands/compile-rules.md` now uses `MILESTONES`, reads `dq catalogue sources` before choosing a source, asks for a "Why these sources" header note and dry-runs with `dq validate --file` before recompiling an active rule
