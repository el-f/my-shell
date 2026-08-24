"""Tests for xonsh config generation (generate_xonsh_config)."""

import re
from pathlib import Path

from core.merge import generate_xonsh_config


def test_generate_xonsh_config_has_imports(tmp_project: Path):
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "import sys" in content
    assert "from pathlib import Path" in content


def test_generate_xonsh_config_has_commands(tmp_project: Path):
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "from navigation import" in content
    assert "from fuzzy import" in content
    assert "from utilities import" in content


def test_generate_xonsh_config_has_integrations(tmp_project: Path):
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "oh-my-posh" in content
    assert "zoxide" in content


def test_generate_xonsh_config_sets_myshell_dir(tmp_project: Path):
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "$MY_SHELL_DIR" in content


def test_generate_xonsh_config_user_custom(tmp_project: Path):
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "user-custom.xsh" in content


def test_generate_xonsh_config_has_xontrib_load(tmp_project: Path):
    """Generated xonsh config should load essential xontribs."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "xontrib load" in content
    assert "whole_word_jumping" in content
    assert "abbrevs" in content
    assert "bashisms" in content
    assert "argcomplete" in content
    assert "back2dir" in content
    assert "output_search" in content
    assert "vox" in content
    assert "hist_navigator" in content


def test_generate_xonsh_config_xontrib_free_cwd_guarded_by_platform(tmp_project: Path):
    """Generated xonsh config should load free_cwd inside a Windows runtime guard."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "whole_word_jumping" in content
    assert "free_cwd" in content
    assert "_plat.system() == 'Windows'" in content


def test_xonsh_config_layer_ordering(tmp_project: Path):
    """LAYER 1 must appear before LAYER 2, which must appear before LAYER 3."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert content.index("LAYER 1") < content.index("LAYER 2") < content.index("LAYER 3")


def test_generate_xonsh_config_has_version(tmp_project: Path):
    """Generated xonsh config should contain MY_SHELL_VERSION env var."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "$MY_SHELL_VERSION" in content


def test_generate_xonsh_config_has_sysinfo_command(tmp_project: Path):
    """Generated xonsh config should import and register the sysinfo command."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "from sysinfo import _sysinfo" in content
    assert "aliases['sysinfo'] = _sysinfo" in content


def test_xonsh_config_sets_omp_theme(tmp_project: Path):
    """Generated xonsh config should set MY_SHELL_OMP_THEME from settings."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "$MY_SHELL_OMP_THEME = 'jblab_2021'" in content


def test_xonsh_config_custom_omp_theme(tmp_project: Path):
    """Generated xonsh config should use a custom theme from settings.toml."""
    (tmp_project / "config" / "settings.toml").write_text(
        '[oh-my-posh]\ntheme = "night-owl"\n',
        encoding="utf-8",
    )
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "$MY_SHELL_OMP_THEME = 'night-owl'" in content


def test_xonsh_config_omp_theme_default_when_no_settings(tmp_project: Path):
    """When settings.toml is absent, xonsh config should default to jblab_2021."""
    settings_path = tmp_project / "config" / "settings.toml"
    if settings_path.exists():
        settings_path.unlink()

    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "$MY_SHELL_OMP_THEME = 'jblab_2021'" in content


def test_xonsh_config_escapes_omp_theme_quote(tmp_project: Path):
    """A single quote in the theme must be escaped so the generated .xsh stays valid."""
    (tmp_project / "config" / "settings.toml").write_text(
        '[oh-my-posh]\ntheme = "weird\'theme"\n',
        encoding="utf-8",
    )
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert r"$MY_SHELL_OMP_THEME = 'weird\'theme'" in content


def test_xonshrc_base_ensures_home_env_var():
    """xonshrc_base.xsh must conditionally set $HOME for xontribs (e.g. back2dir on Windows)."""
    base = Path("shells/xonsh/xonshrc_base.xsh")
    content = base.read_text(encoding="utf-8")

    assert re.search(
        r"if\s+'HOME'\s+not\s+in\s+__xonsh__\.env.*:\s*\n\s+\$HOME\s*=",
        content,
    ), "xonshrc_base.xsh must conditionally set $HOME via __xonsh__.env guard"

    home_assign = content.index("_home = Path.home()")
    home_guard = content.index("'HOME' not in")
    path_section = content.index("if _is_windows:")
    assert home_assign < home_guard < path_section, (
        "$HOME guard must be between _home assignment and path configuration"
    )


def test_generate_xonsh_config_has_carapace(tmp_project: Path):
    """Generated xonsh config should reference carapace integration."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "carapace" in content.lower()
    assert "Carapace" in content


def test_generate_xonsh_config_has_mise(tmp_project: Path):
    """Generated xonsh config should reference mise integration."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "mise" in content.lower()
    assert "Mise" in content


def test_generate_xonsh_config_exports_mise_trusted_paths(tmp_project: Path):
    """Generated xonsh config should trust MY_SHELL_DIR before sourcing mise hooks."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "MISE_TRUSTED_CONFIG_PATHS" in content
    assert "_my_shell_dir = os.environ.get('MY_SHELL_DIR', '')" in content
    assert "_mise_trusted.append(_my_shell_dir)" in content


def test_generate_xonsh_config_has_atuin(tmp_project: Path):
    """Generated xonsh config should reference atuin integration."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    assert "atuin" in content.lower()
    assert "Atuin" in content


def test_generate_xonsh_config_suppresses_dependency_escape_warning(tmp_project: Path):
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()

    content = generate_xonsh_config(config_dir, tmp_project)

    assert 'message=".*invalid escape sequence.*"' in content
    assert "category=SyntaxWarning" in content
