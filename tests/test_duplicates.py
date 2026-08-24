"""Tests for duplicate shell installation detection and cleanup."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from core.duplicates import (
    DuplicateReport,
    ShellInstallation,
    StaleToolReport,
    _find_standalone_paths,
    _get_mise_tool_path,
    _get_version_at_path,
    _is_protected_directory,
    _offer_directory_removal,
    _remove_installation,
    _uninstall_via_winget,
    detect_duplicate_shells,
    detect_stale_tools,
    find_all_installations,
    prompt_cleanup_duplicates,
    prompt_cleanup_stale_tools,
)


class TestIsProtectedDirectory:
    def test_windows_system_root(self):
        with patch("core.duplicates.is_windows", return_value=True):
            assert _is_protected_directory(Path(r"C:\Windows")) is True

    def test_windows_program_files(self):
        with patch("core.duplicates.is_windows", return_value=True):
            assert _is_protected_directory(Path(r"C:\Program Files")) is True

    def test_windows_program_files_x86(self):
        with patch("core.duplicates.is_windows", return_value=True):
            assert _is_protected_directory(Path(r"C:\Program Files (x86)")) is True

    def test_windows_non_protected(self, tmp_path):
        with patch("core.duplicates.is_windows", return_value=True):
            assert _is_protected_directory(tmp_path) is False

    def test_unix_usr_bin(self):
        with patch("core.duplicates.is_windows", return_value=False):
            assert _is_protected_directory(Path("/usr/bin")) is True

    def test_unix_bin(self):
        with patch("core.duplicates.is_windows", return_value=False):
            assert _is_protected_directory(Path("/bin")) is True

    def test_unix_usr_local_bin(self):
        with patch("core.duplicates.is_windows", return_value=False):
            assert _is_protected_directory(Path("/usr/local/bin")) is True

    @pytest.mark.parametrize(
        "path",
        ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/home/linuxbrew/.linuxbrew/bin"],
    )
    def test_homebrew_prefixes(self, path):
        with patch("core.duplicates.is_windows", return_value=False):
            assert _is_protected_directory(Path(path)) is True

    def test_unix_non_protected(self, tmp_path):
        with patch("core.duplicates.is_windows", return_value=False):
            assert _is_protected_directory(tmp_path) is False


class TestGetVersionAtPath:
    def test_nu_version(self):
        with patch(
            "core.duplicates.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="0.107.0\n", stderr=""
            ),
        ):
            assert _get_version_at_path(Path("/usr/bin/nu")) == "0.107.0"

    def test_xonsh_version_slash_format(self):
        with patch(
            "core.duplicates.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="xonsh/0.22.3\n", stderr=""
            ),
        ):
            assert _get_version_at_path(Path("/usr/bin/xonsh")) == "0.22.3"

    def test_timeout_returns_none(self):
        with patch(
            "core.duplicates.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="nu", timeout=10),
        ):
            assert _get_version_at_path(Path("/usr/bin/nu")) is None

    def test_file_not_found_returns_none(self):
        with patch(
            "core.duplicates.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _get_version_at_path(Path("/usr/bin/nu")) is None

    def test_nonzero_exit_returns_none(self):
        with patch(
            "core.duplicates.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error"
            ),
        ):
            assert _get_version_at_path(Path("/usr/bin/nu")) is None


class TestFindAllInstallations:
    def test_windows_where_exe(self, tmp_path):
        """On Windows, parses where.exe output."""
        nu_path1 = tmp_path / "loc1" / "nu.exe"
        nu_path2 = tmp_path / "loc2" / "nu.exe"
        nu_path1.parent.mkdir(parents=True)
        nu_path2.parent.mkdir(parents=True)
        nu_path1.write_bytes(b"")
        nu_path2.write_bytes(b"")

        where_output = f"{nu_path1}\n{nu_path2}\n"

        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch(
                "core.duplicates.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=where_output, stderr=""
                ),
            ),
            patch("core.duplicates._known_locations", return_value=[]),
        ):
            result = find_all_installations("nu")

        assert len(result) == 2

    def test_unix_path_walk(self, tmp_path):
        """On Unix, walks PATH directories."""
        bin1 = tmp_path / "bin1"
        bin2 = tmp_path / "bin2"
        bin1.mkdir()
        bin2.mkdir()
        (bin1 / "nu").write_bytes(b"")
        (bin2 / "nu").write_bytes(b"")

        with (
            patch("core.duplicates.is_windows", return_value=False),
            patch.dict(os.environ, {"PATH": f"{bin1}{os.pathsep}{bin2}"}),
            patch("core.duplicates._known_locations", return_value=[]),
        ):
            result = find_all_installations("nu")

        assert len(result) == 2

    def test_deduplication(self, tmp_path):
        """Same resolved path should only appear once."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "nu").write_bytes(b"")

        with (
            patch("core.duplicates.is_windows", return_value=False),
            patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{bin_dir}"}),
            patch("core.duplicates._known_locations", return_value=[]),
        ):
            result = find_all_installations("nu")

        assert len(result) == 1

    def test_known_location_probing(self, tmp_path):
        """Probes known locations in addition to PATH/where."""
        known_dir = tmp_path / "known"
        known_dir.mkdir()
        (known_dir / "nu").write_bytes(b"")

        with (
            patch("core.duplicates.is_windows", return_value=False),
            patch.dict(os.environ, {"PATH": ""}),
            patch("core.duplicates._known_locations", return_value=[known_dir]),
        ):
            result = find_all_installations("nu")

        assert len(result) == 1
        assert result[0] == known_dir / "nu"

    def test_where_exe_failure_graceful(self):
        """where.exe failing should not crash, just return empty from that source."""
        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch(
                "core.duplicates.subprocess.run",
                side_effect=FileNotFoundError,
            ),
            patch("core.duplicates._known_locations", return_value=[]),
        ):
            result = find_all_installations("nu")

        assert result == []

    def test_empty_when_nothing_found(self):
        """Returns empty list when binary not found anywhere."""
        with (
            patch("core.duplicates.is_windows", return_value=False),
            patch.dict(os.environ, {"PATH": "/nonexistent"}),
            patch("core.duplicates._known_locations", return_value=[]),
        ):
            result = find_all_installations("nu")

        assert result == []


