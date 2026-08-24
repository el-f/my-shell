"""End-to-end tests that run real shell interpreters.

These tests deploy real config and execute it inside nu/xonsh.
Skipped automatically when the shell binary is not installed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HAS_NU = shutil.which("nu") is not None


def _xonsh_works() -> bool:
    """Check xonsh binary exists AND can actually start."""
    if not shutil.which("xonsh"):
        return False
    try:
        r = subprocess.run(
            ["xonsh", "-c", "print('ok')"], capture_output=True, text=True, timeout=30
        )
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


HAS_XONSH = _xonsh_works()

pytestmark = pytest.mark.e2e


def _run_nu(config_dir: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "nu",
            "--no-history",
            "--config",
            str(config_dir / "config.nu"),
            "--env-config",
            str(config_dir / "env.nu"),
            "-c",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_xonsh(xonshrc: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["xonsh", "--rc", str(xonshrc), "-c", command],
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestShellAvailability:
    """Sanity check: fail loudly if shells aren't available instead of silently skipping."""

    def test_nushell_is_available(self):
        assert HAS_NU, "nu binary not found -- e2e nushell tests would be silently skipped"

    def test_xonsh_is_available(self):
        assert HAS_XONSH, "xonsh not functional -- e2e xonsh tests would be silently skipped"


