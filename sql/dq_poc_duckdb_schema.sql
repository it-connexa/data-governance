-- =====================================================================
-- Connexa Data Governance PoC — local governance store (DuckDB)
-- ---------------------------------------------------------------------
-- Runs locally. Snowflake is used read-only for source data and Cortex.
-- Create with:
--   duckdb ~/dq-data/governance.duckdb < dq_poc_duckdb_schema.sql
--
-- Portability: identifiers and types are chosen so this ports to
-- Snowflake with minimal change (JSON -> VARIANT, VARCHAR[] -> ARRAY,
-- sequences -> AUTOINCREMENT). CHECK constraints are DuckDB-enforced
-- here; in Snowflake they become application-enforced.
--
-- DuckDB can't add or drop CHECK constraints on an existing table, so
-- only stable value sets are constrained. DOMAIN is deliberately open.
--
-- Placeholders in <ANGLE_BRACKETS>: replace the owner emails before use
-- (see setup steps), or update DQ_OWNER / DQ_RULE afterwards.
-- =====================================================================

CREATE SEQUENCE IF NOT EXISTS SEQ_RULE_ID        START 1;
CREATE SEQUENCE IF NOT EXISTS SEQ_HISTORY_ID     START 1;
CREATE SEQUENCE IF NOT EXISTS SEQ_DISPOSITION_ID START 1;

-- ---------------------------------------------------------------------
-- 1. Reference tables
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_CONFIG (
    CONFIG_KEY    VARCHAR PRIMARY KEY,
    CONFIG_VALUE  VARCHAR NOT NULL,
    DESCRIPTION   VARCHAR
);