class TestDetectDuplicateShells:
    def test_single_install_no_duplicates(self, tmp_path):
        """Single installation per shell -- no duplicates flagged."""
        nu_bin = tmp_path / "bin" / "nu"
        nu_bin.parent.mkdir(parents=True)
        nu_bin.write_bytes(b"")

        with (
            patch("core.duplicates.find_all_installations", return_value=[nu_bin]),
            patch("core.duplicates.shutil.which", return_value=str(nu_bin)),
            patch("core.duplicates._get_version_at_path", return_value="0.107.0"),
        ):
            reports = detect_duplicate_shells()

        # Both nu and xonsh are checked; at least nu should have a report
        nu_reports = [r for r in reports if r.binary_name == "nu"]
        assert len(nu_reports) == 1
        assert not nu_reports[0].has_duplicates

    def test_multiple_installs_marks_active(self, tmp_path):
        """Multiple installations -- active one is correctly marked."""
        path1 = tmp_path / "loc1" / "nu"
        path2 = tmp_path / "loc2" / "nu"
        path1.parent.mkdir(parents=True)
        path2.parent.mkdir(parents=True)
        path1.write_bytes(b"")
        path2.write_bytes(b"")

        def mock_find(binary):
            if binary == "nu":
                return [path1, path2]
            return []

        with (
            patch("core.duplicates.find_all_installations", side_effect=mock_find),
            patch("core.duplicates.shutil.which", return_value=str(path1)),
            patch("core.duplicates._get_version_at_path", return_value="0.107.0"),
        ):
            reports = detect_duplicate_shells()

        nu_reports = [r for r in reports if r.binary_name == "nu"]
        assert len(nu_reports) == 1
        assert nu_reports[0].has_duplicates
        active = [i for i in nu_reports[0].installations if i.is_active]
        assert len(active) == 1
        assert active[0].path == path1

    def test_no_installations_found(self):
        """When no installations found, no reports generated."""
        with (
            patch("core.duplicates.find_all_installations", return_value=[]),
        ):
            reports = detect_duplicate_shells()

        assert reports == []


