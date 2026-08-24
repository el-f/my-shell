"""Configuration schema validation.

Validates aliases.toml, settings.toml, and plugins.toml against expected schemas.
Called automatically at the start of setup/deploy/render commands.
"""

import re
from pathlib import Path

from .config import _DEFAULT_SETTINGS, TOMLLoadError, load_toml
from .plugins import _PLUGIN_NAME_RE
from .utils import get_project_dir, log_error, log_warn

# ── Schema definitions ────────────────────────────────────────────

_THEME_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

# An alias value is a str, or a dict with these keys
_ALIAS_VALUE_KEYS = {"command", "comment", "nushell", "xonsh", "xonsh_fn"}

_WRAPPER_REQUIRED_KEYS = {"preferred", "fallback", "error"}

# settings.toml known sections and keys -- derived from config/settings.toml
_SETTINGS_KNOWN_SECTIONS = {
    section: set(values.keys())
    for section, values in _DEFAULT_SETTINGS.items()
    if isinstance(values, dict)
}

_PLUGIN_KNOWN_KEYS = {"crate", "description", "version"}
_PLUGIN_REQUIRED_KEYS = {"crate", "description"}


class ValidationError:
    """A single validation issue."""

    def __init__(self, file: str, path: str, message: str, *, is_warning: bool = False) -> None:
        self.file = file
        self.path = path
        self.message = message
        self.is_warning = is_warning

    def __str__(self) -> str:
        level = "warn" if self.is_warning else "error"
        return f"[{level}] {self.file}: {self.path}: {self.message}"


def _validate_alias_file(path: Path, filename: str) -> list[ValidationError]:
    """Validate one alias file (base or local override) against the schema."""
    errors: list[ValidationError] = []

    try:
        data = load_toml(path)
    except TOMLLoadError:
        return [ValidationError(filename, "", "Invalid TOML syntax")]

    for section, values in data.items():
        if not isinstance(values, dict):
            errors.append(
                ValidationError(
                    filename, section, f"Section must be a table, got {type(values).__name__}"
                )
            )
            continue

        if section == "wrappers":
            for wrapper_name, wrapper_def in values.items():
                if not isinstance(wrapper_def, dict):
                    errors.append(
                        ValidationError(
                            filename,
                            f"wrappers.{wrapper_name}",
                            f"Wrapper must be a table, got {type(wrapper_def).__name__}",
                        )
                    )
                    continue
                missing = _WRAPPER_REQUIRED_KEYS - set(wrapper_def.keys())
                if missing:
                    errors.append(
                        ValidationError(
                            filename,
                            f"wrappers.{wrapper_name}",
                            f"Missing required keys: {', '.join(sorted(missing))}",
                        )
                    )
                unknown = set(wrapper_def.keys()) - _WRAPPER_REQUIRED_KEYS
                if unknown:
                    errors.append(
                        ValidationError(
                            filename,
                            f"wrappers.{wrapper_name}",
                            f"Unknown keys: {', '.join(sorted(unknown))}",
                            is_warning=True,
                        )
                    )
            continue

        for alias_name, alias_value in values.items():
            alias_path = f"{section}.{alias_name}"
            if isinstance(alias_value, str):
                continue
            if isinstance(alias_value, dict):
                unknown = set(alias_value.keys()) - _ALIAS_VALUE_KEYS
                if unknown:
                    errors.append(
                        ValidationError(
                            filename,
                            alias_path,
                            f"Unknown keys: {', '.join(sorted(unknown))}",
                            is_warning=True,
                        )
                    )
                # Must have at least command, nushell, or xonsh
                has_definition = any(
                    k in alias_value for k in ("command", "nushell", "xonsh", "xonsh_fn")
                )
                if not has_definition:
                    errors.append(
                        ValidationError(
                            filename,
                            alias_path,
                            "Alias dict must have 'command', 'nushell', 'xonsh', or 'xonsh_fn'",
                        )
                    )
            else:
                errors.append(
                    ValidationError(
                        filename,
                        alias_path,
                        f"Alias value must be string or table, got {type(alias_value).__name__}",
                    )
                )

    return errors


