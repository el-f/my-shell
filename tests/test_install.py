"""Unit tests for install module."""

import json
import subprocess
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from core.install import (
    _get_nu_version,
    _get_xonsh_version,
    _install_nushell_from_github,
    _run,
    install_all_tools,
    install_shell,
    install_shells_for_setup,
    install_tool,
)
from core.registry import XONTRIB_PACKAGES


def test_get_nu_version_returns_version():
    """_get_nu_version returns the version string when nu is available."""
    with (
        patch("core.install.is_available", return_value=True),
        patch(
            "core.install.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["nu", "--version"], returncode=0, stdout="0.107.0\n", stderr=""
            ),
        ),
    ):
        assert _get_nu_version() == "0.107.0"


def test_get_nu_version_not_installed():
    """_get_nu_version returns None when nu is not available."""
    with patch("core.install.is_available", return_value=False):
        assert _get_nu_version() is None


def test_get_nu_version_file_not_found():
    """_get_nu_version returns None when subprocess raises FileNotFoundError."""
    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=FileNotFoundError),
    ):
        assert _get_nu_version() is None


def test_get_nu_version_timeout():
    """_get_nu_version returns None when subprocess times out."""
    with (
        patch("core.install.is_available", return_value=True),
        patch(
            "core.install.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="nu", timeout=10),
        ),
    ):
        assert _get_nu_version() is None


def test_install_nushell_already_installed_upgrades(capsys):
    """When nushell is already installed and outdated, should attempt upgrade."""
    mock_run = MagicMock()

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["nu", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="0.107.0\n", stderr=""
            )
        if cmd == ["brew", "outdated", "nushell"]:
            # Real brew exits non-zero for a named formula that is outdated.
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="nushell (0.107.0) < 0.115.0\n", stderr=""
            )
        return mock_run(cmd, **kwargs)

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.shutil.which", return_value="/usr/bin/nu"),
    ):
        install_shell("nushell")

    mock_run.assert_called_once()
    assert "upgrade" in mock_run.call_args[0][0]


def test_install_nushell_brew_up_to_date_skips_upgrade(capsys):
    """When brew reports no newer version, the upgrade command never runs."""
    mock_run = MagicMock()

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["nu", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="0.115.0\n", stderr=""
            )
        if cmd == ["brew", "outdated", "nushell"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return mock_run(cmd, **kwargs)

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.shutil.which", return_value="/usr/bin/nu"),
    ):
        install_shell("nushell")

    mock_run.assert_not_called()
    assert "up to date" in capsys.readouterr().out


@pytest.mark.parametrize(
    "outdated_result",
    [
        subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="No available formula"
        ),
        OSError("brew missing"),
    ],
)
def test_install_nushell_brew_check_failure_still_upgrades(outdated_result):
    """A failed `brew outdated` must not be read as 'up to date'."""
    mock_run = MagicMock()

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["nu", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="0.107.0\n", stderr=""
            )
        if cmd == ["brew", "outdated", "nushell"]:
            if isinstance(outdated_result, BaseException):
                raise outdated_result
            return outdated_result
        return mock_run(cmd, **kwargs)

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.shutil.which", return_value="/usr/bin/nu"),
    ):
        install_shell("nushell")

    mock_run.assert_called_once()
    assert "upgrade" in mock_run.call_args[0][0]


@pytest.mark.parametrize(
    "pkg_manager, expected_cmd",
    [
        (
            "winget",
            [
                "winget",
                "install",
                "--id",
                "Nushell.Nushell",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--disable-interactivity",
            ],
        ),
        ("homebrew", ["brew", "install", "nushell"]),
        # -Sy, not -S: a stale package DB makes pacman request a version the mirrors dropped.
        ("pacman", ["pacman", "-Sy", "--noconfirm", "nushell"]),
    ],
)
def test_install_nushell_pkg_managers(pkg_manager, expected_cmd):
    mock_run = MagicMock()
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value=pkg_manager),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value="/usr/bin/nu"),
    ):
        install_shell("nushell")

    mock_run.assert_called_once_with(
        expected_cmd, check=True, stdin=subprocess.DEVNULL, timeout=120
    )


