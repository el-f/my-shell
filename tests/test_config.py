"""Tests for config module."""

from pathlib import Path

import pytest

from core.config import TOMLLoadError, get_config_dir, load_aliases


def test_load_aliases_from_fixture(tmp_project: Path):
    aliases = load_aliases(tmp_project)
    assert "navigation" in aliases
    assert "git" in aliases
    assert ".." in aliases["navigation"]
    assert aliases["navigation"][".."] == "cd .."


def test_load_aliases_modern_replacements(tmp_project: Path):
    aliases = load_aliases(tmp_project)
    grep_val = aliases["modern_replacements"]["grep"]
    assert grep_val["command"] == "rg"
    assert grep_val["comment"] == "ripgrep"


def test_load_aliases_wrappers(tmp_project: Path):
    aliases = load_aliases(tmp_project)
    assert "wrappers" in aliases
    assert "fd" in aliases["wrappers"]
    assert aliases["wrappers"]["fd"]["preferred"] == "fd"
    assert aliases["wrappers"]["fd"]["fallback"] == "fdfind"


def test_load_aliases_system_info_with_shell_overrides(tmp_project: Path):
    aliases = load_aliases(tmp_project)
    meminfo = aliases["system_info"]["meminfo"]
    assert meminfo["nushell"] == "sys mem"
    assert meminfo["xonsh_fn"] == "platform_meminfo"


def test_load_aliases_local_override(tmp_project: Path):
    """aliases.local.toml should merge into and override base aliases."""
    local_path = tmp_project / "config" / "aliases.local.toml"
    local_path.write_text(
        '[git]\nglog = "git log --oneline"\ncustom = "git custom"\n\n[my_section]\nfoo = "bar"\n',
        encoding="utf-8",
    )

    aliases = load_aliases(tmp_project)

    # Overridden value
    assert aliases["git"]["glog"] == "git log --oneline"
    # Existing value preserved
    assert aliases["git"]["g"] == "git"
    # New value in existing section
    assert aliases["git"]["custom"] == "git custom"
    # Entirely new section
    assert aliases["my_section"]["foo"] == "bar"


def test_load_aliases_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Aliases config not found"):
        load_aliases(tmp_path / "nonexistent")


def test_get_config_dir_nushell():
    d = get_config_dir("nushell")
    assert isinstance(d, Path)
    assert "nushell" in str(d).lower()


def test_get_config_dir_xonsh():
    d = get_config_dir("xonsh")
    assert isinstance(d, Path)
    assert "xonsh" in str(d).lower()


def test_load_aliases_default_project_dir():
    """load_aliases() with no argument should use get_project_dir() fallback."""
    aliases = load_aliases()
    assert isinstance(aliases, dict)
    assert len(aliases) > 0


def test_get_config_dir_unsupported():
    with pytest.raises(ValueError, match="Unsupported shell"):
        get_config_dir("fish")


def test_load_aliases_malformed_toml(tmp_path: Path):
    """Malformed TOML should raise TOMLLoadError with a clear message."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "aliases.toml").write_text(
        "[broken\nnot = valid toml",
        encoding="utf-8",
    )

    with pytest.raises(TOMLLoadError, match="Invalid TOML"):
        load_aliases(tmp_path)


def test_load_aliases_empty_toml(tmp_path: Path):
    """An empty but valid TOML file should return an empty dict."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "aliases.toml").write_text("", encoding="utf-8")

    result = load_aliases(tmp_path)
    assert result == {}


def test_load_settings_missing_file_returns_defaults(tmp_path: Path):
    """When settings.toml doesn't exist, defaults should be returned."""
    from core.config import load_settings

    result = load_settings(tmp_path)
    assert result["oh-my-posh"]["theme"] == "jblab_2021"


def test_load_settings_local_override(tmp_path: Path):
    """settings.local.toml should merge into and override base settings."""
    from core.config import load_settings

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.toml").write_text(
        "[integrations]\nzoxide = true\natuin = true\n",
        encoding="utf-8",
    )
    (config_dir / "settings.local.toml").write_text(
        "[integrations]\nzoxide = false\n",
        encoding="utf-8",
    )

    result = load_settings(tmp_path)
    # Local override should disable zoxide
    assert result["integrations"]["zoxide"] is False
    # Non-overridden value from settings.toml should remain
    assert result["integrations"]["atuin"] is True
