"""Tests for nushell config generation (generate_nushell_config / generate_nushell_env)."""

import re
from pathlib import Path
from unittest.mock import patch

from core.merge import generate_nushell_config, generate_nushell_env
from core.render import render_aliases


def test_generate_nushell_config_has_source_statements(tmp_project: Path):
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert 'source "' in content
    assert "config.nu.template" in content
    assert "aliases.nu" in content
    assert "user-custom.nu" in content


def test_generate_nushell_config_sets_myshell_dir(tmp_project: Path):
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "$env.MY_SHELL_DIR" in content


def test_generate_nushell_config_has_commands(tmp_project: Path):
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert 'navigation.nu"' in content
    assert 'fuzzy.nu"' in content
    assert 'utilities.nu"' in content


def test_generate_nushell_env_has_template_source(tmp_project: Path):
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_env(config_dir, tmp_project)

    assert "env.nu.template" in content
    assert "user-env.nu" in content
    assert "$env.MY_SHELL_DIR" in content


def test_generate_nushell_config_omp_present(tmp_project: Path):
    """When oh-my-posh.nu exists, config should source it."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    (config_dir / "oh-my-posh.nu").write_text("# omp\n", encoding="utf-8")
    content = generate_nushell_config(config_dir, tmp_project)

    assert re.search(r"source \".*oh-my-posh\.nu\"", content)
    assert "# Oh-My-Posh: Not installed" not in content


def test_generate_nushell_config_omp_absent(tmp_project: Path):
    """When oh-my-posh.nu does not exist, config should show placeholder."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "Oh-My-Posh: Not installed" in content
    assert "oh-my-posh.nu'" not in content


def test_generate_nushell_config_zoxide_present(tmp_project: Path):
    """When zoxide.nu exists, config should source it."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    (config_dir / "zoxide.nu").write_text("# zoxide\n", encoding="utf-8")
    content = generate_nushell_config(config_dir, tmp_project)

    assert re.search(r"source \".*zoxide\.nu\"", content)
    assert "Zoxide: Not installed" not in content


def test_generate_nushell_config_zoxide_absent(tmp_project: Path):
    """When zoxide.nu does not exist, config should show placeholder."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "Zoxide: Not installed" in content
    assert "zoxide.nu'" not in content


def test_generate_nushell_config_uses_literal_paths(tmp_project: Path):
    """Paths should be literal strings, not runtime evaluations."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    # On Windows, escape_nushell_path converts backslashes to forward slashes
    path_str = str(tmp_project).replace("\\", "/")
    assert path_str in content


def test_generate_nushell_config_carapace_absent(tmp_project: Path):
    """When carapace.nu does not exist, config should show placeholder."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "Carapace: Not installed" in content
    assert "carapace.nu'" not in content


def test_generate_nushell_config_carapace_present(tmp_project: Path):
    """When carapace.nu exists, config should source it."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    (config_dir / "carapace.nu").write_text("# carapace\n", encoding="utf-8")
    content = generate_nushell_config(config_dir, tmp_project)

    assert re.search(r"source \".*carapace\.nu\"", content)
    assert "# Carapace: Not installed" not in content


def test_generate_nushell_config_has_plugin_section(tmp_project: Path):
    """Generated nushell config should include a Plugins section."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    with patch("core.plugins.is_plugin_installed", return_value=False):
        content = generate_nushell_config(config_dir, tmp_project)

    assert "# Nushell Plugins" in content


def test_generate_nushell_config_plugin_use_when_installed(tmp_project: Path):
    """When plugins are installed, generated config should have plugin use statements."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    with patch("core.plugins.is_plugin_installed", return_value=True):
        content = generate_nushell_config(config_dir, tmp_project)

    assert "plugin use gstat" in content


def test_nushell_config_layer_ordering(tmp_project: Path):
    """LAYER 1 must appear before LAYER 2, which must appear before LAYER 3."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert content.index("LAYER 1") < content.index("LAYER 2") < content.index("LAYER 3")