CREATE TABLE IF NOT EXISTS DQ_SEVERITY (
    SEVERITY      VARCHAR PRIMARY KEY CHECK (SEVERITY IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    WEIGHT        INTEGER NOT NULL,
    SORT_ORDER    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS DQ_OWNER (
    OWNER_EMAIL   VARCHAR PRIMARY KEY,          -- must match Entra UPN for Power BI RLS
    DISPLAY_NAME  VARCHAR NOT NULL,
    DOMAINS       VARCHAR[],                    -- e.g. ['SITE_MASTER','PROJECT_MILESTONE']
    IS_APPROVER   BOOLEAN NOT NULL DEFAULT FALSE,
    IS_ACTIVE     BOOLEAN NOT NULL DEFAULT TRUE
);

-- Column metadata for in-scope Snowflake sources, refreshed by
-- `dq catalogue refresh`. Claude Code compiles rules from this table,
-- never from Snowflake directly.
CREATE TABLE IF NOT EXISTS SOURCE_COLUMN (
    SOURCE_NAME       VARCHAR NOT NULL,         -- logical name from sources.yml (e.g. SITE)
    COLUMN_NAME       VARCHAR NOT NULL,
    COLUMN_LABEL      VARCHAR,                  -- Salesforce field label (loaded from label export)
    DATA_TYPE         VARCHAR NOT NULL,
    IS_NULLABLE       BOOLEAN,
    ORDINAL_POSITION  INTEGER,
    COLUMN_COMMENT    VARCHAR,
    REFRESHED_AT      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (SOURCE_NAME, COLUMN_NAME)
);

-- ---------------------------------------------------------------------
-- 2. Rule registry
-- ---------------------------------------------------------------------
-- Lifecycle:
--   DRAFT -> NEEDS_CLARIFICATION -> DRAFT (loop until clear)
--   DRAFT -> COMPILED            (SQL written and validated)
--   COMPILED -> OWNER_CONFIRMED  (owner previewed results and agreed)
--   OWNER_CONFIRMED -> ACTIVE    (approver reviewed SQL)
--   any -> RETIRED
-- Editing RULE_STATEMENT resets to DRAFT; editing CHECK_SQL resets to
-- COMPILED. Both increment VERSION and write DQ_RULE_HISTORY.

CREATE TABLE IF NOT EXISTS DQ_RULE (
    RULE_ID                  INTEGER PRIMARY KEY DEFAULT nextval('SEQ_RULE_ID'),
    RULE_CODE                VARCHAR NOT NULL UNIQUE,     -- object-based: SITE-001, MS-003, PRJ-002
    RULE_NAME                VARCHAR NOT NULL,
    RULE_STATEMENT           VARCHAR NOT NULL,            -- plain English, owner-authored
    DOMAIN                   VARCHAR NOT NULL,            -- SITE_MASTER | PROJECT_MILESTONE | CROSS_DOMAIN | ...
    TARGET_OBJECT            VARCHAR NOT NULL,            -- logical source name from sources.yml
    RECORD_LABEL             VARCHAR NOT NULL DEFAULT 'records',  -- plural noun for headline, e.g. 'sites', 'projects'
    ITEM_LABEL               VARCHAR,                     -- set for multi-item rules, e.g. 'milestones'; NULL otherwise
    SCOPE                    VARCHAR NOT NULL CHECK (SCOPE IN ('OBJECT','SITE')),
    DIMENSION                VARCHAR NOT NULL CHECK (DIMENSION IN
                               ('COMPLETENESS','VALIDITY','CONSISTENCY','TIMELINESS','UNIQUENESS','INTEGRITY')),
    SEVERITY                 VARCHAR NOT NULL CHECK (SEVERITY IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    OWNER_EMAIL              VARCHAR NOT NULL,
    STEWARD_EMAIL            VARCHAR,
    STATUS                   VARCHAR NOT NULL DEFAULT 'DRAFT' CHECK (STATUS IN
                               ('DRAFT','NEEDS_CLARIFICATION','COMPILED','OWNER_CONFIRMED','ACTIVE','RETIRED')),

    CLARIFICATION_QUESTION   VARCHAR,
    CLARIFICATION_ANSWER     VARCHAR,

    CHECK_SQL                VARCHAR,                     -- Snowflake SQL using {{ src.NAME }} placeholders
    CHECK_SQL_GENERATED_BY   VARCHAR,                     -- 'claude-code' | 'cortex:<model>' | 'manual'
    CHECK_SQL_GENERATED_AT   TIMESTAMPTZ,
    VALIDATION_RESULT        JSON,                        -- counts and column metadata only

    PREVIEW_EVALUATED_COUNT  BIGINT,
    PREVIEW_FAILED_COUNT     BIGINT,
    PREVIEW_AT               TIMESTAMPTZ,

    OWNER_CONFIRMED_BY       VARCHAR,
    OWNER_CONFIRMED_AT       TIMESTAMPTZ,
    SQL_APPROVED_BY          VARCHAR,
    SQL_APPROVED_AT          TIMESTAMPTZ,
    RETIRED_REASON           VARCHAR,

    VERSION                  INTEGER NOT NULL DEFAULT 1,
    CREATED_BY               VARCHAR,
    CREATED_AT               TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    UPDATED_BY               VARCHAR,
    UPDATED_AT               TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    NOTES                    VARCHAR
);

-- Append-only audit. CHANGED_BY is the person the action is recorded
-- against (the "acting as" owner in facilitated sessions);
-- FACILITATED_BY is who was at the keyboard (OS user).
CREATE TABLE IF NOT EXISTS DQ_RULE_HISTORY (
    HISTORY_ID      INTEGER PRIMARY KEY DEFAULT nextval('SEQ_HISTORY_ID'),
    RULE_ID         INTEGER NOT NULL,
    RULE_CODE       VARCHAR NOT NULL,
    VERSION         INTEGER NOT NULL,
    CHANGE_TYPE     VARCHAR NOT NULL CHECK (CHANGE_TYPE IN
                      ('CREATED','STATEMENT_EDITED','CLARIFICATION','SQL_COMPILED','VALIDATED',
                       'PREVIEWED','OWNER_CONFIRMED','OWNER_REJECTED','APPROVED','APPROVAL_REJECTED','RETIRED')),
    STATUS_AFTER    VARCHAR NOT NULL,
    SNAPSHOT        JSON NOT NULL,                 -- full DQ_RULE row after the change
    CHANGED_BY      VARCHAR NOT NULL,
    FACILITATED_BY  VARCHAR,
    CHANGED_AT      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    COMMENT         VARCHAR
);

-- ---------------------------------------------------------------------
-- 3. Runs and per-rule results
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_RUN (
    RUN_ID            VARCHAR PRIMARY KEY,          -- UUID
    RUN_TYPE          VARCHAR NOT NULL CHECK (RUN_TYPE IN ('SCHEDULED','MANUAL')),
    TRIGGERED_BY      VARCHAR NOT NULL,
    STARTED_AT        TIMESTAMPTZ NOT NULL,
    FINISHED_AT       TIMESTAMPTZ,
    STATUS            VARCHAR NOT NULL CHECK (STATUS IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED')),
    RULES_ATTEMPTED   INTEGER,
    RULES_SUCCEEDED   INTEGER,
    RULES_ERRORED     INTEGER,
    ENGINE_VERSION    VARCHAR,
    SNOWFLAKE_QUERY_TAG VARCHAR,                    -- for cost tracking in QUERY_HISTORY
    ERROR_MESSAGE     VARCHAR
);

CREATE TABLE IF NOT EXISTS DQ_RUN_SOURCE_FRESHNESS (
    RUN_ID          VARCHAR NOT NULL,
    SOURCE_NAME     VARCHAR NOT NULL,
    LAST_SYNCED_AT  TIMESTAMPTZ,
    PRIMARY KEY (RUN_ID, SOURCE_NAME)
);

CREATE TABLE IF NOT EXISTS DQ_RULE_RESULT (
    RUN_ID               VARCHAR NOT NULL,
    RULE_ID              INTEGER NOT NULL,
    RULE_CODE            VARCHAR NOT NULL,
    RULE_VERSION         INTEGER NOT NULL,
    STATUS               VARCHAR NOT NULL CHECK (STATUS IN ('SUCCEEDED','ERRORED')),
    OBJECT_TOTAL         BIGINT,                    -- all records in the target source (context)
    RECORDS_EVALUATED    BIGINT,                    -- distinct RECORD_IDs the rule applies to (headline denominator)
    RECORDS_FAILED       BIGINT,                    -- distinct records with >=1 failing item (raw)
    RECORDS_FAILED_OPEN  BIGINT,                    -- as above, excluding active dispositions (headline numerator)
    PASS_RATE            DECIMAL(5,2),              -- record-level: 100 * (evaluated - failed_open) / evaluated
    ITEMS_EVALUATED      BIGINT,                    -- rows returned (= RECORDS_EVALUATED for single-item rules)
    ITEMS_FAILED         BIGINT,
    ITEMS_FAILED_OPEN    BIGINT,
    ITEM_PASS_RATE       DECIMAL(5,2),
    DURATION_MS          BIGINT,
    SNOWFLAKE_QUERY_ID   VARCHAR,
    ERROR_MESSAGE        VARCHAR,
    PRIMARY KEY (RUN_ID, RULE_ID)
);

-- Object-level rollup per run: the headline for the object view.
CREATE TABLE IF NOT EXISTS DQ_OBJECT_RESULT (
    RUN_ID                  VARCHAR NOT NULL,
    TARGET_OBJECT           VARCHAR NOT NULL,
    RECORD_LABEL            VARCHAR NOT NULL,       -- from sources.yml
    OBJECT_TOTAL            BIGINT,                 -- all records in the source
    ACTIVE_RULES            INTEGER NOT NULL,
    RULES_SUCCEEDED         INTEGER NOT NULL,
    RECORDS_EVALUATED       BIGINT NOT NULL,        -- distinct records evaluated by >=1 successful rule
    RECORDS_WITH_EXCEPTION  BIGINT NOT NULL,        -- distinct records with >=1 OPEN finding
    OPEN_FINDINGS           BIGINT NOT NULL,
    EXCEPTION_RATE          DECIMAL(5,2),           -- 100 * records_with_exception / records_evaluated
    PRIMARY KEY (RUN_ID, TARGET_OBJECT)
);

-- ---------------------------------------------------------------------
-- 4. Findings and dispositions
-- ---------------------------------------------------------------------
-- One row per (rule, record, item). ITEM_KEY is NULL for single-item rules
-- and e.g. the milestone label for multi-item rules over wide tables.
-- FINDING_ID = sha256(RULE_CODE || '|' || RECORD_ID || '|' || COALESCE(ITEM_KEY, '')),
-- stable across runs.
CREATE TABLE IF NOT EXISTS DQ_FINDING (
    FINDING_ID         VARCHAR PRIMARY KEY,
    RULE_ID            INTEGER NOT NULL,
    RULE_CODE          VARCHAR NOT NULL,
    LAST_RULE_VERSION  INTEGER NOT NULL,
    SOURCE_OBJECT      VARCHAR NOT NULL,
    RECORD_ID          VARCHAR NOT NULL,
    ITEM_KEY           VARCHAR,
    RECORD_NAME        VARCHAR,
    SITE_ID            VARCHAR,
    SITE_NAME          VARCHAR,
    RECORD_URL         VARCHAR,
    DETAIL             JSON,
    STATUS             VARCHAR NOT NULL CHECK (STATUS IN ('OPEN','RESOLVED','ACCEPTED_EXCEPTION','FALSE_POSITIVE')),
    RESOLUTION_REASON  VARCHAR CHECK (RESOLUTION_REASON IS NULL OR RESOLUTION_REASON IN ('FIXED','OUT_OF_SCOPE','RULE_RETIRED')),
    FIRST_SEEN_RUN_ID  VARCHAR NOT NULL,
    FIRST_SEEN_AT      TIMESTAMPTZ NOT NULL,
    LAST_SEEN_RUN_ID   VARCHAR NOT NULL,
    LAST_SEEN_AT       TIMESTAMPTZ NOT NULL,
    RESOLVED_RUN_ID    VARCHAR,
    RESOLVED_AT        TIMESTAMPTZ,
    TIMES_SEEN         INTEGER NOT NULL DEFAULT 1,
    REOPENED_COUNT     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS DQ_FINDING_DISPOSITION (
    DISPOSITION_ID  INTEGER PRIMARY KEY DEFAULT nextval('SEQ_DISPOSITION_ID'),
    FINDING_ID      VARCHAR NOT NULL,
    DISPOSITION     VARCHAR NOT NULL CHECK (DISPOSITION IN ('ACCEPTED_EXCEPTION','FALSE_POSITIVE','REVOKED')),
    REASON          VARCHAR NOT NULL,
    SET_BY          VARCHAR NOT NULL,
    FACILITATED_BY  VARCHAR,
    SET_AT          TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    EXPIRES_AT      TIMESTAMPTZ,                   -- app requires this for ACCEPTED_EXCEPTION
    CHECK (DISPOSITION <> 'ACCEPTED_EXCEPTION' OR EXPIRES_AT IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- 5. Scores and narratives
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DQ_SITE_SCORE (
    RUN_ID            VARCHAR NOT NULL,
    SITE_ID           VARCHAR NOT NULL,
    SITE_NAME         VARCHAR,
    RULES_APPLICABLE  INTEGER NOT NULL,
    RULES_FAILED      INTEGER NOT NULL,
    OPEN_FINDINGS     INTEGER NOT NULL,
    CRITICAL_OPEN     INTEGER NOT NULL,
    WEIGHTED_SCORE    DECIMAL(5,2),                -- 0–100; NULL if no applicable rules
    SCORED_AT         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (RUN_ID, SITE_ID)
);

CREATE TABLE IF NOT EXISTS DQ_NARRATIVE (
    RUN_ID          VARCHAR NOT NULL,
    OWNER_EMAIL     VARCHAR NOT NULL,
    NARRATIVE       VARCHAR,
    MODEL           VARCHAR,
    PROMPT_VERSION  VARCHAR,
    INPUT_SUMMARY   JSON,                          -- aggregated facts sent to Cortex (audit)
    STATUS          VARCHAR NOT NULL CHECK (STATUS IN ('SUCCEEDED','ERRORED')),
    ERROR_MESSAGE   VARCHAR,
    GENERATED_AT    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (RUN_ID, OWNER_EMAIL)
);

-- ---------------------------------------------------------------------
-- 6. Reporting views (exported to Parquet for Power BI by `dq export`)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW V_LATEST_RUN AS
SELECT *
FROM DQ_RUN
WHERE STATUS IN ('SUCCEEDED','PARTIAL')
QUALIFY ROW_NUMBER() OVER (ORDER BY STARTED_AT DESC) = 1;

CREATE OR REPLACE VIEW V_OPEN_FINDINGS AS
SELECT
    f.FINDING_ID,
    f.RULE_CODE,
    r.RULE_NAME,
    r.DOMAIN,
    r.DIMENSION,
    r.SEVERITY,
    s.SORT_ORDER                                          AS SEVERITY_SORT,
    r.OWNER_EMAIL,                                        -- Power BI RLS key
    f.SOURCE_OBJECT,
    f.RECORD_ID,
    f.ITEM_KEY,
    f.RECORD_NAME,
    f.SITE_ID,
    f.SITE_NAME,
    f.RECORD_URL,
    CAST(f.DETAIL AS VARCHAR)                             AS DETAIL,
    f.FIRST_SEEN_AT,
    f.LAST_SEEN_AT,
    date_diff('day', f.FIRST_SEEN_AT, current_timestamp)  AS AGE_DAYS,
    f.REOPENED_COUNT
FROM DQ_FINDING f
JOIN DQ_RULE r     ON r.RULE_ID = f.RULE_ID
JOIN DQ_SEVERITY s ON s.SEVERITY = r.SEVERITY
WHERE f.STATUS = 'OPEN';

CREATE OR REPLACE VIEW V_RULE_TREND AS
SELECT
    rr.RUN_ID,
    ru.STARTED_AT          AS RUN_AT,
    rr.RULE_CODE,
    r.RULE_NAME,
    r.DOMAIN,
    r.DIMENSION,
    r.SEVERITY,
    r.OWNER_EMAIL,
    r.TARGET_OBJECT,
    r.RECORD_LABEL,
    r.ITEM_LABEL,
    rr.OBJECT_TOTAL,
    rr.RECORDS_EVALUATED,
    rr.RECORDS_FAILED_OPEN,
    round(100.0 * rr.RECORDS_FAILED_OPEN / NULLIF(rr.RECORDS_EVALUATED, 0), 1) AS EXCEPTION_RATE,
    rr.PASS_RATE,
    rr.ITEMS_EVALUATED,
    rr.ITEMS_FAILED_OPEN,
    round(100.0 * rr.ITEMS_FAILED_OPEN / NULLIF(rr.ITEMS_EVALUATED, 0), 1) AS ITEM_EXCEPTION_RATE,
    rr.ITEM_PASS_RATE
FROM DQ_RULE_RESULT rr
JOIN DQ_RUN ru ON ru.RUN_ID = rr.RUN_ID
JOIN DQ_RULE r ON r.RULE_ID = rr.RULE_ID
WHERE rr.STATUS = 'SUCCEEDED';

-- Latest result per rule with a deterministic headline sentence, e.g.
-- "SITE-134 as at 26-Sep-2026: of 1,546 sites, 45 exceptions (2.9%)"
-- "MS-001 as at 26-Sep-2026: of 1,546 projects, 45 exceptions (2.9%); 61 of 4,638 milestones (1.3%)"
CREATE OR REPLACE VIEW V_RULE_SUMMARY_LATEST AS
SELECT
    t.*,
    t.RULE_CODE || ' as at ' || strftime(timezone('Pacific/Auckland', t.RUN_AT), '%d-%b-%Y')
        || ': of ' || format('{:,}', t.RECORDS_EVALUATED) || ' ' || t.RECORD_LABEL
        || ', ' || format('{:,}', t.RECORDS_FAILED_OPEN) || ' exceptions ('
        || CAST(COALESCE(t.EXCEPTION_RATE, 0) AS VARCHAR) || '%)'
        || CASE WHEN t.ITEM_LABEL IS NOT NULL THEN
               '; ' || format('{:,}', t.ITEMS_FAILED_OPEN) || ' of ' || format('{:,}', t.ITEMS_EVALUATED)
               || ' ' || t.ITEM_LABEL || ' (' || CAST(COALESCE(t.ITEM_EXCEPTION_RATE, 0) AS VARCHAR) || '%)'
           ELSE '' END                                   AS SUMMARY_TEXT
FROM V_RULE_TREND t
JOIN V_LATEST_RUN lr ON lr.RUN_ID = t.RUN_ID;

CREATE OR REPLACE VIEW V_OBJECT_TREND AS
SELECT o.*, ru.STARTED_AT AS RUN_AT
FROM DQ_OBJECT_RESULT o
JOIN DQ_RUN ru ON ru.RUN_ID = o.RUN_ID;

-- e.g. "SITE as at 26-Sep-2026: 7 active rules; of 1,546 sites evaluated, 118 have at least one exception (7.6%)"
CREATE OR REPLACE VIEW V_OBJECT_SUMMARY_LATEST AS
SELECT
    o.*,
    o.TARGET_OBJECT || ' as at ' || strftime(timezone('Pacific/Auckland', o.RUN_AT), '%d-%b-%Y')
        || ': ' || CAST(o.ACTIVE_RULES AS VARCHAR) || ' active rules; of '
        || format('{:,}', o.RECORDS_EVALUATED) || ' ' || o.RECORD_LABEL || ' evaluated, '
        || format('{:,}', o.RECORDS_WITH_EXCEPTION) || ' have at least one exception ('
        || CAST(CAST(round(COALESCE(o.EXCEPTION_RATE, 0), 1) AS DOUBLE) AS VARCHAR) || '%)'   AS SUMMARY_TEXT
FROM V_OBJECT_TREND o
JOIN V_LATEST_RUN lr ON lr.RUN_ID = o.RUN_ID;

CREATE OR REPLACE VIEW V_SITE_SCORE_LATEST AS
SELECT ss.*
FROM DQ_SITE_SCORE ss
JOIN V_LATEST_RUN lr ON lr.RUN_ID = ss.RUN_ID;

CREATE OR REPLACE VIEW V_SITE_SCORE_TREND AS
SELECT ss.*, ru.STARTED_AT AS RUN_AT
FROM DQ_SITE_SCORE ss
JOIN DQ_RUN ru ON ru.RUN_ID = ss.RUN_ID;

CREATE OR REPLACE VIEW V_OWNER_NARRATIVE_LATEST AS
SELECT n.RUN_ID, n.OWNER_EMAIL, n.NARRATIVE, n.MODEL, n.PROMPT_VERSION, n.GENERATED_AT
FROM DQ_NARRATIVE n
JOIN V_LATEST_RUN lr ON lr.RUN_ID = n.RUN_ID
WHERE n.STATUS = 'SUCCEEDED';

CREATE OR REPLACE VIEW V_SOURCE_FRESHNESS_LATEST AS
SELECT fr.SOURCE_NAME, fr.LAST_SYNCED_AT, lr.STARTED_AT AS RUN_AT
FROM DQ_RUN_SOURCE_FRESHNESS fr
JOIN V_LATEST_RUN lr ON lr.RUN_ID = fr.RUN_ID;

CREATE OR REPLACE VIEW V_RULES AS
SELECT RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT, RECORD_LABEL, ITEM_LABEL, SCOPE,
       DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, VERSION, UPDATED_AT
FROM DQ_RULE
WHERE STATUS <> 'RETIRED';

-- ---------------------------------------------------------------------
-- 7. Seed data
-- ---------------------------------------------------------------------
INSERT INTO DQ_SEVERITY (SEVERITY, WEIGHT, SORT_ORDER)
SELECT v.* FROM (VALUES
    ('CRITICAL', 5, 1),
    ('HIGH',     3, 2),
    ('MEDIUM',   2, 3),
    ('LOW',      1, 4)
) v(SEVERITY, WEIGHT, SORT_ORDER)
WHERE NOT EXISTS (SELECT 1 FROM DQ_SEVERITY s WHERE s.SEVERITY = v.SEVERITY);

INSERT INTO DQ_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION)
SELECT v.* FROM (VALUES
    ('SF_BASE_URL',              '<SF_BASE_URL>',  'Salesforce Lightning base URL for record deep links'),
    ('CORTEX_MODEL',             '<CORTEX_MODEL>', 'Cortex model for narratives'),
    ('NARRATIVE_PROMPT_VERSION', 'v1',             'prompts/narrative_v1.md'),
    ('RULE_TIMEOUT_SECONDS',     '120',            'Max runtime per rule in Snowflake before ERRORED'),
    ('PREVIEW_SAMPLE_SIZE',      '10',             'Failing records shown in preview'),
    ('EXCEPTION_DEFAULT_DAYS',   '365',            'Default expiry for accepted exceptions'),
    ('EXPORT_PATH',              '<EXPORT_PATH>',  'Folder synced to the SharePoint library for Power BI')
) v(CONFIG_KEY, CONFIG_VALUE, DESCRIPTION)
WHERE NOT EXISTS (SELECT 1 FROM DQ_CONFIG c WHERE c.CONFIG_KEY = v.CONFIG_KEY);

-- Owners: replace placeholders (see setup steps).
INSERT INTO DQ_OWNER (OWNER_EMAIL, DISPLAY_NAME, DOMAINS, IS_APPROVER)
SELECT v.* FROM (VALUES
    ('<owner.site@connexa.co.nz>',     '<Site data owner>',    ['SITE_MASTER','CROSS_DOMAIN'],                    FALSE),
    ('<owner.projects@connexa.co.nz>', '<Project data owner>', ['PROJECT_MILESTONE','CROSS_DOMAIN'],              FALSE),
    ('<approver@connexa.co.nz>',       '<Tech approver>',      ['SITE_MASTER','PROJECT_MILESTONE','CROSS_DOMAIN'], TRUE)
) v(OWNER_EMAIL, DISPLAY_NAME, DOMAINS, IS_APPROVER)
WHERE NOT EXISTS (SELECT 1 FROM DQ_OWNER o WHERE o.OWNER_EMAIL = v.OWNER_EMAIL);

-- Starter rules: plain English only, DRAFT, no SQL. Wording is deliberately
-- loose ("active", "operational") and uses <PLACEHOLDERS> for templates,
-- milestones and fields, so compilation raises clarification questions
-- that owners answer in facilitated sessions. Codes are object-based.
INSERT INTO DQ_RULE (RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT, RECORD_LABEL, ITEM_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, CREATED_BY)
SELECT v.* FROM (VALUES
  -- Site
  ('SITE-001', 'Operational sites have a commissioning date',
   'Every site with an operational status must have a commissioning date recorded.',
   'SITE_MASTER', 'SITE', 'sites', NULL, 'SITE', 'CONSISTENCY', 'HIGH', '<owner.site@connexa.co.nz>', 'seed'),
  ('SITE-002', 'Decommissioned sites have a decommission date',
   'Every site with a decommissioned status must have a decommission date, and it must not be in the future.',
   'SITE_MASTER', 'SITE', 'sites', NULL, 'SITE', 'CONSISTENCY', 'MEDIUM', '<owner.site@connexa.co.nz>', 'seed'),
  ('SITE-003', 'Site coordinates are within New Zealand',
   'Every active site must have latitude and longitude populated, and the coordinates must fall within New Zealand (including offshore islands in scope).',
   'SITE_MASTER', 'SITE', 'sites', NULL, 'SITE', 'VALIDITY', 'HIGH', '<owner.site@connexa.co.nz>', 'seed'),
  ('SITE-004', 'No duplicate active sites',
   'No two active sites should share the same site identifier, or have coordinates within 10 metres of each other.',
   'SITE_MASTER', 'SITE', 'sites', NULL, 'SITE', 'UNIQUENESS', 'HIGH', '<owner.site@connexa.co.nz>', 'seed'),
  ('SITE-005', 'Mandatory site attributes populated',
   'Every active site must have structure type, region and landowner populated.',
   'SITE_MASTER', 'SITE', 'sites', NULL, 'SITE', 'COMPLETENESS', 'MEDIUM', '<owner.site@connexa.co.nz>', 'seed'),
  ('SITE-006', 'Lightpole sites with <EQUIPMENT_FIELD> equipment have <SITE_FIELD>',
   'If a site''s tower type is Lightpole and any related equipment record has <EQUIPMENT_FIELD> populated, then the site''s <SITE_FIELD> must not be blank.',
   'SITE_MASTER', 'SITE', 'sites', NULL, 'SITE', 'COMPLETENESS', 'MEDIUM', '<owner.site@connexa.co.nz>', 'seed'),
  ('SITE-007', 'Operational sites have commissioning milestone actual',
   'A site with an operational status must not have a build project whose commissioning milestone has no actual date.',
   'CROSS_DOMAIN', 'SITE', 'sites', NULL, 'SITE', 'CONSISTENCY', 'HIGH', '<owner.site@connexa.co.nz>', 'seed'),
  -- Milestone (wide table: one row per project, milestones as columns)
  ('MS-001', 'No stale forecasts on key <TEMPLATE_NAME> milestones',
   'For active projects using the <TEMPLATE_NAME> template, the milestones <MILESTONE_A>, <MILESTONE_B> and <MILESTONE_C> must not have a forecast date in the past unless an actual date has been recorded.',
   'PROJECT_MILESTONE', 'MILESTONE', 'projects', 'milestones', 'SITE', 'TIMELINESS', 'HIGH', '<owner.projects@connexa.co.nz>', 'seed'),
  ('MS-002', 'Milestone actuals respect sequence on <TEMPLATE_NAME>',
   'For projects using the <TEMPLATE_NAME> template, the actual date of each of these milestones must not be earlier than the actual date of the one before it in this list: <MILESTONE_A>, <MILESTONE_B>, <MILESTONE_C>.',
   'PROJECT_MILESTONE', 'MILESTONE', 'projects', 'milestones', 'SITE', 'CONSISTENCY', 'MEDIUM', '<owner.projects@connexa.co.nz>', 'seed'),
  ('MS-003', 'No actual dates in the future',
   'No milestone actual date on any project may be later than today.',
   'PROJECT_MILESTONE', 'MILESTONE', 'projects', 'milestones', 'SITE', 'VALIDITY', 'HIGH', '<owner.projects@connexa.co.nz>', 'seed'),
  -- Project
  ('PRJ-001', 'Completed projects have closed status',
   'If a project''s final milestone (<FINAL_MILESTONE> for the <TEMPLATE_NAME> template) has an actual date, the project status must reflect that it is complete or closed.',
   'PROJECT_MILESTONE', 'PROJECT', 'projects', NULL, 'SITE', 'CONSISTENCY', 'MEDIUM', '<owner.projects@connexa.co.nz>', 'seed'),
  ('PRJ-002', 'No active projects on decommissioned sites',
   'No active project should be linked to a site with a decommissioned status.',
   'CROSS_DOMAIN', 'PROJECT', 'projects', NULL, 'SITE', 'INTEGRITY', 'HIGH', '<owner.projects@connexa.co.nz>', 'seed')
) v(RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT, RECORD_LABEL, ITEM_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, CREATED_BY)
WHERE NOT EXISTS (SELECT 1 FROM DQ_RULE r WHERE r.RULE_CODE = v.RULE_CODE);

-- Record creation of seeded rules in history.
INSERT INTO DQ_RULE_HISTORY (RULE_ID, RULE_CODE, VERSION, CHANGE_TYPE, STATUS_AFTER, SNAPSHOT, CHANGED_BY, COMMENT)
SELECT r.RULE_ID, r.RULE_CODE, r.VERSION, 'CREATED', r.STATUS, to_json(r), 'seed', 'Seeded starter rule'
FROM DQ_RULE r
WHERE r.CREATED_BY = 'seed'
  AND NOT EXISTS (SELECT 1 FROM DQ_RULE_HISTORY h WHERE h.RULE_ID = r.RULE_ID AND h.CHANGE_TYPE = 'CREATED');
