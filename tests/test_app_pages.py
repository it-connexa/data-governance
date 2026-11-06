"""Smoke tests for the Streamlit pages, using Streamlit's AppTest harness against a
temporary store (built from the real schema file) and the synthetic sources file.
Never the real governance store, never Snowflake.

These check that every page renders without an exception for an empty store and for
a store with rules in every lifecycle state, findings and a run, that the pages show
what they should, and that the "acting as" choice survives a page change. Clicking
through dialogs and popovers is checked live in the browser instead.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from dq.store.base import FindingInput
from dq.store.duckdb_store import DuckDBStore

APP = str(Path(__file__).parent.parent / "src" / "dq" / "app" / "streamlit_app.py")
RUN_1 = "aaaaaaaa-0000-0000-0000-000000000001"
RUN_2 = "aaaaaaaa-0000-0000-0000-000000000002"

RULES = [
    # code, name, status, owner
    ("SITE-001", "Operational sites have a commissioning date", "ACTIVE", "alice@example.test"),
    ("SITE-002", "Site coordinates are within New Zealand", "COMPILED", "alice@example.test"),
    ("SITE-003", "Mandatory attributes are populated", "RETIRED", "alice@example.test"),
    ("SITE-004", "Sites have a land owner recorded", "DRAFT", "alice@example.test"),
    (
        "SITE-005",
        "Crown land sites have a lease reference",
        "READY_FOR_COMPILE",
        "alice@example.test",
    ),
    ("SITE-006", "Operational sites have a site manager", "OWNER_CONFIRMED", "alice@example.test"),
    ("MS-001", "No stale forecasts on key milestones", "NEEDS_CLARIFICATION", "alice@example.test"),
    ("PRJ-001", "Completed projects have closed status", "ACTIVE", "bob@example.test"),
]


def _write_settings(tmp_path: Path, store_path: Path, fixtures_dir: Path) -> Path:
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text(
        f"""
[snowflake]
account = "dummy"
user = "dummy"
role = "dummy"
warehouse = "dummy"
keychain_service = "dummy"
keychain_account = "dummy"