@pytest.mark.parametrize("pkg_manager", ["apt", "yum", "none"])
def test_install_nushell_linux_falls_back_to_github(pkg_manager):
    """Any Linux without a matching install command uses the release tarball.

    dnf (reported as yum) and zypper (reported as none) have no command of their own,
    so Fedora and openSUSE reach nushell through this path.
    """
    mock_github = MagicMock()
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value=pkg_manager),
        patch("core.install.get_os", return_value="linux"),
        patch("core.install._install_nushell_from_github", mock_github),
        patch("core.install.shutil.which", return_value="/usr/bin/nu"),
    ):
        install_shell("nushell")

    mock_github.assert_called_once()


def test_install_nushell_linux_keeps_unmanaged_existing_binary(capsys):
    """An existing mise/manual Nu must not trigger repeated GitHub downloads."""
    mock_github = MagicMock()
    with (
        patch("core.install._get_nu_version", return_value="0.115.0"),
        patch("core.install.detect_package_manager", return_value="apt"),
        patch("core.install.get_os", return_value="linux"),
        patch("core.install._install_nushell_from_github", mock_github),
    ):
        install_shell("nushell")

    mock_github.assert_not_called()
    assert "no upgrade method for apt" in capsys.readouterr().out


def test_install_nushell_no_pkg_manager_off_linux():
    """macOS without brew has no fallback, so it must still fail loudly."""
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="none"),
        patch("core.install.get_os", return_value="macos"),
        pytest.raises(RuntimeError, match="No supported package manager"),
    ):
        install_shell("nushell")


def test_get_xonsh_version_returns_version():
    """_get_xonsh_version parses 'xonsh/0.22.3' format."""
    with (
        patch("core.install.is_available", return_value=True),
        patch(
            "core.install.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["xonsh", "--version"], returncode=0, stdout="xonsh/0.22.3\n", stderr=""
            ),
        ),
    ):
        assert _get_xonsh_version() == "0.22.3"


def test_get_xonsh_version_not_installed():
    """_get_xonsh_version returns None when xonsh is not available."""
    with patch("core.install.is_available", return_value=False):
        assert _get_xonsh_version() is None


def test_install_xonsh_upgrade_via_uv():
    """A uv upgrade must restore xontribs into the persistent tool receipt."""
    mock_run = MagicMock()

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["xonsh", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="xonsh/0.22.3\n", stderr=""
            )
        return mock_run(cmd, **kwargs)

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.shutil.which", return_value="/usr/bin/xonsh"),
    ):
        install_shell("xonsh")

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0] == ["uv", "tool", "upgrade", "xonsh"]
    persisted = mock_run.call_args_list[1].args[0]
    assert persisted[:5] == ["uv", "tool", "install", "--force", "--upgrade"]
    assert "xonsh[full]" in persisted
    for package in XONTRIB_PACKAGES:
        assert package in persisted


def test_install_xonsh_upgrade_failure_keeps_existing_binary():
    """An existing non-uv xonsh must not be replaced by a second tool install."""
    calls = []

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["xonsh", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="xonsh/0.22.3\n", stderr=""
            )
        if cmd == ["uv", "tool", "upgrade", "xonsh"]:
            calls.append(cmd)
            raise subprocess.CalledProcessError(1, cmd)
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.shutil.which", return_value="/usr/bin/xonsh"),
    ):
        install_shell("xonsh")

    assert calls == [["uv", "tool", "upgrade", "xonsh"]]


def test_install_xonsh_upgrade_via_pip():
    """When xonsh is installed and only pip available, should call pip upgrade."""
    mock_run = MagicMock()

    def _avail(tool):
        return tool in ("xonsh", "pip")

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["xonsh", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="xonsh/0.22.3\n", stderr=""
            )
        return mock_run(cmd, **kwargs)

    with (
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.shutil.which", return_value="/usr/bin/xonsh"),
    ):
        install_shell("xonsh")

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == ["pip", "install", "--user", "--upgrade", "xonsh[full]"]


