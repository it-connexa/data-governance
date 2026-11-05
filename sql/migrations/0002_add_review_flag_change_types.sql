-- Migration 0002: allow CHANGE_TYPE = 'REVIEW_FLAGGED' and 'REVIEW_CLEARED'
-- on DQ_RULE_HISTORY. An owner who marks a finding as a false positive can
-- flag the rule for review (brief §10.6, page 4); the flag is recorded as a
-- history row rather than a new column on DQ_RULE.
--
-- DuckDB can't alter a CHECK constraint in place (see the note at the top
-- of dq_poc_duckdb_schema.sql), so this recreates DQ_RULE_HISTORY with the
-- wider CHECK and copies every row across by name. HISTORY_ID values are
-- preserved exactly (supplied by the copy, not the DEFAULT) and the
-- sequence is untouched, so new rows keep numbering from where they left
-- off. No other table or view depends on DQ_RULE_HISTORY.
--
-- Run this once, against the real store (Kevin only -- Claude Code never
-- touches ~/dq-data/**):
--   cp ~/dq-data/governance.duckdb ~/dq-data/governance.duckdb.bak-$(date +%Y%m%d)
--   duckdb ~/dq-data/governance.duckdb < sql/migrations/0002_add_review_flag_change_types.sql

BEGIN TRANSACTION;

ALTER TABLE DQ_RULE_HISTORY RENAME TO DQ_RULE_HISTORY_PRE_0002;

CREATE TABLE DQ_RULE_HISTORY (
    HISTORY_ID      INTEGER PRIMARY KEY DEFAULT nextval('SEQ_HISTORY_ID'),
    RULE_ID         INTEGER NOT NULL,
    RULE_CODE       VARCHAR NOT NULL,
    VERSION         INTEGER NOT NULL,
    CHANGE_TYPE     VARCHAR NOT NULL CHECK (CHANGE_TYPE IN
                      ('CREATED','STATEMENT_EDITED','CLARIFICATION','SUBMITTED','SQL_COMPILED','VALIDATED',
                       'PREVIEWED','OWNER_CONFIRMED','OWNER_REJECTED','APPROVED','APPROVAL_REJECTED','RETIRED',
                       'REVIEW_FLAGGED','REVIEW_CLEARED')),
    STATUS_AFTER    VARCHAR NOT NULL,
    SNAPSHOT        JSON NOT NULL,
    CHANGED_BY      VARCHAR NOT NULL,
    FACILITATED_BY  VARCHAR,
    CHANGED_AT      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    COMMENT         VARCHAR
);

INSERT INTO DQ_RULE_HISTORY BY NAME SELECT * FROM DQ_RULE_HISTORY_PRE_0002;

DROP TABLE DQ_RULE_HISTORY_PRE_0002;

COMMIT;
