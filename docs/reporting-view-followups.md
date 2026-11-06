# Reporting view follow-ups for the Data Lead

Raised 2026-11-06 from the data governance proof of concept (`dq-governance`).

## Background

The PoC checks Sitetracker data with plain-English rules compiled to SQL. Two of its
live rules now read the reporting layer instead of the raw Fivetran tables:

| Rule | What it checks | Reads |
|---|---|---|
| MS-001 | Active New Site projects have no past-due forecast on 12 named milestones unless an actual date is recorded | `PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES` (the *milestones view*), joined to raw `SITETRACKER_PROJECT_C` and `SITETRACKER_SITE_C` |
| PRJ-002 | No active project is linked to a decommissioned site | `PROD.REPORTING.RPT_WIP_PROJECT_DETAIL` (the *WIP view*) as the scope list, joined to raw project and site |

The PoC role (`DQ_POC_READER`) is read-only. We compared the views to the raw tables with
count-only queries. That turned up the gaps below. **We have not seen any of the
underlying rows**, so each item says what we measured, what we think it means, and what
we need from you.

Everything below was found against data as at 2026-11-06.

## Summary

| # | Follow-up | What we found | Effect today | Priority |
|---|---|---|---|---|
| 1 | "Connexa Notified Equipment is Live" mapping | The view's forecast matches `NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_*` (the field the business says is stale), not `..._IS_L_*` (the current one) | 7 MS-001 findings are probably false positives | High |
| 2 | "Site Confirmation Notice Signed" mapping | The view's forecast does not match `CO_LO_SITE_CONFIRM_NOTICE_SIGNED_*` (the field the business confirmed) | 3 MS-001 findings may be false positives | High |
| 3 | 36 active New Site projects have no rows in the milestones view | All 36 have a raw `PROJECT_EXT_C` row and are in the WIP view; 29 were created in the last 30 days, 7 are older | MS-001 does not check these projects | High for the 7, low for the 29 if it is only refresh lag |
| 4 | 1 project is in scope but missing from the WIP view | Active, allowed template, not a test site, still absent | PRJ-002 does not check it | Low |
| 5 | Confirm all 12 mappings | We only tested two milestones, and "Tower Installed" and "Project Started" were mappings we were told or assumed | Unknown | Medium |

## How to run these

Run each query with any role that can read `RAW.TMP` and `PROD.REPORTING` (the PoC role
cannot read the raw `PROJECT_EXT_C` and `PROJECT_EXT_2_C` tables any more, so these need
your own role). Every query is read-only. Each one starts with the same CTE, so they can
be run on their own, in any order.

"Active" means project status not one of `Completed`, `Cancelled`, `Pending Cancellation`,
`On-hold`, `Acceptance & Closure`, `Decommissioning`. This is the PoC's working definition
and is not necessarily the view's.

---

## 1. "Connexa Notified Equipment is Live" mapping

**What we found.** Among active New Site projects where the view shows this milestone as
overdue (no actual date, forecast in the past), there are 7. In 5 of the 7, the view's
forecast equals the raw `NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_F_C`. In none does it equal
`NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C`. The business told us the `IS_L` field is the
current one and the `LIVE` field is stale. The older version of the rule used `IS_L` and
found none overdue.

**What we need.** Which raw field the view reads for this milestone, and (if it is the
`LIVE` field) a change to read `NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C` and `_A_C`.

**Query 1a: how often the view agrees with each candidate field.**