def test_install_xonsh_already_latest(capsys):
    """When xonsh version unchanged after upgrade, should log 'up to date'."""

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["xonsh", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="xonsh/0.22.3\n", stderr=""
            )
        return MagicMock()

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.shutil.which", return_value="/usr/bin/xonsh"),
    ):
        install_shell("xonsh")

    assert "up to date" in capsys.readouterr().out


def test_install_xonsh_uv():
    mock_run = MagicMock()

    def _avail(tool):
        return tool == "uv"

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["xonsh", "--version"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return mock_run(cmd, **kwargs)

    with (
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.shutil.which", return_value="/usr/bin/xonsh"),
    ):
        install_shell("xonsh")

    mock_run.assert_called_once()
    command = mock_run.call_args.args[0]
    assert command[:4] == ["uv", "tool", "install", "xonsh[full]"]
    for package in XONTRIB_PACKAGES:
        assert package in command
    assert mock_run.call_args.kwargs == {
        "check": True,
        "stdin": subprocess.DEVNULL,
        "timeout": 120,
    }


def test_install_xonsh_pip_fallback():
    mock_run = MagicMock()

    def _avail(tool):
        return tool == "pip"

    with (
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value="/usr/bin/xonsh"),
    ):
        install_shell("xonsh")

    mock_run.assert_called_once_with(
        ["pip", "install", "--user", "xonsh[full]"],
        check=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def test_install_xonsh_no_uv_no_pip():
    with (
        patch("core.install.is_available", return_value=False),
        pytest.raises(RuntimeError, match="Neither uv nor pip found"),
    ):
        install_shell("xonsh")


def test_install_unknown_shell():
    with pytest.raises(ValueError, match="Unknown shell"):
        install_shell("fish")


def test_install_nushell_binary_not_found_after_install(capsys):
    """After install succeeds but binary is not in PATH, should log error."""
    mock_run = MagicMock()
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value=None),
    ):
        install_shell("nushell")

    assert "not found in PATH" in capsys.readouterr().err


def test_install_xonsh_binary_not_found_after_install(capsys):
    """After install succeeds but binary is not in PATH, should log error."""
    mock_run = MagicMock()
    with (
        patch("core.install.is_available", side_effect=lambda t: t == "uv"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value=None),
    ):
        install_shell("xonsh")

    assert "not found in PATH" in capsys.readouterr().err


# _install_nushell_from_github


def _make_github_release(
    tag: str = "0.110.0",
    target: str = "x86_64-unknown-linux-gnu",
    full_suffix: bool = False,
):
    """Create a mock GitHub release JSON with matching asset."""
    suffix = "-full" if full_suffix else ""
    asset_name = f"nu-{tag}-{target}{suffix}.tar.gz"
    return {
        "tag_name": tag,
        "assets": [
            {
                "name": asset_name,
                "browser_download_url": f"https://github.com/nushell/nushell/releases/download/{tag}/{asset_name}",
            }
        ],
    }


def _mock_urlopen(release_data):
    """Create a mock context manager for urllib.request.urlopen."""
    response = MagicMock()
    response.read.return_value = json.dumps(release_data).encode()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


