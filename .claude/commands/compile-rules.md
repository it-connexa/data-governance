---
description: Compile READY_FOR_COMPILE rules to CHECK_SQL, or ask a clarification question instead of guessing.
---

Compile plain-English data governance rules into deterministic Snowflake `CHECK_SQL` (brief §10.8). Optional argument: a rule code (e.g. `SITE-005`) to compile just that one rule; with no argument, work through every eligible rule.

## 1. Find the work

Run `uv run dq rule list --status READY_FOR_COMPILE`. A rule lands here either because the owner submitted a fresh `DRAFT` ("Submit for compilation" in the app), or because they answered a clarification question — both routes leave `CLARIFICATION_ANSWER` filled in when there was a question to answer.

## 2. Load context before writing any SQL

- `uv run dq catalogue show <SOURCE>` for every source the rule's `TARGET_OBJECT` touches, and `uv run dq catalogue show --search <label>` to map a plain-English field or milestone name to its column API name.
- `docs/data-dictionary.md` — status values, what "active"/"operational"/"decommissioned" mean, template names, tower type values.
- Existing `rules/sql/*.sql` for style and precedent (e.g. how a similar rule handled COALESCE, template scoping, or a wide-table unpivot).

## 3. For each rule, one at a time

**If ambiguous** — an undefined term (e.g. "active", "operational"), a missing threshold, an unclear relationship, or a field/milestone label that matches several columns or none in the catalogue — run:

```bash
uv run dq rule clarify <CODE> --question "specific question here"
```

Don't guess. A specific, answerable question beats a vague one — name the exact term or field that's unclear.

**Otherwise**, write `rules/sql/<CODE>.sql`:
- A header comment quoting the rule statement and version (`-- SITE-004 v1: <statement>`), plus a short note on any definition pulled from the data dictionary (see the two existing files for the pattern).
- Reference sources only via `{{ src.NAME }}` — never a raw database/schema/table name (the linter rejects this anyway, brief §8).
- Match the check SQL contract exactly (brief §9): `RECORD_ID`, `ITEM_KEY`, `RECORD_NAME`, `SITE_ID`, `SITE_NAME`, `IS_VALID`, `DETAIL`. `IS_VALID` must never be NULL — wrap in `COALESCE(..., <default>)` wherever the underlying comparison could be unknown, and say in a comment what the chosen default means.

Then run:

```bash
uv run dq rule set-sql <CODE> --file rules/sql/<CODE>.sql
```

This lints, executes and validates against the real contract, and sets `STATUS = COMPILED` only on success — nothing is written to the rule if it fails.

**On validation failure**, read the JSON output's `errors`, fix the SQL, and retry — up to 3 attempts. After 3 failed attempts, leave the rule as `READY_FOR_COMPILE` and flag it in the summary (step 4) rather than continuing to guess.

## Heuristics (brief §10.8)

- **Wide tables** (milestones): for rules naming specific milestones, unpivot with `UNION ALL` (see `SITE-004`'s pattern for CTE style, or brief §9's `MS-001` example), `ITEM_KEY` = the milestone label. Set the rule's `ITEM_LABEL`.
- **One-to-many related tables** (e.g. equipment per site): use `EXISTS` or a pre-aggregated CTE, never a plain join — `RECORD_ID` must stay unique per the contract.
- **Conditional rules**: put the condition in `WHERE`, so the denominator is the applicable population. Never return a non-applicable record as passing.
- **Template scoping**: join through the project to `PROJECT_TEMPLATE` and filter on the template name the owner gave, rather than hard-coding a template ID.
- Use "active" and other definitions from `docs/data-dictionary.md`; if one's missing, that's a clarification question, not a guess.
- Prefer base fields to formula fields (Fivetran formula values can be stale); if a formula field is unavoidable, say so in a comment.
- Rule codes are object-based (`SITE-nnn`, `MS-nnn`, `PRJ-nnn`) and already assigned in `DQ_RULE` — this workflow never invents a new code.

## 4. Summarise

Report: rules compiled (with their codes), rules now needing clarification (with the question asked), and rules that failed validation after 3 attempts (with the last error). Don't guess past this — a failed rule stays `READY_FOR_COMPILE` for the developer to look at.

## 5. Handover

The developer commits `rules/sql/` in a PR — this workflow doesn't commit on its own.