```sql
WITH scope AS (
    SELECT p.ID, p.NAME
    FROM RAW.TMP.SITETRACKER_PROJECT_C p
    JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
      AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
          'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
          'Acceptance & Closure', 'Decommissioning')
)
SELECT
    COUNT(*)                                                                              AS PROJECTS,
    COUNT_IF(m.FORECAST_DATE IS NOT DISTINCT FROM CAST(e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C AS DATE)) AS FORECAST_MATCHES_IS_L,
    COUNT_IF(m.FORECAST_DATE IS NOT DISTINCT FROM CAST(e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_F_C AS DATE)) AS FORECAST_MATCHES_LIVE,
    COUNT_IF(m.ACTUAL_DATE   IS NOT DISTINCT FROM CAST(e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_A_C AS DATE)) AS ACTUAL_MATCHES_IS_L,
    COUNT_IF(m.ACTUAL_DATE   IS NOT DISTINCT FROM CAST(e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_A_C AS DATE)) AS ACTUAL_MATCHES_LIVE
FROM PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES m
JOIN scope s ON s.ID = m.PROJECT_TRAN_ID
JOIN RAW.TMP.PROJECT_EXT_C e
  ON e.PROJECT_C = s.ID
 AND COALESCE(e._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(e.IS_DELETED, FALSE) = FALSE
WHERE m.PROJECT_TEMPLATE_NAME = 'New Site'
  AND m.MILESTONE_NAME = 'Connexa Notified Equipment is Live';
```

*Reading it:* a correct mapping matches on (nearly) every project. If the `LIVE` columns
match far more often than the `IS_L` columns, the view reads the stale field.

**Query 1b: the 7 overdue projects, side by side.**

```sql
WITH scope AS (
    SELECT p.ID, p.NAME
    FROM RAW.TMP.SITETRACKER_PROJECT_C p
    JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
      AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
          'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
          'Acceptance & Closure', 'Decommissioning')
)
SELECT
    s.NAME                                       AS PROJECT_NAME,
    m.FORECAST_DATE                              AS VIEW_FORECAST,
    m.ACTUAL_DATE                                AS VIEW_ACTUAL,
    e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C   AS IS_L_FORECAST,
    e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_A_C   AS IS_L_ACTUAL,
    e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_F_C   AS LIVE_FORECAST,
    e.NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_A_C   AS LIVE_ACTUAL
FROM PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES m
JOIN scope s ON s.ID = m.PROJECT_TRAN_ID
JOIN RAW.TMP.PROJECT_EXT_C e
  ON e.PROJECT_C = s.ID
 AND COALESCE(e._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(e.IS_DELETED, FALSE) = FALSE
WHERE m.PROJECT_TEMPLATE_NAME = 'New Site'
  AND m.MILESTONE_NAME = 'Connexa Notified Equipment is Live'
  AND m.ACTUAL_DATE IS NULL
  AND m.FORECAST_DATE < CURRENT_DATE()
ORDER BY s.NAME;
```

*Expect 7 rows.* `VIEW_FORECAST` should equal `IS_L_FORECAST` if the mapping is right.

---

## 2. "Site Confirmation Notice Signed" mapping

**What we found.** The business confirmed the right field is
`CO_LO_SITE_CONFIRM_NOTICE_SIGNED_F_C` / `_A_C`, despite the `CO_LO` prefix. Among the 3
active New Site projects the view shows as overdue for this milestone, none has a view
forecast equal to that field's forecast. We could not find any other column in
`PROJECT_EXT_C` or `PROJECT_EXT_2_C` that could be the view's source.

**What we need.** Which raw field, or which other object, the view reads for this
milestone. If it is not the `CO_LO` field, we need to know why, and whether the `CO_LO`
field really is the right one for New Site.

**Query 2a: how often the view agrees with the `CO_LO` field.**