def _run_github_install_test(
    tmp_path,
    *,
    machine="x86_64",
    target="x86_64-unknown-linux-gnu",
    tag="0.110.0",
    full_suffix=False,
):
    """Shared setup for _install_nushell_from_github happy-path tests."""
    release = _make_github_release(tag=tag, target=target, full_suffix=full_suffix)
    mock_response = _mock_urlopen(release)

    mock_tar = MagicMock()
    mock_member = MagicMock()
    mock_member.name = f"nu-{tag}-{target}/nu"
    mock_tar.__enter__ = MagicMock(return_value=mock_tar)
    mock_tar.__exit__ = MagicMock(return_value=False)
    mock_tar.getmembers.return_value = [mock_member]

    install_dir = tmp_path / ".local" / "bin"
    install_dir.mkdir(parents=True)
    (install_dir / "nu").write_bytes(b"")

    mock_download_response = MagicMock()
    mock_download_response.read.return_value = b"fake-tar-data"
    mock_download_response.__enter__ = MagicMock(return_value=mock_download_response)
    mock_download_response.__exit__ = MagicMock(return_value=False)

    patches = [
        patch("core.install.platform.machine", return_value=machine),
        patch(
            "core.install.urllib.request.urlopen",
            side_effect=[mock_response, mock_download_response],
        ),
        patch("core.install.tarfile.open", return_value=mock_tar),
        patch("core.install.Path.home", return_value=tmp_path),
    ]
    if sys.platform != "win32":
        patches.append(patch("core.install.os.getuid", return_value=1000))
    else:
        patches.append(patch("core.install.os.getuid", create=True, return_value=1000))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        _install_nushell_from_github()

    mock_tar.extract.assert_called_once()
    assert mock_member.name == "nu"


def test_install_nushell_from_github_happy_path(tmp_path):
    """Happy path: mock release JSON, verify tar extraction is called."""
    _run_github_install_test(tmp_path)


def test_install_nushell_from_github_pinned_version(tmp_path, monkeypatch):
    """MY_SHELL_NU_VERSION pins the fetched release (CI one-back leg)."""
    monkeypatch.setenv("MY_SHELL_NU_VERSION", "0.109.0")
    release = _make_github_release(tag="0.109.0")
    seen_urls = []

    mock_download_response = MagicMock()
    mock_download_response.read.return_value = b"fake-tar-data"
    mock_download_response.__enter__ = MagicMock(return_value=mock_download_response)
    mock_download_response.__exit__ = MagicMock(return_value=False)

    def _urlopen(req, **kwargs):
        seen_urls.append(req if isinstance(req, str) else req.full_url)
        return _mock_urlopen(release) if len(seen_urls) == 1 else mock_download_response

    mock_tar = MagicMock()
    mock_member = MagicMock()
    mock_member.name = "nu-0.109.0-x86_64-unknown-linux-gnu/nu"
    mock_tar.__enter__ = MagicMock(return_value=mock_tar)
    mock_tar.__exit__ = MagicMock(return_value=False)
    mock_tar.getmembers.return_value = [mock_member]

    install_dir = tmp_path / ".local" / "bin"
    install_dir.mkdir(parents=True)
    (install_dir / "nu").write_bytes(b"")

    patches = [
        patch("core.install.platform.machine", return_value="x86_64"),
        patch("core.install.urllib.request.urlopen", side_effect=_urlopen),
        patch("core.install.tarfile.open", return_value=mock_tar),
        patch("core.install.Path.home", return_value=tmp_path),
    ]
    if sys.platform != "win32":
        patches.append(patch("core.install.os.getuid", return_value=1000))
    else:
        patches.append(patch("core.install.os.getuid", create=True, return_value=1000))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        _install_nushell_from_github()

    assert seen_urls[0].endswith("/releases/tags/0.109.0"), seen_urls


def test_install_nushell_from_github_missing_asset():
    """Release has no matching asset -- should raise RuntimeError."""
    release = {
        "tag_name": "0.110.0",
        "assets": [
            {
                "name": "nu-0.110.0-aarch64-unknown-linux-gnu.tar.gz",
                "browser_download_url": "...",
            }
        ],
    }
    mock_response = _mock_urlopen(release)

    with (
        patch("core.install.platform.machine", return_value="x86_64"),
        patch("core.install.urllib.request.urlopen", return_value=mock_response),
        pytest.raises(RuntimeError, match="Could not find release asset"),
    ):
        _install_nushell_from_github()


def test_install_nushell_from_github_network_error():
    """Network failure on urlopen should propagate URLError."""
    with (
        patch("core.install.platform.machine", return_value="x86_64"),
        patch("core.install.urllib.request.urlopen", side_effect=URLError("connection refused")),
        pytest.raises(URLError),
    ):
        _install_nushell_from_github()


