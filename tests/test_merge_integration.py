"""Integration tests for the xonsh-python discovery and xontrib install helpers.

Mocks subprocess.run at the return value, with realistic CompletedProcess objects, so the
helpers' own parsing runs for real.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.merge import (
    XONTRIB_PACKAGES,
    _find_xonsh_python,
    _find_xonsh_python_uv,
    _install_xontribs,
)


@pytest.mark.integration
def test_find_xonsh_python_uv_realistic(tmp_path: Path):
    """Create fake uv tools dir with Linux layout, verify path resolution."""
    # Simulate: uv tool dir -> /home/user/.local/share/uv/tools
    tools_dir = tmp_path / "uv" / "tools"
    xonsh_bin = tools_dir / "xonsh" / "bin"
    xonsh_bin.mkdir(parents=True)
    python3 = xonsh_bin / "python3"
    python3.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    fake_result = subprocess.CompletedProcess(
        args=["uv", "tool", "dir"],
        returncode=0,
        stdout=str(tools_dir) + "\n",
        stderr="",
    )

    with (
        patch("core.merge.shutil.which", return_value="/usr/bin/uv"),
        patch("core.merge.subprocess.run", return_value=fake_result),
        patch("core.merge.is_windows", return_value=False),
    ):
        result = _find_xonsh_python_uv()

    assert result == str(python3)


@pytest.mark.integration
def test_find_xonsh_python_uv_windows_layout(tmp_path: Path):
    """Windows layout: Scripts/python.exe instead of bin/python3."""
    tools_dir = tmp_path / "uv" / "tools"
    xonsh_scripts = tools_dir / "xonsh" / "Scripts"
    xonsh_scripts.mkdir(parents=True)
    python_exe = xonsh_scripts / "python.exe"
    python_exe.write_text("fake python\n", encoding="utf-8")

    fake_result = subprocess.CompletedProcess(
        args=["uv", "tool", "dir"],
        returncode=0,
        stdout=str(tools_dir) + "\n",
        stderr="",
    )

    with (
        patch("core.merge.shutil.which", return_value="C:\\Users\\test\\.local\\bin\\uv.exe"),
        patch("core.merge.subprocess.run", return_value=fake_result),
        patch("core.merge.is_windows", return_value=True),
    ):
        result = _find_xonsh_python_uv()

    assert result == str(python_exe)


@pytest.mark.integration
def test_find_xonsh_python_uv_missing_dir(tmp_path: Path):
    """uv tool dir returns nonexistent path -> returns None."""
    fake_result = subprocess.CompletedProcess(
        args=["uv", "tool", "dir"],
        returncode=0,
        stdout=str(tmp_path / "nonexistent" / "tools") + "\n",
        stderr="",
    )

    with (
        patch("core.merge.shutil.which", return_value="/usr/bin/uv"),
        patch("core.merge.subprocess.run", return_value=fake_result),
        patch("core.merge.is_windows", return_value=False),
    ):
        result = _find_xonsh_python_uv()

    assert result is None


@pytest.mark.integration
def test_find_xonsh_python_uv_no_uv():
    """uv not in PATH -> returns None immediately."""
    with patch("core.merge.shutil.which", return_value=None):
        result = _find_xonsh_python_uv()

    assert result is None


@pytest.mark.integration
def test_find_xonsh_python_uv_subprocess_error():
    """Returns None when 'uv tool dir' fails."""
    with (
        patch("core.merge.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "core.merge.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "uv"),
        ),
    ):
        assert _find_xonsh_python_uv() is None


@pytest.mark.integration
def test_find_xonsh_python_uv_no_candidates(tmp_path: Path):
    """Returns None when uv tool dir exists but no python binary found."""
    xonsh_venv = tmp_path / "xonsh" / "bin"
    xonsh_venv.mkdir(parents=True)

    fake_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=str(tmp_path) + "\n", stderr=""
    )
    with (
        patch("core.merge.shutil.which", return_value="/usr/bin/uv"),
        patch("core.merge.subprocess.run", return_value=fake_result),
        patch("core.merge.is_windows", return_value=False),
    ):
        assert _find_xonsh_python_uv() is None


@pytest.mark.integration
def test_find_xonsh_python_returns_none_when_which_fails():
    """Returns None when xonsh is not in PATH."""
    with patch("core.merge.shutil.which", return_value=None):
        assert _find_xonsh_python() is None


@pytest.mark.integration
def test_find_xonsh_python_windows_parent_fallback(tmp_path: Path):
    """On Windows, falls back to python.exe in parent directory."""
    scripts_dir = tmp_path / "Scripts"
    scripts_dir.mkdir()
    xonsh_exe = scripts_dir / "xonsh.exe"
    xonsh_exe.write_text("fake", encoding="utf-8")
    (tmp_path / "python.exe").write_text("fake", encoding="utf-8")

    with (
        patch("core.merge.shutil.which", return_value=str(xonsh_exe)),
        patch("core.merge.is_windows", return_value=True),
    ):
        result = _find_xonsh_python()

    assert result == str(tmp_path / "python.exe")


@pytest.mark.integration
def test_find_xonsh_python_uv_fallback(tmp_path: Path):
    """When sibling python not found, falls back to _find_xonsh_python_uv."""
    xonsh_exe = tmp_path / "xonsh"
    xonsh_exe.write_text("fake", encoding="utf-8")

    with (
        patch("core.merge.shutil.which", return_value=str(xonsh_exe)),
        patch("core.merge.is_windows", return_value=False),
        patch("core.merge._find_xonsh_python_uv", return_value="/uv/python3"),
    ):
        result = _find_xonsh_python()

    assert result == "/uv/python3"


@pytest.mark.integration
def test_find_xonsh_python_all_fallbacks_fail(tmp_path: Path):
    """Returns None when all fallbacks fail."""
    xonsh_exe = tmp_path / "xonsh"
    xonsh_exe.write_text("fake", encoding="utf-8")

    with (
        patch("core.merge.shutil.which", return_value=str(xonsh_exe)),
        patch("core.merge.is_windows", return_value=False),
        patch("core.merge._find_xonsh_python_uv", return_value=None),
    ):
        result = _find_xonsh_python()

    assert result is None


@pytest.mark.integration
def test_find_xonsh_python_sibling(tmp_path: Path):
    """xonsh and python3 side by side -> found via sibling check."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xonsh").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    python3 = bin_dir / "python3"
    python3.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    with (
        patch("core.merge.shutil.which", return_value=str(bin_dir / "xonsh")),
        patch("core.merge.is_windows", return_value=False),
    ):
        result = _find_xonsh_python()

    assert result == str(python3)