class TestRemoveInstallation:
    def test_removes_binary(self, tmp_path):
        """Removes the binary file."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        binary = bin_dir / "nu"
        binary.write_bytes(b"")

        _remove_installation(binary, bin_dir)

        assert not binary.exists()

    def test_removes_empty_parent(self, tmp_path):
        """Removes empty bin directory after binary removal."""
        parent = tmp_path / "programs" / "nu"
        bin_dir = parent / "bin"
        bin_dir.mkdir(parents=True)
        binary = bin_dir / "nu"
        binary.write_bytes(b"")

        _remove_installation(binary, bin_dir)

        assert not bin_dir.exists()
        assert not parent.exists()

    def test_keeps_nonempty_parent(self, tmp_path):
        """Does not remove bin directory if other files remain."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        binary = bin_dir / "nu"
        binary.write_bytes(b"")
        (bin_dir / "other").write_bytes(b"")

        _remove_installation(binary, bin_dir)

        assert not binary.exists()
        assert bin_dir.exists()

    def test_handles_removal_error(self, tmp_path, capsys):
        """Logs error instead of raising when removal fails."""
        binary = tmp_path / "nu"
        # binary doesn't exist -- unlink will fail
        _remove_installation(binary, tmp_path)

        assert "Failed to remove" in capsys.readouterr().err


