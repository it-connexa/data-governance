import pytest

from dq.sources import (
    LintError,
    UnknownSourceError,
    lint,
    load_sources,
    render,
    resolve_target_source,
)


@pytest.fixture
def config(fixtures_dir):
    return load_sources(fixtures_dir / "sources_test.yml")


# --- loader -----------------------------------------------------------------


def test_loads_top_level_config(config):
    assert config.database == "TESTDB"
    assert config.schema == "TESTSCHEMA"
    assert config.freshness_column == "_FIVETRAN_SYNCED"
    assert len(config.default_filters) == 2


def test_loads_sources_with_defaults(config):
    site = config.sources["SITE"]
    assert site.table == "SITE__C"
    assert site.sf_object == "Site__c"
    assert site.record_label == "sites"
    assert site.columns == ["*"]
    assert site.item_label is None
    assert site.apply_default_filters is True


def test_loads_source_with_explicit_columns_and_filters(config):
    project = config.sources["PROJECT"]
    assert project.columns == ["ID", "NAME", "STATUS__C"]
    assert project.filters == ["STATUS__C IS NOT NULL"]


def test_loads_source_with_apply_default_filters_false(config):
    legacy = config.sources["NO_DEFAULT_FILTERS"]
    assert legacy.apply_default_filters is False


# --- renderer -----------------------------------------------------------------


def test_render_substitutes_single_source(config):
    rendered = render("SELECT s.ID FROM {{ src.SITE }} s", config)

    assert "{{ src.SITE }}" not in rendered
    assert "TESTDB.TESTSCHEMA.SITE__C" in rendered
    assert "SELECT *" in rendered
    assert "_FIVETRAN_DELETED" in rendered
    assert "IS_DELETED" in rendered


def test_render_uses_explicit_column_list(config):
    rendered = render("SELECT p.ID FROM {{ src.PROJECT }} p", config)

    assert "SELECT ID, NAME, STATUS__C FROM TESTDB.TESTSCHEMA.PROJECT__C" in rendered
    assert "STATUS__C IS NOT NULL" in rendered


def test_render_skips_default_filters_when_disabled(config):
    rendered = render("SELECT l.ID FROM {{ src.NO_DEFAULT_FILTERS }} l", config)

    assert "_FIVETRAN_DELETED" not in rendered
    assert "ARCHIVED__C = FALSE" in rendered


def test_render_substitutes_multiple_sources(config):
    sql = "SELECT m.ID FROM {{ src.PROJECT }} p JOIN {{ src.SITE }} s ON s.ID = p.SITE_ID"
    rendered = render(sql, config)

    assert "TESTDB.TESTSCHEMA.PROJECT__C" in rendered
    assert "TESTDB.TESTSCHEMA.SITE__C" in rendered


def test_render_unknown_source_raises(config):
    with pytest.raises(UnknownSourceError, match="NOT_A_SOURCE"):
        render("SELECT * FROM {{ src.NOT_A_SOURCE }}", config)


# --- linter: happy path -------------------------------------------------------


def test_lint_accepts_simple_select(config):
    lint("SELECT s.ID AS RECORD_ID FROM {{ src.SITE }} s WHERE s.STATUS__C = 'Active'", config)


def test_lint_accepts_multi_cte_query(config):
    sql = """
    WITH scoped AS (
        SELECT p.ID, p.NAME FROM {{ src.PROJECT }} p
    ),
    items AS (
        SELECT ID, NAME, 'Milestone A' AS ITEM_KEY FROM scoped
    )
    SELECT ID, NAME, ITEM_KEY FROM items
    """
    lint(sql, config)


# --- linter: bypass attempts --------------------------------------------------


def test_lint_rejects_three_part_identifier(config):
    with pytest.raises(LintError, match="three-part"):
        lint("SELECT s.ID FROM TESTDB.TESTSCHEMA.SITE__C s", config)