def test_install_nushell_from_github_unsupported_arch():
    """Unsupported architecture should raise RuntimeError."""
    with (
        patch("core.install.platform.machine", return_value="armv7l"),
        pytest.raises(RuntimeError, match="Unsupported architecture"),
    ):
        _install_nushell_from_github()


def test_install_nushell_from_github_no_nu_in_tar(tmp_path):
    """If the tar archive contains no 'nu' binary, should raise RuntimeError."""
    release = _make_github_release()
    mock_response = _mock_urlopen(release)

    mock_tar = MagicMock()
    mock_member = MagicMock()
    mock_member.name = "nu-0.110.0-x86_64-unknown-linux-gnu/README.md"
    mock_tar.__enter__ = MagicMock(return_value=mock_tar)
    mock_tar.__exit__ = MagicMock(return_value=False)
    mock_tar.getmembers.return_value = [mock_member]

    install_dir = tmp_path / ".local" / "bin"
    install_dir.mkdir(parents=True)

    mock_download_response = MagicMock()
    mock_download_response.read.return_value = b"fake-tar-data"
    mock_download_response.__enter__ = MagicMock(return_value=mock_download_response)
    mock_download_response.__exit__ = MagicMock(return_value=False)

    patches = [
        patch("core.install.platform.machine", return_value="x86_64"),
        patch(
            "core.install.urllib.request.urlopen",
            side_effect=[mock_response, mock_download_response],
        ),
        patch("core.install.tarfile.open", return_value=mock_tar),
        patch("core.install.Path.home", return_value=tmp_path),
    ]
    if sys.platform != "win32":
        patches.append(patch("core.install.os.getuid", return_value=1000))
    else:
        patches.append(patch("core.install.os.getuid", create=True, return_value=1000))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with pytest.raises(RuntimeError, match="nu binary not found in archive"):
            _install_nushell_from_github()


def test_install_nushell_from_github_arm64(tmp_path):
    """arm64 (macOS Apple Silicon) should map to aarch64-unknown-linux-gnu target."""
    _run_github_install_test(
        tmp_path,
        machine="arm64",
        target="aarch64-unknown-linux-gnu",
    )


def test_install_nushell_from_github_full_suffix_fallback(tmp_path):
    """Older releases with -full suffix should still work via fallback."""
    _run_github_install_test(
        tmp_path,
        tag="0.99.0",
        full_suffix=True,
    )


def test_install_tool_already_installed(capsys):
    """Should skip if tool is already available."""
    with patch("core.install.is_available", return_value=True):
        install_tool("atuin")

    assert "already installed" in capsys.readouterr().out


def test_install_tool_unknown():
    """Should raise ValueError for unknown tools."""
    with pytest.raises(ValueError, match="Unknown tool"):
        install_tool("doesnotexist")


@pytest.mark.parametrize("tool", ["atuin", "carapace"])
def test_install_tool_via_pkg_manager(tool):
    """Should use package manager command when available."""
    mock_run = MagicMock()
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value=f"/usr/bin/{tool}"),
    ):
        install_tool(tool)

    mock_run.assert_called_once()
    assert "brew" in mock_run.call_args[0][0]


def test_install_tool_fallback_to_mise():
    """When no pkg manager matches, should try mise fallback."""
    mock_run = MagicMock()

    def _avail(t):
        return t == "mise"

    with (
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.detect_package_manager", return_value="none"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value="/usr/bin/carapace"),
    ):
        install_tool("carapace")

    mock_run.assert_called_once()
    assert "mise" in mock_run.call_args[0][0]


def test_install_tool_fallback_to_cargo():
    """When no pkg manager or mise, should try cargo fallback for atuin."""
    mock_run = MagicMock()

    def _avail(t):
        return t == "cargo"

    with (
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.detect_package_manager", return_value="none"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value="/usr/bin/atuin"),
    ):
        install_tool("atuin")

    mock_run.assert_called_once()
    assert "cargo" in mock_run.call_args[0][0]


