from pathlib import Path

import pytest

from dq.settings import SettingsError, load_settings

VALID_TOML = """
[snowflake]
account          = "connexa-abc123"
user             = "DQ_POC_SVC"
role             = "DQ_POC_READER"
warehouse        = "DQ_POC_WH"
keychain_service = "dq-snowflake-key"
keychain_account = "dq_poc"

[paths]
duckdb  = "~/dq-data/governance.duckdb"
sources = "~/dev/dq-governance/config/sources.yml"
export  = "~/Library/CloudStorage/OneDrive-Connexa/exports"
backups = "~/dq-data/backups"
"""


def write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(content)
    return path


def test_loads_valid_settings(tmp_path: Path) -> None:
    path = write_toml(tmp_path, VALID_TOML)

    settings = load_settings(path)

    assert settings.snowflake.account == "connexa-abc123"
    assert settings.snowflake.user == "DQ_POC_SVC"
    assert settings.snowflake.role == "DQ_POC_READER"
    assert settings.snowflake.warehouse == "DQ_POC_WH"
    assert settings.snowflake.keychain_service == "dq-snowflake-key"
    assert settings.snowflake.keychain_account == "dq_poc"


def test_expands_home_in_every_path_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    path = write_toml(tmp_path, VALID_TOML)

    settings = load_settings(path)

    assert settings.paths.duckdb == fake_home / "dq-data" / "governance.duckdb"
    assert settings.paths.sources == fake_home / "dev" / "dq-governance" / "config" / "sources.yml"
    assert settings.paths.backups == fake_home / "dq-data" / "backups"
    # export path has no leading ~ in this fixture, so it stays absolute-as-given
    assert (
        settings.paths.export
        == Path("~/Library/CloudStorage/OneDrive-Connexa/exports").expanduser()
    )


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.toml"

    with pytest.raises(SettingsError, match="not found"):
        load_settings(missing)


def test_missing_section_raises(tmp_path: Path) -> None:
    path = write_toml(tmp_path, '[paths]\nduckdb = "~/dq-data/governance.duckdb"\n')

    with pytest.raises(SettingsError, match="snowflake"):
        load_settings(path)


def test_missing_key_in_section_raises(tmp_path: Path) -> None:
    content = """
[snowflake]
account = "connexa-abc123"
user    = "DQ_POC_SVC"
role    = "DQ_POC_READER"
# warehouse missing
keychain_service = "dq-snowflake-key"
keychain_account = "dq_poc"

[paths]
duckdb  = "~/dq-data/governance.duckdb"
sources = "~/dq-governance/config/sources.yml"
export  = "~/exports"
backups = "~/dq-data/backups"
"""
    path = write_toml(tmp_path, content)

    with pytest.raises(SettingsError, match="warehouse"):
        load_settings(path)


def test_unreadable_toml_raises(tmp_path: Path) -> None:
    path = write_toml(tmp_path, "this is not [valid toml")

    with pytest.raises(SettingsError):
        load_settings(path)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    content = VALID_TOML + '\n[snowflake]\nextra_future_field = "ignored"\n'
    # tomllib rejects duplicate tables, so append the field to the existing one instead
    content = VALID_TOML.replace(
        'keychain_account = "dq_poc"', 'keychain_account = "dq_poc"\nextra_future_field = "ignored"'
    )
    path = write_toml(tmp_path, content)

    settings = load_settings(path)

    assert settings.snowflake.account == "connexa-abc123"
