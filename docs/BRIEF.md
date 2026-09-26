# Data Governance PoC — Build Brief (v2.1, local-first)

**Audience:** Claude Code (and the developer driving it)
**Owner:** Kevin, Head of Technology, Connexa. Will hand over to the AI Solutions Specialist.
**Status:** PoC, timeboxed to 4–6 weeks
**Companion files:**
- `sql/dq_poc_duckdb_schema.sql`: the local governance store
- `config/sources.yml`: logical source definitions
- `sql/snowflake_readonly_setup.sql`: Snowflake access, run by the admin

> Put this file in the repo as `docs/BRIEF.md` and reference it from a short `CLAUDE.md` at the repo root.

---

## 1. Context and goal

Connexa builds, operates and maintains cellular tower sites across New Zealand. Sitetracker (on Salesforce) is the core system. Its data is replicated to Snowflake by Fivetran.

We want to prove that **data owners can express governance rules in plain English, have them turned into deterministic checks, and see weekly findings in Power BI**, at both object level and site level.

**PoC deployment constraint:** everything runs locally on Kevin's Mac. Snowflake is used **read-only**, for source data and for Cortex. Nothing is created in Snowflake beyond one read-only role, one service user and one warehouse, all set up by the admin. If the PoC succeeds, the governance store and engine will be refactored into Snowflake (§12).

**Design principles**
1. **The LLM compiles and explains, it never detects.** Rules become SQL that is validated, previewed by the owner, approved by tech, then run deterministically.
2. **Push-down execution.** Rule SQL runs in Snowflake (Snowflake dialect). Only results come back locally: record IDs, site IDs, pass/fail and a small detail object. No bulk copies of source tables.
3. **Portable by construction.** Rule SQL doesn't change when the governance store moves to Snowflake.

## 2. Scope

**In scope**
- Sitetracker site master and project milestone data, via Fivetran tables (read-only)
- A local DuckDB governance store: registry, runs, findings, dispositions, scores and narratives
- The `dq` Python CLI, including the engine, catalogue, validation, preview, export and narratives
- A local Streamlit app for the full owner experience: capture, clarify, preview, confirm, approve and manage findings
- Cortex narratives, called read-only through the Snowflake connector
- Parquet extracts to a restricted SharePoint library for Power BI (the report itself is built separately)

**Out of scope for the PoC**
- Creating objects in Snowflake
- Hosting, multi-user access or authentication (sessions are facilitated by Kevin)
- Remediation workflow, non-Sitetracker systems, Scout integration, writing back to Sitetracker

## 3. Architecture

```
 Snowflake (prod, read-only: DQ_POC_SVC / DQ_POC_READER)
 ┌──────────────────────────────────────────────┐
 │ <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.* tables   │◄── rendered rule SQL (SELECT only)
 │ INFORMATION_SCHEMA.COLUMNS                   │◄── catalogue refresh
 │ SNOWFLAKE.CORTEX.COMPLETE                    │◄── narrative (aggregated input)
 └──────────────────────────────────────────────┘
                    ▲  results as Arrow
                    │
 Local Mac          │
 ┌──────────────────┴───────────────────────────────────────────┐
 │ dq CLI (Python)                                              │
 │  sources.yml ─► renderer ─► executor ─► lifecycle ─► scoring │
 │                                     └─► narratives           │
 │            │                                                 │
 │            ▼                                                 │
 │  ~/dq-data/governance.duckdb   (NOT in a synced folder)      │
 │            │                         │                       │
 │  Streamlit app (local)        dq export ─► Parquet           │
 └──────────────────────────────────────┬───────────────────────┘
                                        ▼
                   SharePoint library (restricted, synced folder)
                                        ▼
                         Power BI Service (RLS on OWNER_EMAIL)
```

## 4. Repository layout