def test_resolve_prefers_mise_over_pkg_manager():
    """With mise available, resolution picks mise even when a pkg manager matches."""
    from core.install import resolve_install_command

    def _avail(t):
        return t == "mise"

    with (
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.detect_package_manager", return_value="winget"),
    ):
        cmd = resolve_install_command("fzf")

    assert cmd is not None and cmd[0] == "mise"


def test_resolve_eza_uses_homebrew_when_mise_is_available():
    """The GitHub mise backend has no macOS asset; Homebrew must win for eza."""
    from core.install import resolve_install_command

    with (
        patch("core.install.is_available", side_effect=lambda tool: tool == "mise"),
        patch("core.install.detect_package_manager", return_value="homebrew"),
    ):
        cmd = resolve_install_command("eza")

    assert cmd == ["brew", "install", "eza"]


def test_resolve_apt_command_drops_sudo_when_already_root():
    """A root container (no `sudo` binary) must not have sudo prepended."""
    from core.install import resolve_install_command

    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="apt"),
        patch("core.install.os.getuid", create=True, return_value=0),
    ):
        cmd = resolve_install_command("eza")

    assert cmd == ["apt-get", "install", "-y", "eza"]


def test_resolve_apt_command_keeps_sudo_for_non_root_user():
    from core.install import resolve_install_command

    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="apt"),
        patch("core.install.os.getuid", create=True, return_value=1000),
    ):
        cmd = resolve_install_command("eza")

    assert cmd == ["sudo", "apt-get", "install", "-y", "eza"]


def test_resolve_unknown_tool_returns_none():
    from core.install import resolve_install_command

    assert resolve_install_command("doesnotexist") is None


def test_resolve_cargo_fallback():
    """No mise, no matching pkg manager: resolution falls back to cargo."""
    from core.install import resolve_install_command

    with (
        patch("core.install.is_available", side_effect=lambda t: t == "cargo"),
        patch("core.install.detect_package_manager", return_value="none"),
    ):
        cmd = resolve_install_command("atuin")

    assert cmd is not None and cmd[0] == "cargo"


def test_install_tool_no_method():
    """Should raise RuntimeError when no install method is available."""
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="none"),
        pytest.raises(RuntimeError, match="No supported install method"),
    ):
        install_tool("atuin")


def test_install_tool_not_found_after_install(capsys):
    """Should log error when binary not on PATH after install."""
    mock_run = MagicMock()
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value=None),
    ):
        install_tool("atuin")

    assert "not found in PATH" in capsys.readouterr().err


def test_install_tool_clears_availability_cache_after_install():
    """Successful installs should invalidate cached tool lookups before deploy."""
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch(
            "core.install.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["brew"], returncode=0),
        ),
        patch("core.install.shutil.which", return_value="/usr/bin/atuin"),
        patch("core.install.clear_availability_cache") as mock_clear,
    ):
        install_tool("atuin")

    mock_clear.assert_called_once()


def test_install_all_tools_installs_each(capsys):
    """install_all_tools should attempt each tool."""
    from core.registry import INSTALLABLE_TOOLS

    mock_run = MagicMock()
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="homebrew"),
        patch("core.install.subprocess.run", mock_run),
        patch("core.install.shutil.which", return_value="/usr/bin/tool"),
    ):
        install_all_tools()

    assert mock_run.call_count == len(INSTALLABLE_TOOLS)


def test_install_all_tools_installs_mise_first():
    """mise lands before any tool that could resolve through it."""
    installed: list[str] = []
    with (
        patch("core.install.install_tool", side_effect=installed.append),
        patch("core.config.load_settings", return_value={}),
        patch("core.utils.get_project_dir", return_value=None),
        patch("core.config.is_integration_enabled", return_value=True),
    ):
        install_all_tools()

    assert installed[0] == "mise"


def test_install_all_tools_continues_on_error(capsys):
    """install_all_tools should continue if one tool fails."""
    with (
        patch("core.install.is_available", return_value=False),
        patch("core.install.detect_package_manager", return_value="none"),
    ):
        install_all_tools()

    output = capsys.readouterr().err
    assert "No supported install method" in output