```sql
WITH scope AS (
    SELECT p.ID, p.NAME
    FROM RAW.TMP.SITETRACKER_PROJECT_C p
    JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
      AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
          'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
          'Acceptance & Closure', 'Decommissioning')
)
SELECT
    COUNT(*)                                                                                       AS PROJECTS,
    COUNT_IF(m.FORECAST_DATE IS NOT DISTINCT FROM CAST(e.CO_LO_SITE_CONFIRM_NOTICE_SIGNED_F_C AS DATE)) AS FORECAST_MATCHES_CO_LO,
    COUNT_IF(m.ACTUAL_DATE   IS NOT DISTINCT FROM CAST(e.CO_LO_SITE_CONFIRM_NOTICE_SIGNED_A_C AS DATE)) AS ACTUAL_MATCHES_CO_LO
FROM PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES m
JOIN scope s ON s.ID = m.PROJECT_TRAN_ID
JOIN RAW.TMP.PROJECT_EXT_C e
  ON e.PROJECT_C = s.ID
 AND COALESCE(e._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(e.IS_DELETED, FALSE) = FALSE
WHERE m.PROJECT_TEMPLATE_NAME = 'New Site'
  AND m.MILESTONE_NAME = 'Site Confirmation Notice Signed';
```

**Query 2b: the 3 overdue projects.**

```sql
WITH scope AS (
    SELECT p.ID, p.NAME
    FROM RAW.TMP.SITETRACKER_PROJECT_C p
    JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
      AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
          'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
          'Acceptance & Closure', 'Decommissioning')
)
SELECT
    s.NAME                                          AS PROJECT_NAME,
    m.FORECAST_DATE                                 AS VIEW_FORECAST,
    m.ACTUAL_DATE                                   AS VIEW_ACTUAL,
    e.CO_LO_SITE_CONFIRM_NOTICE_SIGNED_F_C          AS CO_LO_FORECAST,
    e.CO_LO_SITE_CONFIRM_NOTICE_SIGNED_A_C          AS CO_LO_ACTUAL
FROM PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES m
JOIN scope s ON s.ID = m.PROJECT_TRAN_ID
JOIN RAW.TMP.PROJECT_EXT_C e
  ON e.PROJECT_C = s.ID
 AND COALESCE(e._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(e.IS_DELETED, FALSE) = FALSE
WHERE m.PROJECT_TEMPLATE_NAME = 'New Site'
  AND m.MILESTONE_NAME = 'Site Confirmation Notice Signed'
  AND m.ACTUAL_DATE IS NULL
  AND m.FORECAST_DATE < CURRENT_DATE()
ORDER BY s.NAME;
```

*Expect 3 rows.*

---

## 3. Projects missing from the milestones view

**What we found.** 345 active New Site projects have a row in the raw `PROJECT_EXT_C`.
The milestones view has **no rows at all** for 38 of them. 2 are test sites (also absent
from the WIP view, so that is expected). The other **36 are in the WIP view but missing
from the milestones view**: 29 were created in the last 30 days, and 7 are older. Every
one of the 36 has a raw `PROJECT_EXT_C` row, and none is on a different template.

**Why it matters.** MS-001 cannot check a project that has no milestone rows. For the 29
new projects this is probably refresh lag and low risk, because a brand-new project rarely
has an overdue forecast. The 7 older projects are the real concern: they have been silently
ungoverned.

**What we need.**
1. The refresh schedule of the milestones model compared with the project data and the WIP view.
   If the milestones model runs later than the others, the 29 explain themselves.
2. Why the 7 older projects have no rows. Is there an inner join or filter in the model
   (a program, customer, financial year or milestone that is null for them)?

**Query 3a: the 38 projects, with the columns most likely to explain the gap.**

