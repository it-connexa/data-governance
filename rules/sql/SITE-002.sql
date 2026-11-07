-- SITE-008 v1: When a new site is created it is sometimes given a temporary
-- site ID, but it cannot stay as a temporary site ID indefinitely. Call out
-- any exceptions where site ID hasn't been finalised one week after the
-- site record is created.
--
-- Clarification (Kevin, 2026-09-27): sites that are cancelled, abandoned or
-- decommissioned before their ID was finalised are excluded -- only active
-- sites (docs/data-dictionary.md's "active" list, same as SITE-004) count.
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
