-- MS-002 v5: For active projects using the New Site template, a milestone's actual date
-- must not be earlier than the actual date of the milestone before it, in this order:
-- Project Started; Site Candidate Report Compiled; Primary Candidate Confirmed; Candidate
-- Proposed; then these four in any order, none before Candidate Proposed: Site Confirmation
-- Notice Signed, Site & Land Plans Approved, Access Template Compiled & Issued, and
-- Construction Drawings Approved; then Civil Work Started (not earlier than any of those
-- four); Tower Installed; RFI Certificate Sent to Customer; Connexa Notified Equipment is
-- Live. The same date is fine, and a milestone with no actual date yet is not checked.
--
-- Decisions (Kevin, 2026-11-07), from count-only analysis of the milestones view:
-- - Active projects only. Read as listed, the chain failed 264 of 421 testable New Site
--   projects (136 of 274 active): too many to act on.
-- - The middle four milestones are parallel work, not a line: Construction Drawings was
--   approved before the Access Template in 57% of projects, and the Notice / Land Plans /
--   Access Template / Drawings pairs were reversed in 12% to 20% of the others. With those
--   four in any order, 25 of 274 active projects fail.
-- - The chain is drawn as a dependency table below (each milestone and what it must not
--   precede), so a milestone with several prerequisites (Civil Work Started) is compared with
--   the latest of those that have an actual date.
--
-- "Active" project (docs/data-dictionary.md, same list as PRJ-002 and MS-001):
-- SITETRACKER_PROJECT_STATUS_C not one of Completed / Cancelled / Pending Cancellation /
-- On-hold / Acceptance & Closure / Decommissioning.
--
-- Applicability: a milestone is only checked when it has an actual date and at least one of
-- its prerequisites does too. Anything else is not returned (not a pass, not a failure), so
-- a project is never flagged for a step it has not reached.
--
-- Why these sources: MILESTONES (reporting layer, one row per project per milestone, front-end
-- names) gives the actual dates and the template name. PROJECT gives the Salesforce ID, name,
-- status and site; SITE gives the site name. Not used: the raw wide milestone tables (no
-- longer registered). Known gaps (docs/reporting-view-followups.md): the milestones view has no
-- rows for some recent projects, which are therefore not tested yet, and its mapping of
-- "Site Confirmation Notice Signed" and "Connexa Notified Equipment is Live" is under review,
-- so failures involving those two may change when the view is corrected.
--
-- DETAIL describes the comparison in one sentence and then lists both milestones side by
-- side, each with its actual date: "milestone" and "prerequisite" (the milestone it must not
-- be earlier than; for Civil Work Started, the latest of the four that has a date). JSON
-- cannot repeat a key, so each date carries its milestone's role in its name. Snowflake lists
-- keys alphabetically, so they are named to read in order.
--
-- IS_VALID is never NULL: both dates are present by construction.
WITH dependencies AS (
    -- (milestone, a milestone it must not be earlier than)
    SELECT $1 AS MILESTONE, $2 AS PREREQUISITE FROM VALUES
        ('Site Candidate Report Compiled', 'Project Started'),
        ('Primary Candidate Confirmed', 'Site Candidate Report Compiled'),
        ('Candidate Proposed', 'Primary Candidate Confirmed'),
        ('Site Confirmation Notice Signed', 'Candidate Proposed'),
        ('Site & Land Plans Approved', 'Candidate Proposed'),
        ('Access Template Compiled & Issued', 'Candidate Proposed'),
        ('Construction Drawings Approved', 'Candidate Proposed'),
        ('Civil Work Started', 'Site Confirmation Notice Signed'),
        ('Civil Work Started', 'Site & Land Plans Approved'),
        ('Civil Work Started', 'Access Template Compiled & Issued'),
        ('Civil Work Started', 'Construction Drawings Approved'),
        ('Tower Installed', 'Civil Work Started'),
        ('RFI Certificate Sent to Customer', 'Tower Installed'),
        ('Connexa Notified Equipment is Live', 'RFI Certificate Sent to Customer')
),
actuals AS (
    SELECT m.PROJECT_TRAN_ID AS PROJECT_ID, m.MILESTONE_NAME, m.ACTUAL_DATE
    FROM {{ src.MILESTONES }} m
    WHERE m.PROJECT_TEMPLATE_NAME = 'New Site'
      AND m.ACTUAL_DATE IS NOT NULL
      AND m.MILESTONE_NAME IN (
        'Project Started', 'Site Candidate Report Compiled', 'Primary Candidate Confirmed',
        'Candidate Proposed', 'Site Confirmation Notice Signed', 'Site & Land Plans Approved',
        'Access Template Compiled & Issued', 'Construction Drawings Approved',
        'Civil Work Started', 'Tower Installed', 'RFI Certificate Sent to Customer',
        'Connexa Notified Equipment is Live'
      )
),
comparisons AS (
    SELECT
        cur.PROJECT_ID,
        cur.MILESTONE_NAME,
        cur.ACTUAL_DATE,
        d.PREREQUISITE,
        prev.ACTUAL_DATE AS PREREQUISITE_DATE
    FROM actuals cur
    JOIN dependencies d ON d.MILESTONE = cur.MILESTONE_NAME
    JOIN actuals prev ON prev.PROJECT_ID = cur.PROJECT_ID AND prev.MILESTONE_NAME = d.PREREQUISITE
),
items AS (
    -- one row per project and milestone, compared with the latest prerequisite that has a date
    SELECT
        PROJECT_ID,
        MILESTONE_NAME,
        MAX(ACTUAL_DATE)                           AS ACTUAL_DATE,
        MAX(PREREQUISITE_DATE)                     AS LATEST_PREREQUISITE_DATE,
        MAX_BY(PREREQUISITE, PREREQUISITE_DATE)    AS LATEST_PREREQUISITE
    FROM comparisons
    GROUP BY PROJECT_ID, MILESTONE_NAME
)
SELECT
    p.ID                                           AS RECORD_ID,
    i.MILESTONE_NAME                               AS ITEM_KEY,
    p.NAME                                         AS RECORD_NAME,
    p.SITETRACKER_SITE_C                           AS SITE_ID,
    s.NAME                                         AS SITE_NAME,
    (i.ACTUAL_DATE >= i.LATEST_PREREQUISITE_DATE)  AS IS_VALID,
    OBJECT_CONSTRUCT(
        'issue',
            i.MILESTONE_NAME || ' (' || TO_CHAR(i.ACTUAL_DATE, 'DD Mon YYYY') || ') is '
            || DATEDIFF('day', i.ACTUAL_DATE, i.LATEST_PREREQUISITE_DATE)
            || IFF(DATEDIFF('day', i.ACTUAL_DATE, i.LATEST_PREREQUISITE_DATE) = 1, ' day', ' days')
            || ' earlier than ' || i.LATEST_PREREQUISITE
            || ' (' || TO_CHAR(i.LATEST_PREREQUISITE_DATE, 'DD Mon YYYY') || ')',
        'milestone', i.MILESTONE_NAME,
        'milestone_actual', i.ACTUAL_DATE,
        'prerequisite', i.LATEST_PREREQUISITE,
        'prerequisite_actual', i.LATEST_PREREQUISITE_DATE
    )                                              AS DETAIL
FROM items i
JOIN {{ src.PROJECT }} p ON p.ID = i.PROJECT_ID
LEFT JOIN {{ src.SITE }} s ON s.ID = p.SITETRACKER_SITE_C
WHERE p.SITETRACKER_PROJECT_STATUS_C NOT IN (
    'Completed', 'Cancelled', 'Pending Cancellation',
    'On-hold', 'Acceptance & Closure', 'Decommissioning'
)
