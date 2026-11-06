---
description: Compile READY_FOR_COMPILE rules to CHECK_SQL, or ask a clarification question instead of guessing.
---

Compile plain-English data governance rules into deterministic Snowflake `CHECK_SQL` (brief §10.8). Optional argument: a rule code (e.g. `SITE-005`) to compile just that one rule; with no argument, work through every eligible rule.

## 1. Find the work

Run `uv run dq rule list --status READY_FOR_COMPILE`. A rule lands here either because the owner submitted a fresh `DRAFT` ("Submit for compilation" in the app), or because they answered a clarification question — both routes leave `CLARIFICATION_ANSWER` filled in when there was a question to answer.

## 2. Load context before writing any SQL

- `uv run dq catalogue sources` first. It prints the source registry from `config/sources.yml`: each source's table, description, `grain` (what one row is) and `not_for` (what to use a different source for). Several sources look alike (for example `PROJECT` vs `WIP_PROJECT`), so choose from this, not from the table name.
- `uv run dq catalogue show <SOURCE>` for every source the rule's `TARGET_OBJECT` touches, and `uv run dq catalogue show --search <label>` to map a plain-English field to its column API name. Milestone names are values in `MILESTONES.MILESTONE_NAME`, not columns, so the catalogue can't confirm them (see the milestone heuristic below).
- `docs/data-dictionary.md` — status values, what "active"/"operational"/"decommissioned" mean, template names, tower type values.
- Existing `rules/sql/*.sql` for style and precedent: `MS-001.sql` for milestone rules on the reporting `MILESTONES` source, `PRJ-002.sql` for scoping with `WIP_PROJECT`, and `SITE-004.sql` for a raw-table rule.

## 3. For each rule, one at a time

**If ambiguous** — an undefined term (e.g. "active", "operational"), a missing threshold, an unclear relationship, or a field/milestone label that matches several columns or none in the catalogue — run:

```bash
uv run dq rule clarify <CODE> --question "specific question here"
```

Don't guess. A specific, answerable question beats a vague one — name the exact term or field that's unclear.