class TestPromptCleanupDuplicates:
    def test_no_duplicates_no_output(self, capsys):
        """No duplicates means no output at all."""
        report = DuplicateReport(
            binary_name="nu",
            display_name="Nushell",
            installations=[ShellInstallation(path=Path("/usr/bin/nu"), is_active=True)],
        )

        prompt_cleanup_duplicates([report])

        output = capsys.readouterr()
        assert output.out == ""

    def test_user_declines_no_removal(self, tmp_path):
        """User declining confirmation should not remove anything."""
        path1 = tmp_path / "loc1" / "nu"
        path2 = tmp_path / "loc2" / "nu"
        path1.parent.mkdir(parents=True)
        path2.parent.mkdir(parents=True)
        path1.write_bytes(b"")
        path2.write_bytes(b"")

        report = DuplicateReport(
            binary_name="nu",
            display_name="Nushell",
            installations=[
                ShellInstallation(path=path1, is_active=True),
                ShellInstallation(path=path2, is_active=False),
            ],
        )

        with (
            patch("core.duplicates.typer.confirm", return_value=False),
            patch("core.duplicates.sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = True
            prompt_cleanup_duplicates([report])

        assert path2.exists()

    def test_user_accepts_triggers_removal(self, tmp_path):
        """User accepting confirmation should remove the stale binary."""
        path1 = tmp_path / "loc1" / "nu"
        path2 = tmp_path / "loc2" / "nu"
        path1.parent.mkdir(parents=True)
        path2.parent.mkdir(parents=True)
        path1.write_bytes(b"")
        path2.write_bytes(b"")

        report = DuplicateReport(
            binary_name="nu",
            display_name="Nushell",
            installations=[
                ShellInstallation(path=path1, is_active=True),
                ShellInstallation(path=path2, is_active=False),
            ],
        )

        with (
            patch("core.duplicates.typer.confirm", return_value=True),
            patch("core.duplicates.sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = True
            prompt_cleanup_duplicates([report])

        assert not path2.exists()
        assert path1.exists()

    def test_skips_protected_directory(self, capsys):
        """Protected directories are skipped with a warning."""
        report = DuplicateReport(
            binary_name="nu",
            display_name="Nushell",
            installations=[
                ShellInstallation(path=Path("/usr/local/bin/nu"), is_active=True),
                ShellInstallation(path=Path("/usr/bin/nu"), is_active=False),
            ],
        )

        with (
            patch("core.duplicates._is_protected_directory", return_value=True),
            patch("core.duplicates.os.access", return_value=True),
        ):
            prompt_cleanup_duplicates([report])

        assert "protected" in capsys.readouterr().err.lower()

    def test_warns_no_write_access(self, capsys):
        """Warns when directory is not writable."""
        report = DuplicateReport(
            binary_name="nu",
            display_name="Nushell",
            installations=[
                ShellInstallation(path=Path("/usr/local/bin/nu"), is_active=True),
                ShellInstallation(path=Path("/opt/nu/bin/nu"), is_active=False),
            ],
        )

        with (
            patch("core.duplicates._is_protected_directory", return_value=False),
            patch("core.duplicates.os.access", return_value=False),
        ):
            prompt_cleanup_duplicates([report])

        stderr = capsys.readouterr().err.lower()
        assert "write access" in stderr or "admin" in stderr


class TestDoctorIntegration:
    def test_no_duplicates_empty_results(self):
        """Doctor check returns empty when no duplicates found."""
        from core.doctor import _check_duplicate_shells

        with patch(
            "core.duplicates.detect_duplicate_shells",
            return_value=[
                DuplicateReport(
                    binary_name="nu",
                    display_name="Nushell",
                    installations=[ShellInstallation(path=Path("/usr/bin/nu"), is_active=True)],
                )
            ],
        ):
            results = _check_duplicate_shells()

        assert results == []

    def test_duplicates_return_warn(self):
        """Doctor check returns warn when duplicates found."""
        from core.doctor import _check_duplicate_shells

        with patch(
            "core.duplicates.detect_duplicate_shells",
            return_value=[
                DuplicateReport(
                    binary_name="nu",
                    display_name="Nushell",
                    installations=[
                        ShellInstallation(path=Path("/usr/bin/nu"), is_active=True),
                        ShellInstallation(path=Path("/usr/local/bin/nu"), is_active=False),
                    ],
                )
            ],
        ):
            results = _check_duplicate_shells()

        assert len(results) == 1
        assert results[0].status == "warn"
        assert "Nushell" in results[0].name
        assert "my-shell setup" in results[0].fix


class TestGetMiseToolPath:
    def test_mise_manages_tool(self):
        """Returns path when mise manages the tool."""
        with (
            patch("core.duplicates.shutil.which", return_value="/usr/bin/mise"),
            patch(
                "core.duplicates.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="/home/user/.local/share/mise/installs/oh-my-posh/latest/bin/oh-my-posh\n",
                    stderr="",
                ),
            ),
        ):
            result = _get_mise_tool_path("oh-my-posh")

        assert result == "/home/user/.local/share/mise/installs/oh-my-posh/latest/bin/oh-my-posh"

    def test_mise_not_installed(self):
        """Returns None when mise is not installed."""
        with patch("core.duplicates.shutil.which", return_value=None):
            assert _get_mise_tool_path("oh-my-posh") is None

    def test_tool_not_in_mise(self):
        """Returns None when mise doesn't manage the tool."""
        with (
            patch("core.duplicates.shutil.which", return_value="/usr/bin/mise"),
            patch(
                "core.duplicates.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr=""
                ),
            ),
        ):
            assert _get_mise_tool_path("oh-my-posh") is None

    def test_timeout_returns_none(self):
        """Returns None on timeout."""
        with (
            patch("core.duplicates.shutil.which", return_value="/usr/bin/mise"),
            patch(
                "core.duplicates.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="mise", timeout=10),
            ),
        ):
            assert _get_mise_tool_path("oh-my-posh") is None


class TestFindStandalonePaths:
    def test_finds_standalone_dir(self, tmp_path):
        """Finds standalone installation directory."""
        standalone = tmp_path / "Programs" / "oh-my-posh"
        standalone.mkdir(parents=True)

        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}),
        ):
            result = _find_standalone_paths("oh-my-posh", ("Programs/oh-my-posh",))

        assert len(result) == 1
        assert result[0] == standalone

    def test_no_standalone_dir(self, tmp_path):
        """Returns empty when standalone dir doesn't exist."""
        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}),
        ):
            result = _find_standalone_paths("oh-my-posh", ("Programs/oh-my-posh",))

        assert result == []

    def test_not_windows(self):
        """Returns empty on non-Windows."""
        with patch("core.duplicates.is_windows", return_value=False):
            result = _find_standalone_paths("oh-my-posh", ("Programs/oh-my-posh",))

        assert result == []