def validate_aliases(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/aliases.toml schema."""
    root = project_dir or get_project_dir()
    path = root / "config" / "aliases.toml"

    if not path.exists():
        return [ValidationError("aliases.toml", "", "File not found")]

    return _validate_alias_file(path, "aliases.toml")


def validate_aliases_local(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/aliases.local.toml, which render merges over aliases.toml."""
    root = project_dir or get_project_dir()
    path = root / "config" / "aliases.local.toml"

    if not path.exists():
        return []  # local overrides are optional

    return _validate_alias_file(path, "aliases.local.toml")


def _check_settings_section(
    filename: str, section: str, values: object
) -> tuple[list[ValidationError], bool]:
    """Section-header checks shared by settings.toml and settings.local.toml.

    Returns (errors, skip). skip=True means the section is unknown or not a
    table, so per-section type checks must not run on it.
    """
    if section not in _SETTINGS_KNOWN_SECTIONS:
        return [
            ValidationError(filename, section, f"Unknown section '{section}'", is_warning=True)
        ], True
    if not isinstance(values, dict):
        return (
            [
                ValidationError(
                    filename, section, f"Section must be a table, got {type(values).__name__}"
                )
            ],
            True,
        )
    unknown = set(values.keys()) - _SETTINGS_KNOWN_SECTIONS[section]
    if unknown:
        return [
            ValidationError(
                filename, section, f"Unknown keys: {', '.join(sorted(unknown))}", is_warning=True
            )
        ], False
    return [], False


def _check_settings_values(filename: str, section: str, values: dict) -> list[ValidationError]:
    """Per-key type and range checks for one known settings section."""
    errors: list[ValidationError] = []
    known_keys = _SETTINGS_KNOWN_SECTIONS[section]

    if section in ("shells", "git"):
        for key, value in values.items():
            if key not in known_keys:
                continue
            if not isinstance(value, bool):
                errors.append(
                    ValidationError(
                        filename,
                        f"{section}.{key}",
                        f"Must be a boolean, got {type(value).__name__}",
                    )
                )
    elif section == "oh-my-posh":
        theme = values.get("theme")
        if "theme" in values and not isinstance(theme, str):
            errors.append(
                ValidationError(
                    filename,
                    f"{section}.theme",
                    f"Must be a string, got {type(theme).__name__}",
                )
            )
        elif isinstance(theme, str) and not _THEME_NAME_RE.fullmatch(theme):
            # The theme name is written into generated shell code.
            errors.append(
                ValidationError(
                    filename,
                    f"{section}.theme",
                    "Must be a theme name: letters, digits, and any of _ . - only",
                )
            )
    elif section in ("integrations", "commands"):
        for key, value in values.items():
            if key not in known_keys:
                continue
            if isinstance(value, dict):
                # Dict form: { enabled = true, defer = true }
                unknown_sub = set(value.keys()) - {"enabled", "defer"}
                if unknown_sub:
                    errors.append(
                        ValidationError(
                            filename,
                            f"{section}.{key}",
                            f"Unknown keys in table: {', '.join(sorted(unknown_sub))}",
                            is_warning=True,
                        )
                    )
            elif not isinstance(value, bool):
                errors.append(
                    ValidationError(
                        filename,
                        f"{section}.{key}",
                        f"Must be boolean or table, got {type(value).__name__}",
                    )
                )
    elif section == "fonts":
        if "nerd_font" in values and not isinstance(values["nerd_font"], str):
            errors.append(
                ValidationError(
                    filename,
                    f"{section}.nerd_font",
                    f"Must be a string, got {type(values['nerd_font']).__name__}",
                )
            )
        if "auto_install" in values and not isinstance(values["auto_install"], bool):
            errors.append(
                ValidationError(
                    filename,
                    f"{section}.auto_install",
                    f"Must be a boolean, got {type(values['auto_install']).__name__}",
                )
            )
    elif section == "backup":
        if "max_count" in values and not isinstance(values["max_count"], int):
            errors.append(
                ValidationError(
                    filename,
                    f"{section}.max_count",
                    f"Must be an integer, got {type(values['max_count']).__name__}",
                )
            )
        elif (
            "max_count" in values
            and isinstance(values["max_count"], int)
            and values["max_count"] < 1
        ):
            errors.append(
                ValidationError(
                    filename,
                    f"{section}.max_count",
                    f"Must be >= 1, got {values['max_count']}",
                )
            )

    return errors


def _validate_settings_file(path: Path, filename: str) -> list[ValidationError]:
    """Validate one settings file (base or local override) against the schema."""
    errors: list[ValidationError] = []

    try:
        data = load_toml(path)
    except TOMLLoadError:
        return [ValidationError(filename, "", "Invalid TOML syntax")]

    for section, values in data.items():
        section_errors, skip = _check_settings_section(filename, section, values)
        errors.extend(section_errors)
        if skip:
            continue
        errors.extend(_check_settings_values(filename, section, values))

    return errors


def validate_settings(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/settings.toml schema."""
    root = project_dir or get_project_dir()
    path = root / "config" / "settings.toml"

    if not path.exists():
        return []  # settings.toml is optional

    return _validate_settings_file(path, "settings.toml")


def _validate_plugin_file(path: Path, filename: str) -> list[ValidationError]:
    """Validate one plugin file (base or local override) against the schema."""
    errors: list[ValidationError] = []

    try:
        data = load_toml(path)
    except TOMLLoadError:
        return [ValidationError(filename, "", "Invalid TOML syntax")]

    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        return [ValidationError(filename, "plugins", "Must be a table")]

    for name, entry in plugins.items():
        if not _PLUGIN_NAME_RE.match(name):
            errors.append(
                ValidationError(
                    filename,
                    f"plugins.{name}",
                    "Plugin name must start with a letter or digit, then letters, digits, '.', '_' or '-'",
                )
            )

        if not isinstance(entry, dict):
            errors.append(
                ValidationError(
                    filename,
                    f"plugins.{name}",
                    f"Plugin entry must be a table, got {type(entry).__name__}",
                )
            )
            continue

        missing = _PLUGIN_REQUIRED_KEYS - set(entry.keys())
        if missing:
            errors.append(
                ValidationError(
                    filename,
                    f"plugins.{name}",
                    f"Missing required keys: {', '.join(sorted(missing))}",
                )
            )

        unknown = set(entry.keys()) - _PLUGIN_KNOWN_KEYS
        if unknown:
            errors.append(
                ValidationError(
                    filename,
                    f"plugins.{name}",
                    f"Unknown keys: {', '.join(sorted(unknown))}",
                    is_warning=True,
                )
            )

        crate = entry.get("crate")
        if isinstance(crate, str) and not _PLUGIN_NAME_RE.match(crate):
            errors.append(
                ValidationError(
                    filename,
                    f"plugins.{name}.crate",
                    "Crate name must start with a letter or digit, then letters, digits, '.', '_' or '-'",
                )
            )

    return errors


def validate_plugins(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/plugins.toml schema."""
    root = project_dir or get_project_dir()
    path = root / "config" / "plugins.toml"

    if not path.exists():
        return []  # plugins.toml is optional

    return _validate_plugin_file(path, "plugins.toml")


def validate_plugins_local(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/plugins.local.toml, which the plugin loader merges over plugins.toml."""
    root = project_dir or get_project_dir()
    path = root / "config" / "plugins.local.toml"

    if not path.exists():
        return []  # local overrides are optional

    return _validate_plugin_file(path, "plugins.local.toml")


def validate_profiles(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/profiles.toml schema."""
    root = project_dir or get_project_dir()
    path = root / "config" / "profiles.toml"
    errors: list[ValidationError] = []

    if not path.exists():
        return errors  # profiles.toml is optional (built-in defaults used)

    try:
        data = load_toml(path)
    except TOMLLoadError:
        errors.append(ValidationError("profiles.toml", "", "Invalid TOML syntax"))
        return errors

    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        errors.append(ValidationError("profiles.toml", "profiles", "Must be a table"))
        return errors

    _PROFILE_KNOWN_KEYS = {"integrations", "commands", "inherits"}
    valid_integrations = set(_DEFAULT_SETTINGS["integrations"].keys())
    valid_commands = set(_DEFAULT_SETTINGS["commands"].keys())
    profile_names = set(profiles.keys())

    for name, entry in profiles.items():
        base = f"profiles.{name}"
        if not isinstance(entry, dict):
            errors.append(
                ValidationError(
                    "profiles.toml",
                    base,
                    f"Profile must be a table, got {type(entry).__name__}",
                )
            )
            continue

        unknown = set(entry.keys()) - _PROFILE_KNOWN_KEYS
        if unknown:
            errors.append(
                ValidationError(
                    "profiles.toml",
                    base,
                    f"Unknown keys: {', '.join(sorted(unknown))}",
                    is_warning=True,
                )
            )

        inherits = entry.get("inherits")
        if inherits is not None and inherits not in profile_names:
            errors.append(
                ValidationError(
                    "profiles.toml",
                    f"{base}.inherits",
                    f"Inherits unknown profile '{inherits}'",
                )
            )

        for key, valid_set in (
            ("integrations", valid_integrations),
            ("commands", valid_commands),
        ):
            value = entry.get(key)
            if value is None:
                continue
            if not isinstance(value, list):
                errors.append(
                    ValidationError(
                        "profiles.toml",
                        f"{base}.{key}",
                        f"Must be a list, got {type(value).__name__}",
                    )
                )
                continue
            unknown_items = [str(v) for v in value if v not in valid_set]
            if unknown_items:
                errors.append(
                    ValidationError(
                        "profiles.toml",
                        f"{base}.{key}",
                        f"Unknown {key}: {', '.join(unknown_items)}",
                        is_warning=True,
                    )
                )

    return errors


def validate_settings_local(project_dir: Path | None = None) -> list[ValidationError]:
    """Validate config/settings.local.toml overrides against the settings schema."""
    root = project_dir or get_project_dir()
    path = root / "config" / "settings.local.toml"

    if not path.exists():
        return []  # local overrides are optional

    return _validate_settings_file(path, "settings.local.toml")


def validate_all(project_dir: Path | None = None) -> list[ValidationError]:
    """Run all config validations. Returns list of errors."""
    root = project_dir or get_project_dir()
    errors: list[ValidationError] = []
    errors.extend(validate_aliases(root))
    errors.extend(validate_aliases_local(root))
    errors.extend(validate_settings(root))
    errors.extend(validate_settings_local(root))
    errors.extend(validate_plugins(root))
    errors.extend(validate_plugins_local(root))
    errors.extend(validate_profiles(root))
    return errors


def validate_and_report(project_dir: Path | None = None) -> bool:
    """Validate all configs and print results. Returns True if no errors (warnings OK)."""
    errors = validate_all(project_dir)

    warnings = [e for e in errors if e.is_warning]
    hard_errors = [e for e in errors if not e.is_warning]

    for w in warnings:
        log_warn(str(w))

    for e in hard_errors:
        log_error(str(e))

    if not errors:
        from .utils import log_success

        root = project_dir or get_project_dir()
        files_checked = ["aliases.toml", "settings.toml", "plugins.toml"]
        files_checked += [
            name
            for name in ("aliases.local.toml", "settings.local.toml", "plugins.local.toml")
            if (root / "config" / name).exists()
        ]
        log_success(f"All config files valid ({', '.join(files_checked)})")

    return len(hard_errors) == 0