@pytest.mark.skipif(not HAS_NU, reason="nu not installed")
def test_nu_parses_config_from_a_path_with_an_apostrophe(tmp_path: Path, real_project_dir: Path):
    """Nushell has no escape inside '...', so paths must be emitted double-quoted."""
    import shutil as _shutil
    from unittest.mock import patch

    from core.merge import deploy

    project_dir = tmp_path / "O'Brien" / "my-shell"
    project_dir.parent.mkdir()
    _shutil.copytree(real_project_dir / "shells", project_dir / "shells")
    _shutil.copytree(real_project_dir / "config", project_dir / "config")

    config_dir = tmp_path / "nushell"
    with patch("core.merge.is_available", return_value=False):
        deploy("nushell", config_dir=config_dir, project_dir=project_dir)

    check = subprocess.run(
        ["nu", "--ide-check", "100", str(config_dir / "config.nu")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    errors = [
        line
        for line in check.stdout.splitlines()
        if '"severity":"Error"' in line and "Plugin registry file not set" not in line
    ]
    assert not errors, "\n".join(errors)


@pytest.mark.skipif(not HAS_NU, reason="nu not installed")
def test_nu_parses_config_with_a_deferred_integration(tmp_path: Path, real_project_dir: Path):
    """`defer = true` is a user-writable setting, so its output must still parse."""
    import shutil as _shutil
    from unittest.mock import patch

    from core.merge import deploy

    project_dir = tmp_path / "my-shell"
    _shutil.copytree(real_project_dir / "shells", project_dir / "shells")
    _shutil.copytree(real_project_dir / "config", project_dir / "config")
    (project_dir / "config" / "settings.local.toml").write_text(
        "[integrations]\nzoxide = { enabled = true, defer = true }\n", encoding="utf-8"
    )

    config_dir = tmp_path / "nushell"
    with patch("core.merge.is_available", return_value=False):
        deploy("nushell", config_dir=config_dir, project_dir=project_dir)

    rendered = (config_dir / "config.nu").read_text(encoding="utf-8")
    assert "defer ignored" in rendered, "deferred branch never rendered -- test is vacuous"

    check = subprocess.run(
        ["nu", "--ide-check", "100", str(config_dir / "config.nu")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    errors = [
        line
        for line in check.stdout.splitlines()
        if '"severity":"Error"' in line and "Plugin registry file not set" not in line
    ]
    assert not errors, "\n".join(errors)


@pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
def test_xonsh_deferred_integration_runs_on_first_prompt(tmp_path: Path, real_project_dir: Path):
    """A deferred integration that silently never loads looks identical to a working one."""
    import shutil as _shutil
    from unittest.mock import patch

    from core.config import _DEFAULT_SETTINGS
    from core.merge import deploy

    project_dir = tmp_path / "my-shell"
    _shutil.copytree(real_project_dir / "shells", project_dir / "shells")
    _shutil.copytree(real_project_dir / "config", project_dir / "config")
    # zoxide's real init no-ops without the binary, so give it an observable effect.
    (project_dir / "shells" / "xonsh" / "integrations" / "zoxide" / "init.xsh").write_text(
        "aliases['deferred_probe'] = lambda args: None\n", encoding="utf-8"
    )

    settings = {k: dict(v) for k, v in _DEFAULT_SETTINGS.items()}
    settings["integrations"]["zoxide"] = {"enabled": True, "defer": True}

    config_dir = tmp_path / "xonsh"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    with (
        patch("core.merge.is_available", return_value=False),
        patch("core.merge.get_home_dir", return_value=fake_home),
        patch("core.merge._find_xonsh_python", return_value=None),
        patch("core.merge.load_settings", return_value=settings),
    ):
        deploy("xonsh", config_dir=config_dir, project_dir=project_dir)

    xonshrc = fake_home / ".xonshrc"
    assert "_load_zoxide" in xonshrc.read_text(encoding="utf-8"), (
        "deferred branch never rendered -- test is vacuous"
    )

    result = _run_xonsh(
        xonshrc,
        "print('before', 'deferred_probe' in aliases); events.on_pre_prompt.fire(); "
        "print('after', 'deferred_probe' in aliases)",
    )
    assert result.returncode == 0, result.stderr
    assert "before False" in result.stdout, f"loaded too early:\n{result.stdout}"
    assert "after True" in result.stdout, f"deferred init never loaded:\n{result.stdout}"


@pytest.mark.skipif(not HAS_NU, reason="nu not installed")
class TestNushellE2E:
    def test_nu_config_loads_cleanly(self, nu_deployed):
        result = _run_nu(nu_deployed, "null")
        assert "nu::parser::" not in result.stderr, f"Parser error:\n{result.stderr}"
        assert "nu::shell::" not in result.stderr, f"Shell error:\n{result.stderr}"

    def test_nu_my_shell_dir_set(self, nu_deployed):
        result = _run_nu(nu_deployed, "$env.MY_SHELL_DIR")
        assert result.returncode == 0
        assert result.stdout.strip(), "MY_SHELL_DIR is empty"

    def test_nu_my_shell_version_set(self, nu_deployed):
        result = _run_nu(nu_deployed, "$env.MY_SHELL_VERSION")
        assert result.returncode == 0
        version = result.stdout.strip()
        assert version, "MY_SHELL_VERSION is empty"

    def test_nu_alias_g_available(self, nu_deployed):
        result = _run_nu(nu_deployed, "help aliases | where name == g | get 0.expansion")
        assert result.returncode == 0
        assert "git" in result.stdout.strip(), (
            f"Expected 'g' to expand to git, got: {result.stdout}"
        )

    def test_nu_sysinfo_runs(self, nu_deployed):
        result = _run_nu(nu_deployed, "sysinfo")
        assert result.returncode == 0, f"sysinfo failed:\n{result.stderr}"
        out = result.stdout.lower()
        assert "system" in out or "my-shell" in out, f"Unexpected sysinfo output:\n{result.stdout}"

    def test_nu_sysinfo_shows_real_arch(self, nu_deployed):
        """The OS line shows the real arch from $nu.os-info, not '(unknown)'.

        `sys host` has no arch column, so the old `sys host | get arch?` fell back
        to 'unknown' on every machine.
        """
        arch = _run_nu(nu_deployed, "$nu.os-info.arch").stdout.strip()
        assert arch, "could not read $nu.os-info.arch"
        result = _run_nu(nu_deployed, "sysinfo")
        assert f"({arch})" in result.stdout, f"arch {arch!r} missing from:\n{result.stdout}"

    def test_nu_three_layers_present(self, nu_deployed):
        config_content = (nu_deployed / "config.nu").read_text(encoding="utf-8")
        assert "LAYER 1" in config_content
        assert "LAYER 2" in config_content
        assert "LAYER 3" in config_content

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("trash", "Usage:"),
            ("port", "Usage:"),
        ],
    )
    def test_nu_command_prints_usage(self, nu_deployed, cmd, expected):
        """Commands with no args should print usage info without errors."""
        result = _run_nu(nu_deployed, cmd)
        assert result.returncode == 0, f"{cmd} failed:\n{result.stderr}"
        assert expected in result.stdout

    def test_nu_commands_lists_real_commands_not_tool_noise(self, nu_deployed):
        """`commands` derives its list from the command modules, not a hand-kept table.

        It must show the real my-shell commands and must NOT leak commands sourced
        by integration inits (zoxide/atuin/mise) or nushell std-lib.
        """
        (nu_deployed / "zoxide.nu").write_text("def __zoxide_z [] { }\n", encoding="utf-8")
        (nu_deployed / "atuin.nu").write_text("def _atuin_search_cmd [] { }\n", encoding="utf-8")
        (nu_deployed / "mise.nu").write_text("def mise_hook [] { }\n", encoding="utf-8")

        out = _run_nu(nu_deployed, "commands").stdout
        for name in ("fk", "sysinfo", "port", "clip", "commands"):
            assert name in out, f"'{name}' missing from commands output"
        for noise in ("__zoxide_z", "_atuin_search_cmd", "mise_hook", "banner"):
            assert noise not in out, f"tool/std command '{noise}' leaked into commands output"

    @pytest.mark.parametrize("cmd", ["fj", "fx", "fk", "clip", "y", "fh"])
    def test_nu_command_defined(self, nu_deployed, cmd):
        """Custom commands should be defined without nushell parser errors."""
        result = _run_nu(nu_deployed, f"which {cmd} | get 0.type")
        assert "nu::parser::" not in result.stderr, f"Parser error for '{cmd}':\n{result.stderr}"
        assert "custom" in result.stdout.strip(), (
            f"'{cmd}' not defined as custom command: {result.stdout}"
        )


@pytest.mark.skipif(not HAS_NU, reason="nu not installed")
class TestExtractPid:
    """The shared extract-pid helper -- fk (tasklist CSV / procs) and port (netstat)."""

    def _run(self, project_dir: Path, line: str, flag: str) -> str:
        utils = project_dir / "shells" / "nushell" / "utils.nu"
        # Single-quote the line so its embedded double-quotes (CSV) survive.
        cmd = f"use '{utils}' *; extract-pid '{line}' {flag}"
        result = subprocess.run(
            ["nu", "--no-config-file", "-c", cmd], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, f"extract-pid failed:\n{result.stderr}"
        return result.stdout.strip()

    def test_csv_tasklist_picks_pid_column_with_embedded_comma(self, real_project_dir):
        # fk on Windows feeds `tasklist /fo csv`; PID is column index 1, and the
        # Mem Usage field has a comma that must not split the row.
        line = '"chrome.exe","4321","Console","1","123,456 K"'
        assert self._run(real_project_dir, line, "--csv") == "4321"

    def test_windows_netstat_picks_last_numeric(self, real_project_dir):
        # port --kill on Windows feeds `netstat -ano`; PID is the last column.
        line = "  TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    1234"
        assert self._run(real_project_dir, line, "--windows") == "1234"

    def test_procs_tree_picks_first_numeric(self, real_project_dir):
        line = "|-- 4321 node   0.5  1.2  running"
        assert self._run(real_project_dir, line, "--procs") == "4321"

    def test_ps_aux_picks_pid_column_not_numeric_uid(self, real_project_dir):
        line = "99999 4321 0.1 python"
        assert self._run(real_project_dir, line, "") == "4321"

    def test_procnet_netstat_tlnp_picks_pid_before_slash(self, real_project_dir):
        # port --kill on Linux (no lsof) feeds `netstat -tlnp`; the last column is
        # PID/program, so the PID is the part before the slash.
        line = "tcp   0   0 0.0.0.0:8080   0.0.0.0:*   LISTEN   4321/python"
        assert self._run(real_project_dir, line, "--procnet") == "4321"

    def test_procnet_dash_not_owned_is_not_killable(self, real_project_dir):
        # netstat -tlnp without root shows "-" for another user's process.
        # extract-pid returns "-", which port --kill's guard must reject.
        line = "tcp   0   0 0.0.0.0:8080   0.0.0.0:*   LISTEN   -"
        utils = real_project_dir / "shells" / "nushell" / "utils.nu"
        cmd = (
            f"use '{utils}' *; "
            f"let pid = (extract-pid '{line}' --procnet); "
            f"print ($pid =~ '^[1-9]\\d*$')"
        )
        result = subprocess.run(
            ["nu", "--no-config-file", "-c", cmd], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "false"  # "-" is not a killable PID

    def test_is_valid_pid_guard(self, real_project_dir):
        """The shared is-valid-pid guard (used by fk + port + kill-process)."""
        utils = real_project_dir / "shells" / "nushell" / "utils.nu"
        cmd = (
            f"use '{utils}' *; "
            "[(is-valid-pid '1234') (is-valid-pid '0') (is-valid-pid '-') (is-valid-pid '')] | to nuon"
        )
        result = subprocess.run(
            ["nu", "--no-config-file", "-c", cmd], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[true, false, false, false]"


@pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
class TestXonshE2E:
    def test_xonsh_config_loads_cleanly(self, xonsh_deployed):
        result = _run_xonsh(xonsh_deployed, "print('ok')")
        assert "Traceback" not in result.stderr, f"Traceback in stderr:\n{result.stderr}"
        assert "ok" in result.stdout

    def test_xonsh_my_shell_dir_set(self, xonsh_deployed):
        result = _run_xonsh(xonsh_deployed, "print($MY_SHELL_DIR)")
        assert result.returncode == 0
        assert result.stdout.strip(), "MY_SHELL_DIR is empty"

    def test_xonsh_my_shell_version_set(self, xonsh_deployed):
        result = _run_xonsh(xonsh_deployed, "print($MY_SHELL_VERSION)")
        assert result.returncode == 0
        version = result.stdout.strip()
        assert version, "MY_SHELL_VERSION is empty"

    def test_xonsh_alias_gs_available(self, xonsh_deployed):
        result = _run_xonsh(xonsh_deployed, "print('gs' in aliases)")
        assert result.returncode == 0
        assert "True" in result.stdout

    def test_xonsh_carapace_init_loads_cleanly(self, real_project_dir):
        """Source the real carapace init.xsh in xonsh -- must not produce errors."""
        init_xsh = real_project_dir / "shells" / "xonsh" / "integrations" / "carapace" / "init.xsh"
        result = subprocess.run(
            ["xonsh", "-c", f"source '{init_xsh}'; print('ok')"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "SyntaxError" not in result.stderr, f"SyntaxError:\n{result.stderr}"
        assert "Traceback" not in result.stderr, f"Traceback:\n{result.stderr}"
        assert "ok" in result.stdout
