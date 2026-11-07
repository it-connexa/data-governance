-- SITE-002 v4: When a new site is created it is sometimes given a temporary
-- site ID, but it cannot stay temporary indefinitely. Call out any active site whose
-- site ID hasn't been finalised one week after its site record was created. Sites
-- still in Assessment are exempt, because their site ID isn't expected to be final yet.
--
-- Clarification (Kevin, 2026-09-27): sites that are cancelled, abandoned or
-- decommissioned before their ID was finalised are excluded -- only active
-- sites (docs/data-dictionary.md's "active" list, same as SITE-004) count.
--
-- Decision (Kevin, 2026-11-07): sites in Assessment are exempt, completely (no time limit).
-- They are not applicable, so they are not returned at all (neither a pass nor a failure).
-- Evidence at the time: 153 of the 158 failures were Assessment sites, and every later
-- status was at least 96% finalised, so an unfinalised ID in Assessment is the normal
-- state. 131 of those 153 were a single bulk load (one day, one user, no site ID at all).
-- Consequence: a site that leaves Assessment without a finalised ID is flagged at the next
-- run if its record is over a week old; there is no status-change date to give a grace
-- period from. How long a site may stay in Assessment is a separate question, not
-- answered by this rule.
--
-- Why this source: SITE alone (raw, one row per site) holds the status, the record
-- creation date (CREATED_DATE) and the finalised flag (SITE_ID_FINALIZED_C).
WITH active_sites AS (
    SELECT
        s.ID,
        s.NAME,
        s.CREATED_DATE,
        s.SITE_ID_FINALIZED_C
    FROM {{ src.SITE }} s
    WHERE s.SITETRACKER_SITE_STATUS_C NOT IN (
        'Cancelled', 'Pending Cancellation', 'Abandoned - Not Built',
        'Decommission', 'Decommissioned - Removed', 'Decommissioned - left in situ',
        'Landbanked'
    )
      AND s.SITETRACKER_SITE_STATUS_C <> 'Assessment'  -- exempt: the ID is not expected to be final yet
)
SELECT
    a.ID                                                     AS RECORD_ID,
    CAST(NULL AS VARCHAR)                                    AS ITEM_KEY,
    a.NAME                                                    AS RECORD_NAME,
    a.ID                                                       AS SITE_ID,
    a.NAME                                                    AS SITE_NAME,
    (
        COALESCE(a.SITE_ID_FINALIZED_C, FALSE)
        OR a.CREATED_DATE > DATEADD('day', -7, CURRENT_TIMESTAMP())
    )                                                          AS IS_VALID,
    OBJECT_CONSTRUCT(
        'created_date', a.CREATED_DATE,
        'site_id_finalized', a.SITE_ID_FINALIZED_C,
        'days_since_created', DATEDIFF('day', a.CREATED_DATE, CURRENT_TIMESTAMP())
    )                                                          AS DETAIL
FROM active_sites a
