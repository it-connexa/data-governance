"""Tests snowflake_client.py against a fake connector -- never a real
Snowflake connection. Verifies session parameters, query tagging, Arrow
fetch, timing, connection lifecycle and the RuleExecutor adapter."""

from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from dq.settings import PathSettings, Settings, SnowflakeSettings
from dq.snowflake_client import SnowflakeClient, SnowflakeExecutor


@pytest.fixture
def settings():
    return Settings(
        snowflake=SnowflakeSettings(
            account="CONNEXA-DATA",
            user="DQ_POC_SVC",
            role="DQ_POC_READER",
            warehouse="DQ_POC_WH",
            keychain_service="dq-snowflake-key",
            keychain_account="dq_poc",
        ),
        paths=PathSettings(
            duckdb="/tmp/governance.duckdb",
            sources="/tmp/sources.yml",
            export="/tmp/export",
            backups="/tmp/backups",
        ),
    )


class FakeCursor:
    def __init__(self, table: pa.Table, query_id: str = "fake-query-id"):
        self._table = table
        self.sfqid = query_id
        self.executed: list[str] = []

    def execute(self, sql):
        self.executed.append(sql)
        return self

    def fetch_arrow_all(self):
        return self._table


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def make_client(settings, table: pa.Table, monkeypatch) -> tuple[SnowflakeClient, MagicMock]:
    monkeypatch.setattr("dq.snowflake_client.load_private_key_der", lambda **kw: b"fake-der-bytes")
    cursor = FakeCursor(table)
    connection = FakeConnection(cursor)
    connect_fn = MagicMock(return_value=connection)
    client = SnowflakeClient(settings, connect_fn=connect_fn)
    return client, connect_fn, connection


def sample_table() -> pa.Table:
    return pa.table({"N": pa.array([42])})


def test_execute_returns_table_query_id_and_duration(settings, monkeypatch):
    client, connect_fn, connection = make_client(settings, sample_table(), monkeypatch)

    result = client.execute("SELECT 42 AS N", query_tag="dq_poc:test:abc")

    assert result.table.column("N")[0].as_py() == 42
    assert result.query_id == "fake-query-id"
    assert result.duration_ms >= 0
    assert connection.closed is True


def test_execute_passes_query_tag_and_timeout_as_session_parameters(settings, monkeypatch):
    client, connect_fn, _ = make_client(settings, sample_table(), monkeypatch)

    client.execute("SELECT 1", query_tag="dq_poc:validate:SITE-004", timeout_seconds=120)

    kwargs = connect_fn.call_args.kwargs
    assert kwargs["session_parameters"]["QUERY_TAG"] == "dq_poc:validate:SITE-004"
    assert kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 120


def test_execute_omits_timeout_session_parameter_when_not_given(settings, monkeypatch):
    client, connect_fn, _ = make_client(settings, sample_table(), monkeypatch)

    client.execute("SELECT 1", query_tag="dq_poc:validate:SITE-004")

    kwargs = connect_fn.call_args.kwargs
    assert "STATEMENT_TIMEOUT_IN_SECONDS" not in kwargs["session_parameters"]


def test_execute_uses_settings_for_connection_identity(settings, monkeypatch):
    client, connect_fn, _ = make_client(settings, sample_table(), monkeypatch)

    client.execute("SELECT 1", query_tag="dq_poc:test:abc")

    kwargs = connect_fn.call_args.kwargs
    assert kwargs["account"] == "CONNEXA-DATA"
    assert kwargs["user"] == "DQ_POC_SVC"
    assert kwargs["role"] == "DQ_POC_READER"
    assert kwargs["warehouse"] == "DQ_POC_WH"
    assert kwargs["private_key"] == b"fake-der-bytes"


def test_connection_closed_even_if_execute_raises(settings, monkeypatch):
    monkeypatch.setattr("dq.snowflake_client.load_private_key_der", lambda **kw: b"fake-der-bytes")
    cursor = FakeCursor(sample_table())

    def boom(sql):
        raise RuntimeError("boom")

    cursor.execute = boom
    connection = FakeConnection(cursor)
    client = SnowflakeClient(settings, connect_fn=MagicMock(return_value=connection))

    with pytest.raises(RuntimeError, match="boom"):
        client.execute("SELECT 1", query_tag="dq_poc:test:abc")

    assert connection.closed is True


# --- SnowflakeExecutor (engine.RuleExecutor adapter) ---------------------------


def test_snowflake_executor_execute_rule_returns_execution_result(settings, monkeypatch):
    client, _, _ = make_client(settings, sample_table(), monkeypatch)
    executor = SnowflakeExecutor(client)

    result = executor.execute_rule(
        "SITE-004", "SELECT 42 AS N", query_tag="dq_poc:run:1", timeout_seconds=60
    )

    assert result.table.column("N")[0].as_py() == 42


def test_snowflake_executor_count_object_returns_scalar(settings, monkeypatch):
    count_table = pa.table({"COUNT": pa.array([1546])})
    client, _, _ = make_client(settings, count_table, monkeypatch)
    executor = SnowflakeExecutor(client)

    count = executor.count_object("SITE", "SELECT COUNT(*) FROM x", query_tag="dq_poc:run:1")

    assert count == 1546


def test_snowflake_executor_scalar_returns_arbitrary_value(settings, monkeypatch):
    import datetime as dt

    freshness_table = pa.table({"MAX": pa.array([dt.datetime(2026, 9, 27, 3, 0, 0)])})
    client, _, _ = make_client(settings, freshness_table, monkeypatch)
    executor = SnowflakeExecutor(client)

    value = executor.scalar("SITE", "SELECT MAX(_FIVETRAN_SYNCED) FROM x", query_tag="dq_poc:run:1")

    assert value == dt.datetime(2026, 9, 27, 3, 0, 0)