```
dq-governance/
├── CLAUDE.md
├── pyproject.toml                  # uv-managed; entry point `dq = dq.cli:app`
├── config/
│   ├── sources.yml
│   └── settings.example.toml       # copy to ~/.dq/settings.toml (not committed)
├── docs/
│   ├── BRIEF.md
│   ├── data-dictionary.md          # in-scope objects/fields + business meaning (human-maintained)
│   ├── runbook.md
│   └── adr/
├── sql/
│   ├── dq_poc_duckdb_schema.sql
│   └── snowflake_readonly_setup.sql
├── rules/sql/<RULE_CODE>.sql       # mirror of CHECK_SQL for PR review
├── prompts/
│   ├── narrative_v1.md
│   └── compile_rule_v1.md          # stretch: in-app Cortex compilation
├── src/dq/
│   ├── cli.py                      # Typer CLI — the only interface Claude Code uses
│   ├── settings.py                 # loads ~/.dq/settings.toml
│   ├── secrets.py                  # private key from macOS Keychain via `keyring`
│   ├── snowflake_client.py         # connection, query tag, timeouts, Arrow fetch
│   ├── sources.py                  # sources.yml loader + {{ src.X }} renderer + SQL lint
│   ├── contract.py                 # check SQL contract + result validation
│   ├── store/
│   │   ├── base.py                 # GovernanceStore protocol
│   │   └── duckdb_store.py         # DuckDB implementation
│   ├── engine.py                   # run orchestration
│   ├── lifecycle.py                # finding state transitions
│   ├── scoring.py
│   ├── narratives.py
│   ├── catalogue.py
│   ├── export.py
│   └── app/                        # Streamlit
│       ├── streamlit_app.py
│       └── pages/
├── tests/
└── .claude/
    ├── settings.json               # permission allow/deny (see §6)
    └── commands/compile-rules.md
```

## 5. Environment and conventions

- Python 3.11+ managed with `uv`.
- Dependencies: `duckdb` (≥1.4, which adds `MERGE`), `snowflake-connector-python[pandas]`, `pyarrow`, `pytz` (needed by DuckDB for timezone-aware fetches), `keyring`, `cryptography`, `pyyaml`, `typer`, `streamlit`, `pytest` and `ruff`.
- Local paths:
  - governance DB at `~/dq-data/governance.duckdb`
  - settings at `~/.dq/settings.toml` (Snowflake account, user, warehouse, role, DB path, export path)
  - neither is committed or placed in an iCloud-, OneDrive- or SharePoint-synced folder
- The private key comes from macOS Keychain (service `dq-snowflake-key`, account `dq_poc`, stored base64-encoded) through `keyring`. It is never written to disk by the app.
- Every Snowflake session sets:
  - `QUERY_TAG = 'dq_poc:<command>:<run_id>'`, so PoC cost is measurable in `QUERY_HISTORY`
  - `STATEMENT_TIMEOUT_IN_SECONDS` from `RULE_TIMEOUT_SECONDS`
- All timestamps are `TIMESTAMPTZ`, displayed in `Pacific/Auckland`.
- Use New Zealand English in all user-facing text.
- Use type hints, `ruff`, and `pytest`. Keep business logic in plain modules and tests independent of Snowflake (§11).
- DuckDB is single-writer. Open short-lived connections per operation through the store and never hold a connection across user interaction. The Streamlit "Run checks now" action calls the engine in-process.

## 6. Security and data handling

### Snowflake
- The engine connects as `DQ_POC_SVC` with role `DQ_POC_READER`, which has `SELECT` on named Fivetran tables and `CORTEX_USER`, and nothing else.
- The role *can* read the raw tables, so the soft-delete guarantee is enforced by **the renderer and linter** (§8), not by privileges. Record this in an ADR.

