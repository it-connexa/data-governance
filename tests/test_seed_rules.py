"""Lints and renders the compiled seed rules in rules/sql/ against the real
config/sources.yml -- catches drift between the two without ever touching
Snowflake. This is the local check `dq rule set-sql` will eventually
automate (brief §10.8); until that CLI command exists, this test is it.
"""

from pathlib import Path

import pytest

from dq.sources import lint, load_sources, render

REPO_ROOT = Path(__file__).parent.parent
RULES_DIR = REPO_ROOT / "rules" / "sql"


@pytest.fixture(scope="module")
def real_sources_config():
    return load_sources(REPO_ROOT / "config" / "sources.yml")


@pytest.mark.parametrize("rule_code", ["SITE-004", "PRJ-002"])
def test_seed_rule_lints_and_renders_cleanly(real_sources_config, rule_code):
    sql = (RULES_DIR / f"{rule_code}.sql").read_text()

    lint(sql, real_sources_config)  # raises LintError on any violation
    rendered = render(sql, real_sources_config)

    assert "{{ src." not in rendered
    assert "RAW.TMP.SITETRACKER_SITE_C" in rendered or "RAW.TMP.SITETRACKER_PROJECT_C" in rendered
