# ADR 0005: A three-page top-navigation app; narratives hidden and off by default

**Status:** Accepted

## Context

The first version of the app had four pages and a home page that was only a menu,
built one at a time. A review (2026-11-06) found: a long Rule Catalogue with the
action to confirm a rule above the statement and SQL it depends on; Findings that
never said what a rule is for; a Run results page with eight sections; counts that
disagreed between pages (projects on one, project-milestones on the other); a
separate Add rule page just to reach a form; status colours that failed contrast;
and a sidebar taking a fifth of the width.

## Decision

- **Shell.** `streamlit_app.py` is a router with three pages in a top navigation bar
  (Overview, Findings, Rules), in `src/dq/app/views/`. No sidebar. Each page draws one
  compact header with the "Acting as" selector, whose choice is also kept under a plain
  session key because Streamlit drops widget state on a page change.
- **Rules** is a clickable list on the left and the selected rule on the right: a
  lifecycle strip, one primary button per state, a "More" menu, and Definition /
  SQL and preview / History tabs. New rule and delete are dialogs. The list is plain
  buttons, not a table: a click anywhere selects it, it works with a keyboard and a
  screen reader, and there is no table selection state to go stale.
- **Findings** is a table on the left and, on the right, the rules' plain-English
  statements, then the record, why it failed, and the action. Status pills carry the
  counts and are the main filter.
- **Overview** replaces Home and Run results: the latest run on one line, four numbers,
  a rules table with sparklines, and a to-do list. One unit everywhere: findings.
- **Narratives** are no longer shown in the app, and `dq run` writes them only with
  `--narratives` (`--no-narratives` is still accepted). The module, prompt and table stay
  so the weekly report (brief steps 7 and 9) can reuse them.
- **Site scores** are no longer shown. The engine still computes and stores them.
- **Layout CSS** lives in one place (`ui._LAYOUT_CSS`). Scrolling panes are sized from the
  viewport by overriding the height and `flex-basis` Streamlit puts on a container's
  layout wrapper, and their children are stopped from shrinking so content scrolls instead
  of squashing.
- **Cross-page links** use query parameters (`?rule=`, `?show=`), so the browser back
  button works.

## Consequences

- The CSS touches Streamlit's own markup (`stLayoutWrapper`, `stMainBlockContainer`,
  `stPageLink-NavLink`, `stBaseButton-*`). A Streamlit upgrade may need that one block
  revisited; the smoke tests will not catch a purely visual regression, so check the
  panes at about 1440 x 900 and 1280 x 720 after one.
- Pages are verified by `tests/test_app_pages.py` (Streamlit's AppTest: every page
  renders for an empty and a seeded store, key content is present, acting-as survives a
  page change) and by running the app against a disposable store (ADR 0002). Dialogs and
  popovers are only checked live.
- Overview counts are as at the last run; Findings is live. The Findings page says so.
- **Parked:** site scores by project manager. It needs a new optional contract column
  (for example `ASSIGNED_TO`) fed from `WIP_PROJECT.PROJECT_MANAGER`, stored on findings,
  and would also shape the weekly report. A separate step if wanted.