def test_install_shells_for_setup_always_installs_nushell():
    """install_shells_for_setup always calls _install_nushell."""
    settings = {"shells": {"nushell": True, "xonsh": False}}
    with (
        patch("core.install._install_nushell") as mock_nu,
        patch("core.install._install_xonsh") as mock_xonsh,
    ):
        install_shells_for_setup(settings=settings)

    mock_nu.assert_called_once()
    mock_xonsh.assert_not_called()


def test_install_shells_for_setup_skips_xonsh_by_default():
    """xonsh is not installed when shells.xonsh is False and no override."""
    settings = {"shells": {"nushell": True, "xonsh": False}}
    with (
        patch("core.install._install_nushell"),
        patch("core.install._install_xonsh") as mock_xonsh,
    ):
        install_shells_for_setup(settings=settings)

    mock_xonsh.assert_not_called()


def test_install_shells_for_setup_xonsh_when_enabled():
    """xonsh is installed when shells.xonsh is True in settings."""
    settings = {"shells": {"nushell": True, "xonsh": True}}
    with (
        patch("core.install._install_nushell"),
        patch("core.install._install_xonsh") as mock_xonsh,
    ):
        install_shells_for_setup(settings=settings)

    mock_xonsh.assert_called_once()


def test_install_shells_for_setup_xonsh_override():
    """xonsh is installed when CLI flag overrides even if setting is False."""
    settings = {"shells": {"nushell": True, "xonsh": False}}
    with (
        patch("core.install._install_nushell"),
        patch("core.install._install_xonsh") as mock_xonsh,
    ):
        install_shells_for_setup(settings=settings, install_xonsh_override=True)

    mock_xonsh.assert_called_once()


