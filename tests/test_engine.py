"""Engine orchestration tests, using a FakeExecutor (brief §11) against a
temp DuckDB store built from the real schema file -- never Snowflake, never
~/dq-data/governance.duckdb.
"""

from datetime import UTC, datetime, timedelta

import duckdb
import pyarrow as pa
import pytest

from dq import engine
from dq.engine import EngineConfig, ExecutionResult
from dq.sources import load_sources
from dq.store.duckdb_store import DuckDBStore

RUN_1 = "11111111-1111-1111-1111-111111111111"
RUN_2 = "22222222-2222-2222-2222-222222222222"
RUN_3 = "33333333-3333-3333-3333-333333333333"
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 8, tzinfo=UTC)
T3 = datetime(2026, 1, 15, tzinfo=UTC)

CONFIG = EngineConfig(sf_base_url="https://connexa.lightning.force.com")


class FakeExecutor:
    """Canned Arrow tables per rule (brief §11), instead of Snowflake."""

    def __init__(self) -> None:
        self.rule_tables: dict[str, pa.Table] = {}
        self.rule_errors: dict[str, Exception] = {}
        self.object_totals: dict[str, int] = {}
        self.scalars: dict[str, object] = {}
        self.calls: list[str] = []

    def execute_rule(self, rule_code, sql, *, query_tag, timeout_seconds):
        self.calls.append(rule_code)
        if rule_code in self.rule_errors:
            raise self.rule_errors[rule_code]
        return ExecutionResult(
            table=self.rule_tables[rule_code], query_id=f"fake-{rule_code}", duration_ms=1
        )

    def count_object(self, target_object, sql, *, query_tag):
        return self.object_totals.get(target_object, 0)

    def scalar(self, label, sql, *, query_tag):
        return self.scalars.get(label)


def make_table(rows: list[tuple[str, bool]]) -> pa.Table:
    record_ids = [r[0] for r in rows]
    is_valid = [r[1] for r in rows]
    n = len(record_ids)
    return pa.table(
        {
            "RECORD_ID": pa.array(record_ids, type=pa.string()),
            "ITEM_KEY": pa.array([None] * n, type=pa.string()),
            "RECORD_NAME": pa.array(record_ids, type=pa.string()),
            "SITE_ID": pa.array(record_ids, type=pa.string()),
            "SITE_NAME": pa.array(record_ids, type=pa.string()),
            "IS_VALID": pa.array(is_valid, type=pa.bool_()),
            "DETAIL": pa.array([None] * n, type=pa.string()),
        }
    )


def insert_rule(store_path, *, rule_code: str, severity: str = "HIGH", scope: str = "SITE") -> int:
    con = duckdb.connect(str(store_path))
    try:
        con.execute(
            """
            INSERT INTO DQ_RULE (
                RULE_CODE, RULE_NAME, RULE_STATEMENT, DOMAIN, TARGET_OBJECT,
                RECORD_LABEL, SCOPE, DIMENSION, SEVERITY, OWNER_EMAIL, STATUS,
                CHECK_SQL, CREATED_BY
            ) VALUES (?, ?, 'stmt', 'SITE_MASTER', 'SITE', 'sites', ?, 'VALIDITY', ?,
                      'owner@connexa.co.nz', 'ACTIVE',
                      'SELECT s.ID AS RECORD_ID FROM {{ src.SITE }} s', 'test')
            """,
            [rule_code, f"{rule_code} test rule", scope, severity],
        )
        rule_id = con.execute(
            "SELECT RULE_ID FROM DQ_RULE WHERE RULE_CODE = ?", [rule_code]
        ).fetchone()[0]
    finally:
        con.close()
    return rule_id