class TestDetectStaleTools:
    def test_detects_stale_omp(self, tmp_path):
        """Detects oh-my-posh installed both via mise and standalone."""
        standalone = tmp_path / "Programs" / "oh-my-posh"
        standalone.mkdir(parents=True)

        with (
            patch(
                "core.duplicates._get_mise_tool_path",
                side_effect=lambda t: "/mise/oh-my-posh" if t == "oh-my-posh" else None,
            ),
            patch(
                "core.duplicates._find_standalone_paths",
                side_effect=lambda name, dirs: [standalone] if name == "oh-my-posh" else [],
            ),
        ):
            reports = detect_stale_tools()

        omp_reports = [r for r in reports if r.tool_name == "oh-my-posh"]
        assert len(omp_reports) == 1
        assert omp_reports[0].mise_path == "/mise/oh-my-posh"
        assert omp_reports[0].standalone_paths == [standalone]

    def test_no_stale_when_no_mise(self, tmp_path):
        """No reports when tool is not managed by mise."""
        with patch("core.duplicates._get_mise_tool_path", return_value=None):
            reports = detect_stale_tools()

        assert reports == []

    def test_no_stale_when_no_standalone(self):
        """No reports when no standalone installation exists."""
        with (
            patch("core.duplicates._get_mise_tool_path", return_value="/mise/oh-my-posh"),
            patch("core.duplicates._find_standalone_paths", return_value=[]),
        ):
            reports = detect_stale_tools()

        assert reports == []


