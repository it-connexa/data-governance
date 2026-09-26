-- =====================================================================
-- Connexa Data Governance PoC — Snowflake read-only access
-- ---------------------------------------------------------------------
-- For the Snowflake account administrator to run.
--
-- What this creates in prod:
--   * one role (DQ_POC_READER): SELECT on named Fivetran tables + Cortex
--   * one service user (DQ_POC_SVC): key-pair auth only, no password
--   * one XS warehouse with a resource monitor
-- What it does NOT create: no databases, schemas, tables, views,
-- procedures, tasks or stages. The role cannot write anywhere.
--
-- Placeholders:
--   <FIVETRAN_DB>, <SALESFORCE_SCHEMA>, <SITE_TABLE>, <PROJECT_TABLE>,
--   <MILESTONE_TABLE>, <PROJECT_TEMPLATE_TABLE>, <EQUIPMENT_TABLE>
--                       from sources.yml
--   <RSA_PUBLIC_KEY>    public key body from Kevin (no BEGIN/END lines)
--   <CREDIT_QUOTA>      monthly warehouse credit cap, e.g. 10
--   <NOTIFY_EMAIL>      optional, for resource monitor alerts
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Role
-- ---------------------------------------------------------------------
USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS DQ_POC_READER
    COMMENT = 'Data governance PoC: read-only SELECT on in-scope Fivetran tables + Cortex. Owner: Head of Technology.';

-- Keep it out of the general role hierarchy except for administration
GRANT ROLE DQ_POC_READER TO ROLE SYSADMIN;

-- ---------------------------------------------------------------------
-- 2. Warehouse with cost guardrails
-- ---------------------------------------------------------------------
USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS DQ_POC_WH
    WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    STATEMENT_TIMEOUT_IN_SECONDS = 300
    COMMENT = 'Data governance PoC — read-only rule checks';

GRANT USAGE ON WAREHOUSE DQ_POC_WH TO ROLE DQ_POC_READER;

USE ROLE ACCOUNTADMIN;

CREATE RESOURCE MONITOR IF NOT EXISTS DQ_POC_RM
    WITH CREDIT_QUOTA = <CREDIT_QUOTA>
    FREQUENCY = MONTHLY
    START_TIMESTAMP = IMMEDIATELY
    TRIGGERS
        ON 80 PERCENT DO NOTIFY
        ON 100 PERCENT DO SUSPEND;

ALTER WAREHOUSE DQ_POC_WH SET RESOURCE_MONITOR = DQ_POC_RM;

-- Note: a warehouse resource monitor does not cap Cortex LLM function
-- usage, which is billed separately. Monitor Cortex spend through the
-- account usage views (e.g. CORTEX_FUNCTIONS_USAGE_HISTORY) during the PoC.

-- ---------------------------------------------------------------------
-- 3. Read access to in-scope Fivetran tables only
-- ---------------------------------------------------------------------
-- Explicit table grants (not ALL TABLES / FUTURE TABLES) so scope
-- only grows by deliberate change. Add a line per new source.
USE ROLE SECURITYADMIN;

GRANT USAGE ON DATABASE <FIVETRAN_DB>                    TO ROLE DQ_POC_READER;
GRANT USAGE ON SCHEMA   <FIVETRAN_DB>.<SALESFORCE_SCHEMA> TO ROLE DQ_POC_READER;

GRANT SELECT ON TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<SITE_TABLE>      TO ROLE DQ_POC_READER;
GRANT SELECT ON TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<PROJECT_TABLE>   TO ROLE DQ_POC_READER;
GRANT SELECT ON TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<MILESTONE_TABLE> TO ROLE DQ_POC_READER;
GRANT SELECT ON TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<PROJECT_TEMPLATE_TABLE> TO ROLE DQ_POC_READER;
GRANT SELECT ON TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<EQUIPMENT_TABLE> TO ROLE DQ_POC_READER;

-- ---------------------------------------------------------------------
-- 4. Cortex
-- ---------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE DQ_POC_READER;

-- Check before relying on Cortex (run as ACCOUNTADMIN, share results with Kevin):
-- SELECT CURRENT_REGION();
-- SHOW PARAMETERS LIKE 'CORTEX_ENABLED_CROSS_REGION' IN ACCOUNT;

-- ---------------------------------------------------------------------
-- 5. Service user (key-pair only)
-- ---------------------------------------------------------------------
USE ROLE SECURITYADMIN;

CREATE USER IF NOT EXISTS DQ_POC_SVC
    TYPE = SERVICE
    DEFAULT_ROLE = DQ_POC_READER
    DEFAULT_WAREHOUSE = DQ_POC_WH
    RSA_PUBLIC_KEY = '<RSA_PUBLIC_KEY>'
    COMMENT = 'Data governance PoC engine, runs on Head of Technology laptop. Key-pair auth only.';

GRANT ROLE DQ_POC_READER TO USER DQ_POC_SVC;

-- Optional: restrict to Connexa egress IPs / VPN
-- CREATE NETWORK POLICY DQ_POC_NP ALLOWED_IP_LIST = ('<IP_1>', '<IP_2>');
-- ALTER USER DQ_POC_SVC SET NETWORK_POLICY = DQ_POC_NP;

-- ---------------------------------------------------------------------
-- 6. Teardown (end of PoC)
-- ---------------------------------------------------------------------
-- USE ROLE SECURITYADMIN;
-- DROP USER IF EXISTS DQ_POC_SVC;
-- DROP ROLE IF EXISTS DQ_POC_READER;
-- USE ROLE ACCOUNTADMIN;
-- DROP WAREHOUSE IF EXISTS DQ_POC_WH;
-- DROP RESOURCE MONITOR IF EXISTS DQ_POC_RM;
