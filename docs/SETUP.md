# Data Governance PoC — Database Setup Steps

These are the steps you need to action yourself, or hand to your Snowflake admin, before Claude Code starts building. They take roughly half a day of elapsed effort, plus admin turnaround.

| # | Step | Who | Where |
|---|---|---|---|
| 1 | Describe the in-scope tables and export field labels | You (+ Sitetracker admin for labels) | Snowflake, Salesforce |
| 2 | Confirm Cortex availability | You + admin | Snowflake |
| 3 | Generate a key pair and store it in Keychain | You | Mac terminal |
| 4 | Create the read-only role, service user and warehouse | Admin | Snowflake |
| 5 | Verify read-only access | You | Mac terminal |
| 6 | Create the local DuckDB governance store | You | Mac terminal |
| 7 | Load owners and config | You | DuckDB |
| 8 | Set up the SharePoint export library | You | SharePoint / OneDrive |
| 9 | Set up local settings and backups | You | Mac |

---

## Step 1 — Identify the Fivetran tables and fields

**Goal:** fill in `sources.yml`, the table placeholders in `snowflake_readonly_setup.sql`, and produce a field label file.

**1a. Describe the tables.** Using your own Snowflake login, `DESCRIBE` each in-scope table: site, project, milestone, project template and equipment. `SHOW TABLES LIKE '%MILESTONE%' IN SCHEMA <FIVETRAN_DB>.<SALESFORCE_SCHEMA>;` helps with any name you're unsure of.

```sql
DESCRIBE TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<SITE_TABLE>;
DESCRIBE TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<PROJECT_TABLE>;
DESCRIBE TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<MILESTONE_TABLE>;
DESCRIBE TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<PROJECT_TEMPLATE_TABLE>;
DESCRIBE TABLE <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<EQUIPMENT_TABLE>;
```

In each output, check for:
- `_FIVETRAN_DELETED`, `IS_DELETED` and `_FIVETRAN_SYNCED`. If a table lacks either delete column, set `apply_default_filters: false` on that source in `sources.yml` and add the right filter explicitly.
- The **lookup columns** that link the tables: milestone → project, project → template, project → site, and equipment → site. Rules depend on these joins.

Fill in `database`, `schema` and each source's `table` in `sources.yml`. For `sf_object`, use the Salesforce API name from Sitetracker's Object Manager; it's used for record deep links.

**1b. Export field labels.** `DESCRIBE` gives API names (e.g. `M030_FORECAST_DATE__C`), but owners will write rules using labels (e.g. "Site Acquisition — Forecast"). With 100+ milestone columns, the compiler needs the mapping. Produce a CSV with columns `source,column_name,label`, where `source` is the logical name from `sources.yml` (SITE, MILESTONE, and so on) and `column_name` is the API name in uppercase, as Fivetran stores it. Options, easiest first:
- Ask your Sitetracker or Salesforce admin for a field export per object. Object Manager, Schema Builder or a metadata tool can all produce one.
- Run a one-off describe call against the Salesforce API (Python `simple-salesforce` or a Workato recipe) and write out `name` and `label` per field.

Save it as `field_labels.csv`. It's loaded with `dq catalogue load-labels` once Claude Code has built that command.

**1c. Start the data dictionary.** In `docs/data-dictionary.md`, record what the seed rules need:
- site status values, and what "active" and "operational" mean
- tower type values (confirm the exact "Lightpole" value)
- project status values, and what "active" means for a project
- the template names owners care about
- for each of those templates, the handful of milestones that matter most

The status values matter most. They answer the first clarification questions before they're asked.

## Step 2 — Confirm Cortex availability

Ask the admin to run the following as `ACCOUNTADMIN` and send you the results:

```sql
SELECT CURRENT_REGION();
SHOW PARAMETERS LIKE 'CORTEX_ENABLED_CROSS_REGION' IN ACCOUNT;
```

Check Snowflake's documentation for which models run natively in that region. If cross-region inference is enabled, prompts may be processed outside your region, so decide whether that's acceptable for aggregated narrative inputs and record the decision (ADR). Pick a model and note it for step 7.

## Step 3 — Generate a key pair and store it in Keychain

`.dq/` is a project-local, gitignored folder for local Snowflake connection
config (see `docs/adr/0001-local-config-location.md`). From the repo root:

```bash
mkdir -p .dq && chmod 700 .dq
cd .dq

# Generate private key (PKCS#8) and public key
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub

# Store the private key in macOS Keychain (base64-encoded, single line)
security add-generic-password -a dq_poc -s dq-snowflake-key -w "$(base64 -i rsa_key.p8)"

# Confirm it's there (prints the value length, not the key)
security find-generic-password -a dq_poc -s dq-snowflake-key -w | wc -c

# Remove the private key file
rm rsa_key.p8

cd ..
```

