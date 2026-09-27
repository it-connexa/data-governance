-- MS-001 v2: For active projects using the 'New Site' template, the milestones
-- Project Started, Site Candidate Report Compiled, Primary Candidate Confirmed,
-- Candidate Proposed, Site Confirmation Notice Signed, Site & Land Plans Approved,
-- Access Template Compiled & Issued, Construction Drawings Approved, Civil Work
-- Started, Tower Installed, RFI Certificate Sent to Customer, Connexa Notified
-- Equipment is Live must not have a forecast date in the past unless an actual
-- date has been recorded.
--
-- Clarified with Kevin (2026-09-27):
-- - Template name is exactly 'New Site'.
-- - "Site Confirmation Notice Signed" -> CO_LO_SITE_CONFIRM_NOTICE_SIGNED_F_C/_A_C
--   (despite the "CO_LO" prefix, confirmed as the right field for New Site too).
-- - "Site & Land Plans Approved" -> SITE_LAND_PLANS_FINALISED_F_C/_A_C.
-- - "Connexa Notified Equipment is Live" -> NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C/_A_C
--   (the other similarly-named field is stale/not current).
-- - "Project Started" -> MILESTONE_EXT_2.NEW_SITE_PROJECT_KICKED_OFF_F_C/_A_C and
--   "Tower Installed" -> TOWER_INSTALL_FINISHED_F_C/_A_C (completion, not start) were
--   Claude Code's working assumptions, unchallenged when the rest was answered.
--
-- "Active" project (docs/data-dictionary.md, same list as PRJ-002):
-- SITETRACKER_PROJECT_STATUS_C not one of Completed / Cancelled /
-- Pending Cancellation / On-hold / Acceptance & Closure / Decommissioning.
--
-- IS_VALID is TRUE whenever FORECAST is NULL (milestone not yet scheduled --
-- nothing to flag) or ACTUAL is recorded (done, regardless of forecast).
WITH scoped_ext AS (
    SELECT
        p.ID AS PROJECT_ID, p.NAME AS PROJECT_NAME,
        p.SITETRACKER_SITE_C AS SITE_ID, s.NAME AS SITE_NAME,
        m.SITE_CANDIDATE_REPORT_COMPILED_F_C AS CANDIDATE_REPORT_F,
        m.SITE_CANDIDATE_REPORT_COMPILED_A_C AS CANDIDATE_REPORT_A,
        m.PRIMARY_CANDIDATE_CONFIRMED_F_C AS PRIMARY_CANDIDATE_F,
        m.PRIMARY_CANDIDATE_CONFIRMED_A_C AS PRIMARY_CANDIDATE_A,
        m.CANDIDATE_PROPOSED_F_C AS CANDIDATE_PROPOSED_F,
        m.CANDIDATE_PROPOSED_A_C AS CANDIDATE_PROPOSED_A,
        m.CO_LO_SITE_CONFIRM_NOTICE_SIGNED_F_C AS CONFIRM_NOTICE_F,
        m.CO_LO_SITE_CONFIRM_NOTICE_SIGNED_A_C AS CONFIRM_NOTICE_A,
        m.SITE_LAND_PLANS_FINALISED_F_C AS LAND_PLANS_F,
        m.SITE_LAND_PLANS_FINALISED_A_C AS LAND_PLANS_A,
        m.ACCESS_TEMPLATE_COMPILED_ISSUED_F_C AS ACCESS_TEMPLATE_F,
        m.ACCESS_TEMPLATE_COMPILED_ISSUED_A_C AS ACCESS_TEMPLATE_A,
        m.CONSTRUCTION_DRAWINGS_APPROVED_F_C AS DRAWINGS_F,
        m.CONSTRUCTION_DRAWINGS_APPROVED_A_C AS DRAWINGS_A,
        m.CIVIL_WORK_START_F_C AS CIVIL_WORK_F,
        m.CIVIL_WORK_START_A_C AS CIVIL_WORK_A,
        m.TOWER_INSTALL_FINISHED_F_C AS TOWER_INSTALL_F,
        m.TOWER_INSTALL_FINISHED_A_C AS TOWER_INSTALL_A,
        m.RFI_CERTIFICATE_SENT_TO_CUSTOMER_F_C AS RFI_CERT_F,
        m.RFI_CERTIFICATE_SENT_TO_CUSTOMER_A_C AS RFI_CERT_A,
        m.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C AS EQUIP_LIVE_F,
        m.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_A_C AS EQUIP_LIVE_A
    FROM {{ src.PROJECT }} p
    JOIN {{ src.PROJECT_TEMPLATE }} t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    JOIN {{ src.MILESTONE_EXT }} m    ON m.PROJECT_C = p.ID
    LEFT JOIN {{ src.SITE }} s        ON s.ID = p.SITETRACKER_SITE_C
    WHERE t.NAME = 'New Site'
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
        'Completed', 'Cancelled', 'Pending Cancellation',
        'On-hold', 'Acceptance & Closure', 'Decommissioning'
      )
),
scoped_ext2 AS (
    SELECT
        p.ID AS PROJECT_ID, p.NAME AS PROJECT_NAME,
        p.SITETRACKER_SITE_C AS SITE_ID, s.NAME AS SITE_NAME,
        m2.NEW_SITE_PROJECT_KICKED_OFF_F_C AS PROJECT_STARTED_F,
        m2.NEW_SITE_PROJECT_KICKED_OFF_A_C AS PROJECT_STARTED_A
    FROM {{ src.PROJECT }} p
    JOIN {{ src.PROJECT_TEMPLATE }} t   ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    JOIN {{ src.MILESTONE_EXT_2 }} m2   ON m2.PROJECT_C = p.ID
    LEFT JOIN {{ src.SITE }} s          ON s.ID = p.SITETRACKER_SITE_C
    WHERE t.NAME = 'New Site'
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
        'Completed', 'Cancelled', 'Pending Cancellation',
        'On-hold', 'Acceptance & Closure', 'Decommissioning'
      )
),
items AS (
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Project Started' AS ITEM_KEY, PROJECT_STARTED_F AS FORECAST, PROJECT_STARTED_A AS ACTUAL FROM scoped_ext2
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Site Candidate Report Compiled', CANDIDATE_REPORT_F, CANDIDATE_REPORT_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Primary Candidate Confirmed', PRIMARY_CANDIDATE_F, PRIMARY_CANDIDATE_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Candidate Proposed', CANDIDATE_PROPOSED_F, CANDIDATE_PROPOSED_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Site Confirmation Notice Signed', CONFIRM_NOTICE_F, CONFIRM_NOTICE_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Site & Land Plans Approved', LAND_PLANS_F, LAND_PLANS_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Access Template Compiled & Issued', ACCESS_TEMPLATE_F, ACCESS_TEMPLATE_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Construction Drawings Approved', DRAWINGS_F, DRAWINGS_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Civil Work Started', CIVIL_WORK_F, CIVIL_WORK_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Tower Installed', TOWER_INSTALL_F, TOWER_INSTALL_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'RFI Certificate Sent to Customer', RFI_CERT_F, RFI_CERT_A FROM scoped_ext
    UNION ALL
    SELECT PROJECT_ID, PROJECT_NAME, SITE_ID, SITE_NAME, 'Connexa Notified Equipment is Live', EQUIP_LIVE_F, EQUIP_LIVE_A FROM scoped_ext
)
SELECT
    PROJECT_ID   AS RECORD_ID,
    ITEM_KEY,
    PROJECT_NAME AS RECORD_NAME,
    SITE_ID,
    SITE_NAME,
    COALESCE(
        NOT (ACTUAL IS NULL AND FORECAST IS NOT NULL AND FORECAST < CURRENT_DATE()),
        TRUE
    )            AS IS_VALID,
    OBJECT_CONSTRUCT('forecast', FORECAST, 'actual', ACTUAL) AS DETAIL
FROM items
