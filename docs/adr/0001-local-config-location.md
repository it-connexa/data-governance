# ADR 0001: Local Snowflake connection config lives in `.dq/` at the project root, not `~/.dq/`

**Status:** Accepted

## Context

`docs/SETUP.md` originally put the local, non-secret connection config
(`settings.toml`: Snowflake account/role/warehouse, local paths) and the
public half of the key pair (`rsa_key.pub`) under `~/.dq/` in the user's
home directory, alongside `CLAUDE.md`'s deny-list entry blocking Claude
Code from reading it.

The private key itself never touches disk either way: `docs/SETUP.md`
step 3 base64-encodes it straight into macOS Keychain (service
`dq-snowflake-key`, account `dq_poc`) and deletes the key file
immediately after. So this decision is only about where `settings.toml`
and `rsa_key.pub` live — not about the private key.

Kevin asked to keep this local config under the project folder instead,
gitignored, rather than in the home directory.

## Decision

Local connection config lives in `.dq/` at the repo root:
- `.dq/settings.toml` (copied from `config/settings.example.toml`)
- `.dq/rsa_key.pub` (the public key, harmless if seen)
- `.dq/verify.py` (the one-off Snowflake connectivity check from
  `docs/SETUP.md` step 5)

`.dq/` is gitignored. `src/dq/settings.py`'s `DEFAULT_SETTINGS_PATH` is
`.dq/settings.toml`, resolved relative to the current working directory
(the project root, since `dq` is run via `uv run dq ...` from there).
`CLAUDE.md`'s hard rule and `.claude/settings.json`'s deny list block
reads of `.dq/**` the same way they previously blocked `~/.dq/**`.

`~/dq-data/governance.duckdb` (the governance store) is unaffected by
this decision — it stays in the home directory, unsynced and
FileVault-encrypted, per `docs/SETUP.md` step 6's separate reasoning.

## Consequences

- One fewer path to remember: everything project-specific (code, docs,
  local config) sits under one root.
- The private key's actual protection is still Keychain, so this change
  doesn't weaken it. Keeping `settings.toml` gitignored inside the repo
  relies on `.gitignore` being correct; a home-directory location would
  have relied on it never being inside a repo at all. This is judged an
  acceptable trade for the convenience, given the file itself never holds
  the private key.
- Anyone cloning or copying the whole project directory (e.g. for
  backup, or handing the repo to the AI Solutions Specialist) must
  remember to exclude `.dq/` explicitly if copying outside of git.
