"""Tests for detect module."""

import platform
from unittest.mock import patch

from core.detect import (
    detect_all,
    detect_current_shell,
    detect_distro,
    detect_package_manager,
    print_detection_info,
)
from core.utils import get_os


def test_detect_distro_consistency_with_os():
    """If os != 'linux', distro must be 'not-linux' and vice versa."""
    os_name = get_os()
    distro = detect_distro()
    if os_name != "linux":
        assert distro == "not-linux"
    else:
        assert distro != "not-linux"


_SHELL_ENV_VARS = ("NU_VERSION", "XONSH_VERSION", "SHELL", "PSModulePath")


def _clean_shell_env(monkeypatch):
    """Remove env vars that affect shell detection without wiping the full environment."""
    for var in _SHELL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_detect_current_shell_unknown_when_no_shell_env(monkeypatch):
    _clean_shell_env(monkeypatch)
    with patch("core.detect.is_windows", return_value=False):
        result = detect_current_shell()
    assert result == "unknown"


def test_detect_current_shell_xonsh_env(monkeypatch):
    """XONSH_VERSION env var should trigger xonsh detection."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("XONSH_VERSION", "0.14.0")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "xonsh"


def test_detect_current_shell_bash_via_shell_var(monkeypatch):
    """$SHELL pointing to bash should be detected."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("SHELL", "/bin/bash")
    with patch("core.detect.is_windows", return_value=False):
        result = detect_current_shell()
    assert result == "bash"


def test_detect_current_shell_nushell_basename(monkeypatch):
    """$SHELL=/usr/bin/nu should detect nushell, not match 'gnu' etc."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("SHELL", "/usr/bin/nu")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "nushell"


def test_detect_current_shell_nushell_full_name(monkeypatch):
    """$SHELL=/usr/bin/nushell should also detect nushell."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("SHELL", "/usr/bin/nushell")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "nushell"


def test_detect_current_shell_does_not_match_gnu(monkeypatch):
    """$SHELL=/usr/bin/gnu_parallel should NOT match nushell."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("SHELL", "/usr/bin/gnu_parallel")
    with patch("core.detect.is_windows", return_value=False):
        result = detect_current_shell()
    assert result == "unknown"


def test_detect_current_shell_nushell_via_nu_version(monkeypatch):
    """NU_VERSION env var should trigger nushell detection."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("NU_VERSION", "0.109.1")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "nushell"


def test_detect_package_manager_returns_known_value():
    """Package manager must be one of the known values."""
    result = detect_package_manager()
    assert result in {"winget", "homebrew", "apt", "yum", "pacman", "none"}


def test_detect_all_returns_complete_dict():
    result = detect_all()
    assert "os" in result
    assert "distro" in result
    assert "shell" in result
    assert "package_manager" in result
    assert "python_version" in result
    # All values should be non-empty strings
    for _key, value in result.items():
        assert isinstance(value, str)
        assert len(value) > 0
    # Values must match individual detect functions
    assert result["os"] == get_os()
    assert result["distro"] == detect_distro()
    assert result["shell"] == detect_current_shell()
    assert result["package_manager"] == detect_package_manager()
    assert result["python_version"] == platform.python_version()


def test_print_detection_info_shows_resolved_path(capsys):
    """Each present tool prints the binary its bare name resolves to; absent ones show missing."""
    with patch(
        "core.detect.resolve_tool_path",
        side_effect=lambda t: "/opt/bin/fzf" if t == "fzf" else None,
    ):
        print_detection_info()
    out = capsys.readouterr().out
    assert "Tool Status" in out
    assert "/opt/bin/fzf" in out
    assert "missing" in out


def test_detect_package_manager_brew_on_macos():
    """On macOS with brew available, should return 'homebrew'."""
    with (
        patch("core.detect.get_os", return_value="macos"),
        patch("core.detect.is_available", side_effect=lambda t: t == "brew"),
    ):
        assert detect_package_manager() == "homebrew"


def test_detect_package_manager_apt_on_linux():
    """On Linux with apt available, should return 'apt'."""
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", side_effect=lambda t: t == "apt"),
    ):
        assert detect_package_manager() == "apt"


def test_detect_package_manager_none_when_nothing():
    """When no package manager is found, should return 'none'."""
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", return_value=False),
    ):
        assert detect_package_manager() == "none"


def test_detect_current_shell_powershell_on_windows(monkeypatch):
    """PSModulePath on Windows should detect powershell."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\Modules")
    with patch("core.detect.is_windows", return_value=True):
        assert detect_current_shell() == "powershell"


def test_detect_current_shell_zsh_via_shell_var(monkeypatch):
    """$SHELL pointing to zsh should be detected."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "zsh"


def test_detect_current_shell_xonsh_via_shell_basename(monkeypatch):
    """$SHELL pointing to xonsh binary should detect xonsh."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("SHELL", "/usr/local/bin/xonsh")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "xonsh"


def test_detect_current_shell_nushell_precedence_over_xonsh(monkeypatch):
    """When both NU_VERSION and XONSH_VERSION are set, nushell should win."""
    _clean_shell_env(monkeypatch)
    monkeypatch.setenv("NU_VERSION", "0.90.0")
    monkeypatch.setenv("XONSH_VERSION", "0.14.0")
    with patch("core.detect.is_windows", return_value=False):
        assert detect_current_shell() == "nushell"


def test_detect_distro_debian():
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", side_effect=lambda t: t == "apt"),
    ):
        assert detect_distro() == "debian"


def test_detect_distro_rhel():
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", side_effect=lambda t: t == "yum"),
    ):
        assert detect_distro() == "rhel"


def test_detect_distro_arch():
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", side_effect=lambda t: t == "pacman"),
    ):
        assert detect_distro() == "arch"


def test_detect_distro_unknown_linux():
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", return_value=False),
    ):
        assert detect_distro() == "unknown"


def test_detect_package_manager_winget_on_windows():
    with (
        patch("core.detect.get_os", return_value="windows"),
        patch("core.detect.is_available", side_effect=lambda t: t == "winget"),
    ):
        assert detect_package_manager() == "winget"


def test_detect_package_manager_yum_on_linux():
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", side_effect=lambda t: t == "yum"),
    ):
        assert detect_package_manager() == "yum"


def test_detect_package_manager_pacman_on_linux():
    with (
        patch("core.detect.get_os", return_value="linux"),
        patch("core.detect.is_available", side_effect=lambda t: t == "pacman"),
    ):
        assert detect_package_manager() == "pacman"


def test_detect_package_manager_none_on_windows():
    with (
        patch("core.detect.get_os", return_value="windows"),
        patch("core.detect.is_available", return_value=False),
    ):
        assert detect_package_manager() == "none"


def test_detect_package_manager_none_on_macos():
    with (
        patch("core.detect.get_os", return_value="macos"),
        patch("core.detect.is_available", return_value=False),
    ):
        assert detect_package_manager() == "none"
