"""Loader for .dq/settings.toml (docs/SETUP.md step 9).

.dq/ lives at the project root, gitignored (docs/adr/0001). Snowflake-free:
only parses TOML and resolves local paths. Never touches Keychain or opens
a network connection.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SETTINGS_PATH = Path(".dq") / "settings.toml"

_SNOWFLAKE_KEYS = ("account", "user", "role", "warehouse", "keychain_service", "keychain_account")
_PATH_KEYS = ("duckdb", "sources", "export", "backups")


class SettingsError(Exception):
    """Raised when .dq/settings.toml is missing, unreadable or incomplete."""


@dataclass(frozen=True)
class SnowflakeSettings:
    account: str
    user: str
    role: str
    warehouse: str
    keychain_service: str
    keychain_account: str


@dataclass(frozen=True)
class PathSettings:
    duckdb: Path
    sources: Path
    export: Path
    backups: Path


@dataclass(frozen=True)
class Settings:
    snowflake: SnowflakeSettings
    paths: PathSettings


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate settings from `path` (defaults to .dq/settings.toml)."""
    settings_path = path if path is not None else DEFAULT_SETTINGS_PATH

    if not settings_path.exists():
        raise SettingsError(f"Settings file not found: {settings_path}")

    try:
        with settings_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"Could not parse {settings_path}: {exc}") from exc

    snowflake_section = _require_section(data, "snowflake", settings_path)
    paths_section = _require_section(data, "paths", settings_path)

    snowflake = SnowflakeSettings(
        **{
            key: _require_key(snowflake_section, key, "snowflake", settings_path)
            for key in _SNOWFLAKE_KEYS
        }
    )
    paths = PathSettings(
        **{
            key: Path(_require_key(paths_section, key, "paths", settings_path)).expanduser()
            for key in _PATH_KEYS
        }
    )

    return Settings(snowflake=snowflake, paths=paths)


def _require_section(data: dict, name: str, settings_path: Path) -> dict:
    section = data.get(name)
    if not isinstance(section, dict):
        raise SettingsError(f"Missing [{name}] section in {settings_path}")
    return section


def _require_key(section: dict, key: str, section_name: str, settings_path: Path) -> str:
    if key not in section:
        raise SettingsError(f"Missing '{key}' in [{section_name}] of {settings_path}")
    return section[key]
