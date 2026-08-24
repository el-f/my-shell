"""Configuration loading (TOML-based)."""

import tomllib
from pathlib import Path
from typing import Any

from .utils import get_nushell_config_dir, get_project_dir, get_xonsh_config_dir, log_debug


class TOMLLoadError(Exception):
    """Raised when a TOML file cannot be parsed."""


def load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file and return its contents as a dict."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise TOMLLoadError(f"Invalid TOML in {path}: {exc}") from exc


def load_aliases(project_dir: Path | None = None) -> dict[str, Any]:
    """Load config/aliases.toml (with optional local overrides).

    Returns the full parsed dict with sections like 'navigation', 'git', 'wrappers', etc.
    """
    root = project_dir or get_project_dir()
    base_path = root / "config" / "aliases.toml"

    if not base_path.exists():
        raise FileNotFoundError(f"Aliases config not found: {base_path}")

    log_debug(f"Loading aliases from {base_path}")
    base = load_toml(base_path)

    # Shallow merge: section keys update, nested dicts replace.
    local_path = root / "config" / "aliases.local.toml"
    if local_path.exists():
        log_debug(f"Merging local overrides from {local_path}")
        local = load_toml(local_path)
        for key, value in local.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key].update(value)
            else:
                base[key] = value

    return base


def _load_default_settings() -> dict[str, Any]:
    """Load default settings from config/settings.toml (single source of truth)."""
    path = Path(__file__).parent.parent / "config" / "settings.toml"
    if path.exists():
        return load_toml(path)
    return {}


_DEFAULT_SETTINGS: dict[str, Any] = _load_default_settings()


def load_settings(project_dir: Path | None = None) -> dict[str, Any]:
    """Load settings with defaults from config/settings.toml and local overrides."""
    root = project_dir or get_project_dir()

    settings = {k: dict(v) if isinstance(v, dict) else v for k, v in _DEFAULT_SETTINGS.items()}

    # Merge project-dir settings.toml (supports tests / non-default project dirs)
    path = root / "config" / "settings.toml"
    if path.exists():
        data = load_toml(path)
        for section, values in data.items():
            if (
                section in settings
                and isinstance(settings[section], dict)
                and isinstance(values, dict)
            ):
                settings[section].update(values)
            else:
                settings[section] = values

    # Merge settings.local.toml overrides if present
    local_path = root / "config" / "settings.local.toml"
    if local_path.exists():
        log_debug(f"Merging local settings from {local_path}")
        local = load_toml(local_path)
        for section, values in local.items():
            if (
                section in settings
                and isinstance(settings[section], dict)
                and isinstance(values, dict)
            ):
                settings[section].update(values)
            else:
                settings[section] = values

    return settings


def is_integration_enabled(settings: dict[str, Any], name: str) -> bool:
    """Check if an integration is enabled in settings.

    Supports both bool and dict forms:
      integration = true
      integration = { enabled = true, defer = true }
    """
    integrations = settings.get("integrations", {})
    value = integrations.get(name, True)  # default: enabled
    if isinstance(value, dict):
        return bool(value.get("enabled", True))
    return bool(value)


def is_integration_deferred(settings: dict[str, Any], name: str) -> bool:
    """Check if an integration should be deferred (lazy loaded)."""
    integrations = settings.get("integrations", {})
    value = integrations.get(name)
    if isinstance(value, dict):
        return bool(value.get("defer", False))
    return False


def is_command_group_enabled(settings: dict[str, Any], name: str) -> bool:
    """Check if a command group is enabled in settings."""
    commands = settings.get("commands", {})
    return bool(commands.get(name, True))


def get_config_dir(shell: str) -> Path:
    """Get the config directory for a given shell."""
    if shell == "nushell":
        return get_nushell_config_dir()
    if shell == "xonsh":
        return get_xonsh_config_dir()
    raise ValueError(f"Unsupported shell: {shell}")
