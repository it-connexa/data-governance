-- SITE-004 v1: No two active sites should share the same site identifier,
-- or have coordinates within 10 metres of each other.
--
-- "Active" site (docs/data-dictionary.md): SITETRACKER_SITE_STATUS_C not one
-- of Cancelled / Pending Cancellation / Abandoned - Not Built / Decommission /
-- Decommissioned - Removed / Decommissioned - left in situ / Landbanked.
WITH active_sites AS (
    SELECT
        s.ID,
        s.NAME,
        s.SITE_ID_C,
        s.SITETRACKER_LOCATION_LATITUDE_S  AS LAT,
        s.SITETRACKER_LOCATION_LONGITUDE_S AS LON
    FROM {{ src.SITE }} s
    WHERE s.SITETRACKER_SITE_STATUS_C NOT IN (
        'Cancelled', 'Pending Cancellation', 'Abandoned - Not Built',
        'Decommission', 'Decommissioned - Removed', 'Decommissioned - left in situ',
        'Landbanked'
    )
),
id_dupes AS (
    -- Other active sites sharing the same SITE_ID_C.
    SELECT
        a.ID,
        LISTAGG(DISTINCT b.NAME, ', ') WITHIN GROUP (ORDER BY b.NAME) AS other_site_id_names
    FROM active_sites a
    JOIN active_sites b
        ON b.SITE_ID_C = a.SITE_ID_C
       AND b.ID <> a.ID
    WHERE a.SITE_ID_C IS NOT NULL
    GROUP BY a.ID
),
coord_dupes AS (
    -- Other active sites within 10 metres (geodesic distance).
    SELECT
        a.ID,
        LISTAGG(DISTINCT b.NAME, ', ') WITHIN GROUP (ORDER BY b.NAME) AS other_coord_names,
        MIN(ST_DISTANCE(ST_MAKEPOINT(a.LON, a.LAT), ST_MAKEPOINT(b.LON, b.LAT))) AS min_distance_m
    FROM active_sites a
    JOIN active_sites b
        ON b.ID <> a.ID
       AND ST_DISTANCE(ST_MAKEPOINT(a.LON, a.LAT), ST_MAKEPOINT(b.LON, b.LAT)) <= 10
    WHERE a.LAT IS NOT NULL AND a.LON IS NOT NULL
      AND b.LAT IS NOT NULL AND b.LON IS NOT NULL
    GROUP BY a.ID
)
SELECT
    a.ID                               AS RECORD_ID,
    CAST(NULL AS VARCHAR)              AS ITEM_KEY,
    a.NAME                             AS RECORD_NAME,
    a.ID                               AS SITE_ID,
    a.NAME                             AS SITE_NAME,
    (id.ID IS NULL AND cd.ID IS NULL)  AS IS_VALID,
    OBJECT_CONSTRUCT(
        'site_id_c', a.SITE_ID_C,
        'duplicate_site_id_with', id.other_site_id_names,
        'duplicate_coordinates_with', cd.other_coord_names,
        'min_distance_m', cd.min_distance_m
    )                                  AS DETAIL
FROM active_sites a
LEFT JOIN id_dupes id ON id.ID = a.ID
LEFT JOIN coord_dupes cd ON cd.ID = a.ID
