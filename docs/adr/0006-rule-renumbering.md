# ADR 0006: Rules can be renumbered, and what that moves

**Status:** Accepted

## Context

Rule codes (`SITE-003`) are chosen when a rule is created, so deleting abandoned
ideas leaves gaps and the numbering stops reflecting the rules that matter. The code
is copied into the rule's history, its run results and its findings, and a finding's
ID is a hash that includes the code, so a code can't be changed by editing one row.

## Decision

`dq rule renumber --map OLD:NEW ... [--dry-run]` changes codes everywhere in one
transaction:

- the rule, its history rows, its run results and its findings are updated by `RULE_ID`;
- every finding's `FINDING_ID` is recomputed from the new code, and its dispositions
  move with it, so accepted exceptions and false positives stay attached and the next
  run matches the same findings instead of raising them again;
- the first line of the stored SQL (`-- SITE-008 v1: ...`) is updated to match the file.

A new code may be one another rule in the same request is giving up (003 to 001, then
007 to 003). The request is checked first and refused, with nothing written, if a
code is unknown, malformed, has a different prefix, would be used twice, or is taken by
a rule that isn't also being renumbered. `--dry-run` prints counts only.

It writes the real store, so like `dq run` it is developer-only: Claude Code does not
run it, and you take a backup first.

Not changed: runs, object results and site scores (they aren't per rule); stored
narratives and the JSON snapshots inside history rows, which are historical records and
keep the code the rule had at the time. No audit-trail row is written, since the history
table has no "renumbered" event and adding one needs a migration; this ADR and the
commit record the mapping instead.

## Mapping applied

SITE-001 and SITE-002 (unstarted drafts) were deleted in the app. Then:

| Was | Now |
|---|---|
| SITE-003 | SITE-001 |
| SITE-008 | SITE-002 |
| SITE-007 | SITE-003 |

Earlier ADRs and commit messages refer to rules by their old codes (for example
SITE-008 in ADR 0003).

## Consequences

- The starter rules in `sql/dq_poc_duckdb_schema.sql` keep their original numbering. A
  store built fresh from that file will have the old codes; the real store is already
  initialised and is unaffected.
- The next new SITE rule takes the code after the highest in use.