### Claude Code sessions (mandatory)
- Claude Code interacts with data **only through `dq` CLI commands that return metadata or counts**. It never reads the DuckDB file directly, never runs ad-hoc Snowflake queries, and never runs `dq preview`, which returns rows.
- Metadata for compilation comes from the local `SOURCE_COLUMN` table (`dq catalogue show`) and `docs/data-dictionary.md`.
- `.claude/settings.json` (starting point; adjust to the actual CLI):

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run dq rule list:*)",
      "Bash(uv run dq rule show:*)",
      "Bash(uv run dq rule set-sql:*)",
      "Bash(uv run dq rule clarify:*)",
      "Bash(uv run dq catalogue show:*)",
      "Bash(uv run dq validate:*)",
      "Bash(uv run pytest:*)",
      "Bash(uv run ruff:*)"
    ],
    "deny": [
      "Bash(uv run dq preview:*)",
      "Bash(uv run dq run:*)",
      "Bash(uv run dq export:*)",
      "Bash(snow:*)",
      "Bash(duckdb:*)",
      "Bash(security:*)",
      "Bash(python -c:*)",
      "Bash(uv run python -c:*)",
      "Read(~/dq-data/**)",
      "Read(~/.dq/**)",
      "Read(~/.snowflake/**)"
    ]
  }
}
```

- These are tooling guardrails, not server-side enforcement. The residual risk is noted in an ADR, and it goes away on migration to Snowflake roles.
- If a task seems to need production rows, stop and ask the developer.

### Local data
- The governance DB holds IDs, site and record names, and small `DETAIL` objects, not source tables. Keep `DETAIL` under 1 KB and don't copy free-text fields.
- Parquet extracts go only to the restricted SharePoint library configured in `EXPORT_PATH`.
- Narrative inputs are aggregated and stored in `DQ_NARRATIVE.INPUT_SUMMARY` for audit.

## 7. The `dq` CLI

| Command | Purpose | Returns rows? | Claude Code |
|---|---|---|---|
| `dq init` | Create or upgrade the DuckDB store from the schema file | No | Deny (developer only) |
| `dq catalogue refresh` | Pull column metadata for sources in `sources.yml` from Snowflake `INFORMATION_SCHEMA` into `SOURCE_COLUMN` | No | Deny (developer only) |
| `dq catalogue load-labels --file PATH` | Load Salesforce field labels (CSV: source, column API name, label) into `SOURCE_COLUMN.COLUMN_LABEL` | No | Deny (developer only) |
| `dq catalogue show [SOURCE] [--search TEXT]` | Print columns, labels and types from local `SOURCE_COLUMN`; `--search` matches on label or API name | No | Allow |
| `dq rule list [--status S]` | List rules | Registry only | Allow |
| `dq rule show CODE` | Show a rule, including history | Registry only | Allow |
| `dq rule set-sql CODE --file PATH` | Write `CHECK_SQL`, run lint + validate, set `COMPILED` on success | No | Allow |
| `dq rule clarify CODE --question TEXT` | Set `NEEDS_CLARIFICATION` with a question | No | Allow |
| `dq validate CODE` | Render, lint, execute, and return counts + column metadata (§10.5) | **No** | Allow |
| `dq preview CODE` | Validate, then return a sample of failing rows | **Yes** | **Deny** |
| `dq run [--manual]` | Full engine run | No (writes store) | Deny |
| `dq export` | Write reporting views to Parquet in `EXPORT_PATH` | No | Deny |
| `dq app` | Launch the Streamlit app | — | Deny |

## 8. Sources, rendering and lint

`config/sources.yml` maps logical names (SITE, PROJECT, MILESTONE, and so on) to Fivetran tables, with default soft-delete filters, source-specific filters, an explicit column list and the Salesforce object API name for deep links.

**Rendering:** `{{ src.NAME }}` in rule SQL is replaced with:

```sql
(SELECT <columns> FROM <DB>.<SCHEMA>.<TABLE> WHERE <default_filters> AND <filters>)
```

An unknown `NAME` is an error.

**Lint:** runs on the *unrendered* SQL before any execution. It rejects:
- anything referencing the Fivetran database or schema name directly, or containing a three-part identifier (`a.b.c`), since tables must be reached through `{{ src.X }}`
- more than one statement, or a trailing semicolon
- DML, DDL or control keywords as statements (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `ALTER`, `DROP`, `GRANT`, `CALL`, `COPY`, `PUT`, `GET`, `USE`, `SET`, `EXECUTE`)
- `SAMPLE`, `TABLESAMPLE`, `RANDOM()`, or a top-level `LIMIT`

The role is read-only anyway, so this is defence in depth. The soft-delete guarantee is the part that matters.

## 9. The check SQL contract

Every `CHECK_SQL` is a single Snowflake `SELECT` (CTEs allowed) that returns **every in-scope record (or record × item)** with a pass/fail flag. The rows returned form the population used for totals.

| Column | Type | Required | Notes |
|---|---|---|---|
| `RECORD_ID` | VARCHAR | Yes, non-null | Salesforce Id of the record the finding is about |
| `ITEM_KEY` | VARCHAR | Column required, value nullable | NULL for single-item rules. For multi-item rules (e.g. several milestones on one wide-table row), identifies the item: use the human-readable milestone label. `(RECORD_ID, ITEM_KEY)` must be unique. |
| `RECORD_NAME` | VARCHAR | Column required, value nullable | |
| `SITE_ID` | VARCHAR | Column required; non-null when `SCOPE = 'SITE'` | Salesforce Id of the related site |
| `SITE_NAME` | VARCHAR | Column required, value nullable | |
| `IS_VALID` | BOOLEAN | Yes, non-null | `TRUE` = passes |
| `DETAIL` | OBJECT | Column required, value nullable | Small explanation, under 1 KB |

Example (placeholder field names; take the real ones from `dq catalogue show` and the data dictionary):

```sql
-- SITE-001 v1: Every site with an operational status must have a commissioning date recorded.
SELECT
    s.ID                                        AS RECORD_ID,
    s.NAME                                      AS RECORD_NAME,
    s.ID                                        AS SITE_ID,
    s.NAME                                      AS SITE_NAME,
    (s.<COMMISSIONING_DATE> IS NOT NULL)        AS IS_VALID,
    OBJECT_CONSTRUCT('status', s.<STATUS>,
                     'commissioning_date', s.<COMMISSIONING_DATE>) AS DETAIL