def test_lint_rejects_raw_schema_reference(config):
    with pytest.raises(LintError, match="database or schema"):
        lint("SELECT s.ID FROM TESTSCHEMA.SITE__C s", config)


def test_lint_rejects_raw_database_name_in_text(config):
    with pytest.raises(LintError, match="database or schema"):
        lint(
            "SELECT s.ID FROM {{ src.SITE }} s WHERE s.NOTE = 'see TESTDB for details'",
            config,
        )


def test_lint_rejects_multiple_statements(config):
    with pytest.raises(LintError, match="single statement|multiple statements"):
        lint("SELECT 1 FROM {{ src.SITE }}; SELECT 2 FROM {{ src.SITE }}", config)


def test_lint_rejects_trailing_semicolon(config):
    with pytest.raises(LintError, match="semicolon"):
        lint("SELECT s.ID FROM {{ src.SITE }} s;", config)


def test_lint_ignores_semicolon_hidden_in_comment(config):
    # A semicolon inside a comment is not a real statement separator and must
    # not be miscounted as a bypass attempt.
    lint("SELECT s.ID FROM {{ src.SITE }} s /* ; */ WHERE s.ID IS NOT NULL", config)


def test_lint_still_rejects_real_second_statement_after_comment(config):
    with pytest.raises(LintError, match="single statement|multiple statements"):
        lint(
            "SELECT s.ID FROM {{ src.SITE }} s -- trailing comment\n; DROP TABLE X",
            config,
        )


@pytest.mark.parametrize(
    "keyword,sql",
    [
        ("INSERT", "INSERT INTO {{ src.SITE }} (ID) VALUES (1)"),
        ("UPDATE", "UPDATE {{ src.SITE }} SET ID = 1"),
        ("DELETE", "DELETE FROM {{ src.SITE }}"),
        (
            "MERGE",
            "MERGE INTO {{ src.SITE }} USING {{ src.PROJECT }} ON TRUE WHEN MATCHED THEN DELETE",
        ),
        ("CREATE", "CREATE TABLE X (ID INT)"),
        ("ALTER", "ALTER TABLE {{ src.SITE }} ADD COLUMN X INT"),
        ("DROP", "DROP TABLE {{ src.SITE }}"),
        ("GRANT", "GRANT SELECT ON TABLE X TO ROLE Y"),
        ("CALL", "CALL SOME_PROC()"),
        ("COPY", "COPY INTO X FROM {{ src.SITE }}"),
        ("USE", "USE ROLE FOO"),
        ("SET", "SET X = 1"),
        ("EXECUTE", "EXECUTE IMMEDIATE 'SELECT 1'"),
    ],
)
def test_lint_rejects_dml_ddl_control_statements(config, keyword, sql):
    with pytest.raises(LintError):
        lint(sql, config)


def test_lint_rejects_sample(config):
    with pytest.raises(LintError, match="SAMPLE"):
        lint("SELECT * FROM {{ src.SITE }} SAMPLE (10)", config)


def test_lint_rejects_tablesample(config):
    with pytest.raises(LintError, match="SAMPLE"):
        lint("SELECT * FROM {{ src.SITE }} TABLESAMPLE (10 ROWS)", config)


def test_lint_rejects_random(config):
    with pytest.raises(LintError, match="RANDOM"):
        lint("SELECT RANDOM() FROM {{ src.SITE }}", config)


def test_lint_rejects_top_level_limit(config):
    with pytest.raises(LintError, match="LIMIT"):
        lint("SELECT * FROM {{ src.SITE }} LIMIT 10", config)


def test_lint_allows_limit_in_subquery(config):
    # A LIMIT nested inside a derived table is not a top-level LIMIT.
    sql = "SELECT * FROM (SELECT * FROM {{ src.SITE }} ORDER BY ID LIMIT 10) t"
    lint(sql, config)


# --- resolve_target_source (milestone rules resolve via PROJECT) --------------


def test_resolve_target_source_passes_through_known_sources():
    assert resolve_target_source("SITE") == "SITE"
    assert resolve_target_source("PROJECT") == "PROJECT"


