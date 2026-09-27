-- SITE-004 v2: No two active sites with a finalised site identifier should
-- share the same site identifier.
--
-- "Active" site (docs/data-dictionary.md): SITETRACKER_SITE_STATUS_C not one
-- of Cancelled / Pending Cancellation / Abandoned - Not Built / Decommission /
-- Decommissioned - Removed / Decommissioned - left in situ / Landbanked.
--
-- Dropped in v2: the coordinate-proximity check. Several active sites are
-- legitimately within 10m of each other (co-located sites), so proximity
-- alone isn't a data quality signal -- Kevin confirmed this in review.
--
-- Added in v2: SITE_ID_FINALIZED_C (docs/data-dictionary.md) -- while a
-- site's identifier is still provisional, a shared value is expected and
-- not a violation. Only sites with a finalised identifier are compared;
-- an active site with a non-finalised identifier is never flagged here.
WITH active_sites AS (
    SELECT
        s.ID,
        s.NAME,
        s.SITE_ID_C,
        s.SITE_ID_FINALIZED_C
    FROM {{ src.SITE }} s
    WHERE s.SITETRACKER_SITE_STATUS_C NOT IN (
        'Cancelled', 'Pending Cancellation', 'Abandoned - Not Built',
        'Decommission', 'Decommissioned - Removed', 'Decommissioned - left in situ',
        'Landbanked'
    )
),
finalised_sites AS (
    SELECT * FROM active_sites WHERE COALESCE(SITE_ID_FINALIZED_C, FALSE)
),
id_dupes AS (
    -- Other active, finalised sites sharing the same SITE_ID_C.
    SELECT
        a.ID,
        LISTAGG(DISTINCT b.NAME, ', ') WITHIN GROUP (ORDER BY b.NAME) AS other_site_id_names
    FROM finalised_sites a
    JOIN finalised_sites b
        ON b.SITE_ID_C = a.SITE_ID_C
       AND b.ID <> a.ID
    WHERE a.SITE_ID_C IS NOT NULL
    GROUP BY a.ID
)
SELECT
    a.ID                   AS RECORD_ID,
    CAST(NULL AS VARCHAR)  AS ITEM_KEY,
    a.NAME                 AS RECORD_NAME,
    a.ID                   AS SITE_ID,
    a.NAME                 AS SITE_NAME,
    (id.ID IS NULL)        AS IS_VALID,
    OBJECT_CONSTRUCT(
        'site_id_c', a.SITE_ID_C,
        'site_id_finalized', a.SITE_ID_FINALIZED_C,
        'duplicate_site_id_with', id.other_site_id_names
    )                      AS DETAIL
FROM active_sites a
LEFT JOIN id_dupes id ON id.ID = a.ID