```sql
WITH scope AS (
    SELECT p.ID, p.NAME, p.CREATED_DATE, p.SITETRACKER_PROJECT_STATUS_C AS STATUS
    FROM RAW.TMP.SITETRACKER_PROJECT_C p
    JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
      AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
          'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
          'Acceptance & Closure', 'Decommissioning')
)
SELECT
    s.NAME                                          AS PROJECT_NAME,
    s.CREATED_DATE,
    DATEDIFF('day', s.CREATED_DATE, CURRENT_DATE()) AS AGE_DAYS,
    s.STATUS,
    (w.PROJECT_NAME IS NOT NULL)                    AS IN_WIP_VIEW,
    w.PROGRAM, w.SUB_PROGRAM, w.CUSTOMER_NAME,
    w.SITE_TYPE, w.PROJECT_TYPE, w.PROJECT_MANAGER
FROM scope s
LEFT JOIN PROD.REPORTING.RPT_WIP_PROJECT_DETAIL w ON w.PROJECT_NAME = s.NAME
WHERE NOT EXISTS (
        SELECT 1 FROM PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES m
        WHERE m.PROJECT_TRAN_ID = s.ID)
  AND EXISTS (
        SELECT 1 FROM RAW.TMP.PROJECT_EXT_C e
        WHERE e.PROJECT_C = s.ID
          AND COALESCE(e._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(e.IS_DELETED, FALSE) = FALSE)
ORDER BY s.CREATED_DATE;
```

*Expect 38 rows: 36 with `IN_WIP_VIEW` true and 2 false.* Look at the rows older than 30
days. Do they share a null or an unusual `PROGRAM`, `CUSTOMER_NAME` or `PROJECT_TYPE`?

**Query 3b: what the milestones view is built from.**

```sql
SELECT GET_DDL('VIEW', 'PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES');

SELECT REFERENCED_DATABASE, REFERENCED_SCHEMA, REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_DOMAIN
FROM SNOWFLAKE.ACCOUNT_USAGE.OBJECT_DEPENDENCIES
WHERE REFERENCING_DATABASE = 'PROD'
  AND REFERENCING_SCHEMA   = 'REPORTING'
  AND REFERENCING_OBJECT_NAME = 'RPT_PROJECT_UNPIVOT_MILESTONES'
ORDER BY 1, 2, 3;
```

The first statement also answers items 1, 2 and 5: it shows exactly which raw field each
milestone name is built from. (`ACCOUNT_USAGE` can lag by a few hours.)

**Query 3c: when each reporting object last changed.**

```sql
SELECT TABLE_NAME, TABLE_TYPE, CREATED, LAST_ALTERED
FROM PROD.INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'REPORTING'
  AND TABLE_NAME IN ('RPT_PROJECT_UNPIVOT_MILESTONES', 'RPT_WIP_PROJECT_DETAIL')
ORDER BY TABLE_NAME;
```

Run the same query against the upstream tables found in 3b, to compare their last build
times with the newest project's `CREATED_DATE` (from 3a).

---

## 4. One project in scope but missing from the WIP view

**What we found.** The WIP view's rules are: no test sites (`SITE_NAME_SEARCH` not like
`%TEST%`), and the templates New Site, Modification / Upgrade, Co-Location,
Co-Location Old and Co-Lo Initial Assessment. One active project that passes both tests
is still absent.

**What we need.** Why it is excluded. This may be the same cause as item 3.

**Query 4a.**

```sql
SELECT
    p.NAME                              AS PROJECT_NAME,
    p.CREATED_DATE,
    p.SITETRACKER_PROJECT_STATUS_C      AS STATUS,
    t.NAME                              AS TEMPLATE_NAME,
    st.NAME                             AS SITE_NAME
FROM RAW.TMP.SITETRACKER_PROJECT_C p
JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
JOIN RAW.TMP.SITETRACKER_SITE_C st            ON st.ID = p.SITETRACKER_SITE_C
LEFT JOIN PROD.REPORTING.RPT_WIP_PROJECT_DETAIL w ON w.PROJECT_NAME = p.NAME
WHERE COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
  AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
  AND COALESCE(st._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(st.IS_DELETED, FALSE) = FALSE
  AND t.NAME IN ('New Site', 'Modification / Upgrade', 'Co-Location', 'Co-Location Old',
                 'Co-Lo Initial Assessment')
  AND UPPER(st.NAME) NOT LIKE '%TEST%'
  AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
      'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
      'Acceptance & Closure', 'Decommissioning')
  AND w.PROJECT_NAME IS NULL
ORDER BY p.CREATED_DATE;
```