@pytest.mark.integration
def test_find_xonsh_python_trampoline_only(tmp_path: Path):
    """xonsh exists but no sibling python -> falls through to uv fallback."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "xonsh").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    # No python3 sibling -- should fall through to _find_xonsh_python_uv

    with (
        patch("core.merge.shutil.which", return_value=str(bin_dir / "xonsh")),
        patch("core.merge.is_windows", return_value=False),
        patch("core.merge._find_xonsh_python_uv", return_value=None) as mock_uv,
    ):
        result = _find_xonsh_python()

    assert result is None
    mock_uv.assert_called_once()


@pytest.mark.integration
def test_install_xontribs_uses_uv_pip_for_uv_venv(tmp_path: Path):
    """When _find_xonsh_python_uv() succeeds, command uses 'uv pip install --python'."""
    uv_python = str(tmp_path / "uv" / "tools" / "xonsh" / "bin" / "python3")

    calls = []

    def capture_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("core.merge._find_xonsh_python", return_value=uv_python),
        patch("core.merge._find_xonsh_python_uv", return_value=uv_python),
        patch("core.merge.is_available", side_effect=lambda t: t == "uv"),
        patch("core.merge.is_windows", return_value=False),
        patch("core.merge.subprocess.run", side_effect=capture_run),
    ):
        _install_xontribs()

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "uv"
    assert cmd[1] == "pip"
    assert cmd[2] == "install"
    assert "--python" in cmd
    assert uv_python in cmd
    assert "--quiet" in cmd
    for pkg in XONTRIB_PACKAGES:
        assert pkg in cmd


@pytest.mark.integration
def test_install_xontribs_uses_python_pip_fallback():
    """When uv path not found, command uses 'python -m pip'."""
    python_path = "/usr/bin/python3"

    calls = []

    def capture_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("core.merge._find_xonsh_python", return_value=python_path),
        patch("core.merge._find_xonsh_python_uv", return_value=None),
        patch("core.merge.is_windows", return_value=False),
        patch("core.merge.subprocess.run", side_effect=capture_run),
    ):
        _install_xontribs()

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == python_path
    assert cmd[1] == "-m"
    assert cmd[2] == "pip"
    assert "--quiet" in cmd
    for pkg in XONTRIB_PACKAGES:
        assert pkg in cmd


@pytest.mark.integration
def test_install_xontribs_handles_subprocess_failure():
    """subprocess raises CalledProcessError -> logs error, doesn't crash."""
    python_path = "/usr/bin/python3"

    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="error")

    with (
        patch("core.merge._find_xonsh_python", return_value=python_path),
        patch("core.merge._find_xonsh_python_uv", return_value=None),
        patch("core.merge.is_windows", return_value=False),
        patch("core.merge.subprocess.run", side_effect=failing_run),
        patch("core.merge.log_error") as mock_log_error,
    ):
        _install_xontribs()

    mock_log_error.assert_called_once()
    assert "xontrib" in mock_log_error.call_args[0][0].lower()