def get_finding_status(store_path, record_id: str) -> str | None:
    con = duckdb.connect(str(store_path))
    try:
        row = con.execute(
            "SELECT STATUS FROM DQ_FINDING WHERE RECORD_ID = ?", [record_id]
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def insert_disposition(store_path, *, finding_record_id: str, disposition: str, expires_at) -> None:
    con = duckdb.connect(str(store_path))
    try:
        finding_id = con.execute(
            "SELECT FINDING_ID FROM DQ_FINDING WHERE RECORD_ID = ?", [finding_record_id]
        ).fetchone()[0]
        con.execute(
            """
            INSERT INTO DQ_FINDING_DISPOSITION (FINDING_ID, DISPOSITION, REASON, SET_BY, EXPIRES_AT)
            VALUES (?, ?, 'test', 'owner@connexa.co.nz', ?)
            """,
            [finding_id, disposition, expires_at],
        )
    finally:
        con.close()


@pytest.fixture
def sources_config(fixtures_dir):
    return load_sources(fixtures_dir / "sources_test.yml")


def run_engine(store_path, sources_config, executor, run_id, run_at):
    store = DuckDBStore(store_path)
    return engine.run(
        store=store,
        executor=executor,
        sources_config=sources_config,
        config=CONFIG,
        run_id=run_id,
        run_type="MANUAL",
        triggered_by="kevin",
        started_at=run_at,
    )


# --- basic run: new failure creates a finding, writes rollups -----------------


def test_run_creates_finding_and_object_result(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    executor = FakeExecutor()
    executor.object_totals["SITE"] = 3
    executor.rule_tables["SITE-900"] = make_table([("r1", False), ("r2", True)])

    summary = run_engine(store_path, sources_config, executor, RUN_1, T1)

    assert summary.status == "SUCCEEDED"
    assert summary.rules_attempted == 1
    assert summary.rules_succeeded == 1
    assert get_finding_status(store_path, "r1") == "OPEN"
    assert get_finding_status(store_path, "r2") is None  # passing row, no finding

    con = duckdb.connect(str(store_path))
    obj = con.execute(
        "SELECT OBJECT_TOTAL, RECORDS_EVALUATED, RECORDS_WITH_EXCEPTION, OPEN_FINDINGS "
        "FROM DQ_OBJECT_RESULT WHERE RUN_ID = ?",
        [RUN_1],
    ).fetchone()
    con.close()
    assert obj == (3, 2, 1, 1)


# --- fix / break / disposition-expiry scenarios (brief §11) --------------------


def test_fix_record_then_run_again_resolves(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False)])
    run_engine(store_path, sources_config, executor, RUN_1, T1)
    assert get_finding_status(store_path, "r1") == "OPEN"

    executor.rule_tables["SITE-900"] = make_table([("r1", True)])
    run_engine(store_path, sources_config, executor, RUN_2, T2)

    assert get_finding_status(store_path, "r1") == "RESOLVED"


def test_break_again_after_resolved_reopens(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False)])
    run_engine(store_path, sources_config, executor, RUN_1, T1)

    executor.rule_tables["SITE-900"] = make_table([("r1", True)])
    run_engine(store_path, sources_config, executor, RUN_2, T2)
    assert get_finding_status(store_path, "r1") == "RESOLVED"

    executor.rule_tables["SITE-900"] = make_table([("r1", False)])
    run_engine(store_path, sources_config, executor, RUN_3, T3)
    assert get_finding_status(store_path, "r1") == "OPEN"


def test_set_exception_then_expire_goes_open_again(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False)])
    run_engine(store_path, sources_config, executor, RUN_1, T1)

    insert_disposition(
        store_path,
        finding_record_id="r1",
        disposition="ACCEPTED_EXCEPTION",
        expires_at=T2 + timedelta(days=1),
    )
    run_engine(store_path, sources_config, executor, RUN_2, T2)
    assert get_finding_status(store_path, "r1") == "ACCEPTED_EXCEPTION"

    run_engine(store_path, sources_config, executor, RUN_3, T3)
    assert get_finding_status(store_path, "r1") == "OPEN"


def test_running_twice_unchanged_makes_no_status_changes(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False)])

    run_engine(store_path, sources_config, executor, RUN_1, T1)
    run_engine(store_path, sources_config, executor, RUN_2, T2)

    assert get_finding_status(store_path, "r1") == "OPEN"
    con = duckdb.connect(str(store_path))
    times_seen = con.execute("SELECT TIMES_SEEN FROM DQ_FINDING WHERE RECORD_ID = 'r1'").fetchone()[
        0
    ]
    con.close()
    assert times_seen == 2


# --- isolation: an erroring rule never affects another rule --------------------