*Expect 1 row.* The PoC tests for a test site on the site name. The view uses
`SITE_NAME_SEARCH`, which may differ, so a second row would be a site-name difference.

---

## 5. Confirm all 12 milestone mappings

**What we found.** We only tested two milestones directly. Two others were not tested:
"Tower Installed" (the business says the front-end label "Tower Installed Finished" is stored
in the backend as `TOWER_INSTALL_START_*`, so the view should read the `START` columns) and
"Project Started" (we had assumed `NEW_SITE_PROJECT_KICKED_OFF_*` in `PROJECT_EXT_2_C`).

**What we need.** Confirmation that each of the 12 milestones reads the field below, or a
correction. The query shows, per milestone and candidate field, how many projects the
view agrees with. A correct mapping agrees on (nearly) all of them.

**Query 5a.** For the milestones with more than one candidate (Tower Installed, Equipment
Live), both candidates appear so you can see which one the view follows.

```sql
WITH scope AS (
    SELECT p.ID
    FROM RAW.TMP.SITETRACKER_PROJECT_C p
    JOIN RAW.TMP.SITETRACKER_PROJECT_TEMPLATE_C t ON t.ID = p.SITETRACKER_PROJECT_TEMPLATE_C
    WHERE t.NAME = 'New Site'
      AND COALESCE(p._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(p.IS_DELETED, FALSE) = FALSE
      AND COALESCE(t._FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(t.IS_DELETED, FALSE) = FALSE
      AND p.SITETRACKER_PROJECT_STATUS_C NOT IN (
          'Completed', 'Cancelled', 'Pending Cancellation', 'On-hold',
          'Acceptance & Closure', 'Decommissioning')
),
ext AS (
    SELECT * FROM RAW.TMP.PROJECT_EXT_C
    WHERE COALESCE(_FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(IS_DELETED, FALSE) = FALSE
),
ext2 AS (
    SELECT * FROM RAW.TMP.PROJECT_EXT_2_C
    WHERE COALESCE(_FIVETRAN_DELETED, FALSE) = FALSE AND COALESCE(IS_DELETED, FALSE) = FALSE
),
raw_pairs AS (
    SELECT PROJECT_C AS PROJECT_ID, 'Project Started' AS VIEW_MILESTONE, 'NEW_SITE_PROJECT_KICKED_OFF (ext2)' AS RAW_FIELD,
           CAST(NEW_SITE_PROJECT_KICKED_OFF_F_C AS DATE) AS F, CAST(NEW_SITE_PROJECT_KICKED_OFF_A_C AS DATE) AS A FROM ext2
    UNION ALL
    SELECT PROJECT_C, 'Site Candidate Report Compiled', 'SITE_CANDIDATE_REPORT_COMPILED',
           CAST(SITE_CANDIDATE_REPORT_COMPILED_F_C AS DATE), CAST(SITE_CANDIDATE_REPORT_COMPILED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Primary Candidate Confirmed', 'PRIMARY_CANDIDATE_CONFIRMED',
           CAST(PRIMARY_CANDIDATE_CONFIRMED_F_C AS DATE), CAST(PRIMARY_CANDIDATE_CONFIRMED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Candidate Proposed', 'CANDIDATE_PROPOSED',
           CAST(CANDIDATE_PROPOSED_F_C AS DATE), CAST(CANDIDATE_PROPOSED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Site Confirmation Notice Signed', 'CO_LO_SITE_CONFIRM_NOTICE_SIGNED',
           CAST(CO_LO_SITE_CONFIRM_NOTICE_SIGNED_F_C AS DATE), CAST(CO_LO_SITE_CONFIRM_NOTICE_SIGNED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Site & Land Plans Approved', 'SITE_LAND_PLANS_FINALISED',
           CAST(SITE_LAND_PLANS_FINALISED_F_C AS DATE), CAST(SITE_LAND_PLANS_FINALISED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Access Template Compiled & Issued', 'ACCESS_TEMPLATE_COMPILED_ISSUED',
           CAST(ACCESS_TEMPLATE_COMPILED_ISSUED_F_C AS DATE), CAST(ACCESS_TEMPLATE_COMPILED_ISSUED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Construction Drawings Approved', 'CONSTRUCTION_DRAWINGS_APPROVED',
           CAST(CONSTRUCTION_DRAWINGS_APPROVED_F_C AS DATE), CAST(CONSTRUCTION_DRAWINGS_APPROVED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Civil Work Started', 'CIVIL_WORK_START',
           CAST(CIVIL_WORK_START_F_C AS DATE), CAST(CIVIL_WORK_START_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Tower Installed', 'TOWER_INSTALL_START (expected)',
           CAST(TOWER_INSTALL_START_F_C AS DATE), CAST(TOWER_INSTALL_START_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Tower Installed', 'TOWER_INSTALL_FINISHED (the other candidate)',
           CAST(TOWER_INSTALL_FINISHED_F_C AS DATE), CAST(TOWER_INSTALL_FINISHED_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'RFI Certificate Sent to Customer', 'RFI_CERTIFICATE_SENT_TO_CUSTOMER',
           CAST(RFI_CERTIFICATE_SENT_TO_CUSTOMER_F_C AS DATE), CAST(RFI_CERTIFICATE_SENT_TO_CUSTOMER_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Connexa Notified Equipment is Live', 'NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L (expected)',
           CAST(NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_F_C AS DATE), CAST(NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_IS_L_A_C AS DATE) FROM ext
    UNION ALL
    SELECT PROJECT_C, 'Connexa Notified Equipment is Live', 'NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE (the stale one)',
           CAST(NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_F_C AS DATE), CAST(NOTIFY_CONNEXA_ACTIVE_EQUIPMENT_LIVE_A_C AS DATE) FROM ext
)
SELECT
    r.VIEW_MILESTONE,
    r.RAW_FIELD,
    COUNT(*)                                                    AS PROJECTS_COMPARED,
    COUNT_IF(m.FORECAST_DATE IS NOT DISTINCT FROM r.F)          AS FORECAST_AGREES,
    COUNT_IF(m.ACTUAL_DATE   IS NOT DISTINCT FROM r.A)          AS ACTUAL_AGREES
FROM raw_pairs r
JOIN scope s ON s.ID = r.PROJECT_ID
JOIN PROD.REPORTING.RPT_PROJECT_UNPIVOT_MILESTONES m
  ON m.PROJECT_TRAN_ID = r.PROJECT_ID
 AND m.MILESTONE_NAME  = r.VIEW_MILESTONE
 AND m.PROJECT_TEMPLATE_NAME = 'New Site'
GROUP BY r.VIEW_MILESTONE, r.RAW_FIELD
ORDER BY r.VIEW_MILESTONE, r.RAW_FIELD;
```

*Reading it:* one row per milestone and candidate field. `FORECAST_AGREES` and
`ACTUAL_AGREES` should be close to `PROJECTS_COMPARED` for the expected field. Where
there are two candidates, the one with the much higher agreement is the one the view reads.
These counts compare dates only. They will not show a milestone whose forecast and actual
both happen to be empty on both sides.

---

## What we would like back

1. The source field behind each of the 12 milestone names (query 3b's `GET_DDL`, or a
   short list), with a fix for items 1 and 2 if they are wrong.
2. An explanation for the 7 older projects missing from the milestones view, and for the
   1 missing from the WIP view.
3. The refresh schedule of the milestones view, the WIP view and the raw Fivetran tables,
   so we know how current each rule's data is.

Once the view is corrected, MS-001 and PRJ-002 do not need to change or be re-approved:
they read the views, not the raw fields.