FROM {{ src.SITE }} s
WHERE s.<STATUS> = 'Operational'
```

The engine executes a rule as:

```sql
SELECT RECORD_ID, ITEM_KEY, RECORD_NAME, SITE_ID, SITE_NAME, IS_VALID, TO_JSON(DETAIL) AS DETAIL
FROM ( <rendered CHECK_SQL> )
```

It fetches the result as Arrow and registers it in DuckDB for lifecycle processing.

**Denominator = applicable population.** The rows returned are the records the rule applies to, not every record in the object. For "Lightpole sites with <field> equipment must have X", only qualifying sites are returned. The object total is recorded separately for context (§10.3).

**Multi-item example (wide milestone table).** Milestones are forecast/actual column pairs on one row per project. The rule unpivots only the milestones the owner named:

```sql
-- MS-001 v1: For active projects using the <TEMPLATE_NAME> template, the milestones
-- <MILESTONE_A>, <MILESTONE_B> and <MILESTONE_C> must not have a forecast date in the
-- past unless an actual date has been recorded.
WITH scoped AS (
    SELECT m.ID, m.NAME, p.<SITE_LOOKUP> AS SITE_ID, s.NAME AS SITE_NAME,
           m.<A_FORECAST>, m.<A_ACTUAL>, m.<B_FORECAST>, m.<B_ACTUAL>, m.<C_FORECAST>, m.<C_ACTUAL>
    FROM {{ src.MILESTONE }} m
    JOIN {{ src.PROJECT }} p           ON p.ID = m.<PROJECT_LOOKUP>
    JOIN {{ src.PROJECT_TEMPLATE }} t  ON t.ID = p.<TEMPLATE_LOOKUP>
    LEFT JOIN {{ src.SITE }} s         ON s.ID = p.<SITE_LOOKUP>
    WHERE t.NAME = '<TEMPLATE_NAME>' AND p.<STATUS> = 'Active'
),
items AS (
    SELECT ID, NAME, SITE_ID, SITE_NAME, '<Milestone A label>' AS ITEM_KEY, <A_FORECAST> AS FORECAST, <A_ACTUAL> AS ACTUAL FROM scoped
    UNION ALL
    SELECT ID, NAME, SITE_ID, SITE_NAME, '<Milestone B label>', <B_FORECAST>, <B_ACTUAL> FROM scoped
    UNION ALL
    SELECT ID, NAME, SITE_ID, SITE_NAME, '<Milestone C label>', <C_FORECAST>, <C_ACTUAL> FROM scoped
)
SELECT
    ID AS RECORD_ID, ITEM_KEY, NAME AS RECORD_NAME, SITE_ID, SITE_NAME,
    NOT (ACTUAL IS NULL AND FORECAST < CURRENT_DATE())                      AS IS_VALID,
    OBJECT_CONSTRUCT('forecast', FORECAST, 'actual', ACTUAL)                AS DETAIL