[paths]
duckdb = "{store_path}"
sources = "{fixtures_dir / "sources_test.yml"}"
export = "{tmp_path / "export"}"
backups = "{tmp_path / "backups"}"
"""
    )
    return settings_path


def _add_owners(store_path: Path) -> None:
    """Clear the starter rows the schema file seeds, then add three owners."""
    con = duckdb.connect(str(store_path))
    try:
        con.execute("DELETE FROM DQ_RULE_HISTORY")
        con.execute("DELETE FROM DQ_RULE")
        con.execute("DELETE FROM DQ_OWNER")
        for email, name, approver in [
            ("alice@example.test", "Alice Owner", False),
            ("bob@example.test", "Bob Owner", False),
            ("carol@example.test", "Carol Approver", True),
        ]:
            con.execute(
                "INSERT INTO DQ_OWNER (OWNER_EMAIL, DISPLAY_NAME, DOMAINS, IS_APPROVER) "
                "VALUES (?, ?, ['SITE_MASTER'], ?)",
                [email, name, approver],
            )
    finally:
        con.close()


def _seed_rules_findings_and_runs(store_path: Path) -> None:
    con = duckdb.connect(str(store_path))
    try:
        for code, name, status, owner in RULES:
            con.execute(
                """
                INSERT INTO DQ_RULE (RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                    RECORD_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CHECK_SQL,
                    CREATED_BY)
                VALUES (?, ?, ?, 'SITE_MASTER', 'SITE', 'sites', 'SITE', 'VALIDITY', 'HIGH', ?, ?,
                    ?, 'test')
                """,
                [
                    code,
                    name,
                    f"{name}. Plain-English statement for {code}.",
                    owner,
                    status,
                    "SELECT s.ID AS RECORD_ID FROM {{ src.SITE }} s"
                    if status in ("ACTIVE", "COMPILED", "OWNER_CONFIRMED")
                    else None,
                ],
            )
        rule_ids = dict(con.execute("SELECT RULE_CODE, RULE_ID FROM DQ_RULE").fetchall())
    finally:
        con.close()

    store = DuckDBStore(store_path)
    now = datetime.now(UTC)
    for run_id, run_at, n_fail in [(RUN_1, now - timedelta(days=7), 3), (RUN_2, now, 2)]:
        store.start_run(run_id, "MANUAL", "tester", run_at)
        for code in ("SITE-001", "PRJ-001"):
            rows = [
                FindingInput(
                    f"{code}-{i}",
                    None,
                    f"Record {code}-{i}",
                    f"s{i}",
                    f"Site {i}",
                    i >= n_fail,
                    '{"reason": "synthetic"}',
                )
                for i in range(6)
            ]
            store.apply_findings(
                run_id=run_id,
                run_at=run_at,
                rule_id=rule_ids[code],
                rule_code=code,
                rule_version=1,
                source_object="SITE",
                sf_object="Site__c",
                sf_base_url="https://example.invalid",
                rows=rows,
            )
            store.write_rule_result(
                run_id,
                rule_ids[code],
                code,
                1,
                {
                    "status": "SUCCEEDED",
                    "object_total": 10,
                    "records_evaluated": 6,
                    "records_failed": n_fail,
                    "records_failed_open": n_fail,
                    "pass_rate": 50.0,
                    "items_evaluated": 6,
                    "items_failed": n_fail,
                    "items_failed_open": n_fail,
                    "item_pass_rate": 50.0,
                    "duration_ms": 10,
                },
            )
        store.finish_run(
            run_id,
            status="SUCCEEDED",
            finished_at=run_at + timedelta(minutes=1),
            rules_attempted=2,
            rules_succeeded=2,
            rules_errored=0,
        )
    store.flag_rule_for_review(
        "SITE-001", "Looks stale.", flagged_by="alice@example.test", facilitated_by="tester"
    )


@pytest.fixture
def empty_app(tmp_path, store_path, fixtures_dir, monkeypatch) -> AppTest:
    _add_owners(store_path)
    monkeypatch.setenv("DQ_SETTINGS_PATH", str(_write_settings(tmp_path, store_path, fixtures_dir)))
    return AppTest.from_file(APP, default_timeout=30)


@pytest.fixture
def seeded_app(tmp_path, store_path, fixtures_dir, monkeypatch) -> AppTest:
    _add_owners(store_path)
    _seed_rules_findings_and_runs(store_path)
    monkeypatch.setenv("DQ_SETTINGS_PATH", str(_write_settings(tmp_path, store_path, fixtures_dir)))
    return AppTest.from_file(APP, default_timeout=30)


def _text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [i.value for i in at.info] + [w.value for w in at.warning]
    parts += [str(m.value) for m in at.metric]
    return "\n".join(str(p) for p in parts)


# --- empty store: every page renders its empty state ------------------------------------


@pytest.mark.parametrize("page", ["views/overview.py", "views/findings.py", "views/rules.py"])
def test_every_page_renders_for_an_empty_store(empty_app, page):
    at = empty_app.run()
    assert not at.exception, at.exception
    at.switch_page(page).run()
    assert not at.exception, at.exception


def test_empty_overview_and_findings_say_what_to_do(empty_app):
    at = empty_app.run()
    assert "No runs yet" in _text(at)
    at.switch_page("views/findings.py").run()
    assert "No findings yet" in _text(at)


# --- seeded store ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["views/overview.py", "views/findings.py", "views/rules.py"])
def test_every_page_renders_for_a_seeded_store(seeded_app, page):
    at = seeded_app.run()
    assert not at.exception, at.exception
    at.switch_page(page).run()
    assert not at.exception, at.exception


def test_overview_shows_the_runs_numbers_and_a_to_do_list(seeded_app):
    at = seeded_app.run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Open findings"] == "2"  # SITE-001 only: alice's rules (PRJ-001 is bob's)
    assert metrics["Critical open"] == "0"  # the seeded rules are HIGH
    assert "Latest run" in _text(at)
    # SITE-002 is compiled, so it is waiting on Alice's confirmation: a to-do link.
    links = [e.proto.label for e in at.get("page_link")]
    assert any(label.startswith("Confirm SITE-002") for label in links), links
    assert any(label.startswith("Submit SITE-004") for label in links), links


def test_rules_lists_every_rule_with_its_status_and_a_primary_action(seeded_app):
    at = seeded_app.run()
    at.switch_page("views/rules.py").run()
    pick_buttons = {b.key for b in at.button if b.key and b.key.startswith("pick_")}
    assert pick_buttons == {f"pick_{code}" for code, *_ in RULES if code != "PRJ-001"}
    # The first rule is selected by default and its lifecycle strip is drawn.
    assert any("dq-steps" in str(m.value) or "dq-retired" in str(m.value) for m in at.markdown)


def test_clicking_a_rule_selects_it(seeded_app):
    at = seeded_app.run()
    at.switch_page("views/rules.py").run()
    at.button(key="pick_SITE-002").click().run()
    assert not at.exception, at.exception
    assert at.session_state["selected_rule_code"] == "SITE-002"
    # A compiled rule asks for exactly one thing: confirmation.
    assert any(b.label.startswith("Confirm") for b in at.button)


def test_findings_page_shows_counts_in_the_pills_and_the_rules_behind_them(seeded_app):
    at = seeded_app.run()
    at.switch_page("views/findings.py").run()
    assert not at.exception, at.exception
    text = _text(at)
    assert "findings" in text
    assert "Plain-English statement for SITE-001" in "\n".join(
        str(e.value) for e in at.markdown
    ) or any("SITE-001" in str(e.label) for e in at.expander)


# --- acting as ------------------------------------------------------------------------------


def test_acting_as_choice_survives_a_page_change(seeded_app):
    at = seeded_app.run()
    at.selectbox(key="acting_as_widget").select("carol@example.test").run()
    assert at.session_state["acting_as_email"] == "carol@example.test"
    at.switch_page("views/rules.py").run()
    assert not at.exception, at.exception
    assert at.session_state["acting_as_email"] == "carol@example.test"
    assert at.selectbox(key="acting_as_widget").value == "carol@example.test"
