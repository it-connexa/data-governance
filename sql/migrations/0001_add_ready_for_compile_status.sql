-- Migration 0001: allow STATUS = 'READY_FOR_COMPILE' on DQ_RULE, and
-- CHANGE_TYPE = 'SUBMITTED' on DQ_RULE_HISTORY.
--
-- DuckDB can't alter a CHECK constraint in place (see the note at the top
-- of dq_poc_duckdb_schema.sql), so this recreates both tables with the
-- wider CHECKs and copies every row across by name. Primary key values
-- are preserved exactly (they're supplied by the copy, not the DEFAULT),
-- and the sequences are untouched, so new rows keep numbering from where
-- they left off. No other table has a foreign key on either one, so
-- nothing else needs to change.
--
-- Run this once, against the real store (Kevin only -- Claude Code never
-- touches ~/dq-data/**):
--   cp ~/dq-data/governance.duckdb ~/dq-data/governance.duckdb.bak-$(date +%Y%m%d)
--   duckdb ~/dq-data/governance.duckdb < sql/migrations/0001_add_ready_for_compile_status.sql

BEGIN TRANSACTION;

ALTER TABLE DQ_RULE RENAME TO DQ_RULE_PRE_0001;

CREATE TABLE DQ_RULE (
    RULE_ID                  INTEGER PRIMARY KEY DEFAULT nextval('SEQ_RULE_ID'),
    RULE_CODE                VARCHAR NOT NULL UNIQUE,
    RULE_NAME                VARCHAR NOT NULL,
    RULE_STATEMENT           VARCHAR NOT NULL,
    DOMAIN                   VARCHAR NOT NULL,
    TARGET_OBJECT            VARCHAR NOT NULL,
    RECORD_LABEL             VARCHAR NOT NULL DEFAULT 'records',
    ITEM_LABEL               VARCHAR,
    SCOPE                    VARCHAR NOT NULL CHECK (SCOPE IN ('OBJECT','SITE')),
    DIMENSION                VARCHAR NOT NULL CHECK (DIMENSION IN
                               ('COMPLETENESS','VALIDITY','CONSISTENCY','TIMELINESS','UNIQUENESS','INTEGRITY')),
    SEVERITY                 VARCHAR NOT NULL CHECK (SEVERITY IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    OWNER_EMAIL              VARCHAR NOT NULL,
    STEWARD_EMAIL            VARCHAR,
    STATUS                   VARCHAR NOT NULL DEFAULT 'DRAFT' CHECK (STATUS IN
                               ('DRAFT','NEEDS_CLARIFICATION','READY_FOR_COMPILE','COMPILED',
                                'OWNER_CONFIRMED','ACTIVE','RETIRED')),

    CLARIFICATION_QUESTION   VARCHAR,
    CLARIFICATION_ANSWER     VARCHAR,

    CHECK_SQL                VARCHAR,
    CHECK_SQL_GENERATED_BY   VARCHAR,
    CHECK_SQL_GENERATED_AT   TIMESTAMPTZ,
    VALIDATION_RESULT        JSON,

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

INSERT INTO DQ_RULE BY NAME SELECT * FROM DQ_RULE_PRE_0001;

DROP TABLE DQ_RULE_PRE_0001;

ALTER TABLE DQ_RULE_HISTORY RENAME TO DQ_RULE_HISTORY_PRE_0001;

CREATE TABLE DQ_RULE_HISTORY (
    HISTORY_ID      INTEGER PRIMARY KEY DEFAULT nextval('SEQ_HISTORY_ID'),
    RULE_ID         INTEGER NOT NULL,
    RULE_CODE       VARCHAR NOT NULL,
    VERSION         INTEGER NOT NULL,
    CHANGE_TYPE     VARCHAR NOT NULL CHECK (CHANGE_TYPE IN
                      ('CREATED','STATEMENT_EDITED','CLARIFICATION','SUBMITTED','SQL_COMPILED','VALIDATED',
                       'PREVIEWED','OWNER_CONFIRMED','OWNER_REJECTED','APPROVED','APPROVAL_REJECTED','RETIRED')),
    STATUS_AFTER    VARCHAR NOT NULL,
    SNAPSHOT        JSON NOT NULL,
    CHANGED_BY      VARCHAR NOT NULL,
    FACILITATED_BY  VARCHAR,
    CHANGED_AT      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    COMMENT         VARCHAR
);

INSERT INTO DQ_RULE_HISTORY BY NAME SELECT * FROM DQ_RULE_HISTORY_PRE_0001;

DROP TABLE DQ_RULE_HISTORY_PRE_0001;

COMMIT;