FROM items
```

`UNION ALL` is preferred over `UNPIVOT` for readability when naming a handful of milestones. For rules over *all* milestone columns (e.g. MS-003, "no actual dates in the future"), generate the `UNION ALL` from the catalogue, or use `UNPIVOT` with `INCLUDE NULLS`.

For uniqueness rules, every record in a duplicate group is returned with `IS_VALID = FALSE`, and `DETAIL` names the other record(s). Handle NULLs explicitly so `IS_VALID` is never NULL.

## 10. Component specifications

### 10.1 Engine (`dq run`)

1. Create a `DQ_RUN` row with a UUID and `STATUS = 'RUNNING'`. Record per-source freshness (`MAX(<freshness_column>)`) in `DQ_RUN_SOURCE_FRESHNESS`.
2. Load `ACTIVE` rules from the store. For each distinct `TARGET_OBJECT`, get `OBJECT_TOTAL` once with `SELECT COUNT(*) FROM {{ src.<TARGET> }}`.
3. For each rule, **independently**:
   - Render, lint and execute it in Snowflake, recording the query ID and duration.
   - Validate the result against the contract (nulls, `(RECORD_ID, ITEM_KEY)` uniqueness, SITE_ID for SITE scope). A violation means `ERRORED`.
   - On success, apply the lifecycle updates (§10.2) in a single DuckDB transaction, then write `DQ_RULE_RESULT` with record-level and item-level counts (§10.3).
   - On error, write `DQ_RULE_RESULT` with the error and **leave that rule's findings untouched**.
4. Close open findings for rules retired since the last run (`RULE_RETIRED`).
5. Score sites and write the object rollups (§10.3).
6. Generate narratives (§10.4). Failures are logged but don't fail the run.
7. Set run status: `SUCCEEDED` if every rule succeeded, `PARTIAL` if some errored, `FAILED` if the run itself failed.
8. Export (§10.7) unless `--no-export`.
9. Print a summary.

**Requirements**
- Idempotent in outcome: re-running on unchanged data only updates `LAST_SEEN_*` and `TIMES_SEEN`.
- Isolated: one rule never affects another.
- The Snowflake executor sits behind an interface (`RuleExecutor`) so tests can substitute a fake.

### 10.2 Finding lifecycle

- `FINDING_ID = sha256(RULE_CODE + '|' + RECORD_ID + '|' + (ITEM_KEY or ''))`, so each milestone on a project is tracked, resolved and reopened independently.
- `RECORD_URL = SF_BASE_URL + '/lightning/r/' + sf_object + '/' + RECORD_ID + '/view'`, where `sf_object` comes from `sources.yml` for the rule's `TARGET_OBJECT`.
- An **active disposition** is the latest disposition row for the finding where `DISPOSITION <> 'REVOKED'` and (`EXPIRES_AT IS NULL` or `EXPIRES_AT > now`).

| Situation (for a rule that ran successfully) | Action |
|---|---|
| Fails, no finding | Insert `OPEN`, setting first/last seen to now |
| Fails, finding `OPEN` | Update last seen and `DETAIL`, increment `TIMES_SEEN` |
| Fails, finding `RESOLVED` | Reopen: set `OPEN`, clear resolved fields, increment `REOPENED_COUNT` |
| Fails, active disposition exists | Set `STATUS` to that disposition, update last seen |
| Fails, disposition expired or revoked | `OPEN` |
| Passes, finding not `RESOLVED` | `RESOLVED` with `FIXED` |
| Not in evaluated set, finding not `RESOLVED` | `RESOLVED` with `OUT_OF_SCOPE` |
| Rule `ERRORED` | No change |

Implement with DuckDB `MERGE` plus follow-up updates. Write every row of this table as a unit test first.

### 10.3 Totals, rollups and scoring

**Per rule, per run (`DQ_RULE_RESULT`).** Record-level figures are the default headline, and item-level figures are kept alongside.

| Field | Definition |
|---|---|
| `OBJECT_TOTAL` | All records in the target source (context only) |
| `RECORDS_EVALUATED` | Distinct `RECORD_ID`s returned: the headline denominator |
| `RECORDS_FAILED` | Distinct records with at least one failing row (raw) |
| `RECORDS_FAILED_OPEN` | As above, excluding rows whose finding has an active disposition: the headline numerator |
| `PASS_RATE` | `100 × (RECORDS_EVALUATED − RECORDS_FAILED_OPEN) / RECORDS_EVALUATED` |
| `ITEMS_EVALUATED` / `ITEMS_FAILED` / `ITEMS_FAILED_OPEN` / `ITEM_PASS_RATE` | Same, counted over rows. They equal the record figures for single-item rules. |

The headline sentence comes from `V_RULE_SUMMARY_LATEST.SUMMARY_TEXT`, which is deterministic SQL with no LLM. For example:
- *"SITE-001 as at 26-Sep-2026: of 1,546 sites, 45 exceptions (2.9%)"*
- *"MS-001 as at 26-Sep-2026: of 1,546 projects, 45 exceptions (2.9%); 61 of 4,638 milestones (1.3%)"*

`RECORD_LABEL` and `ITEM_LABEL` on the rule supply the nouns. They default from `record_label` and `item_label` in `sources.yml` when a rule is created.

**Per object, per run (`DQ_OBJECT_RESULT`).** For each `TARGET_OBJECT` with active rules:
- `ACTIVE_RULES` and `RULES_SUCCEEDED`
- `RECORDS_EVALUATED`: distinct records evaluated by at least one successful rule
- `RECORDS_WITH_EXCEPTION`: distinct records with at least one `OPEN` finding across those rules
- `OPEN_FINDINGS` and `EXCEPTION_RATE`

The sentence comes from `V_OBJECT_SUMMARY_LATEST`, e.g. *"SITE as at 26-Sep-2026: 7 active rules; of 1,546 sites evaluated, 118 have at least one exception (7.6%)"*. Week-on-week trends come from `V_RULE_TREND` and `V_OBJECT_TREND`.

**Site scoring.** For SITE-scope rules that succeeded in this run:
- A rule is applicable to a site if the site appears in the rule's evaluated set.
- A rule is failed for a site if the site has at least one `OPEN` finding for it.
- `WEIGHTED_SCORE = 100 × (1 − Σ weight(failed) / Σ weight(applicable))`, or `NULL` if nothing is applicable.
- Also record `OPEN_FINDINGS` and `CRITICAL_OPEN` counts.
Keep evaluated sets per rule in a temp table during the run to support the "applicable" calculation. Don't persist them.

### 10.4 Narratives (Cortex, read-only)

For each active owner with at least one active rule:

1. Build an aggregated input object:
   - per-rule counts and change versus the previous run
   - new and resolved findings
   - the top 10 sites by critical and high open findings (names only)
   - the 5 oldest open findings (rule, site, age)
   - source freshness

   No `DETAIL` payloads and no free text from source records.
2. Execute `SELECT SNOWFLAKE.CORTEX.COMPLETE(%(model)s, %(prompt)s)`, or `AI_COMPLETE` if that's current in the account, with bound parameters. The model comes from `DQ_CONFIG.CORTEX_MODEL` and the prompt from `prompts/narrative_v<N>.md`.
3. Prompt requirements:
   - 150–250 words in New Zealand English, factual, for a data owner
   - lead with what changed and what needs attention
   - only claim what the numbers show; any suggested cause is labelled as a possibility
   - no invented figures
4. Store the result with `INPUT_SUMMARY`. On error, set `STATUS = 'ERRORED'` and continue.

### 10.5 Validate and preview

**`dq validate CODE`** renders, lints and executes the rule, then returns JSON only:
- `ok` and `errors[]`
- `columns` (names and types)
- `records_evaluated`, `records_failed`, `items_evaluated` and `items_failed`
- `duplicate_record_items` (duplicate `(RECORD_ID, ITEM_KEY)` pairs), `null_record_ids`, `null_is_valid` and `null_site_ids` (the last only for SITE scope)
- `item_key_used` (whether any row has a non-null `ITEM_KEY`). This must match whether the rule has an `ITEM_LABEL`.
- `duration_ms` and `query_id`

It fails on a missing column, an incompatible type, a non-zero null or duplicate count, or a timeout. The result is written to `DQ_RULE.VALIDATION_RESULT` along with a `VALIDATED` history row. **It never prints rows.**

**`dq preview CODE`** (used by the Streamlit app, denied to Claude Code) runs validation, then returns up to `PREVIEW_SAMPLE_SIZE` failing rows plus `RECORD_URL`, and updates the `PREVIEW_*` columns.

### 10.6 Streamlit app (local, facilitated sessions)

The app gives owners the full experience while Kevin drives.

**Acting as:** a sidebar selector chooses the owner from `DQ_OWNER`. All actions record `CHANGED_BY` / `SET_BY` as that owner and `FACILITATED_BY` as the OS user. Approver-only actions require the acting-as owner to have `IS_APPROVER`. A visible banner shows "Facilitated session — acting as <name>".

Every state change writes a `DQ_RULE_HISTORY` row with a full snapshot (`to_json` of the row).

**Page 1 — My rules**
- An editable grid of the acting-as owner's `DRAFT` / `NEEDS_CLARIFICATION` rules, with later states read-only.
- Dropdowns for domain, target object (from `sources.yml`), scope, dimension and severity. Record and item labels default from `sources.yml` and are editable.
- A column picker on the rule form lets owners search the catalogue by label (e.g. "acquisition") and insert milestone or field labels into the statement. This avoids ambiguity and saves the owner typing 100+ column names.
- "Add rule" generates the next code for the domain.
- Editing a statement on a compiled or later rule resets it to `DRAFT`, bumps the version and clears approvals.

**Page 2 — Rule detail and preview**
- Shows the statement, the clarification Q&A (the owner answers here), and the compiled SQL (read-only for non-approvers), with the rendered SQL available in an expander.
- **Preview** shows counts and sample failing rows with deep links.
- **"Yes, this is what I meant"** sets `OWNER_CONFIRMED`. **"Not right"** returns the rule to `DRAFT` with a required comment.

**Page 3 — Approvals** (approvers only)
- Shows the SQL diff against the previous version and the validation result.
- Approve sets `ACTIVE`. Reject returns the rule to `COMPILED` with a comment. Approvers can also retire a rule with a reason.

**Page 4 — My findings**
- Open findings with filters and deep links.
- Set or revoke dispositions with a required reason. Exceptions default to `EXCEPTION_DEFAULT_DAYS` expiry.
- Marking a false positive prompts "flag rule for review".

**Page 5 — Run and results**
- "Run checks now" (in-process engine run) and the latest run summary.
- The object headline sentences, then the per-rule headline sentences with a record/item toggle, a week-on-week sparkline per rule, the site score table and the owner narrative.
- This lets owners see the end-to-end loop in one session, even before Power BI is built.

**Stretch:** a "Generate check with Cortex" button on page 2, using `prompts/compile_rule_v1.md` plus `SOURCE_COLUMN` metadata, followed by validate.

### 10.7 Export (`dq export`)

Write each reporting view to `EXPORT_PATH` as Parquet, overwriting each run using DuckDB `COPY (SELECT * FROM <view>) TO '<file>.parquet' (FORMAT parquet)`:

| View | File |
|---|---|
| `V_OPEN_FINDINGS` | `open_findings.parquet` |
| `V_RULE_TREND` | `rule_trend.parquet` |
| `V_RULE_SUMMARY_LATEST` | `rule_summary_latest.parquet` |
| `V_OBJECT_TREND` | `object_trend.parquet` |
| `V_OBJECT_SUMMARY_LATEST` | `object_summary_latest.parquet` |
| `V_SITE_SCORE_LATEST` | `site_score_latest.parquet` |
| `V_SITE_SCORE_TREND` | `site_score_trend.parquet` |
| `V_OWNER_NARRATIVE_LATEST` | `owner_narrative_latest.parquet` |
| `V_SOURCE_FRESHNESS_LATEST` | `source_freshness_latest.parquet` |
| `V_RULES` | `rules.parquet` |

Write to a temp file and then rename, so a sync never picks up a half-written file.

### 10.8 Rule compilation workflow (`.claude/commands/compile-rules.md`)

1. `dq rule list --status DRAFT` (plus `NEEDS_CLARIFICATION` rules that have an answer).
2. Load context: `dq catalogue show` for each relevant source, `docs/data-dictionary.md`, and existing `rules/sql/*.sql` for style.
3. For each rule:
   - **If ambiguous** (an undefined term such as "active" or "operational", a missing threshold, or an unclear relationship), run `dq rule clarify CODE --question "..."` with a specific question. Don't guess.
   - **Otherwise**, write `rules/sql/<CODE>.sql` using `{{ src.X }}`, with a header quoting the statement and version. Then run `dq rule set-sql CODE --file rules/sql/<CODE>.sql`, which lints, validates and sets `COMPILED`.
   - On validation failure, fix and retry up to 3 times, then flag the rule to the developer.
4. Print a summary of rules compiled, needing clarification, and failed.
5. The developer commits `rules/sql/` in a PR.

Heuristics:
- Map milestone and field **labels** in the statement to column API names with `dq catalogue show --search`. If a label matches several columns, or none, ask a clarification question rather than guessing.
- **Wide tables:** for rules naming specific milestones, use the `UNION ALL` unpivot pattern in §9, with `ITEM_KEY` set to the milestone label. Set the rule's `ITEM_LABEL`.
- **One-to-many related tables** (e.g. equipment per site): use `EXISTS` or a pre-aggregated CTE, never a plain join, so `RECORD_ID` stays unique.
- **Conditional rules:** put the condition in `WHERE`, so the denominator is the applicable population. Don't return non-applicable records as passing.
- Template scoping: join through the project to `PROJECT_TEMPLATE` and filter on template name, as given by the owner, rather than hard-coding template IDs.
- Rule codes are object-based (`SITE-nnn`, `MS-nnn`, `PRJ-nnn`), assigned by the app.
- Use "active" and other definitions from the data dictionary; ask if one is missing.
- Use `COALESCE` so `IS_VALID` is never NULL.
- Prefer base fields to formula fields, since Fivetran formula values can be stale. Comment when a formula field is unavoidable.

## 11. Testing

- **Unit tests (no Snowflake):**
  - the lifecycle table in §10.2, row by row
  - scoring maths
  - the contract validator
  - the renderer and linter, including bypass attempts such as three-part names, the raw DB name and comments hiding a semicolon
  - the URL builder
  - export atomicity
- **Engine tests with a `FakeExecutor`** that returns canned Arrow tables per rule:
  - run, fix a record, run again: resolved
  - break it again: reopened
  - set an exception, then expire it: open again
  - inject an erroring rule: others unaffected and its findings untouched
  - run twice with no change: no status changes
- **Smoke tests against Snowflake** (developer-run, not in CI): `dq catalogue refresh` and `dq validate` on one compiled rule.
- Tests use a temporary DuckDB file created from the schema file.

## 12. Store abstraction and migration path

All store access goes through the `GovernanceStore` protocol in `store/base.py`, with methods like `get_active_rules`, `record_run`, `apply_findings`, `write_scores` and `add_history`. Nothing outside `store/` imports `duckdb` directly, apart from the Arrow registration helper.

Migration to Snowflake, if the PoC succeeds:
1. Port the DDL (JSON → VARIANT, `VARCHAR[]` → ARRAY, sequences → AUTOINCREMENT, CHECKs → app-enforced).
2. Copy the tables across.
3. Turn `sources.yml` entries into real `DQ_SRC` views.
4. Add a `SnowflakeStore`.
5. Move the engine to a Snowpark procedure and Task.
6. Move the app to Streamlit in Snowflake.
7. Replace Parquet export with direct Power BI views.

**Rule SQL doesn't change,** apart from the renderer emitting `DQ_SRC.<NAME>` instead of subqueries.

## 13. Acceptance criteria

**Functional**
- [ ] 15–20 rules `ACTIVE` across site master, project milestones and cross-domain, refined with at least 2 real owners in facilitated sessions
- [ ] At least two weekly runs completed, showing trend and resolution
- [ ] At least 90% of owner-reviewed findings confirmed genuine
- [ ] Power BI owner "my findings" and site scorecard views working from SharePoint Parquet, with RLS
- [ ] A Cortex narrative per owner per run
- [ ] Per-rule and per-object headline totals (record-level default, item-level alongside) visible and trending week on week
- [ ] At least one template-scoped multi-milestone rule and one conditional cross-table rule active

**Technical**
- [ ] All unit and engine tests pass
- [ ] Nothing created in Snowflake beyond the admin setup script
- [ ] Every rule execution tagged, and PoC Snowflake and Cortex cost reported
- [ ] Claude Code permission settings in place, with no row-returning commands run by Claude Code
- [ ] A full weekly run completes in under 15 minutes
- [ ] Runbook and ADRs written, and a handover walkthrough done with the AI Solutions Specialist

## 14. Runbook topics

- Weekly run: `dq run`, check the summary, confirm the export synced, refresh Power BI
- Adding a source: update `sources.yml`, get the admin to grant `SELECT`, run `dq catalogue refresh`, consider Fivetran MAR
- A rule is flagging wrongly: mark false positives, return the rule to `DRAFT`, recompile
- A rule errored: check the query ID in Snowflake `QUERY_HISTORY` and rerun with `dq run --manual`
- Fivetran is late: check the freshness view, then skip or rerun
- Backup: `EXPORT DATABASE` weekly, after the run, to an encrypted, unsynced location
- Key rotation for `DQ_POC_SVC`
- End of PoC teardown (Snowflake script section 6, SharePoint library, local files)

## 15. Build order

1. Scaffold the project, `settings.py`, `secrets.py` and `snowflake_client.py`, and prove connectivity (`dq catalogue refresh`).
2. Build `sources.py` (renderer and lint) and `contract.py`, test-first.
3. Build `dq catalogue load-labels`, `dq validate` and `dq rule set-sql`/`clarify`, then run `/compile-rules` on the seed rules and expect clarification questions (the seed rules contain template, milestone and field placeholders on purpose).
4. Build `lifecycle.py` and `engine.py` with `FakeExecutor` tests, then run against Snowflake.
5. Add `scoring.py`.
6. Build the Streamlit pages in this order: 2, 5, 3, 1, 4.
7. Build `export.py` and the SharePoint sync, and start Power BI.
8. Add `narratives.py`.
9. Run the owner sessions and two weekly runs.
10. Measure against §13 and write the findings, including cost.

## 16. Open items

- Fivetran database, schema and table names, and field names for the in-scope objects
- The definition of "active" for sites and projects
- Template and equipment table names, and the lookup fields from milestone → project → template and site → equipment
- A field label export for the in-scope objects (especially the 100+ milestone columns), loaded with `dq catalogue load-labels`
- Which milestones matter per template: owners provide these in sessions, filling the seed rule placeholders
- Cortex model, region and cross-region inference setting
- Owners and approvers, with emails matching Entra UPNs
- SharePoint library location and membership
- Salesforce Lightning base URL
