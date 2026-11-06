-- MS-001 v4: For active projects using the 'New Site' template, the milestones
-- Project Started, Site Candidate Report Compiled, Primary Candidate Confirmed,
-- Candidate Proposed, Site Confirmation Notice Signed, Site & Land Plans Approved,
-- Access Template Compiled & Issued, Construction Drawings Approved, Civil Work
-- Started, Tower Installed, RFI Certificate Sent to Customer, Connexa Notified
-- Equipment is Live must not have a forecast date in the past unless an actual
-- date has been recorded.
--
-- Why these sources: the reporting-layer milestones table (MILESTONES) is already
-- unpivoted (one row per project per milestone, confirmed against the catalogue
-- and a per-milestone row count) and uses the front-end Sitetracker milestone
-- names, so the rule filters on MILESTONE_NAME. That replaces v3's hand-mapped
-- wide columns on MILESTONE_EXT / MILESTONE_EXT_2, including the mapping quirks
-- (CO_LO_ prefix field, stale equipment-live field, and Tower Installed, whose
-- backend column did not match its front-end name). PROJECT is still joined for
-- the project name and status, which the reporting table does not carry, and
-- SITE for the site name. Alternatives not used: MILESTONE_EXT, MILESTONE_EXT_2
-- (raw wide tables).
--
-- Join key: in the reporting table PROJECT_TRAN_ID is the Salesforce project ID
-- and PROJECT_ID is the project name (checked by dry-run counts: joining
-- p.ID = m.PROJECT_ID matched no projects, p.ID = m.PROJECT_TRAN_ID matched all).
--
-- Template: PROJECT_TEMPLATE_NAME is exactly 'New Site' (confirmed by Kevin).
--
-- "Active" project (docs/data-dictionary.md, same list as PRJ-002):
-- SITETRACKER_PROJECT_STATUS_C not one of Completed / Cancelled /
-- Pending Cancellation / On-hold / Acceptance & Closure / Decommissioning.
--
-- A project is only evaluated for a milestone it has a row for in the reporting
-- table; no row means the milestone does not apply, so it is not counted.
--
-- IS_VALID is TRUE whenever FORECAST_DATE is NULL (milestone not yet scheduled --
-- nothing to flag) or ACTUAL_DATE is recorded (done, regardless of forecast).
SELECT
    p.ID                AS RECORD_ID,
    m.MILESTONE_NAME    AS ITEM_KEY,
    p.NAME              AS RECORD_NAME,
    p.SITETRACKER_SITE_C AS SITE_ID,
    s.NAME              AS SITE_NAME,
    COALESCE(
        NOT (m.ACTUAL_DATE IS NULL AND m.FORECAST_DATE IS NOT NULL AND m.FORECAST_DATE < CURRENT_DATE()),
        TRUE
    )                   AS IS_VALID,
    OBJECT_CONSTRUCT('forecast', m.FORECAST_DATE, 'actual', m.ACTUAL_DATE) AS DETAIL
FROM {{ src.MILESTONES }} m
JOIN {{ src.PROJECT }} p     ON p.ID = m.PROJECT_TRAN_ID
LEFT JOIN {{ src.SITE }} s   ON s.ID = p.SITETRACKER_SITE_C
WHERE m.PROJECT_TEMPLATE_NAME = 'New Site'
  AND m.MILESTONE_NAME IN (
    'Project Started',
    'Site Candidate Report Compiled',
    'Primary Candidate Confirmed',
    'Candidate Proposed',
    'Site Confirmation Notice Signed',
    'Site & Land Plans Approved',
    'Access Template Compiled & Issued',
    'Construction Drawings Approved',
    'Civil Work Started',
    'Tower Installed',
    'RFI Certificate Sent to Customer',
    'Connexa Notified Equipment is Live'
  )
  AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
    'Completed', 'Cancelled', 'Pending Cancellation',
    'On-hold', 'Acceptance & Closure', 'Decommissioning'
  )
