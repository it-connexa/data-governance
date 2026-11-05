"""Tests for the reporting reads, narrative storage and rule review flag on
DuckDBStore -- against a temp DB built from the real schema file."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from dq.store.duckdb_store import DuckDBStore

RUNS = [
    ("aaaaaaaa-0000-0000-0000-000000000001", datetime(2026, 1, 1, tzinfo=UTC)),
    ("aaaaaaaa-0000-0000-0000-000000000002", datetime(2026, 1, 8, tzinfo=UTC)),
    ("aaaaaaaa-0000-0000-0000-000000000003", datetime(2026, 1, 15, tzinfo=UTC)),
]
OWNER = "owner@connexa.co.nz"


def insert_rule(store_path, *, rule_code: str, owner: str = OWNER, status: str = "ACTIVE") -> int:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (
                RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT, RECORD_LABEL,
                SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS, CREATED_BY
            ) VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'sites', 'SITE', 'VALIDITY', 'HIGH',
                      ?, ?, 'test')
            """,
            [rule_code, f"{rule_code} test rule", owner, status],
        )
        return con.execute(
            "SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code]
        ).fetchone()[0]
    finally:
        con.close()


def rule_result(failed_open: int, evaluated: int = 100) -> dict:
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
        "duration_ms": 10,
    }


def seed_runs(store_path, store: DuckDBStore) -> int:
    """Three SUCCEEDED runs of one rule with 10, 8 then 5 failures."""
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    for (run_id, at), failed in zip(RUNS, (10, 8, 5), strict=True):
        store.start_run(run_id, "MANUAL", "kevin", at)
        store.write_rule_result(run_id, rule_id, "SITE-900", 1, rule_result(failed))
        store.record_source_freshness(run_id, "SITE", at - timedelta(hours=2))
        store.finish_run(
            run_id,
            status="SUCCEEDED",
            finished_at=at + timedelta(minutes=1),
            rules_attempted=1,
            rules_succeeded=1,
            rules_errored=0,
        )
    return rule_id


# --- runs ------------------------------------------------------------------------