Send the admin the **public key body only**: the contents of `rsa_key.pub` without the `-----BEGIN PUBLIC KEY-----` / `-----END PUBLIC KEY-----` lines, joined into one line:

```bash
grep -v "PUBLIC KEY" .dq/rsa_key.pub | tr -d '\n'
```

The app reads the key from Keychain through `keyring`. Nothing sensitive stays on disk — `.dq/rsa_key.pub` is the public half, harmless if seen, and `.gitignore` already excludes `.dq/` so it's never committed.

## Step 4 — Admin creates the read-only access

Send the admin `snowflake_readonly_setup.sql` with the placeholders filled in:
- the Fivetran database, schema and five table names from step 1 (site, project, milestone, project template, equipment)
- the public key body from step 3
- a monthly credit quota; start small, as 5–10 credits is ample for weekly runs on XS
- optionally, your office or VPN egress IPs for the network policy

The script creates only a role, a service user, a warehouse and a resource monitor. It doesn't create any schemas or tables. Point the admin to section 6 of the script, which is the teardown for the end of the PoC.

Worth mentioning to the admin: the resource monitor caps warehouse credits, not Cortex usage. You'll track Cortex spend separately.

## Step 5 — Verify read-only access

Install the tools you'll need for this and later steps:

```bash
brew install uv duckdb
```

Save this as `.dq/verify.py` (from the repo root) and run it with `uv run --with snowflake-connector-python --with keyring --with cryptography python .dq/verify.py`, filling in your account identifier and one table name:

```python
import base64, keyring, snowflake.connector
from cryptography.hazmat.primitives import serialization

pem = base64.b64decode(keyring.get_password("dq-snowflake-key", "dq_poc"))
key = serialization.load_pem_private_key(pem, password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption())

con = snowflake.connector.connect(
    account="<ACCOUNT_IDENTIFIER>", user="DQ_POC_SVC", private_key=der,
    role="DQ_POC_READER", warehouse="DQ_POC_WH",
    session_parameters={"QUERY_TAG": "dq_poc:verify"},
)
cur = con.cursor()

print(cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()").fetchone())
print("Site rows:", cur.execute(
    "SELECT COUNT(*) FROM <FIVETRAN_DB>.<SALESFORCE_SCHEMA>.<SITE_TABLE> "
    "WHERE COALESCE(_FIVETRAN_DELETED, FALSE) = FALSE").fetchone())

# Must FAIL — proves the role can't write
try:
    cur.execute("CREATE TEMPORARY TABLE DQ_WRITE_TEST (X INT)")
    print("WARNING: role can create objects — check grants")
except Exception as e:
    print("Write correctly blocked:", type(e).__name__)

print("Cortex:", cur.execute(
    "SELECT SNOWFLAKE.CORTEX.COMPLETE('<CORTEX_MODEL>', 'Reply with the single word: ready')").fetchone())
```

You should see:
- the service user, role and warehouse
- a site row count
- the write correctly blocked
- a Cortex reply

If the Cortex call fails, check the model name and region from step 2.

A temporary table needs a current database and schema, so the write test may fail for that reason rather than privileges. Either way it's blocked. The real guarantee is that the role holds no `CREATE` grants, which the admin can confirm with `SHOW GRANTS TO ROLE DQ_POC_READER;`.

## Step 6 — Create the local DuckDB governance store

```bash
# Local, unsynced, FileVault-encrypted location
mkdir -p ~/dq-data && chmod 700 ~/dq-data

# Confirm FileVault is on
fdesetup status

# Replace owner placeholders in the schema first (step 7), or update afterwards.
duckdb ~/dq-data/governance.duckdb < dq_poc_duckdb_schema.sql
```

Check it:

```bash
duckdb ~/dq-data/governance.duckdb -c "SELECT RULE_CODE, STATUS, OWNER_EMAIL FROM DQ_RULE ORDER BY RULE_CODE;"
```

You should see 12 draft rules. Several contain `<TEMPLATE_NAME>`, `<MILESTONE_A>` or field placeholders on purpose; owners fill these in during facilitated sessions. The schema script is safe to re-run, because it won't duplicate seed data.

Make sure `~/dq-data` isn't inside a folder synced by iCloud Drive ("Desktop & Documents" sync) or OneDrive. Your home directory root is fine.

## Step 7 — Load owners and config

Open the store with `duckdb ~/dq-data/governance.duckdb` and run the following with real values. Owner emails must match their Entra UPNs, because Power BI RLS depends on it.

