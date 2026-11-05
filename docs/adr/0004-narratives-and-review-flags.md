# ADR 0004: Narratives send aggregates only; the review flag is a history row

**Status:** Accepted

## Context

Step 6/8 added two things that needed a design choice: the Cortex owner
narratives (brief §10.4) and the "flag this rule for review" prompt shown
when an owner marks a finding as a false positive (brief §10.6, page 4).

## Decision

**Narratives.**
- Only aggregated facts reach the model: per-rule counts and change versus
  the previous run, new/resolved totals, site *names* with critical/high
  counts, the five oldest open findings as (rule, site, age), source
  freshness expressed as hours before the run, and the run's NZ date. Never
  `DETAIL`, record names, error messages or any free text from source
  records. A test seeds a secret `DETAIL` and record name and asserts neither
  appears in the input.
- The exact input is stored with each narrative (`DQ_NARRATIVE.INPUT_SUMMARY`)
  and shown on the Run results page ("What was sent to Cortex") so an owner
  can see it.
- The Cortex call sits behind a `NarrativeCompleter` protocol, separate from
  the engine's `RuleExecutor`, with the model and prompt passed as bound
  parameters (`SnowflakeClient.execute(params=...)`) -- never interpolated
  into the SQL text.
- A failed narrative is stored as `ERRORED` and never raised: it can't fail a
  run. `dq run` generates narratives after the run is closed out; pass
  `--no-narratives` to skip them.

**Review flag.** Recorded as `REVIEW_FLAGGED` / `REVIEW_CLEARED` rows in
`DQ_RULE_HISTORY` rather than a new column on `DQ_RULE`. A flag stays open
until a `REVIEW_CLEARED`, or until the rule is recompiled or its statement
edited (someone acted on it). This keeps the audit trail append-only and
reuses `add_history`, at the cost of migration 0002: DuckDB can't widen a
CHECK in place, so `DQ_RULE_HISTORY` is recreated (same pattern as 0001).

## Consequences

- Cortex cost is bounded to one call per owner per run (plus any manual
  regenerate). Not yet measured against the real account -- brief step 10.
- Flagging fails on a store that hasn't had migration 0002 applied; reading
  flags works either way.
- Narratives are only as good as the prompt (`prompts/narrative_v1.md`,
  versioned via `DQ_CONFIG.NARRATIVE_PROMPT_VERSION`); expect to iterate on it
  once real output has been read.
