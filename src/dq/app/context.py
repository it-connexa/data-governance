"""Shared, testable wiring for the Streamlit app (brief §10.6).

Pure functions only -- no `st.*` calls here, so this module is importable
and testable under plain pytest. The sidebar "acting as" widget and page
rendering live in streamlit_app.py / pages/*.py instead, verified by
running the app itself, never by pytest.

`DQ_SETTINGS_PATH` overrides the default `.dq/settings.toml` -- used only
to point the app at a throwaway temp store while checking the UI renders,
never at a real settings file.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from dq.engine import EngineConfig
from dq.settings import Settings, load_settings
from dq.snowflake_client import SnowflakeClient, SnowflakeExecutor
from dq.sources import SourcesConfig, load_sources
from dq.store.base import GovernanceStore
from dq.store.duckdb_store import DuckDBStore


def load_context() -> tuple[Settings, GovernanceStore, SourcesConfig]:
    settings_path_override = os.environ.get("DQ_SETTINGS_PATH")
    settings = load_settings(Path(settings_path_override) if settings_path_override else None)
    store = DuckDBStore(settings.paths.duckdb)
    sources_config = load_sources(settings.paths.sources)
    return settings, store, sources_config


def make_executor(settings: Settings) -> SnowflakeExecutor:
    return SnowflakeExecutor(SnowflakeClient(settings))


def engine_config(store: GovernanceStore) -> EngineConfig:
    sf_base_url = store.get_config_value("SF_BASE_URL") or ""
    timeout = store.get_config_value("RULE_TIMEOUT_SECONDS")
    return EngineConfig(
        sf_base_url=sf_base_url, rule_timeout_seconds=int(timeout) if timeout else 120
    )


def next_query_tag(command: str) -> str:
    return f"dq_poc:{command}:{uuid.uuid4()}"