def test_nushell_config_source_paths_are_absolute(tmp_project: Path):
    """All source/use paths in generated nushell config must be absolute."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    source_paths = re.findall(r"source \"([^\"]+)\"", content)
    use_paths = re.findall(r"use \"([^\"]+)\"", content)

    for path_str in source_paths + use_paths:
        assert Path(path_str).is_absolute(), f"Path is not absolute: {path_str}"


def test_nushell_config_source_paths_exist(tmp_project: Path):
    """Sourced files (excluding user/tool files) must actually exist in the project."""
    # Render aliases first (mimics real deploy flow)
    render_aliases("nushell", project_dir=tmp_project)

    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    source_paths = re.findall(r"source \"([^\"]+)\"", content)
    use_paths = re.findall(r"use \"([^\"]+)\"", content)

    excludes = {
        "user-custom.nu",
        "user-env.nu",
        "oh-my-posh.nu",
        "zoxide.nu",
        "atuin.nu",
        "carapace.nu",
        "mise.nu",
    }

    for path_str in source_paths + use_paths:
        if Path(path_str).name in excludes:
            continue
        assert Path(path_str).exists(), f"Sourced file does not exist: {path_str}"


def test_generate_nushell_config_has_version(tmp_project: Path):
    """Generated nushell config should contain MY_SHELL_VERSION and MY_SHELL_HASH."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "$env.MY_SHELL_VERSION" in content
    assert "$env.MY_SHELL_HASH" in content


def test_generate_nushell_config_has_sysinfo_command(tmp_project: Path):
    """Generated nushell config should use the sysinfo command module."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert 'sysinfo.nu"' in content
    assert re.search(r"use \".*sysinfo\.nu\" \*", content)


def test_nushell_config_version_before_layer1(tmp_project: Path):
    """MY_SHELL_VERSION should be set before LAYER 1 content."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert content.index("MY_SHELL_VERSION") < content.index("LAYER 1")


def test_nushell_config_sysinfo_in_layer2(tmp_project: Path):
    """sysinfo use statement should be in the LAYER 2 section."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    sysinfo_pos = content.index('sysinfo.nu"')
    assert content.index("LAYER 2") < sysinfo_pos < content.index("LAYER 3")


def test_nushell_sysinfo_path_is_absolute(tmp_project: Path):
    """sysinfo.nu path in generated config must be absolute."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    sysinfo_paths = re.findall(r"use \"([^\"]*sysinfo\.nu)\" \*", content)
    assert len(sysinfo_paths) == 1
    assert Path(sysinfo_paths[0]).is_absolute()


def test_nushell_sysinfo_path_exists(tmp_project: Path):
    """sysinfo.nu referenced in generated config must exist in the project."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    sysinfo_paths = re.findall(r"use \"([^\"]*sysinfo\.nu)\" \*", content)
    assert len(sysinfo_paths) == 1
    assert Path(sysinfo_paths[0]).exists()


def test_generate_nushell_config_atuin_absent(tmp_project: Path):
    """When atuin.nu does not exist, config should show placeholder."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "Atuin: Not installed" in content
    assert "atuin.nu'" not in content


def test_generate_nushell_config_atuin_present(tmp_project: Path):
    """When atuin.nu exists, config should source it."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    (config_dir / "atuin.nu").write_text("# atuin\n", encoding="utf-8")
    content = generate_nushell_config(config_dir, tmp_project)

    assert re.search(r"source \".*atuin\.nu\"", content)
    assert "# Atuin: Not installed" not in content


def test_generate_nushell_config_mise_present(tmp_project: Path):
    """When mise.nu exists, config should source it."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    (config_dir / "mise.nu").write_text("# mise\n", encoding="utf-8")
    content = generate_nushell_config(config_dir, tmp_project)

    assert re.search(r"source \".*mise\.nu\"", content)
    assert "# Mise: Not activated" not in content


def test_generate_nushell_config_mise_absent(tmp_project: Path):
    """When mise.nu does not exist, config should show placeholder."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_config(config_dir, tmp_project)

    assert "Mise: Not activated" in content
    assert "mise.nu'" not in content


def test_generate_nushell_config_exports_mise_trusted_paths(tmp_project: Path):
    """Generated config should trust MY_SHELL_DIR before sourcing mise hooks."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    (config_dir / "mise.nu").write_text("# mise\n", encoding="utf-8")
    content = generate_nushell_config(config_dir, tmp_project)

    assert "MISE_TRUSTED_CONFIG_PATHS" in content
    assert "$env.MY_SHELL_DIR not-in $_trusted_paths" in content


def test_nushell_env_sets_carapace_bridges(tmp_project: Path):
    """Generated env.nu should set CARAPACE_BRIDGES env var."""
    config_dir = tmp_project / "nushell-config"
    config_dir.mkdir()
    content = generate_nushell_env(config_dir, tmp_project)

    assert "$env.CARAPACE_BRIDGES = 'zsh,fish,bash,inshellisense'" in content
