"""Tests for the pure, non-Streamlit helpers in dq.app.context -- never a
real settings.toml, Keychain entry or Snowflake connection."""

import uuid

from dq.app import context
from dq.engine import EngineConfig
from dq.snowflake_client import SnowflakeExecutor
from dq.sources import SourcesConfig
from dq.store.duckdb_store import DuckDBStore

VALID_TOML = """
[snowflake]
account          = "CONNEXA-DATA"
user             = "DQ_POC_SVC"
role             = "DQ_POC_READER"
warehouse        = "DQ_POC_WH"
keychain_service = "dq-snowflake-key"
keychain_account = "dq_poc"

[paths]
duckdb  = "{duckdb}"
sources = "{sources}"
export  = "/tmp/export"
backups = "/tmp/backups"
"""


def write_settings(tmp_path, *, duckdb_path, sources_path):
    path = tmp_path / "settings.toml"
    path.write_text(VALID_TOML.format(duckdb=duckdb_path, sources=sources_path))
    return path


def test_load_context_honours_env_override(monkeypatch, tmp_path, store_path, fixtures_dir):
    settings_path = write_settings(
        tmp_path, duckdb_path=store_path, sources_path=fixtures_dir / "sources_test.yml"
    )
    monkeypatch.setenv("DQ_SETTINGS_PATH", str(settings_path))

    settings, store, sources_config = context.load_context()

    assert settings.snowflake.account == "CONNEXA-DATA"
    assert isinstance(store, DuckDBStore)
    assert isinstance(sources_config, SourcesConfig)
    assert "SITE" in sources_config.sources


def test_engine_config_reads_dq_config_defaults(store_path):
    store = DuckDBStore(store_path)

    config = context.engine_config(store)

    assert isinstance(config, EngineConfig)
    assert config.rule_timeout_seconds == 120  # seeded default


def test_make_executor_returns_snowflake_executor(monkeypatch, tmp_path, store_path, fixtures_dir):
    settings_path = write_settings(
        tmp_path, duckdb_path=store_path, sources_path=fixtures_dir / "sources_test.yml"
    )
    monkeypatch.setenv("DQ_SETTINGS_PATH", str(settings_path))
    settings, _, _ = context.load_context()

    executor = context.make_executor(settings)

    assert isinstance(executor, SnowflakeExecutor)


def test_next_query_tag_is_unique_and_tagged():
    tag1 = context.next_query_tag("app:preview")
    tag2 = context.next_query_tag("app:preview")

    assert tag1 != tag2
    assert tag1.startswith("dq_poc:app:preview:")
    uuid.UUID(tag1.rsplit(":", 1)[-1])  # doesn't raise