def test_install_shells_for_setup_skips_env_scan_without_tty():
    """No tty -> skip the duplicate/stale PATH scan (nothing to prompt to fix)."""
    settings = {"shells": {"nushell": True, "xonsh": False}}
    with (
        patch("core.install._install_nushell"),
        patch("core.install._install_xonsh"),
        patch("core.duplicates.detect_duplicate_shells") as mock_dup,
        patch("core.duplicates.detect_stale_tools") as mock_stale,
        patch("core.install.sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        install_shells_for_setup(settings=settings)

    mock_dup.assert_not_called()
    mock_stale.assert_not_called()


def test_install_shells_for_setup_scans_env_when_interactive():
    """Interactive tty -> run the duplicate/stale scan."""
    settings = {"shells": {"nushell": True, "xonsh": False}}
    with (
        patch("core.install._install_nushell"),
        patch("core.install._install_xonsh"),
        patch("core.duplicates.detect_duplicate_shells", return_value=[]) as mock_dup,
        patch("core.duplicates.detect_stale_tools", return_value=[]) as mock_stale,
        patch("core.duplicates.prompt_cleanup_duplicates"),
        patch("core.duplicates.prompt_cleanup_stale_tools"),
        patch("core.install.sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = True
        install_shells_for_setup(settings=settings)

    mock_dup.assert_called_once()
    mock_stale.assert_called_once()


def test_path_hint_winget_points_to_winget_links():
    from core.install import _path_hint

    with patch("core.install.detect_package_manager", return_value="winget"):
        hint = _path_hint()
    assert "WinGet" in hint
    assert "Links" in hint


def test_path_hint_cargo_fallback():
    from pathlib import Path

    from core.install import _path_hint

    with (
        patch("core.install.detect_package_manager", return_value="apt"),
        patch("core.install.is_available", side_effect=lambda t: t == "cargo"),
    ):
        assert _path_hint() == str(Path.home() / ".cargo" / "bin")


def test_not_in_path_msg_names_the_dir():
    from core.install import _not_in_path_msg

    with patch("core.install.detect_package_manager", return_value="winget"):
        msg = _not_in_path_msg("zoxide")
    assert "not found in PATH" in msg
    assert "WinGet" in msg


def test_path_hint_homebrew_points_to_brew_bin():
    from core.install import _path_hint

    with patch("core.install.detect_package_manager", return_value="homebrew"):
        hint = _path_hint()
    assert "homebrew/bin" in hint or "/usr/local/bin" in hint


def test_run_capture_includes_stderr_in_error():
    """_run(capture=True) captures output and surfaces stderr in the RuntimeError."""

    def _subprocess_run(cmd, **kwargs):
        assert kwargs.get("capture_output") is True
        raise subprocess.CalledProcessError(1, cmd, stderr="boom: permission denied\n")

    with (
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.clear_availability_cache"),
        pytest.raises(RuntimeError, match="boom: permission denied"),
    ):
        _run(["do", "thing"], capture=True)


def test_run_default_does_not_capture_or_leak_stderr():
    """Default _run streams to the console; the error is just the generic exit message."""

    def _subprocess_run(cmd, **kwargs):
        assert not kwargs.get("capture_output")
        raise subprocess.CalledProcessError(1, cmd, stderr="unseen")

    with (
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.clear_availability_cache"),
        pytest.raises(RuntimeError, match="exit 1") as exc,
    ):
        _run(["do", "thing"])
    assert "unseen" not in str(exc.value)


def test_preview_install_all_lists_commands(tmp_path, capsys):
    """--dry-run preview prints the resolved command or the no-method note."""
    from core.install import preview_install_all

    def _avail(t):
        return t in ("mise", "fzf")  # fzf installed -> skip; others resolve via mise

    with (
        patch("core.utils.get_project_dir", return_value=tmp_path),
        patch("core.config.load_settings", return_value={}),
        patch("core.config.is_integration_enabled", return_value=True),
        patch("core.install.is_available", side_effect=_avail),
        patch("core.install.detect_package_manager", return_value="none"),
    ):
        preview_install_all()

    out = capsys.readouterr().out
    assert "fzf: already installed -- skip" in out
    assert "mise use -g" in out


def test_install_all_tools_skips_disabled_integrations():
    installed: list[str] = []
    with (
        patch("core.install.install_tool", side_effect=installed.append),
        patch("core.config.load_settings", return_value={}),
        patch("core.utils.get_project_dir", return_value=None),
        patch("core.config.is_integration_enabled", return_value=False),
    ):
        install_all_tools()

    from core.registry import INTEGRATION_TOOLS

    assert not [t for t in installed if t in INTEGRATION_TOOLS]
    assert installed  # optional tools still attempted


def test_run_timeout_raises_runtime_error():
    from core.install import _run

    with (
        patch(
            "core.install.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=120),
        ),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        _run(["x"])


def test_cargo_install_gets_a_build_length_timeout():
    """cargo compiles from source; the 120s default can never finish a cold build."""
    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with (
        patch("core.install.resolve_install_command", return_value=["cargo", "install", "eza"]),
        patch("core.install.subprocess.run", side_effect=_fake_run),
        patch("core.install.shutil.which", return_value=None),
        patch("core.install.is_available", return_value=False),
    ):
        install_tool("eza")

    assert seen["timeout"] > 120


def test_run_turns_oserror_into_runtime_error():
    """An OSError from the OS must not escape install_all_tools and abort setup."""
    from core.install import _run

    with (
        patch("core.install.subprocess.run", side_effect=OSError("exec format error")),
        pytest.raises(RuntimeError, match="Could not run"),
    ):
        _run(["definitely-not-a-real-tool"])


def test_winget_no_upgrade_available_is_not_an_upgrade_failure(capsys):
    """winget exits 43 when already current; that is not "may need admin rights"."""

    def _subprocess_run(cmd, **kwargs):
        if cmd == ["nu", "--version"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="0.114.1\n", stderr=""
            )
        raise subprocess.CalledProcessError(43, cmd)

    with (
        patch("core.install.is_available", return_value=True),
        patch("core.install.subprocess.run", side_effect=_subprocess_run),
        patch("core.install.detect_package_manager", return_value="winget"),
        patch("core.install.shutil.which", return_value="C:/nu.exe"),
    ):
        install_shell("nushell")

    out = capsys.readouterr().out
    assert "may need admin rights" not in out
    assert "up to date" in out