```sql
-- Replace placeholder owners
UPDATE DQ_OWNER SET OWNER_EMAIL = 'jane.smith@connexa.co.nz', DISPLAY_NAME = 'Jane Smith'
WHERE OWNER_EMAIL = '<owner.site@connexa.co.nz>';

UPDATE DQ_OWNER SET OWNER_EMAIL = 'sam.jones@connexa.co.nz', DISPLAY_NAME = 'Sam Jones'
WHERE OWNER_EMAIL = '<owner.projects@connexa.co.nz>';

UPDATE DQ_OWNER SET OWNER_EMAIL = 'kevin@connexa.co.nz', DISPLAY_NAME = 'Kevin'
WHERE OWNER_EMAIL = '<approver@connexa.co.nz>';

-- Repoint seeded rules to the real owners
UPDATE DQ_RULE SET OWNER_EMAIL = 'jane.smith@connexa.co.nz' WHERE OWNER_EMAIL = '<owner.site@connexa.co.nz>';
UPDATE DQ_RULE SET OWNER_EMAIL = 'sam.jones@connexa.co.nz'  WHERE OWNER_EMAIL = '<owner.projects@connexa.co.nz>';

-- Config
UPDATE DQ_CONFIG SET CONFIG_VALUE = 'https://<yourdomain>.lightning.force.com' WHERE CONFIG_KEY = 'SF_BASE_URL';
UPDATE DQ_CONFIG SET CONFIG_VALUE = '<CORTEX_MODEL>'                          WHERE CONFIG_KEY = 'CORTEX_MODEL';
UPDATE DQ_CONFIG SET CONFIG_VALUE = '<EXPORT_PATH from step 8>'               WHERE CONFIG_KEY = 'EXPORT_PATH';

-- Check nothing is left as a placeholder
SELECT 'owner' AS t, OWNER_EMAIL FROM DQ_OWNER WHERE OWNER_EMAIL LIKE '<%'
UNION ALL SELECT 'rule', RULE_CODE FROM DQ_RULE WHERE OWNER_EMAIL LIKE '<%'
UNION ALL SELECT 'config', CONFIG_KEY FROM DQ_CONFIG WHERE CONFIG_VALUE LIKE '<%';
```

The final query should return no rows. Add more owners with `INSERT INTO DQ_OWNER` as needed. Set `IS_APPROVER = TRUE` only for people who approve rule SQL.

## Step 8 — Set up the SharePoint export library

1. Create a document library, for example `Data Governance PoC` in an appropriate IT or Data site, and restrict membership to you, the data owners and the Power BI dataset owner.
2. Add a folder `exports`.
3. Sync it with the OneDrive client, or use "Add shortcut to My files", and note the local path. It will look something like `~/Library/CloudStorage/OneDrive-Connexa/Data Governance PoC - exports`.
4. Use that path as `EXPORT_PATH` in step 7 and in `.dq/settings.toml`.

This synced folder only ever receives the Parquet extracts. The DuckDB file stays in `~/dq-data`.

Power BI connects later with the SharePoint Folder connector, reading the Parquet files, with RLS on `OWNER_EMAIL` using `USERPRINCIPALNAME()`. That's part of the report build, not this setup.

## Step 9 — Local settings and backups

Create `.dq/settings.toml` at the project root (copy `config/settings.example.toml`
and fill in real values). Claude Code will scaffold the loader against this shape:

```toml
[snowflake]
account   = "<ACCOUNT_IDENTIFIER>"
user      = "DQ_POC_SVC"
role      = "DQ_POC_READER"
warehouse = "DQ_POC_WH"
keychain_service = "dq-snowflake-key"
keychain_account = "dq_poc"

[paths]
duckdb     = "~/dq-data/governance.duckdb"
sources    = "<repo>/config/sources.yml"
export     = "<EXPORT_PATH>"
backups    = "~/dq-data/backups"
```

```bash
chmod 600 .dq/settings.toml
mkdir -p ~/dq-data/backups
```

**Backups:** after each weekly run, with the app closed, run:

```bash
duckdb ~/dq-data/governance.duckdb -c "EXPORT DATABASE '$HOME/dq-data/backups/$(date +%Y-%m-%d)' (FORMAT parquet);"
```

Keep the last 4–6 exports. This goes in the runbook, and Claude Code can wrap it as `dq backup` later.

---

## Ready for Claude Code when

- [ ] `sources.yml` has no placeholders, and the data dictionary covers the seed rules' fields
- [ ] `field_labels.csv` exported for the in-scope objects
- [ ] Verify script passes: data read, write blocked, Cortex responds
- [ ] `governance.duckdb` exists with 12 draft rules, real owners and no placeholder config
- [ ] SharePoint export folder is synced locally and restricted
- [ ] `.dq/settings.toml` is in place
- [ ] Repo created with `docs/BRIEF.md`, `config/sources.yml`, `sql/` files and `.claude/settings.json` from brief §6

Then start Claude Code with build order step 1 in the brief.
