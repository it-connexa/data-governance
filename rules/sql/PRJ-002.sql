-- PRJ-002 v1: No active project should be linked to a site with a
-- decommissioned status.
--
-- "Active" project (docs/data-dictionary.md): SITETRACKER_PROJECT_STATUS_C
-- not one of Completed / Cancelled / Pending Cancellation / On-hold /
-- Acceptance & Closure / Decommissioning. Decommissioning is excluded
-- deliberately: a project whose own job is decommissioning a site is
-- expected to be linked to a site that is now Decommission(ed) -- that is
-- the normal case, not the violation this rule is checking for.
--
-- "Decommissioned" site: SITETRACKER_SITE_STATUS_C in Decommission /
-- Decommissioned - Removed / Decommissioned - left in situ.
SELECT
    p.ID                                                    AS RECORD_ID,
    CAST(NULL AS VARCHAR)                                   AS ITEM_KEY,
    p.NAME                                                  AS RECORD_NAME,
    s.ID                                                     AS SITE_ID,
    s.NAME                                                   AS SITE_NAME,
    COALESCE(
        s.SITETRACKER_SITE_STATUS_C NOT IN (
            'Decommission', 'Decommissioned - Removed', 'Decommissioned - left in situ'
        ),
        TRUE  -- unknown site status: benefit of the doubt, not a violation
    )                                                        AS IS_VALID,
    OBJECT_CONSTRUCT(
        'project_status', p.SITETRACKER_PROJECT_STATUS_C,
        'site_status', s.SITETRACKER_SITE_STATUS_C
    )                                                        AS DETAIL
FROM {{ src.PROJECT }} p
JOIN {{ src.SITE }} s
    ON s.ID = p.SITETRACKER_SITE_C
WHERE p.SITETRACKER_PROJECT_STATUS_C NOT IN (
    'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
    'Acceptance & Closure', 'Decommissioning'
)