def test_resolve_target_source_maps_milestone_to_project():
    assert resolve_target_source("MILESTONE") == "PROJECT"


# --- per-source database / schema / freshness / registry fields ----------------


@pytest.fixture
def multi(fixtures_dir):
    return load_sources(fixtures_dir / "sources_multi_schema_test.yml")


def test_source_location_defaults_to_file_level_database_and_schema(multi):
    assert multi.location("SITE") == ("TESTDB", "TESTSCHEMA")


def test_source_location_honours_per_source_overrides(multi):
    assert multi.location("RPT_PROJECT") == ("REPDB", "REPSCHEMA")


def test_source_location_can_override_only_the_schema(multi):
    assert multi.location("RPT_NO_FRESHNESS") == ("TESTDB", "REPSCHEMA")


def test_source_location_unknown_source_raises(multi):
    with pytest.raises(UnknownSourceError, match="NOPE"):
        multi.location("NOPE")


def test_registry_fields_default_to_empty(multi):
    site = multi.sources["SITE"]
    assert site.grain == ""
    assert site.not_for == ""


def test_registry_fields_are_loaded(multi):
    project = multi.sources["RPT_PROJECT"]
    assert project.grain == "One row per project"
    assert "RPT_PROJECT_HISTORY" in project.not_for


def test_freshness_column_for_defaults_to_file_level_column(multi):
    assert multi.freshness_column_for("SITE") == "_FIVETRAN_SYNCED"


def test_freshness_column_for_honours_per_source_override(multi):
    assert multi.freshness_column_for("RPT_PROJECT") == "LOADED_AT"


def test_freshness_column_for_is_none_when_freshness_check_is_off(multi):
    assert multi.freshness_column_for("RPT_NO_FRESHNESS") is None


def test_render_uses_per_source_database_and_schema(multi):
    rendered = render("SELECT p.ID FROM {{ src.RPT_PROJECT }} p", multi)

    assert "REPDB.REPSCHEMA.RPT_PROJECT" in rendered
    assert "TESTDB" not in rendered
    assert "_FIVETRAN_DELETED" not in rendered  # apply_default_filters: false


def test_render_mixes_sources_from_different_schemas(multi):
    rendered = render(
        "SELECT 1 FROM {{ src.SITE }} s JOIN {{ src.RPT_NO_FRESHNESS }} m ON m.ID = s.ID", multi
    )

    assert "TESTDB.TESTSCHEMA.SITE__C" in rendered
    assert "TESTDB.REPSCHEMA.RPT_MILESTONES" in rendered


def test_lint_rejects_override_schema_reference(multi):
    with pytest.raises(LintError, match="database or schema"):
        lint("SELECT p.ID FROM REPSCHEMA.RPT_PROJECT p", multi)


def test_lint_rejects_override_database_name_in_text(multi):
    with pytest.raises(LintError, match="REPDB"):
        lint("SELECT p.ID FROM {{ src.RPT_PROJECT }} p WHERE p.NOTE = 'REPDB'", multi)


def test_lint_rejects_override_three_part_identifier(multi):
    with pytest.raises(LintError, match="three-part"):
        lint("SELECT p.ID FROM REPDB.REPSCHEMA.RPT_PROJECT p", multi)


def test_lint_still_rejects_file_level_names_when_overrides_exist(multi):
    with pytest.raises(LintError, match="database or schema"):
        lint("SELECT s.ID FROM TESTSCHEMA.SITE__C s", multi)


def test_lint_allows_raw_names_inside_comments(multi):
    # Real database and schema names ("PROD", "REPORTING") are ordinary English
    # words, so a header comment that mentions one must not fail the lint.
    lint(
        "-- reads the REPSCHEMA table via the registry; REPDB is not referenced\n"
        "SELECT p.ID AS RECORD_ID FROM {{ src.RPT_PROJECT }} p /* REPDB */",
        multi,
    )