**Otherwise**, write `rules/sql/<CODE>.sql`:
- A header comment quoting the rule statement and version (`-- SITE-004 v1: <statement>`), plus a short note on any definition pulled from the data dictionary (see the existing files for the pattern).
- In that header, a short **"Why these sources"** note: which sources the rule reads, why, and which look-alike sources you considered and rejected (use the registry's `grain` and `not_for`). Reviewers use it to catch a wrong table choice. Comments are ignored by the linter, so naming a view in prose is fine.
- Reference sources only via `{{ src.NAME }}` — never a raw database/schema/table name (the linter rejects this anyway, brief §8).
- Match the check SQL contract exactly (brief §9): `RECORD_ID`, `ITEM_KEY`, `RECORD_NAME`, `SITE_ID`, `SITE_NAME`, `IS_VALID`, `DETAIL`. `IS_VALID` must never be NULL — wrap in `COALESCE(..., <default>)` wherever the underlying comparison could be unknown, and say in a comment what the chosen default means.

Then check it before compiling:

```bash
uv run dq validate <CODE> --file rules/sql/<CODE>.sql
```

This is a dry run: it lints, executes and validates against the contract, returns counts and column metadata only, and writes nothing. Use it whenever you are changing a rule that is already `ACTIVE`, because `set-sql` recompiles it and takes it out of the weekly run until the owner re-confirms and re-approves it. Compare the counts with the rule's stored `VALIDATION_RESULT` (`uv run dq rule show <CODE>`) and explain any large change before compiling. A check that returns **no rows** is reported as an error, and it usually means a wrong join key or filter value.

Then run:

```bash
uv run dq rule set-sql <CODE> --file rules/sql/<CODE>.sql
```

This lints, executes and validates against the real contract, and sets `STATUS = COMPILED` only on success — nothing is written to the rule if it fails.

**On validation failure**, read the JSON output's `errors`, fix the SQL, and retry — up to 3 attempts. After 3 failed attempts, leave the rule as `READY_FOR_COMPILE` and flag it in the summary (step 4) rather than continuing to guess.

## Heuristics (brief §10.8)

- **Milestones**: use the `MILESTONES` source (`PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES`), which is already unpivoted: one row per project per milestone, with the front-end Sitetracker milestone names. Do not hand-map the raw wide `_F_C` / `_A_C` columns; the raw wide milestone tables are no longer registered as sources. Filter `MILESTONE_NAME IN (...)` and `PROJECT_TEMPLATE_NAME = '<template>'`, and set `ITEM_KEY = MILESTONE_NAME` and the rule's `ITEM_LABEL`. Dates are `FORECAST_DATE` and `ACTUAL_DATE`. `PROJECT_TRAN_ID` is the Salesforce project ID (join to `PROJECT.ID`); `PROJECT_ID` is the project **name**. The view has no project status, so join `PROJECT` for the status, the name and the site. Follow `MS-001.sql`.
- **Milestone names must be exact.** A name that matches no row is silently dropped from an `IN (...)` list. Use the owner's wording exactly, and for each named milestone confirm it exists: dry-run with `uv run dq validate <CODE> --file <path>` after adding `AND m.MILESTONE_NAME = '<name>'`. If zero items are evaluated, the name is wrong, so ask the owner with `dq rule clarify` instead of guessing.
- **Project scope**: `WIP_PROJECT` is the business's own definition of the projects it reports on (no test sites; the New Site, Modification / Upgrade and Co-Location templates). To use it, `JOIN {{ src.WIP_PROJECT }} w ON w.PROJECT_NAME = p.NAME` and still take `RECORD_ID` from `PROJECT`, because the view has no Salesforce ID. Don't read milestone dates from its `MILESTONE_nX` columns; they are report layout. Follow `PRJ-002.sql`.
- **Other wide tables**: if a future source is wide (many forecast/actual column pairs), unpivot the columns the rule names with `UNION ALL`, `ITEM_KEY` = the milestone label.
- **One-to-many related tables** (e.g. equipment per site): use `EXISTS` or a pre-aggregated CTE, never a plain join — `RECORD_ID` must stay unique per the contract.
- **Conditional rules**: put the condition in `WHERE`, so the denominator is the applicable population. Never return a non-applicable record as passing.
- **Template scoping**: on `MILESTONES`, filter `PROJECT_TEMPLATE_NAME`. On the raw `PROJECT`, join through to `PROJECT_TEMPLATE` and filter on the template name the owner gave. Never hard-code a template ID.
- Use "active" and other definitions from `docs/data-dictionary.md`; if one's missing, that's a clarification question, not a guess.
- Prefer base fields to formula fields (Fivetran formula values can be stale); if a formula field is unavoidable, say so in a comment.
- **Reporting sources can be wrong too.** `MILESTONES` and `WIP_PROJECT` are built by someone else's model. Known gaps are in `docs/reporting-view-followups.md` (some milestone-to-field mappings, and projects missing from each view). If a rule's counts look odd against the raw tables, say so in the summary rather than working around it in the SQL.
- Reporting sources have no `_FIVETRAN_DELETED` / `IS_DELETED` columns and no refresh timestamp, so they are registered without the default filters or a freshness check. Don't add those columns to your SQL.
- Rule codes are object-based (`SITE-nnn`, `MS-nnn`, `PRJ-nnn`) and already assigned in `DQ_RULE` — this workflow never invents a new code.

## 4. Summarise

Report: rules compiled (with their codes), rules now needing clarification (with the question asked), and rules that failed validation after 3 attempts (with the last error). Don't guess past this — a failed rule stays `READY_FOR_COMPILE` for the developer to look at.

## 5. Handover

The developer commits `rules/sql/` in a PR — this workflow doesn't commit on its own.
