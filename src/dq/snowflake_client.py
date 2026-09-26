"""Snowflake connection, query tagging, timeouts, Arrow fetch (brief §5, §10.1).

One short-lived connection per call, matching the store's own pattern.
`connect_fn` is injectable so tests never open a real connection.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import snowflake.connector

from dq.engine import ExecutionResult
from dq.secrets import load_private_key_der
from dq.settings import Settings


class _Connector(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


class SnowflakeClient:
    def __init__(self, settings: Settings, *, connect_fn: _Connector = snowflake.connector.connect):
        self._settings = settings
        self._connect_fn = connect_fn

    def _connect(self, *, query_tag: str, timeout_seconds: int | None):
        sf = self._settings.snowflake
        der_key = load_private_key_der(
            keychain_service=sf.keychain_service, keychain_account=sf.keychain_account
        )
        session_parameters: dict[str, Any] = {"QUERY_TAG": query_tag}
        if timeout_seconds is not None:
            session_parameters["STATEMENT_TIMEOUT_IN_SECONDS"] = timeout_seconds

        return self._connect_fn(
            account=sf.account,
            user=sf.user,
            private_key=der_key,
            role=sf.role,
            warehouse=sf.warehouse,
            session_parameters=session_parameters,
        )

    def execute(
        self, sql: str, *, query_tag: str, timeout_seconds: int | None = None
    ) -> ExecutionResult:
        connection = self._connect(query_tag=query_tag, timeout_seconds=timeout_seconds)
        try:
            cursor = connection.cursor()
            started = time.monotonic()
            cursor.execute(sql)
            table = cursor.fetch_arrow_all()
            duration_ms = int((time.monotonic() - started) * 1000)
            return ExecutionResult(table=table, query_id=cursor.sfqid, duration_ms=duration_ms)
        finally:
            connection.close()


class SnowflakeExecutor:
    """Adapts SnowflakeClient to the engine.RuleExecutor protocol."""

    def __init__(self, client: SnowflakeClient):
        self._client = client

    def execute_rule(
        self, rule_code: str, sql: str, *, query_tag: str, timeout_seconds: int
    ) -> ExecutionResult:
        return self._client.execute(sql, query_tag=query_tag, timeout_seconds=timeout_seconds)

    def count_object(self, target_object: str, sql: str, *, query_tag: str) -> int:
        result = self._client.execute(sql, query_tag=query_tag)
        return result.table.column(0)[0].as_py()
