# ADR 0003: Add a `READY_FOR_COMPILE` status between `DRAFT` and `COMPILED`

**Status:** Accepted

## Context

While proving out the end-to-end owner lifecycle in the Streamlit app
(creating SITE-008 from scratch), it wasn't obvious how to tell the
compiler a rule was actually ready. `DRAFT` was overloaded: it meant both
"still being written" and "finished, waiting to be picked up", with no
way to tell the two apart short of asking the owner. The `/compile-rules`
workflow worked around this by treating every `DRAFT` rule as fair game,
plus separately checking `NEEDS_CLARIFICATION` rules for a filled-in
`CLARIFICATION_ANSWER`.

This also raised the question of whether compilation should be a button
in the app. It can't be, today: there's no `compile()` function anywhere
in this codebase — turning a plain-English statement into `CHECK_SQL`
means reading the catalogue and data dictionary and writing SQL, which is
inherently a Claude Code (or developer) task, not something a Streamlit
callback can do. (Brief §10.6 already anticipates the real fix as a
stretch goal: an in-app "Generate check with Cortex" button, calling an
LLM from inside the app itself.) Short of that, the actionable fix was
making the hand-off explicit instead of implicit.

## Decision

Split `DRAFT` into `DRAFT` (being written) and `READY_FOR_COMPILE`
(submitted, queued):

- A `DRAFT` rule moves to `READY_FOR_COMPILE` via a new "Submit for
  compilation" action in the app (`rule_ops.submit_rule`), or by
  answering a clarification question (`answer_clarification` now sets
  `READY_FOR_COMPILE` directly, instead of leaving the rule at
  `NEEDS_CLARIFICATION` until a separate compile pass noticed the
  answer).
- `/compile-rules` and its command doc now look at
  `uv run dq rule list --status READY_FOR_COMPILE` as the queue, full
  stop — no more special-casing answered clarifications.
- `dq rule set-sql` (`rule_ops.set_rule_sql` / `store.compile_rule`)
  still doesn't gate on status at all, so a developer can still recompile
  directly from `COMPILED`/`OWNER_CONFIRMED`/`ACTIVE` without routing
  back through `DRAFT` (this is how the SITE-004 proximity-check fix
  worked, and stays available).

This needed widening two `CHECK` constraints DuckDB can't alter in
place (`DQ_RULE.STATUS`, `DQ_RULE_HISTORY.CHANGE_TYPE` for the new
`SUBMITTED` change type) — `sql/migrations/0001_add_ready_for_compile_status.sql`
recreates both tables and copies every row across by name. Verified
against a simulated copy of the *old* schema (seeded, migrated, checked
row counts and values survived, checked the new status is insertable
afterwards) before handing it to Kevin to run for real.

The Streamlit app also grew a shared lifecycle stepper and per-status
"what happens next" guidance (`src/dq/app/ui.py`), shown on both pages
and the home page, so an owner can see where a rule sits and what to do
without needing the state machine explained to them first.

## Consequences

- The compile queue is now a real, filterable status instead of an
  overloaded one — `dq rule list --status READY_FOR_COMPILE` is
  unambiguous.
- One more status for anything reading `DQ_RULE.STATUS` directly to
  account for (store methods, any future export/reporting views).
- The real store needed a manual, one-off migration — DuckDB's
  can't-alter-CHECK-constraints limitation (noted at the top of
  `sql/dq_poc_duckdb_schema.sql`) means this will recur for any future
  status/enum addition. `sql/migrations/` now exists as the place for
  these.
- Brief §10.6's "Generate check with Cortex" stretch goal remains the
  real fix for making compilation itself self-service; this ADR doesn't
  attempt that, it just makes the current human-in-the-loop hand-off
  legible.