class TestPromptCleanupStaleTools:
    def test_empty_reports_no_output(self, capsys):
        """No output for empty reports."""
        prompt_cleanup_stale_tools([])
        assert capsys.readouterr().out == ""

    def test_non_interactive_warns(self, capsys):
        """Non-interactive mode logs warning instead of prompting."""
        report = StaleToolReport(
            tool_name="oh-my-posh",
            mise_path="/mise/oh-my-posh",
            standalone_paths=[Path("/standalone/oh-my-posh")],
            winget_id="JanDeDobbeleer.OhMyPosh",
        )

        with patch("core.duplicates.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            prompt_cleanup_stale_tools([report])

        stderr = capsys.readouterr().err.lower()
        assert "non-interactive" in stderr

    def test_user_declines_no_uninstall(self, capsys):
        """User declining does not trigger uninstall."""
        report = StaleToolReport(
            tool_name="oh-my-posh",
            mise_path="/mise/oh-my-posh",
            standalone_paths=[Path("/standalone/oh-my-posh")],
            winget_id="JanDeDobbeleer.OhMyPosh",
        )

        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch("core.duplicates.typer.confirm", return_value=False),
            patch("core.duplicates.sys.stdin") as mock_stdin,
            patch("core.duplicates.subprocess.run") as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            prompt_cleanup_stale_tools([report])

        # winget uninstall should NOT have been called
        assert not any("uninstall" in str(c) for c in mock_run.call_args_list)

    def test_no_winget_id_warns(self, capsys):
        """Warns when no winget ID is available."""
        report = StaleToolReport(
            tool_name="some-tool",
            mise_path="/mise/some-tool",
            standalone_paths=[Path("/standalone/some-tool")],
            winget_id=None,
        )

        with patch("core.duplicates.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            prompt_cleanup_stale_tools([report])

        stderr = capsys.readouterr().err.lower()
        assert "no winget id" in stderr

    def test_not_windows_skips(self, capsys):
        """Non-Windows platforms skip winget uninstall silently."""
        report = StaleToolReport(
            tool_name="oh-my-posh",
            mise_path="/mise/oh-my-posh",
            standalone_paths=[Path("/standalone/oh-my-posh")],
            winget_id="JanDeDobbeleer.OhMyPosh",
        )

        with (
            patch("core.duplicates.is_windows", return_value=False),
            patch("core.duplicates.sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = True
            prompt_cleanup_stale_tools([report])

    def test_user_confirms_triggers_uninstall(self, capsys):
        """User confirming triggers _uninstall_via_winget."""
        report = StaleToolReport(
            tool_name="oh-my-posh",
            mise_path="/mise/oh-my-posh",
            standalone_paths=[Path("/standalone/oh-my-posh")],
            winget_id="JanDeDobbeleer.OhMyPosh",
        )

        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch("core.duplicates.typer.confirm", return_value=True),
            patch("core.duplicates.sys.stdin") as mock_stdin,
            patch("core.duplicates.subprocess.run") as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            prompt_cleanup_stale_tools([report])

        assert any("uninstall" in str(c) for c in mock_run.call_args_list)


class TestUninstallViaWinget:
    def test_success(self, capsys):
        """Successful uninstall logs success and returns True."""
        with patch("core.duplicates.subprocess.run"):
            result = _uninstall_via_winget("oh-my-posh", "JanDeDobbeleer.OhMyPosh")

        assert result is True
        assert "uninstalled" in capsys.readouterr().out.lower()

    def test_timeout(self, capsys):
        """Timeout logs error and returns False."""
        with patch(
            "core.duplicates.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="winget", timeout=120),
        ):
            result = _uninstall_via_winget("oh-my-posh", "JanDeDobbeleer.OhMyPosh")

        assert result is False
        assert "timed out" in capsys.readouterr().err.lower()

    def test_called_process_error(self, capsys):
        """CalledProcessError logs error with exit code and returns False."""
        with patch(
            "core.duplicates.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "winget"),
        ):
            result = _uninstall_via_winget("oh-my-posh", "JanDeDobbeleer.OhMyPosh")

        assert result is False
        assert "failed to uninstall" in capsys.readouterr().err.lower()


class TestOfferDirectoryRemoval:
    def test_user_confirms_removes_directory(self, tmp_path, capsys):
        """User confirming removes the directory."""
        target = tmp_path / "oh-my-posh"
        target.mkdir()
        (target / "some-file.txt").write_text("content")

        with patch("core.duplicates.typer.confirm", return_value=True):
            _offer_directory_removal("oh-my-posh", [target])

        assert not target.exists()
        assert "removed" in capsys.readouterr().out.lower()

    def test_user_declines_keeps_directory(self, tmp_path):
        """User declining keeps the directory."""
        target = tmp_path / "oh-my-posh"
        target.mkdir()

        with patch("core.duplicates.typer.confirm", return_value=False):
            _offer_directory_removal("oh-my-posh", [target])

        assert target.exists()

    def test_skips_protected_directory(self, capsys):
        """Protected directories are skipped."""
        with patch("core.duplicates._is_protected_directory", return_value=True):
            _offer_directory_removal("oh-my-posh", [Path("/usr/bin")])

        assert "protected" in capsys.readouterr().err.lower()

    def test_skips_no_write_access(self, tmp_path, capsys):
        """Directories without write access are skipped."""
        target = tmp_path / "oh-my-posh"
        target.mkdir()

        with (
            patch("core.duplicates._is_protected_directory", return_value=False),
            patch("core.duplicates.os.access", return_value=False),
        ):
            _offer_directory_removal("oh-my-posh", [target])

        assert "write access" in capsys.readouterr().err.lower()

    def test_oserror_logs_error(self, tmp_path, capsys):
        """OSError during removal logs error."""
        target = tmp_path / "oh-my-posh"
        target.mkdir()

        with (
            patch("core.duplicates.typer.confirm", return_value=True),
            patch("core.duplicates.shutil.rmtree", side_effect=OSError("denied")),
        ):
            _offer_directory_removal("oh-my-posh", [target])

        assert "failed to remove" in capsys.readouterr().err.lower()


class TestStaleToolsFallbackFlow:
    def test_winget_failure_triggers_directory_removal(self, tmp_path, capsys):
        """When winget fails, fallback offers directory removal."""
        target = tmp_path / "oh-my-posh"
        target.mkdir()
        (target / "bin").mkdir()

        report = StaleToolReport(
            tool_name="oh-my-posh",
            mise_path="/mise/oh-my-posh",
            standalone_paths=[target],
            winget_id="JanDeDobbeleer.OhMyPosh",
        )

        confirm_calls = iter([True, True])  # yes to winget, yes to rmtree

        with (
            patch("core.duplicates.is_windows", return_value=True),
            patch(
                "core.duplicates.typer.confirm", side_effect=lambda *a, **kw: next(confirm_calls)
            ),
            patch("core.duplicates.sys.stdin") as mock_stdin,
            patch(
                "core.duplicates.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "winget"),
            ),
        ):
            mock_stdin.isatty.return_value = True
            prompt_cleanup_stale_tools([report])

        assert not target.exists()


class TestDoctorVendorAutoloadConflicts:
    def test_no_vendor_dir(self):
        """No results when vendor/autoload doesn't exist."""
        from core.doctor import _check_vendor_autoload_conflicts

        with patch("core.doctor.get_config_dir", return_value=Path("/nonexistent")):
            results = _check_vendor_autoload_conflicts()

        assert results == []

    def test_conflicting_file_detected(self, tmp_path):
        """Detects vendor/autoload file that conflicts with managed init."""
        from core.doctor import _check_vendor_autoload_conflicts

        vendor_dir = tmp_path / "vendor" / "autoload"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "oh-my-posh.nu").write_text("# conflict\n", encoding="utf-8")

        with patch("core.doctor.get_config_dir", return_value=tmp_path):
            results = _check_vendor_autoload_conflicts()

        assert len(results) == 1
        assert results[0].status == "warn"
        assert "oh-my-posh.nu" in results[0].message
        assert "deploy" in results[0].fix

    def test_non_conflicting_file_ignored(self, tmp_path):
        """Non-conflicting vendor files are not flagged."""
        from core.doctor import _check_vendor_autoload_conflicts

        vendor_dir = tmp_path / "vendor" / "autoload"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "something-else.nu").write_text("# no conflict\n", encoding="utf-8")

        with patch("core.doctor.get_config_dir", return_value=tmp_path):
            results = _check_vendor_autoload_conflicts()

        assert results == []


class TestClassifyInstallSource:
    @pytest.mark.parametrize(
        ("path", "source"),
        [
            (r"C:\Users\U\AppData\Local\mise\shims\fzf.exe", "mise"),
            (r"C:\Users\U\AppData\Local\mise\installs\fzf\1.0\fzf.exe", "mise"),
            (r"C:\ProgramData\chocolatey\bin\fzf.exe", "choco"),
            (r"C:\Users\U\.cargo\bin\atuin.exe", "cargo"),
            (r"C:\Users\U\AppData\Local\Microsoft\WinGet\Packages\a.b_src\rg.exe", "winget"),
            (r"C:\Users\U\AppData\Local\Microsoft\WinGet\Links\task.exe", "winget"),
            (r"C:\Users\U\scoop\shims\jq.exe", "scoop"),
            (r"C:\Users\U\AppData\Roaming\Python\Python314\Scripts\uv.exe", "pip"),
            (r"C:\Users\U\AppData\Roaming\Python\Python314\uv.exe", "other"),
            (r"C:\Program Files\nodejs\taplo", "npm"),
            (r"C:\Users\U\AppData\Roaming\npm\taplo.cmd", "npm"),
            ("/opt/homebrew/bin/fzf", "homebrew"),
            ("/home/linuxbrew/.linuxbrew/Cellar/fzf/1.0/bin/fzf", "homebrew"),
            ("/usr/bin/fzf", "system"),
            ("/bin/jq", "system"),
            ("/sbin/init", "system"),
            (r"C:\Somewhere\Else\fzf.exe", "other"),
        ],
    )
    def test_classification(self, path, source):
        from core.duplicates import classify_install_source

        assert classify_install_source(Path(path)) == source


class TestDetectMultiSourceTools:
    def test_two_sources_reported(self):
        from core.duplicates import detect_multi_source_tools

        def _fake_find(binary):
            if binary == "fzf":
                return [
                    Path(r"C:\Users\U\AppData\Local\mise\shims\fzf.exe"),
                    Path(r"C:\ProgramData\chocolatey\bin\fzf.exe"),
                ]
            return []

        with patch("core.duplicates.find_all_installations", side_effect=_fake_find):
            reports = detect_multi_source_tools()

        assert len(reports) == 1
        assert reports[0].tool_name == "fzf"
        assert set(reports[0].sources) == {"mise", "choco"}

    def test_single_source_not_reported(self):
        from core.duplicates import detect_multi_source_tools

        def _fake_find(binary):
            if binary == "fzf":
                return [
                    Path(r"C:\Users\U\AppData\Local\mise\shims\fzf.exe"),
                    Path(r"C:\Users\U\AppData\Local\mise\installs\fzf\1.0\fzf.exe"),
                ]
            return []

        with patch("core.duplicates.find_all_installations", side_effect=_fake_find):
            reports = detect_multi_source_tools()

        assert reports == []

    def test_tool_absent_from_registry_scans_by_name(self):
        """A DETECT_TOOLS entry with no registry info falls back to the bare name."""
        from core.duplicates import detect_multi_source_tools

        seen: list[str] = []

        def _fake_find(binary):
            seen.append(binary)
            return []

        with (
            patch("core.registry.DETECT_TOOLS", ["unregistered-tool"]),
            patch("core.duplicates.find_all_installations", side_effect=_fake_find),
        ):
            reports = detect_multi_source_tools()

        assert reports == []
        assert seen == ["unregistered-tool"]


class TestToolSourcesDoctorCheck:
    def test_multi_source_warns(self):
        from core.doctor import _check_tool_sources
        from core.duplicates import MultiSourceReport

        report = MultiSourceReport(
            tool_name="fzf",
            sources={
                "mise": [Path(r"C:\mise\shims\fzf.exe")],
                "choco": [Path(r"C:\ProgramData\chocolatey\bin\fzf.exe")],
            },
        )
        with (
            patch("core.duplicates.detect_multi_source_tools", return_value=[report]),
            patch("shutil.which", return_value=r"C:\ProgramData\chocolatey\bin\fzf.exe"),
        ):
            results = _check_tool_sources()

        assert len(results) == 1
        assert results[0].status == "warn"
        assert "fzf" in results[0].name
        assert results[0].message.index("choco:") < results[0].message.index("mise:")
        assert "Keep the mise copy" in results[0].fix

    def test_multi_source_info_when_path_picks_mise(self):
        from core.doctor import _check_tool_sources
        from core.duplicates import MultiSourceReport

        report = MultiSourceReport(
            tool_name="fzf",
            sources={
                "mise": [Path(r"C:\mise\shims\fzf.exe")],
                "choco": [Path(r"C:\ProgramData\chocolatey\bin\fzf.exe")],
            },
        )
        with (
            patch("core.duplicates.detect_multi_source_tools", return_value=[report]),
            patch("shutil.which", return_value=r"C:\mise\shims\fzf.exe"),
        ):
            results = _check_tool_sources()

        assert len(results) == 1
        assert results[0].status == "info"
        assert "PATH picks the mise copy" in results[0].message

    def test_clean_returns_empty(self):
        from core.doctor import _check_tool_sources

        with patch("core.duplicates.detect_multi_source_tools", return_value=[]):
            results = _check_tool_sources()

        assert results == []

    def test_fix_text_without_mise_source(self):
        """When mise is not among the sources, the fix must not mention it."""
        from core.doctor import _check_tool_sources
        from core.duplicates import MultiSourceReport

        report = MultiSourceReport(
            tool_name="jq",
            sources={
                "winget": [Path(r"C:\WinGet\Links\jq.exe")],
                "choco": [Path(r"C:\ProgramData\chocolatey\bin\jq.exe")],
            },
        )
        with patch("core.duplicates.detect_multi_source_tools", return_value=[report]):
            results = _check_tool_sources()

        assert "Keep the choco copy" in results[0].fix


def test_find_standalone_paths_no_localappdata():
    with (
        patch("core.duplicates.is_windows", return_value=True),
        patch.dict(os.environ, {"LOCALAPPDATA": ""}, clear=False),
    ):
        assert _find_standalone_paths("oh-my-posh", ("Programs/oh-my-posh",)) == []