@pytest.mark.integration
def test_install_xontribs_python_is_none():
    """When _find_xonsh_python() returns None, should log error and skip."""
    with (
        patch("core.merge._find_xonsh_python", return_value=None),
        patch("core.merge.log_error") as mock_log_error,
        patch("core.merge.subprocess.run") as mock_run,
    ):
        _install_xontribs()

    assert "xonsh not found" in mock_log_error.call_args[0][0].lower()
    mock_run.assert_not_called()


@pytest.mark.integration
def test_install_xontribs_windows_includes_free_cwd(tmp_path: Path):
    """On Windows, xontrib-free-cwd should be included in the install command."""
    from core.merge import XONTRIB_PACKAGES_WINDOWS

    python_path = str(tmp_path / "python3")

    calls = []

    def capture_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("core.merge._find_xonsh_python", return_value=python_path),
        patch("core.merge._find_xonsh_python_uv", return_value=None),
        patch("core.merge.is_windows", return_value=True),
        patch("core.merge.subprocess.run", side_effect=capture_run),
    ):
        _install_xontribs()

    assert len(calls) == 1
    cmd = calls[0]
    for pkg in XONTRIB_PACKAGES_WINDOWS:
        assert pkg in cmd


@pytest.mark.integration
def test_install_xontribs_returns_installed_count_on_success():
    """A successful install returns the number of packages installed."""
    from core.merge import XONTRIB_PACKAGES

    with (
        patch("core.merge._find_xonsh_python", return_value="/usr/bin/python3"),
        patch("core.merge._find_xonsh_python_uv", return_value=None),
        patch("core.merge.is_windows", return_value=False),
        patch(
            "core.merge.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        assert _install_xontribs() == len(XONTRIB_PACKAGES)


@pytest.mark.integration
def test_install_xontribs_returns_zero_on_failure():
    """A failed install returns 0 so the summary never claims installs that didn't happen."""
    with (
        patch("core.merge._find_xonsh_python", return_value="/usr/bin/python3"),
        patch("core.merge._find_xonsh_python_uv", return_value=None),
        patch("core.merge.is_windows", return_value=False),
        patch(
            "core.merge.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "cmd"),
        ),
    ):
        assert _install_xontribs() == 0


@pytest.mark.integration
def test_install_xontribs_returns_zero_when_python_missing():
    """No xonsh python -> nothing installed -> 0."""
    with patch("core.merge._find_xonsh_python", return_value=None):
        assert _install_xontribs() == 0