def test_list_runs_newest_first_and_limited(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    runs = store.list_runs(limit=2)

    assert [r["RUN_ID"] for r in runs] == [RUNS[2][0], RUNS[1][0]]
    assert runs[0]["STATUS"] == "SUCCEEDED"


def test_get_latest_run_ignores_failed_and_running_runs(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)
    store.start_run(
        "bbbbbbbb-0000-0000-0000-000000000009", "MANUAL", "kevin", RUNS[2][1] + timedelta(days=1)
    )

    assert store.get_latest_run()["RUN_ID"] == RUNS[2][0]
    assert store.list_runs(limit=1)[0]["STATUS"] == "RUNNING"


def test_get_latest_run_is_none_on_an_empty_store(store_path):
    store = DuckDBStore(store_path)

    assert store.get_latest_run() is None
    assert store.list_runs() == []


def test_get_run_rule_errors_lists_only_errored_rules(store_path):
    store = DuckDBStore(store_path)
    rule_id = seed_runs(store_path, store)
    bad_id = insert_rule(store_path, rule_code="SITE-901")
    run_id = RUNS[2][0]
    store.write_rule_result(
        run_id, bad_id, "SITE-901", 1, {"status": "ERRORED", "error_message": "boom"}
    )

    errors = store.get_run_rule_errors(run_id)

    assert errors == [{"RULE_CODE": "SITE-901", "ERROR_MESSAGE": "boom"}]
    assert rule_id  # the succeeded rule is not listed


# --- rule and object results -------------------------------------------------------


def test_rule_summaries_latest_has_headline_sentence_for_latest_run_only(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    rows = store.list_rule_summaries_latest()

    assert len(rows) == 1
    assert rows[0]["RUN_ID"] == RUNS[2][0]
    assert "SITE-900 as at 15-Jan-2026" in rows[0]["SUMMARY_TEXT"]
    assert "of 100 sites, 5 exceptions" in rows[0]["SUMMARY_TEXT"]


def test_rule_summaries_latest_filters_by_owner(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    assert store.list_rule_summaries_latest(OWNER)
    assert store.list_rule_summaries_latest("someone@else.co.nz") == []


def test_get_rule_trend_returns_oldest_first_and_caps_runs_per_rule(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    trend = store.get_rule_trend(runs=2)

    assert [float(t["EXCEPTION_RATE"]) for t in trend] == [8.0, 5.0]
    assert [t["RULE_CODE"] for t in trend] == ["SITE-900", "SITE-900"]

    assert len(store.get_rule_trend(rule_code="SITE-900")) == 3
    assert store.get_rule_trend(rule_code="NOPE-1") == []


def test_object_summaries_latest(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)
    store.write_object_result(
        RUNS[2][0],
        {
            "target_object": "SITE",
            "record_label": "sites",
            "object_total": 100,
            "active_rules": 1,
            "rules_succeeded": 1,
            "records_evaluated": 100,
            "records_with_exception": 5,
            "open_findings": 5,
            "exception_rate": 5.0,
        },
    )

    rows = store.list_object_summaries_latest()

    assert [r["TARGET_OBJECT"] for r in rows] == ["SITE"]
    assert "5 have at least one exception" in rows[0]["SUMMARY_TEXT"]


def test_site_scores_latest_worst_first(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)
    scores = [
        ("s1", "Site One", 90.0, 1, 0),
        ("s2", "Site Two", 40.0, 4, 2),
        ("s3", "Site Three", None, 0, 0),
    ]
    store.write_site_scores(
        RUNS[2][0],
        RUNS[2][1],
        [
            {
                "site_id": sid,
                "site_name": name,
                "rules_applicable": 3,
                "rules_failed": 1,
                "open_findings": findings,
                "critical_open": crit,
                "weighted_score": score,
            }
            for sid, name, score, findings, crit in scores
        ],
    )

    rows = store.list_site_scores_latest()

    assert [r["SITE_ID"] for r in rows] == ["s2", "s1", "s3"]  # NULL score last
    assert [r["SITE_ID"] for r in store.list_site_scores_latest(limit=1)] == ["s2"]


def test_source_freshness_latest(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    rows = store.get_source_freshness_latest()

    assert [r["SOURCE_NAME"] for r in rows] == ["SITE"]
    assert rows[0]["LAST_SYNCED_AT"] == RUNS[2][1] - timedelta(hours=2)


# --- narratives ----------------------------------------------------------------------


def test_write_narrative_then_get_latest_returns_it(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    store.write_narrative(
        RUNS[2][0],
        OWNER,
        status="SUCCEEDED",
        narrative="Things improved.",
        model="m1",
        prompt_version="v1",
        input_summary={"rules": [{"code": "SITE-900"}]},
        generated_at=RUNS[2][1],
    )

    latest = store.get_latest_narrative(OWNER)
    assert latest["NARRATIVE"] == "Things improved."
    assert latest["STATUS"] == "SUCCEEDED"
    assert latest["MODEL"] == "m1"
    assert '"SITE-900"' in latest["INPUT_SUMMARY"]  # JSON returned as text
    assert store.get_latest_narrative("someone@else.co.nz") is None


def test_write_narrative_replaces_the_same_run_and_owner(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)
    for text in ("first", "second"):
        store.write_narrative(
            RUNS[2][0],
            OWNER,
            status="SUCCEEDED",
            narrative=text,
            model="m1",
            prompt_version="v1",
            input_summary={},
            generated_at=RUNS[2][1],
        )

    assert store.get_latest_narrative(OWNER)["NARRATIVE"] == "second"
    con = duckdb.connect(str(store_path))
    assert con.execute("SELECT COUNT(*) FROM DQ_NARRATIVE").fetchone()[0] == 1
    con.close()


def test_errored_narrative_is_stored_with_its_error(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)

    store.write_narrative(
        RUNS[2][0],
        OWNER,
        status="ERRORED",
        narrative=None,
        model="m1",
        prompt_version="v1",
        input_summary={},
        error_message="cortex unavailable",
        generated_at=RUNS[2][1],
    )

    latest = store.get_latest_narrative(OWNER)
    assert latest["STATUS"] == "ERRORED"
    assert latest["ERROR_MESSAGE"] == "cortex unavailable"


# --- rule review flag ---------------------------------------------------------------


def flag(store: DuckDBStore, code: str = "SITE-900", reason: str = "too many false positives"):
    store.flag_rule_for_review(code, reason, flagged_by=OWNER, facilitated_by="kevin")


def test_flagging_a_rule_records_history_and_is_readable(store_path):
    store = DuckDBStore(store_path)
    insert_rule(store_path, rule_code="SITE-900")

    assert store.get_rule_review_flag("SITE-900") is None
    flag(store)

    got = store.get_rule_review_flag("SITE-900")
    assert got["REASON"] == "too many false positives"
    assert got["FLAGGED_BY"] == OWNER
    assert got["FLAGGED_AT"] is not None
    assert store.get_rule("SITE-900")["STATUS"] == "ACTIVE"  # flagging never changes status


def test_clearing_the_flag(store_path):
    store = DuckDBStore(store_path)
    insert_rule(store_path, rule_code="SITE-900")
    flag(store)

    store.clear_rule_review_flag("SITE-900", cleared_by=OWNER, facilitated_by="kevin")

    assert store.get_rule_review_flag("SITE-900") is None


@pytest.mark.parametrize("change_type", ["SQL_COMPILED", "STATEMENT_EDITED"])
def test_recompiling_or_editing_the_rule_closes_the_flag(store_path, change_type):
    store = DuckDBStore(store_path)
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    flag(store)

    store.add_history(
        rule_id=rule_id,
        rule_code="SITE-900",
        version=1,
        change_type=change_type,
        status_after="ACTIVE",
        snapshot={},
        changed_by=OWNER,
    )

    assert store.get_rule_review_flag("SITE-900") is None


def test_flagging_again_after_clearing_reopens_it(store_path):
    store = DuckDBStore(store_path)
    insert_rule(store_path, rule_code="SITE-900")
    flag(store, reason="first")
    store.clear_rule_review_flag("SITE-900", cleared_by=OWNER)
    flag(store, reason="second")

    assert store.get_rule_review_flag("SITE-900")["REASON"] == "second"


def test_list_rules_flagged_for_review(store_path):
    store = DuckDBStore(store_path)
    insert_rule(store_path, rule_code="SITE-900")
    insert_rule(store_path, rule_code="SITE-901")
    flag(store, "SITE-901")

    flagged = store.list_rules_flagged_for_review()

    assert list(flagged) == ["SITE-901"]
    assert flagged["SITE-901"]["REASON"] == "too many false positives"


def test_flagging_an_unknown_rule_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(ValueError, match="NOPE-1"):
        flag(store, "NOPE-1")


def test_get_object_trend_returns_oldest_first_and_caps_runs_per_object(store_path):
    store = DuckDBStore(store_path)
    seed_runs(store_path, store)
    for (run_id, _), exceptions in zip(RUNS, (30, 20, 10), strict=True):
        store.write_object_result(
            run_id,
            {
                "target_object": "SITE",
                "record_label": "sites",
                "object_total": 100,
                "active_rules": 1,
                "rules_succeeded": 1,
                "records_evaluated": 100,
                "records_with_exception": exceptions,
                "open_findings": exceptions,
                "exception_rate": float(exceptions),
            },
        )

    trend = store.get_object_trend(runs=2)

    assert [t["RECORDS_WITH_EXCEPTION"] for t in trend] == [20, 10]
    assert {t["TARGET_OBJECT"] for t in trend} == {"SITE"}
