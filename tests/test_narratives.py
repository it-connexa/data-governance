"""Tests for narratives.py (brief §10.4): the aggregated input sent to Cortex,
prompt rendering and storage -- against a temp DuckDB store and a fake
completer, never Snowflake."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from dq import narratives
from dq.store.base import FindingInput
from dq.store.duckdb_store import DuckDBStore

OWNER = "owner@connexa.co.nz"
OTHER = "other@connexa.co.nz"
RUN_1 = "11111111-1111-1111-1111-111111111111"
RUN_2 = "22222222-2222-2222-2222-222222222222"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 8, tzinfo=UTC)
SECRET_DETAIL = "SECRET-DETAIL-PAYLOAD"
SECRET_DETAIL_JSON = json.dumps({"note": SECRET_DETAIL})
SECRET_RECORD = "SECRET-RECORD-NAME"


class FakeCompleter:
    def __init__(
        self, text: str = "  Things look better this week.  ", error: Exception | None = None
    ):
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def complete(self, model, prompt, *, query_tag):
        self.calls.append((model, prompt, query_tag))
        if self.error:
            raise self.error
        return self.text


def insert_owner(store_path, email: str, name: str, approver: bool = False) -> None:
    con = duckdb.connect(str(store_path))
    con.execute(
        "INSERT INTO DQ_OWNER (OWNER_EMAIL, DISPLAY_NAME, DOMAINS, IS_APPROVER) "
        "VALUES (?, ?, ?, ?)",
        [email, name, ["SITE_MASTER"], approver],
    )
    con.close()


def insert_rule(store_path, code: str, owner: str, severity: str = "HIGH", status="ACTIVE") -> int:
    con = duckdb.connect(str(store_path))
    con.execute(
        """
        INSERT INTO DQ_RULE (RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
            RECORD_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CREATED_BY)
        VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'sites', 'SITE', 'VALIDITY', ?, ?, ?, 'test')
        """,
        [code, f"{code} name", severity, owner, status],
    )
    rule_id = con.execute("SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [code]).fetchone()[0]
    con.close()
    return rule_id


def result(failed_open: int, evaluated: int = 100) -> dict:
    return {
        "status": "SUCCEEDED",
        "object_total": evaluated,
        "records_evaluated": evaluated,
        "records_failed": failed_open,
        "records_failed_open": failed_open,
        "pass_rate": 100 - failed_open,
        "items_evaluated": evaluated,
        "items_failed": failed_open,
        "items_failed_open": failed_open,
        "item_pass_rate": 100 - failed_open,
        "duration_ms": 5,
    }


def run_rule(store, rule_id, code, run_id, at, failing: list[str], passing: list[str]):
    rows = [
        FindingInput(
            r, None, f"{SECRET_RECORD}-{r}", f"s-{r}", f"Site {r}", False, SECRET_DETAIL_JSON
        )
        for r in failing
    ] + [
        FindingInput(r, None, f"{SECRET_RECORD}-{r}", f"s-{r}", f"Site {r}", True, None)
        for r in passing
    ]
    store.apply_findings(
        run_id=run_id,
        run_at=at,
        rule_id=rule_id,
        rule_code=code,
        rule_version=1,
        source_object="SITE",
        sf_object="Site__c",
        sf_base_url="https://x.invalid",
        rows=rows,
    )
    store.write_rule_result(run_id, rule_id, code, 1, result(len(failing)))


@pytest.fixture
def store(store_path) -> DuckDBStore:
    insert_owner(store_path, OWNER, "Olivia Owner")
    insert_owner(store_path, OTHER, "Otto Other")
    s = DuckDBStore(store_path)
    s.start_run(RUN_1, "SCHEDULED", "kevin", T1)
    s.start_run(RUN_2, "MANUAL", "kevin", T2)
    return s


@pytest.fixture
def seeded(store_path, store):
    """OWNER: SITE-900 (CRITICAL) 5 failing in run 1 -> 3 in run 2 (2 fixed, 1 new).
    OTHER: SITE-901 with its own failures, which must never reach OWNER's input."""
    a = insert_rule(store_path, "SITE-900", OWNER, "CRITICAL")
    b = insert_rule(store_path, "SITE-901", OTHER, "HIGH")
    run_rule(store, a, "SITE-900", RUN_1, T1, ["a1", "a2", "a3", "a4", "a5"], [])
    run_rule(store, a, "SITE-900", RUN_2, T2, ["a1", "a2", "a6"], ["a3", "a4", "a5"])
    run_rule(store, b, "SITE-901", RUN_1, T1, ["b1"], [])
    run_rule(store, b, "SITE-901", RUN_2, T2, ["b1", "b2"], [])
    for run_id, at in ((RUN_1, T1), (RUN_2, T2)):
        store.record_source_freshness(run_id, "SITE", at - timedelta(hours=3))
        store.finish_run(
            run_id,
            status="SUCCEEDED",
            finished_at=at + timedelta(minutes=1),
            rules_attempted=2,
            rules_succeeded=2,
            rules_errored=0,
        )
    return store


