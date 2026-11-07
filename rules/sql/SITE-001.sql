-- SITE-001 v4: Every active site must have latitude and longitude populated, and
-- the coordinates must fall within New Zealand.
--
-- Clarified with Kevin (2026-11-07):
-- - Mainland only: no outer islands. "Within New Zealand" is the box latitude -47.5
--   to -34.0, longitude 166.0 to 179.0, which covers the North and South Islands and
--   nearby islands such as Stewart, Great Barrier and Waiheke. The Chatham, Kermadec
--   and sub-Antarctic islands are outside it. The rule statement was reworded on the
--   same day to say so.
-- - Coordinates are not due on a new site build until its Primary Candidate Confirmed
--   milestone has an actual date. Until then a site with no coordinates is not
--   applicable (not returned at all, so it is neither a pass nor a failure). If it
--   already has coordinates they are still checked against the box.
--
-- "Active" site (docs/data-dictionary.md, same list as SITE-004): SITETRACKER_SITE_STATUS_C
-- not one of Cancelled / Pending Cancellation / Abandoned - Not Built / Decommission /
-- Decommissioned - Removed / Decommissioned - left in situ / Landbanked.
--
-- "New site build": an active project (same list as PRJ-002) on the 'New Site' template
-- linked to the site. Only in-flight builds count, so an old finished build whose
-- milestone was never actualised cannot excuse missing coordinates on an operating site.
--
-- Why these sources: SITE (raw: one row per site) holds the coordinates and status.
-- PROJECT and PROJECT_TEMPLATE identify the site's in-flight New Site projects.
-- MILESTONES (reporting layer, one row per project per milestone, front-end names) gives
-- the Primary Candidate Confirmed actual date, so the rule filters on the milestone name
-- instead of mapping the raw wide columns. Not used: the raw wide milestone tables (no
-- longer registered). Known gap: the milestones view has no rows for some recent
-- projects (docs/reporting-view-followups.md); such a build is treated as not yet past
-- Primary Candidate Confirmed, which is also the usual case for a brand-new project.
--
-- Coordinates: the decimal-degree fields SITETRACKER_LOCATION_LATITUDE_S and
-- SITETRACKER_LOCATION_LONGITUDE_S (docs/data-dictionary.md). No active site has
-- coordinates only in the older text fields, so nothing is missed by ignoring them.
--
-- IS_VALID is never NULL: a missing coordinate makes the comparison unknown, and
-- COALESCE(..., FALSE) treats unknown as a failure (these sites are only returned when
-- coordinates are due).
WITH active_sites AS (
    SELECT
        s.ID,
        s.NAME,
        s.SITETRACKER_SITE_STATUS_C AS SITE_STATUS,
        s.SITETRACKER_LOCATION_LATITUDE_S  AS LATITUDE,
        s.SITETRACKER_LOCATION_LONGITUDE_S AS LONGITUDE
    FROM {{ src.SITE }} s
    WHERE s.SITETRACKER_SITE_STATUS_C NOT IN (
        'Cancelled', 'Pending Cancellation', 'Abandoned - Not Built',
        'Decommission', 'Decommissioned - Removed', 'Decommissioned - left in situ',
        'Landbanked'
    )
),
new_site_builds AS (
    SELECT p.ID AS PROJECT_ID, p.SITETRACKER_SITE_C AS SITE_ID
    FROM {{ src.PROJECT }} p
    JOIN {{ src.PROJECT_TEMPLATE }} t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND p.SITETRACKER_SITE_C IS NOT NULL  -- a NULL here would make the IN below unknown
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
        'Completed', 'Cancelled', 'Pending Cancellation',
        'On-hold', 'Acceptance & Closure', 'Decommissioning'
      )
),
-- Sites with an in-flight New Site build that has not yet actualised Primary Candidate
-- Confirmed: coordinates are not yet due.
coordinates_not_yet_due AS (
    SELECT DISTINCT b.SITE_ID
    FROM new_site_builds b
    WHERE NOT EXISTS (
        SELECT 1
        FROM new_site_builds b2
        JOIN {{ src.MILESTONES }} m ON m.PROJECT_TRAN_ID = b2.PROJECT_ID
        WHERE b2.SITE_ID = b.SITE_ID
          AND m.MILESTONE_NAME = 'Primary Candidate Confirmed'
          AND m.ACTUAL_DATE IS NOT NULL
    )
)
SELECT
    a.ID                        AS RECORD_ID,
    CAST(NULL AS VARCHAR)       AS ITEM_KEY,
    a.NAME                      AS RECORD_NAME,
    a.ID                        AS SITE_ID,
    a.NAME                      AS SITE_NAME,
    COALESCE(
        (a.LATITUDE BETWEEN -47.5 AND -34.0) AND (a.LONGITUDE BETWEEN 166.0 AND 179.0),
        FALSE
    )                           AS IS_VALID,
    OBJECT_CONSTRUCT(
        'reason', CASE
            WHEN a.LATITUDE IS NULL OR a.LONGITUDE IS NULL THEN 'Latitude or longitude is missing'
            WHEN a.LATITUDE > 0 THEN 'Latitude is positive, but New Zealand is in the southern hemisphere'
            ELSE 'Coordinates are outside New Zealand'
        END,
        'latitude', a.LATITUDE,
        'longitude', a.LONGITUDE,
        'site_status', a.SITE_STATUS
    )                           AS DETAIL
FROM active_sites a
WHERE NOT (
    (a.LATITUDE IS NULL OR a.LONGITUDE IS NULL)
    AND a.ID IN (SELECT SITE_ID FROM coordinates_not_yet_due)
)
