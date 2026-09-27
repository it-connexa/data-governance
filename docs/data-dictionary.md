# Data dictionary

Business definitions the rule compiler needs and can't infer from
`INFORMATION_SCHEMA` alone. Filled in incrementally as rules are compiled —
see `docs/SETUP.md` step 1c. Field names below are Fivetran column names
(uppercase, as they appear in Snowflake), confirmed via `DESCRIBE TABLE`.

## Site (`SITETRACKER_SITE_C`)

**Status field:** `SITETRACKER_SITE_STATUS_C`. Observed values:
`Abandoned - Not Built`, `Assessment`, `Cancelled`, `Decommission`,
`Decommissioned - Removed`, `Decommissioned - left in situ`,
`In Acquisition`, `In Build`, `Landbanked`, `On Hold`, `Operational`,
`Pending Cancellation`.

- **"Active" site** (used by SITE-004): `SITETRACKER_SITE_STATUS_C` **not**
  in (`Cancelled`, `Pending Cancellation`, `Abandoned - Not Built`,
  `Decommission`, `Decommissioned - Removed`, `Decommissioned - left in situ`,
  `Landbanked`) — i.e. `Assessment`, `In Acquisition`, `In Build`, `On Hold`,
  `Operational` count as active. `On Hold` is included but was called out as
  debatable; revisit if it should be excluded.
- **"Decommissioned" site** (used by PRJ-002): `SITETRACKER_SITE_STATUS_C`
  in (`Decommission`, `Decommissioned - Removed`, `Decommissioned - left in
  situ`) — includes the in-progress `Decommission` value, not just the two
  completed states.
- **Site identifier** (used by SITE-004's duplicate check): `SITE_ID_C`.
  There are other candidate fields (`CUSTOMER_SITE_ID_C`,
  `HISTORICAL_SITE_ID_C`) not yet confirmed as wrong, just not chosen.
- **Site ID finalised flag** (used by SITE-004 v2 and SITE-008):
  `SITE_ID_FINALIZED_C` (`BOOLEAN`). While unset/false, `SITE_ID_C` is
  provisional and duplicates or a slow finalisation aren't violations.
- **Site record creation date** (used by SITE-008): `CREATED_DATE`
  (`TIMESTAMP_TZ`), the Salesforce system field.
- **Coordinates:** `SITETRACKER_LOCATION_LATITUDE_S` /
  `SITETRACKER_LOCATION_LONGITUDE_S` (`FLOAT`, decimal degrees). Prefer
  these over `SITETRACKER_LAT_C`/`SITETRACKER_LONG_C` (`VARCHAR`, likely
  DMS-formatted strings) for distance calculations.
- **Tower type** ("Lightpole" value, needed for SITE-006): not yet
  confirmed. Candidate field: `TOWER_TYPE_C`.
- Commissioning/decommission date fields (needed for SITE-001/SITE-002):
  not yet identified.

## Project (`SITETRACKER_PROJECT_C`)

**Status field:** `SITETRACKER_PROJECT_STATUS_C`. Observed values:
`Acceptance & Closure`, `Acquisition`, `Active Build`, `Assessment`,
`Cancelled`, `Completed`, `Decommissioning`, `Design`, `Detailed Design`,
`On-hold`, `Passive Build`, `Pending Cancellation`.

- **"Active" project** (used by PRJ-002): `SITETRACKER_PROJECT_STATUS_C`
  **not** in (`Completed`, `Cancelled`, `Pending Cancellation`, `On-hold`,
  `Acceptance & Closure`, `Decommissioning`) — i.e. `Acquisition`,
  `Active Build`, `Assessment`, `Design`, `Detailed Design`, `Passive Build`
  count as active. `Decommissioning` is deliberately excluded: a project
  whose own job is decommissioning a site is expected to be linked to a
  site that is now `Decommission`/`Decommissioned`, which is the normal
  case, not the violation PRJ-002 checks for.
- **Site lookup:** `SITETRACKER_SITE_C` (references `SITE.ID`).
- Template names and which milestones matter per template (needed for
  MS-001/MS-002/PRJ-001): not yet identified — owners provide these in
  facilitated sessions per brief §16.

## Milestone / Project Template / Equipment

Not yet scoped — `sources.yml` doesn't have real table names for these
yet (only `SITE` and `PROJECT` are filled in, for SITE-004 and PRJ-002).