# --- build_input -------------------------------------------------------------------


def test_input_has_per_rule_counts_and_change_versus_the_previous_run(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    assert [r["code"] for r in data["rules"]] == ["SITE-900"]
    rule = data["rules"][0]
    assert rule["severity"] == "CRITICAL"
    assert rule["records_evaluated"] == 100
    assert rule["exceptions"] == 3
    assert rule["previous_exceptions"] == 5
    assert rule["change"] == -2


def test_input_for_a_rules_first_run_has_no_previous(seeded):
    data = narratives.build_input(seeded, RUN_1, OWNER)

    rule = data["rules"][0]
    assert rule["previous_exceptions"] is None
    assert rule["change"] is None


def test_input_is_scoped_to_the_owner(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    text = json.dumps(data)
    assert "SITE-901" not in text
    assert "Site b1" not in text


def test_input_counts_new_and_resolved_findings_in_this_run(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    assert data["findings"]["new"] == 1  # a6
    assert data["findings"]["resolved"] == 3  # a3, a4, a5 now pass
    assert data["findings"]["open_total"] == 3
    assert data["findings"]["open_by_severity"] == {"CRITICAL": 3}


def test_input_lists_top_sites_by_critical_and_high_open_findings_names_only(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    sites = {s["site"]: s for s in data["top_sites"]}
    assert set(sites) == {"Site a1", "Site a2", "Site a6"}
    assert all(set(s) == {"site", "critical", "high"} for s in data["top_sites"])
    assert sites["Site a1"]["critical"] == 1


def test_top_sites_are_capped_at_ten_and_ignore_lower_severities(store_path, store):
    rid = insert_rule(store_path, "SITE-910", OWNER, "CRITICAL")
    low = insert_rule(store_path, "SITE-911", OWNER, "LOW")
    run_rule(store, rid, "SITE-910", RUN_2, T2, [f"c{i:02d}" for i in range(15)], [])
    run_rule(store, low, "SITE-911", RUN_2, T2, ["low1"], [])

    data = narratives.build_input(store, RUN_2, OWNER)

    assert len(data["top_sites"]) == 10
    assert "Site low1" not in [s["site"] for s in data["top_sites"]]


def test_input_has_the_five_oldest_open_findings_as_rule_site_and_age(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    assert 1 <= len(data["oldest_open"]) <= 5
    assert all(set(o) == {"rule", "site", "age_days"} for o in data["oldest_open"])
    ages = [o["age_days"] for o in data["oldest_open"]]
    assert ages == sorted(ages, reverse=True)


def test_input_includes_source_freshness(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    assert data["source_freshness"] == [{"source": "SITE", "synced_hours_before_run": 3.0}]
    assert data["run_date"] == "2026-01-08"  # NZ date of the run


def test_input_never_contains_detail_payloads_or_record_names(seeded):
    data = narratives.build_input(seeded, RUN_2, OWNER)

    text = json.dumps(data)
    assert SECRET_DETAIL not in text
    assert SECRET_RECORD not in text


def test_input_lists_owner_rules_that_errored_without_their_messages(store_path, seeded):
    bad = insert_rule(store_path, "SITE-920", OWNER, "HIGH")
    seeded.write_rule_result(
        RUN_2, bad, "SITE-920", 1, {"status": "ERRORED", "error_message": "table X.Y.Z missing"}
    )

    data = narratives.build_input(seeded, RUN_2, OWNER)

    assert data["errored_rules"] == ["SITE-920"]
    assert "table X.Y.Z" not in json.dumps(data)


# --- generate ------------------------------------------------------------------------


@pytest.fixture
def configured(store_path, seeded):
    con = duckdb.connect(str(store_path))
    con.execute(
        "UPDATE DQ_CONFIG SET CONFIG_VALUE = 'test-model' WHERE CONFIG_KEY = 'CORTEX_MODEL'"
    )
    con.close()
    return seeded


def test_generate_stores_a_succeeded_narrative_with_its_input(configured):
    completer = FakeCompleter()

    result = narratives.generate(configured, completer, OWNER, run_id=RUN_2, now=T2)

    assert result.status == "SUCCEEDED"
    stored = configured.get_latest_narrative(OWNER)
    assert stored["NARRATIVE"] == "Things look better this week."  # stripped
    assert stored["MODEL"] == "test-model"
    assert stored["PROMPT_VERSION"] == "v1"
    assert json.loads(stored["INPUT_SUMMARY"])["rules"][0]["code"] == "SITE-900"


def test_generate_sends_model_the_rendered_prompt_and_a_query_tag(configured):
    completer = FakeCompleter()

    narratives.generate(configured, completer, OWNER, run_id=RUN_2, now=T2)

    model, prompt, tag = completer.calls[0]
    assert model == "test-model"
    assert "SITE-900" in prompt  # the input JSON was substituted in
    assert "{{" not in prompt  # no unfilled placeholder left behind
    assert tag.startswith("dq_poc:narrative:")


def test_generate_regenerating_replaces_the_previous_narrative(configured):
    narratives.generate(configured, FakeCompleter("first"), OWNER, run_id=RUN_2, now=T2)
    narratives.generate(configured, FakeCompleter("second"), OWNER, run_id=RUN_2, now=T2)

    assert configured.get_latest_narrative(OWNER)["NARRATIVE"] == "second"


def test_generate_records_an_error_instead_of_raising(configured):
    completer = FakeCompleter(error=RuntimeError("cortex down"))

    result = narratives.generate(configured, completer, OWNER, run_id=RUN_2, now=T2)

    assert result.status == "ERRORED"
    stored = configured.get_latest_narrative(OWNER)
    assert stored["STATUS"] == "ERRORED"
    assert "cortex down" in stored["ERROR_MESSAGE"]
    assert stored["NARRATIVE"] is None


def test_generate_treats_a_blank_completion_as_an_error(configured):
    result = narratives.generate(configured, FakeCompleter("   "), OWNER, run_id=RUN_2, now=T2)

    assert result.status == "ERRORED"


def test_generate_without_a_configured_model_errors_without_calling_cortex(seeded):
    completer = FakeCompleter()  # CORTEX_MODEL is still the '<CORTEX_MODEL>' placeholder

    result = narratives.generate(seeded, completer, OWNER, run_id=RUN_2, now=T2)

    assert result.status == "ERRORED"
    assert "CORTEX_MODEL" in result.error
    assert completer.calls == []


def test_generate_with_a_missing_prompt_file_errors(configured, tmp_path):
    result = narratives.generate(
        configured, FakeCompleter(), OWNER, run_id=RUN_2, now=T2, prompts_dir=tmp_path
    )

    assert result.status == "ERRORED"
    assert "narrative_v1.md" in result.error


def test_generate_uses_the_configured_prompt_version(configured, store_path, tmp_path):
    (tmp_path / "narrative_v7.md").write_text("Custom v7 prompt:\n{{ input_json }}")
    con = duckdb.connect(str(store_path))
    con.execute(
        "UPDATE DQ_CONFIG SET CONFIG_VALUE = 'v7' WHERE CONFIG_KEY = 'NARRATIVE_PROMPT_VERSION'"
    )
    con.close()
    completer = FakeCompleter()

    narratives.generate(configured, completer, OWNER, run_id=RUN_2, now=T2, prompts_dir=tmp_path)

    assert completer.calls[0][1].startswith("Custom v7 prompt:")
    assert configured.get_latest_narrative(OWNER)["PROMPT_VERSION"] == "v7"


# --- generate_all --------------------------------------------------------------------


def test_generate_all_covers_each_owner_with_an_active_rule(configured, store_path):
    insert_owner(store_path, "norules@connexa.co.nz", "No Rules")
    completer = FakeCompleter()

    results = narratives.generate_all(configured, completer, RUN_2, now=T2)

    assert sorted(r.owner_email for r in results) == sorted([OWNER, OTHER])
    assert len(completer.calls) == 2


def test_generate_all_carries_on_after_one_owner_fails(configured):
    class FlakyCompleter(FakeCompleter):
        def complete(self, model, prompt, *, query_tag):
            if "SITE-900" in prompt:
                raise RuntimeError("boom")
            return "ok"

    results = narratives.generate_all(configured, FlakyCompleter(), RUN_2, now=T2)

    assert {r.owner_email: r.status for r in results} == {OWNER: "ERRORED", OTHER: "SUCCEEDED"}


# --- the shipped prompt ----------------------------------------------------------------


def test_the_shipped_v1_prompt_has_the_placeholder_and_the_brief_s_requirements():
    prompt = (Path(narratives.DEFAULT_PROMPTS_DIR) / "narrative_v1.md").read_text()

    assert "{{ input_json }}" in prompt
    for requirement in ("150", "250", "New Zealand English", "possibilit", "invent"):
        assert requirement in prompt
