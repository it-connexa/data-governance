# ADR 0002: The raw `streamlit run` command stays unblocked for Claude Code, unlike `dq app`

**Status:** Accepted, revisit before handover

## Context

`.claude/settings.json` denies `Bash(uv run dq app:*)` — brief §7 marks `dq app`
as developer-only, since launching the real Streamlit app means driving the
real governance store and (via preview/run actions) real Snowflake.

Building the Streamlit app (brief §10.6) needed the same kind of live check
any frontend change needs: start it, click through it, see that it actually
renders and behaves. `src/dq/app/context.py` supports this by reading a
`DQ_SETTINGS_PATH` environment variable, defaulting to the normal
`.dq/settings.toml` — Claude Code used this to point the app at a disposable
temp store (built fresh from `sql/dq_poc_duckdb_schema.sql`, seeded with
synthetic rules, never touching `~/dq-data/governance.duckdb`) and ran
`streamlit run src/dq/app/streamlit_app.py` directly to check the pages in
the browser pane.

This is not the same command as `dq app` — `.claude/settings.json`'s deny
entry doesn't cover it. Kevin confirmed he's happy leaving this path open
so future UI changes can be verified the same way, rather than every
Streamlit change needing a manual round trip.

## Decision

Leave `streamlit run`/`uv run streamlit` unblocked in `.claude/settings.json`
for now. Claude Code may run it directly, but only against a temp store it
creates itself (via `DQ_SETTINGS_PATH`), and must clean the temp store up
afterwards. It must never point this command at the real `.dq/settings.toml`
or `~/dq-data/governance.duckdb` — that would be equivalent to running the
denied `dq app` command for real, and is out of bounds the same way.

## Consequences

- Streamlit UI changes get the same "verify in a browser before calling it
  done" treatment as any other frontend work, without needing a permission
  prompt every time.
- The guardrail here is judgement (Claude Code choosing a temp store), not
  a structural block the way `.claude/settings.json`'s deny list is for
  `dq app`/`dq run`/`dq preview`/`dq export`. That's a weaker guarantee.
- Revisit before handover to the AI Solutions Specialist: either add an
  explicit deny for the raw command (closing the gap, at the cost of
  needing a different verification approach later), or confirm this
  trade-off is acceptable long-term.