def test_erroring_rule_leaves_other_rule_untouched(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    insert_rule(store_path, rule_code="SITE-901")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False)])
    executor.rule_tables["SITE-901"] = make_table([("r2", False)])
    run_engine(store_path, sources_config, executor, RUN_1, T1)

    # Simulate SITE-900 erroring on the next run (e.g. a Snowflake timeout).
    executor.rule_errors["SITE-900"] = RuntimeError("statement timed out")
    executor.rule_tables["SITE-901"] = make_table([("r2", False)])
    summary = run_engine(store_path, sources_config, executor, RUN_2, T2)

    assert summary.status == "PARTIAL"
    assert summary.rules_errored == 1
    assert "SITE-900" in summary.errors
    con = duckdb.connect(str(store_path))
    r1 = con.execute(
        "SELECT LAST_SEEN_RUN_ID, TIMES_SEEN FROM DQ_FINDING WHERE RECORD_ID = 'r1'"
    ).fetchone()
    r2 = con.execute(
        "SELECT LAST_SEEN_RUN_ID, TIMES_SEEN FROM DQ_FINDING WHERE RECORD_ID = 'r2'"
    ).fetchone()
    error_result = con.execute(
        "SELECT STATUS, ERROR_MESSAGE FROM DQ_RULE_RESULT "
        "WHERE RUN_ID = ? AND RULE_CODE = 'SITE-900'",
        [RUN_2],
    ).fetchone()
    con.close()

    assert r1 == (RUN_1, 1)  # untouched by the errored run
    assert r2 == (RUN_2, 2)  # updated normally
    assert error_result[0] == "ERRORED"
    assert "statement timed out" in error_result[1]


def test_all_rules_succeed_gives_succeeded_status(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", True)])

    summary = run_engine(store_path, sources_config, executor, RUN_1, T1)

    assert summary.status == "SUCCEEDED"
    assert summary.rules_errored == 0


def test_invalid_check_sql_is_isolated_as_errored(store_path, sources_config):
    # A rule whose CHECK_SQL fails lint (three-part identifier) should
    # error out cleanly rather than raising out of run().
    rule_id = insert_rule(store_path, rule_code="SITE-900")
    con = duckdb.connect(str(store_path))
    con.execute(
        "UPDATE DQ_RULE SET CHECK_SQL = 'SELECT ID AS RECORD_ID FROM TESTDB.TESTSCHEMA.SITE__C' "
        "WHERE RULE_ID = ?",
        [rule_id],
    )
    con.close()
    executor = FakeExecutor()

    summary = run_engine(store_path, sources_config, executor, RUN_1, T1)

    assert summary.status == "PARTIAL"
    assert "SITE-900" in summary.errors
    assert executor.calls == []  # never reached Snowflake


# --- site scoring wiring --------------------------------------------------------


def test_site_scope_rule_writes_site_score(store_path, sources_config):
    insert_rule(store_path, rule_code="SITE-900", scope="SITE", severity="HIGH")
    executor = FakeExecutor()
    executor.rule_tables["SITE-900"] = make_table([("r1", False), ("r2", True)])

    run_engine(store_path, sources_config, executor, RUN_1, T1)

    con = duckdb.connect(str(store_path))
    scores = con.execute(
        "SELECT SITE_ID, RULES_APPLICABLE, RULES_FAILED, WEIGHTED_SCORE "
        "FROM DQ_SITE_SCORE WHERE RUN_ID = ? ORDER BY SITE_ID",
        [RUN_1],
    ).fetchall()
    con.close()

    assert scores[0][:3] == ("r1", 1, 1)
    assert float(scores[0][3]) == pytest.approx(0.0)
    assert scores[1][:3] == ("r2", 1, 0)
    assert float(scores[1][3]) == pytest.approx(100.0)


def test_no_active_rules_still_completes(store_path, sources_config):
    executor = FakeExecutor()

    summary = run_engine(store_path, sources_config, executor, RUN_1, T1)

    assert summary.status == "SUCCEEDED"
    assert summary.rules_attempted == 0


# --- collect_source_freshness ----------------------------------------------------


def test_collect_source_freshness_returns_max_per_source(sources_config):
    executor = FakeExecutor()
    executor.scalars["SITE"] = T2
    executor.scalars["PROJECT"] = T1

    freshness = engine.collect_source_freshness(executor, sources_config, query_tag="dq_poc:run:1")

    assert freshness["SITE"] == T2
    assert freshness["PROJECT"] == T1
    assert freshness["NO_DEFAULT_FILTERS"] is None  # nothing configured for it


def test_collect_source_freshness_renders_all_sources(sources_config):
    executor = FakeExecutor()

    freshness = engine.collect_source_freshness(executor, sources_config, query_tag="dq_poc:run:1")

    assert set(freshness) == set(sources_config.sources)
