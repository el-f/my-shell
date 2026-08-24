"""Zero-mock CLI end-to-end tests.

Every test calls `subprocess.run(["uv", "run", "my-shell", ...])` -- no Python
imports from core/, no mocks, no patches.  Runs on all CI platforms; tests skip
gracefully when a shell binary isn't available.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HAS_NU = shutil.which("nu") is not None
HAS_OMP = shutil.which("oh-my-posh") is not None
HAS_ATUIN = shutil.which("atuin") is not None


def _xonsh_works() -> bool:
    """Check xonsh binary exists AND can actually start."""
    if not shutil.which("xonsh"):
        return False
    try:
        r = subprocess.run(
            ["xonsh", "-c", "print('ok')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return r.returncode == 0 and "ok" in r.stdout
    except Exception:
        return False


HAS_XONSH = _xonsh_works()

pytestmark = pytest.mark.e2e


def merged_env(overrides: dict[str, str | None]) -> dict[str, str]:
    """Apply test overrides, treating None as an explicit request to unset a variable."""
    env = os.environ.copy()
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def test_merged_env_unsets_none_values(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/should/not/leak")

    assert "XDG_CONFIG_HOME" not in merged_env({"XDG_CONFIG_HOME": None})


def run_cli(*args: str, env_override: dict[str, str | None] | None = None, timeout: int = 60):
    """Run `uv run my-shell <args>` via subprocess, return CompletedProcess."""
    env = merged_env(env_override or {})
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MY_SHELL_SKIP_FONTS"] = "1"
    return subprocess.run(
        ["uv", "run", "my-shell", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def env_with_home(home: str | Path) -> dict[str, str | None]:
    """Return env dict with HOME overridden (sets USERPROFILE and APPDATA too for Windows).

    Also unsets XDG_CONFIG_HOME so CI environment variables don't leak into
    the deploy and cause config to land in an unexpected directory.  uv's tool
    directories deliberately follow the fake HOME as well: setup can install
    or upgrade xonsh, and an e2e test must never alter the developer's tools.
    """
    home_str = str(home)
    home_path = Path(home_str)
    env = {
        "HOME": home_str,
        "USERPROFILE": home_str,
        "XDG_CONFIG_HOME": None,
        "UV_TOOL_DIR": str(home_path / ".local" / "share" / "uv" / "tools"),
        "UV_TOOL_BIN_DIR": str(home_path / ".local" / "bin"),
        "UV_CACHE_DIR": str(home_path / ".cache" / "uv"),
    }
    if sys.platform == "win32":
        appdata = home_path / "AppData" / "Roaming"
        localappdata = home_path / "AppData" / "Local"
        env["APPDATA"] = str(appdata)
        env["LOCALAPPDATA"] = str(localappdata)
        drive, tail = os.path.splitdrive(home_str)
        if drive:
            env["HOMEDRIVE"] = drive
        if tail:
            env["HOMEPATH"] = tail
        env["UV_CACHE_DIR"] = str(localappdata / "uv-cache")
    return env


def _nushell_config_dir(home: Path) -> Path:
    """Expected nushell config dir, matching core/utils.py logic.

    env_with_home() unsets XDG_CONFIG_HOME, so the deploy always falls
    through to the platform default: ~/Library/Application Support on macOS,
    ~/AppData/Roaming on Windows, ~/.config on Linux.
    """
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "nushell"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "nushell"
    return home / ".config" / "nushell"


def _xonsh_config_dir(home: Path) -> Path:
    """Expected xonsh config dir, matching core/utils.py logic."""
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "xonsh"
    return home / ".config" / "xonsh"


class TestShellAvailability:
    """Sanity check: fail loudly if shells aren't available instead of silently skipping."""

    def test_nushell_is_available(self):
        assert HAS_NU, "nu binary not found -- e2e nushell tests would be silently skipped"

    def test_xonsh_is_available(self):
        assert HAS_XONSH, "xonsh not functional -- e2e xonsh tests would be silently skipped"

    @pytest.mark.slow
    def test_cargo_is_available_for_slow_plugin_tests(self):
        assert shutil.which("cargo"), (
            "cargo not found -- plugin e2e tests would be silently skipped"
        )


class TestDetect:
    def test_exits_zero(self):
        result = run_cli("detect")
        assert result.returncode == 0

    def test_output_contains_os(self):
        result = run_cli("detect")
        assert "OS:" in result.stdout
        output = result.stdout.lower()
        assert any(os_name in output for os_name in ("linux", "macos", "windows"))

    def test_output_contains_shell_info(self):
        result = run_cli("detect")
        assert "Current Shell:" in result.stdout

    def test_output_contains_tool_status(self):
        result = run_cli("detect")
        assert "Tool Status" in result.stdout

    def test_installed_tools_show_ok(self):
        result = run_cli("detect")
        assert "[ok]" in result.stdout


class TestRender:
    def test_render_nushell_exits_zero(self):
        result = run_cli("render", "--shell", "nushell")
        assert result.returncode == 0

    def test_render_nushell_creates_aliases(self):
        run_cli("render", "--shell", "nushell")
        aliases_path = Path("shells/nushell/aliases.nu")
        assert aliases_path.exists()
        content = aliases_path.read_text(encoding="utf-8")
        assert "alias g = git" in content

    def test_render_xonsh_exits_zero(self):
        result = run_cli("render", "--shell", "xonsh")
        assert result.returncode == 0

    def test_render_xonsh_creates_aliases(self):
        run_cli("render", "--shell", "xonsh")
        aliases_path = Path("shells/xonsh/aliases.xsh")
        assert aliases_path.exists()
        content = aliases_path.read_text(encoding="utf-8")
        assert "aliases['g']" in content

    def test_render_all_creates_both(self):
        result = run_cli("render", "--shell", "all")
        assert result.returncode == 0
        assert Path("shells/nushell/aliases.nu").exists()
        assert Path("shells/xonsh/aliases.xsh").exists()

    def test_render_all_mentions_alias_counts(self):
        result = run_cli("render", "--shell", "all")
        assert "aliases for nushell" in result.stdout
        assert "aliases for xonsh" in result.stdout


class TestDeploy:
    def test_deploy_nushell_exits_zero(self, clean_home):
        result = run_cli("deploy", "--shell", "nushell", env_override=env_with_home(clean_home))
        assert result.returncode == 0

    def test_deploy_nushell_creates_config_files(self, clean_home):
        run_cli("deploy", "--shell", "nushell", env_override=env_with_home(clean_home))
        nu_config = _nushell_config_dir(clean_home)
        assert (nu_config / "config.nu").exists()
        assert (nu_config / "env.nu").exists()
        assert (nu_config / "user-custom.nu").exists()
        assert (nu_config / "user-env.nu").exists()

    def test_deploy_nushell_config_content(self, clean_home):
        run_cli("deploy", "--shell", "nushell", env_override=env_with_home(clean_home))
        content = (_nushell_config_dir(clean_home) / "config.nu").read_text(encoding="utf-8")
        assert "Generated by my-shell" in content
        assert "LAYER 1" in content
        assert "LAYER 2" in content
        assert "LAYER 3" in content
        assert "MY_SHELL_DIR" in content

    def test_deploy_nushell_env_content(self, clean_home):
        run_cli("deploy", "--shell", "nushell", env_override=env_with_home(clean_home))
        content = (_nushell_config_dir(clean_home) / "env.nu").read_text(encoding="utf-8")
        assert "CARAPACE_BRIDGES" in content

    def test_deploy_xonsh_exits_zero(self, clean_home):
        result = run_cli("deploy", "--shell", "xonsh", env_override=env_with_home(clean_home))
        assert result.returncode == 0

    def test_deploy_xonsh_creates_xonshrc(self, clean_home):
        run_cli("deploy", "--shell", "xonsh", env_override=env_with_home(clean_home))
        xonshrc = clean_home / ".xonshrc"
        assert xonshrc.exists()
        content = xonshrc.read_text(encoding="utf-8")
        assert "LAYER 1" in content
        assert "LAYER 2" in content
        assert "LAYER 3" in content

    def test_deploy_xonsh_creates_user_custom(self, clean_home):
        run_cli("deploy", "--shell", "xonsh", env_override=env_with_home(clean_home))
        user_custom = _xonsh_config_dir(clean_home) / "user-custom.xsh"
        assert user_custom.exists()

    def test_deploy_all_creates_both(self, clean_home):
        result = run_cli("deploy", "--shell", "all", env_override=env_with_home(clean_home))
        assert result.returncode == 0
        assert (_nushell_config_dir(clean_home) / "config.nu").exists()
        assert (_nushell_config_dir(clean_home) / "env.nu").exists()
        assert (clean_home / ".xonshrc").exists()

    def test_deploy_preserves_user_custom(self, clean_home):
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)
        user_custom = _nushell_config_dir(clean_home) / "user-custom.nu"
        assert user_custom.exists()

        user_custom.write_text("# my personal config\n", encoding="utf-8")

        # --force bypasses the version skip
        run_cli("deploy", "--shell", "nushell", "--force", env_override=env)
        assert user_custom.read_text(encoding="utf-8") == "# my personal config\n"

    def test_deploy_nushell_creates_integration_inits(self, clean_home):
        """Deploy creates init files for available tools (zoxide, carapace, etc.)."""
        run_cli("deploy", "--shell", "nushell", env_override=env_with_home(clean_home))
        nu_config = _nushell_config_dir(clean_home)
        # At least carapace should be available when tools are installed
        init_files = list(nu_config.glob("*.nu"))
        init_names = [f.name for f in init_files]
        # config.nu, env.nu, user-custom.nu, user-env.nu are always created
        assert len(init_names) >= 4


class TestSetup:
    def test_setup_nushell_exits_zero(self, clean_home):
        result = run_cli(
            "setup", "--shell", "nushell", env_override=env_with_home(clean_home), timeout=300
        )
        assert result.returncode == 0

    def test_setup_nushell_output(self, clean_home):
        result = run_cli(
            "setup", "--shell", "nushell", env_override=env_with_home(clean_home), timeout=300
        )
        assert "Setting up nushell" in result.stdout

    def test_setup_xonsh_exits_zero(self, clean_home):
        result = run_cli(
            "setup", "--shell", "xonsh", env_override=env_with_home(clean_home), timeout=300
        )
        assert result.returncode == 0

    def test_setup_xonsh_output(self, clean_home):
        result = run_cli(
            "setup", "--shell", "xonsh", env_override=env_with_home(clean_home), timeout=300
        )
        assert "Setting up xonsh" in result.stdout

    def test_setup_all_exits_zero(self, clean_home):
        result = run_cli(
            "setup", "--shell", "all", env_override=env_with_home(clean_home), timeout=300
        )
        assert result.returncode == 0
        assert "Setting up nushell" in result.stdout
        assert "Setting up xonsh" in result.stdout

    def test_setup_creates_config_and_aliases(self, clean_home):
        """setup renders aliases AND deploys config files."""
        run_cli("setup", "--shell", "nushell", env_override=env_with_home(clean_home))
        assert (_nushell_config_dir(clean_home) / "config.nu").exists()
        assert Path("shells/nushell/aliases.nu").exists()


class TestInstall:
    def test_install_nushell_succeeds(self):
        result = run_cli("install", "--shell", "nushell", timeout=120)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(
            s in output
            for s in (
                "already installed",
                "installed successfully",
                "is up to date",
                "upgraded",
                "no upgrade method",
            )
        )

    def test_install_xonsh_succeeds(self):
        result = run_cli("install", "--shell", "xonsh", timeout=120)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(
            s in output
            for s in (
                "already installed",
                "installed successfully",
                "is up to date",
                "upgraded",
                "no upgrade method",
            )
        )

    def test_install_all_succeeds(self):
        result = run_cli("install", "--shell", "all", timeout=120)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(
            s in output
            for s in (
                "already installed",
                "installed successfully",
                "is up to date",
                "upgraded",
                "no upgrade method",
            )
        )


HAS_CARAPACE = shutil.which("carapace") is not None


class TestInstallTools:
    @pytest.mark.skipif(not HAS_CARAPACE, reason="carapace not in PATH")
    def test_install_tools_carapace_already_installed(self):
        result = run_cli("install-tools", "carapace")
        assert result.returncode == 0
        assert "already installed" in result.stdout.lower()

    def test_install_tools_all_exits_zero(self):
        result = run_cli("install-tools", timeout=300)
        assert result.returncode == 0

    def test_install_tools_unknown_tool_exits_one(self):
        result = run_cli("install-tools", "nonexistent_tool_xyz")
        assert result.returncode == 1


HAS_CARGO = shutil.which("cargo") is not None


@pytest.mark.skipif(not HAS_NU or not HAS_CARGO, reason="nu and cargo required")
class TestPlugins:
    def test_plugins_status_exits_zero(self):
        result = run_cli("plugins", "status")
        assert result.returncode == 0

    def test_plugins_status_output(self):
        result = run_cli("plugins", "status")
        assert "cargo:" in result.stdout
        assert "nu:" in result.stdout
        assert "nu_plugin_gstat" in result.stdout

    @pytest.mark.slow
    def test_plugins_install_exits_zero(self):
        result = run_cli("plugins", "install", timeout=600)
        assert result.returncode == 0

    def test_plugins_register_exits_zero(self):
        result = run_cli("plugins", "register")
        assert result.returncode == 0

    @pytest.mark.slow
    def test_plugins_setup_exits_zero(self):
        result = run_cli("plugins", "setup", timeout=600)
        assert result.returncode == 0


class TestVerbose:
    def test_verbose_detect(self):
        """--verbose detect still exits 0 (detect itself has no debug output)."""
        result = run_cli("--verbose", "detect")
        assert result.returncode == 0

    def test_verbose_render(self):
        result = run_cli("--verbose", "render", "--shell", "nushell")
        assert result.returncode == 0
        assert "[debug]" in result.stdout

    def test_verbose_deploy(self, clean_home):
        result = run_cli(
            "--verbose",
            "deploy",
            "--shell",
            "nushell",
            env_override=env_with_home(clean_home),
        )
        assert result.returncode == 0
        assert "[debug]" in result.stdout


class TestFullRoundtrip:
    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_roundtrip_loads_in_nu(self, clean_home):
        """Deploy nushell via CLI, then load config in real nu interpreter."""
        env = env_with_home(clean_home)
        result = run_cli("deploy", "--shell", "nushell", env_override=env)
        assert result.returncode == 0

        nu_config = _nushell_config_dir(clean_home)
        proc = subprocess.run(
            [
                "nu",
                "--no-history",
                "--config",
                str(nu_config / "config.nu"),
                "--env-config",
                str(nu_config / "env.nu"),
                "-c",
                "print 'ok'",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=merged_env(env),
        )
        assert "nu::parser::" not in proc.stderr, f"Parser error:\n{proc.stderr}"
        assert "nu::shell::" not in proc.stderr, f"Shell error:\n{proc.stderr}"
        assert "ok" in proc.stdout

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_runtime_path_survives_config(self, clean_home):
        """Deploy nushell, then assert PATH is still a usable list and an
        external command resolves through it.

        Guards the class where generated config semantics break while every
        shape assert stays green: mise 2026.7 assigning PATH as one raw string
        left `print 'ok'` (a builtin) passing while every external lookup died.
        """
        env = env_with_home(clean_home)
        result = run_cli("deploy", "--shell", "nushell", env_override=env)
        assert result.returncode == 0

        nu_config = _nushell_config_dir(clean_home)
        proc = subprocess.run(
            [
                "nu",
                "--no-history",
                "--config",
                str(nu_config / "config.nu"),
                "--env-config",
                str(nu_config / "env.nu"),
                "-c",
                "$env.PATH | describe | print; $env.PATH | length | print; "
                "which nu | length | print",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=merged_env(env),
        )
        assert "nu::shell::" not in proc.stderr, f"Shell error:\n{proc.stderr}"
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) >= 3, f"probe output missing:\n{proc.stdout}\n{proc.stderr}"
        describe, path_len, nu_found = lines[-3], lines[-2], lines[-1]
        assert describe.startswith("list<"), f"PATH degraded to {describe}"
        assert path_len.isdigit() and int(path_len) >= 3, f"PATH has {path_len} entries"
        assert nu_found.isdigit() and int(nu_found) >= 1, "external `nu` not resolvable"

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_deploy_tool_init_contract_against_real_tools(self, clean_home):
        """Every post_process pattern must match the real installed tools' init
        output, and both known mise PATH-bake formats must be stripped."""
        env = env_with_home(clean_home)
        result = run_cli("deploy", "--shell", "nushell", env_override=env)
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "post-process pattern not found" not in combined, combined

        if shutil.which("mise"):
            mise_nu = _nushell_config_dir(clean_home) / "mise.nu"
            content = mise_nu.read_text(encoding="utf-8")
            assert "set,PATH," not in content
            assert "$env.PATH = r#'" not in content
            assert "hooks.pre_prompt" not in content
            assert "str upcase" not in content

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_startup_sanity(self, clean_home):
        """Deploy nushell, run the prompt command, verify no ERROR segment.

        Runs the actual PROMPT_COMMAND (same code oh-my-posh executes on
        first prompt) and asserts "ERROR" is not in the output.  This catches:
        - CMD_DURATION_MS crash (sets LAST_EXIT_CODE=1 → ERROR segment)
        - Any config load failure that leaves LAST_EXIT_CODE non-zero
        - Parser errors in stderr
        """
        env = env_with_home(clean_home)
        result = run_cli("deploy", "--shell", "nushell", env_override=env)
        assert result.returncode == 0

        nu_config = _nushell_config_dir(clean_home)
        # Run the actual prompt command (same code oh-my-posh executes on first prompt)
        proc = subprocess.run(
            [
                "nu",
                "--no-history",
                "--config",
                str(nu_config / "config.nu"),
                "--env-config",
                str(nu_config / "env.nu"),
                "-c",
                "$env.CMD_DURATION_MS = 42; do $env.PROMPT_COMMAND | ansi strip",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=merged_env(env),
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        assert proc.returncode == 0, f"nu exited {proc.returncode}\nstderr: {stderr}"
        assert "nu::shell::" not in stderr, f"Shell error:\n{stderr}"
        assert "nu::parser::" not in stderr, f"Parser error:\n{stderr}"
        # The prompt must NOT contain an ERROR segment
        assert "ERROR" not in stdout, (
            f"Prompt contains ERROR segment (LAST_EXIT_CODE was non-zero at render time).\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_alias_g_expands_to_git(self, clean_home):
        """Deploy nushell, load in nu, verify 'g' alias expands to 'git'."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)

        nu_config = _nushell_config_dir(clean_home)
        proc = subprocess.run(
            [
                "nu",
                "--no-history",
                "--config",
                str(nu_config / "config.nu"),
                "--env-config",
                str(nu_config / "env.nu"),
                "-c",
                "help aliases | where name == g | get 0.expansion",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=merged_env(env),
        )
        assert "git" in proc.stdout, f"Expected 'g' to expand to git, got: {proc.stdout}"

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_sysinfo_command_defined(self, clean_home):
        """Deploy nushell, verify sysinfo is a custom command."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)

        nu_config = _nushell_config_dir(clean_home)
        proc = subprocess.run(
            [
                "nu",
                "--no-history",
                "--config",
                str(nu_config / "config.nu"),
                "--env-config",
                str(nu_config / "env.nu"),
                "-c",
                "which sysinfo | get 0.type",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=merged_env(env),
        )
        assert "custom" in proc.stdout.strip(), f"sysinfo not custom: {proc.stdout}"

    @pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
    def test_xonsh_roundtrip_loads_cleanly(self, clean_home):
        """Deploy xonsh via CLI, then load config in real xonsh."""
        env = env_with_home(clean_home)
        result = run_cli("deploy", "--shell", "xonsh", env_override=env)
        assert result.returncode == 0

        xonshrc = clean_home / ".xonshrc"
        proc = subprocess.run(
            ["xonsh", "--rc", str(xonshrc), "-c", "print('ok')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=merged_env(env),
        )
        assert "Traceback" not in proc.stderr, f"Traceback:\n{proc.stderr}"
        assert "ok" in proc.stdout

    @pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
    def test_xonsh_alias_gs_available(self, clean_home):
        """Deploy xonsh, load in xonsh, verify 'gs' alias exists."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "xonsh", env_override=env)

        xonshrc = clean_home / ".xonshrc"
        proc = subprocess.run(
            ["xonsh", "--rc", str(xonshrc), "-c", "print('gs' in aliases)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=merged_env(env),
        )
        assert "True" in proc.stdout, f"Expected 'gs' in aliases, got: {proc.stdout}"

    @pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
    def test_xonsh_xontrib_load_abbrevs(self, clean_home):
        """Deploy xonsh, verify xontrib abbrevs loads without traceback."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "xonsh", env_override=env)

        xonshrc = clean_home / ".xonshrc"
        proc = subprocess.run(
            ["xonsh", "--rc", str(xonshrc), "-c", "xontrib load abbrevs; print('ok')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=merged_env(env),
        )
        assert "Traceback" not in proc.stderr, f"Traceback:\n{proc.stderr}"
        assert "ok" in proc.stdout

    def test_setup_matches_render_plus_deploy(self, clean_home):
        """setup produces same config.nu structure as render + deploy separately.

        Both use separate HOMEs, so we normalise HOME-derived paths and the
        MY_SHELL_VERSION line (git timestamp can shift) before comparing.
        """
        import re

        def _normalise(text: str, home: str) -> str:
            # Replace both forward-slash and backslash variants of the home path
            text = text.replace(home.replace("\\", "/"), "<HOME>")
            text = text.replace(home, "<HOME>")
            text = re.sub(
                r"\$env\.MY_SHELL_VERSION = '[^']*'", "$env.MY_SHELL_VERSION = '<V>'", text
            )
            text = re.sub(r"\$env\.MY_SHELL_HASH = '[^']*'", "$env.MY_SHELL_HASH = '<H>'", text)
            return text

        # Run setup
        setup_home = clean_home / "setup"
        setup_home.mkdir()
        env_setup = env_with_home(setup_home)
        run_cli("setup", "--shell", "nushell", env_override=env_setup)
        setup_config = (_nushell_config_dir(setup_home) / "config.nu").read_text(encoding="utf-8")

        # Run render then deploy separately
        separate_home = clean_home / "separate"
        separate_home.mkdir()
        env_sep = env_with_home(separate_home)
        run_cli("render", "--shell", "nushell")
        run_cli("deploy", "--shell", "nushell", env_override=env_sep)
        separate_config = (_nushell_config_dir(separate_home) / "config.nu").read_text(
            encoding="utf-8"
        )

        assert _normalise(setup_config, str(setup_home)) == _normalise(
            separate_config, str(separate_home)
        )


class TestIntegrationInitFiles:
    def test_nushell_init_files_nonempty(self, clean_home):
        """Deploy nushell to clean HOME, verify tool init files exist and are non-empty."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)

        nu_config = _nushell_config_dir(clean_home)
        init_files = ["carapace.nu", "zoxide.nu", "oh-my-posh.nu"]
        found = []
        for filename in init_files:
            init_path = nu_config / filename
            if init_path.exists():
                content = init_path.read_text(encoding="utf-8")
                if content.strip():
                    found.append(filename)

        # At least carapace should be available when tools are installed
        assert len(found) >= 1, "No non-empty integration init files were generated"

    @pytest.mark.skipif(not HAS_OMP, reason="oh-my-posh not installed")
    def test_nushell_omp_cmd_duration_patched(self, clean_home):
        """Deployed oh-my-posh.nu must be either patched output or a managed stub."""
        import re

        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)

        omp_file = _nushell_config_dir(clean_home) / "oh-my-posh.nu"
        assert omp_file.exists(), "oh-my-posh.nu was not generated"
        content = omp_file.read_text(encoding="utf-8")
        assert len(content) > 0, "oh-my-posh.nu is empty"

        if "init failed during deploy" in content or "Not installed" in content:
            assert "Generated by my-shell" in content
            return

        # The null-safe accessor must be present
        assert "CMD_DURATION_MS?" in content, (
            "oh-my-posh.nu must use null-safe $env.CMD_DURATION_MS? accessor"
        )
        # Bare $env.CMD_DURATION_MS (without ?) must NOT be present
        code = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("#"))
        bare_matches = re.findall(r"\$env\.CMD_DURATION_MS(?!\?)", code)
        assert len(bare_matches) == 0, (
            f"Found {len(bare_matches)} bare $env.CMD_DURATION_MS references "
            f"(without null-safe ?): must all be patched"
        )
        # v30's match-form and the older if-form null-safe CMD_DURATION_MS differently; either is correct.
        assert 'default "0823") {' in content or "default 823 | into int" in content


class TestXonshUserCustomPreservation:
    def test_deploy_xonsh_preserves_user_custom(self, clean_home):
        """Deploy xonsh, write user content, redeploy -- content preserved."""
        env = env_with_home(clean_home)

        run_cli("deploy", "--shell", "xonsh", env_override=env)
        user_custom = _xonsh_config_dir(clean_home) / "user-custom.xsh"
        assert user_custom.exists()

        user_custom.write_text("# my xonsh config\n", encoding="utf-8")

        run_cli("deploy", "--shell", "xonsh", "--force", env_override=env)
        assert user_custom.read_text(encoding="utf-8") == "# my xonsh config\n"


class TestErrorHandling:
    def test_invalid_shell_argument(self):
        """my-shell setup --shell fish should exit non-zero with error message."""
        result = run_cli("setup", "--shell", "fish")
        assert result.returncode != 0

    def test_help_output(self):
        """my-shell --help should exit 0 and mention key subcommands."""
        result = run_cli("--help")
        assert result.returncode == 0
        assert "setup" in result.stdout
        assert "deploy" in result.stdout
        assert "render" in result.stdout


class TestValidate:
    def test_validate_exits_zero(self):
        """validate on real project config should pass (no hard errors)."""
        result = run_cli("validate")
        assert result.returncode == 0

    def test_validate_output_mentions_config(self):
        """validate output mentions config files or passes silently."""
        result = run_cli("--verbose", "validate")
        # verbose mode logs "Config validation passed" on success,
        # or warnings/errors referencing .toml files
        output = result.stdout.lower()
        assert "validation" in output or "config" in output or ".toml" in output


class TestDoctor:
    def test_doctor_exits_zero_or_one(self):
        """doctor should run without crashing (exit 0=healthy, 1=failures)."""
        result = run_cli("doctor")
        assert result.returncode in (0, 1)

    def test_doctor_output_contains_checks(self):
        """doctor output contains check result markers."""
        result = run_cli("doctor")
        output = result.stdout
        # Rich table uses these Unicode markers for check results
        assert any(marker in output for marker in ("✓", "✗", "⚠", "pass", "warn", "fail"))

    def test_doctor_output_mentions_summary(self):
        """doctor output ends with a summary line."""
        result = run_cli("doctor")
        assert "Summary" in result.stdout or "passed" in result.stdout

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_real_deploy_satisfies_doctor_prompt_contract(self, clean_home):
        env = env_with_home(clean_home)
        deployed = run_cli("deploy", "--shell", "nushell", env_override=env, timeout=300)
        assert deployed.returncode == 0, deployed.stderr

        report = run_cli("doctor", "--json", env_override=env, timeout=120)
        data = json.loads(report.stdout)
        relevant = [
            item
            for item in data["results"]
            if item["name"].startswith("Nushell ")
            or item["name"] in {"Managed theme file", "nushell runtime PATH"}
        ]
        assert relevant, "doctor no longer exposes the deployed Nushell contract checks"
        assert not [item for item in relevant if item["status"] == "fail"]


class TestAtuinIntegration:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="mise shims are not on PATH in the Windows job",
    )
    def test_atuin_is_available(self):
        """atuin must be reachable from PATH."""
        assert HAS_ATUIN, "atuin binary not found in PATH"

    @pytest.mark.skipif(not HAS_ATUIN, reason="atuin not installed")
    def test_doctor_reports_atuin_available(self):
        """doctor should report atuin as available when installed."""
        result = run_cli("doctor")
        lines = [line for line in result.stdout.splitlines() if "atuin" in line.lower()]
        assert lines, "doctor output does not mention atuin"
        assert any("not found" not in line.lower() for line in lines)


class TestVersion:
    def test_version_exits_zero(self):
        result = run_cli("version")
        assert result.returncode == 0

    def test_version_output_contains_version_string(self):
        result = run_cli("version")
        assert "my-shell" in result.stdout

    def test_version_shows_deployed_status(self):
        """version mentions deployment status for each shell."""
        result = run_cli("version")
        output = result.stdout
        assert "deployed" in output or "not deployed" in output


class TestConfig:
    def test_config_exits_zero(self):
        result = run_cli("config")
        assert result.returncode == 0

    def test_config_output_contains_settings(self):
        """config output contains known setting keys from settings.toml."""
        result = run_cli("config")
        output = result.stdout.lower()
        # settings.toml always has an oh-my-posh section
        assert "oh-my-posh" in output or "oh_my_posh" in output


class TestStatus:
    def test_status_before_deploy(self, clean_home):
        """status before any deploy should show not-deployed state."""
        result = run_cli("status", "--shell", "nushell", env_override=env_with_home(clean_home))
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "Not deployed yet" in combined

    def test_status_after_deploy(self, clean_home):
        """After deploy, status should show version info or up-to-date."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)
        result = run_cli("status", "--shell", "nushell", env_override=env)
        assert result.returncode == 0
        output = result.stdout
        assert "Up to date" in output or "Deployed version:" in output


class TestDryRun:
    def test_setup_dry_run_exits_zero(self, clean_home):
        result = run_cli(
            "setup", "--dry-run", "--shell", "nushell", env_override=env_with_home(clean_home)
        )
        assert result.returncode == 0

    def test_setup_dry_run_no_files_created(self, clean_home):
        """setup --dry-run should NOT create config files."""
        run_cli("setup", "--dry-run", "--shell", "nushell", env_override=env_with_home(clean_home))
        nu_config = _nushell_config_dir(clean_home)
        assert not (nu_config / "config.nu").exists()

    def test_deploy_dry_run_exits_zero(self, clean_home):
        result = run_cli(
            "deploy", "--dry-run", "--shell", "nushell", env_override=env_with_home(clean_home)
        )
        assert result.returncode == 0


class TestUninstallE2E:
    def test_uninstall_after_deploy(self, clean_home):
        """Deploy then uninstall should remove managed config files."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)

        nu_config = _nushell_config_dir(clean_home)
        assert (nu_config / "config.nu").exists()

        run_cli("uninstall", "--shell", "nushell", env_override=env)
        assert not (nu_config / "config.nu").exists()
        assert not (nu_config / "env.nu").exists()

    def test_uninstall_preserves_user_custom(self, clean_home):
        """Uninstall with default --keep-custom preserves user-custom.nu."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)

        user_custom = _nushell_config_dir(clean_home) / "user-custom.nu"
        user_custom.write_text("# my config\n", encoding="utf-8")

        run_cli("uninstall", "--shell", "nushell", env_override=env)
        assert user_custom.exists()
        assert user_custom.read_text(encoding="utf-8") == "# my config\n"


class TestRollbackE2E:
    def test_rollback_after_deploy(self, clean_home):
        """Deploy, change the config, re-deploy (backs up the change), rollback restores it."""
        env = env_with_home(clean_home)
        run_cli("deploy", "--shell", "nushell", env_override=env)
        nu_config = _nushell_config_dir(clean_home)
        config_path = nu_config / "config.nu"

        # Simulate a change so the backup differs from the next deploy (two deploys at
        # the same commit render identical config, and rollback skips a no-op restore).
        marked = config_path.read_text(encoding="utf-8") + "\n# ROLLBACK_MARKER\n"
        config_path.write_text(marked, encoding="utf-8")

        # Re-deploy --force: backs up the marked config, writes a fresh one (no marker).
        run_cli("deploy", "--shell", "nushell", "--force", env_override=env)
        assert "ROLLBACK_MARKER" not in config_path.read_text(encoding="utf-8")

        # Rollback (non-interactive) restores the marked backup.
        result = run_cli("rollback", "--shell", "nushell", "--yes", env_override=env)
        assert result.returncode == 0, result.stderr
        assert "Restoring backup:" in result.stdout
        assert "ROLLBACK_MARKER" in config_path.read_text(encoding="utf-8")


class TestSetupProfile:
    @staticmethod
    def _copy_real_project_files(tmp_project: Path) -> None:
        root = Path(__file__).resolve().parents[1]
        for directory in ("config", "shells"):
            shutil.copytree(root / directory, tmp_project / directory, dirs_exist_ok=True)

    def test_setup_with_profile_exits_zero(self, clean_home, tmp_project):
        self._copy_real_project_files(tmp_project)
        env = {**env_with_home(clean_home), "MY_SHELL_DIR": str(tmp_project)}
        result = run_cli(
            "setup",
            "--profile",
            "minimal",
            "--shell",
            "nushell",
            env_override=env,
        )
        assert result.returncode == 0

    def test_setup_with_profile_deploys_config(self, clean_home, tmp_project):
        """setup --profile minimal should still create config.nu."""
        self._copy_real_project_files(tmp_project)
        env = {**env_with_home(clean_home), "MY_SHELL_DIR": str(tmp_project)}
        run_cli(
            "setup",
            "--profile",
            "minimal",
            "--shell",
            "nushell",
            env_override=env,
        )
        nu_config = _nushell_config_dir(clean_home)
        assert (nu_config / "config.nu").exists()


class TestBenchmarkE2E:
    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_benchmark_nushell_exits_zero(self):
        result = run_cli("benchmark", "--shell", "nushell", timeout=120)
        assert result.returncode == 0

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_benchmark_output_contains_timing(self):
        """benchmark output should contain timing info (ms)."""
        result = run_cli("benchmark", "--shell", "nushell", timeout=120)
        assert "ms" in result.stdout.lower() or "mean" in result.stdout.lower()


class TestSetupInstallsShells:
    def test_setup_installs_nushell(self, clean_home):
        """setup output mentions nushell install/upgrade."""
        result = run_cli("setup", "--shell", "nushell", env_override=env_with_home(clean_home))
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(s in output for s in ("nushell", "nu", "up to date", "installed", "upgrade"))

    def test_setup_does_not_install_xonsh_by_default(self, clean_home):
        """Default setup does not trigger xonsh install."""
        result = run_cli("setup", "--shell", "nushell", env_override=env_with_home(clean_home))
        assert result.returncode == 0
        output = result.stdout.lower()
        assert "installing xonsh" not in output

    def test_setup_installs_xonsh_with_flag(self, clean_home):
        """--install-xonsh triggers xonsh install."""
        result = run_cli(
            "setup",
            "--shell",
            "all",
            "--install-xonsh",
            env_override=env_with_home(clean_home),
            timeout=120,
        )
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(s in output for s in ("xonsh", "up to date", "installed", "upgrade"))


class TestInstallScenarios:
    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_install_reports_version(self):
        """install nushell output contains a version number."""
        result = run_cli("install", "--shell", "nushell", timeout=120)
        assert result.returncode == 0
        import re

        assert re.search(r"\d+\.\d+\.\d+", result.stdout)

    @pytest.mark.skipif(not HAS_NU, reason="nu not installed")
    def test_nushell_install_idempotent(self):
        """Second install run reports no change (wording varies: no upgrade path on apt)."""
        run_cli("install", "--shell", "nushell", timeout=120)
        result = run_cli("install", "--shell", "nushell", timeout=120)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert "up to date" in output or "no upgrade method" in output

    @pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
    def test_xonsh_install_reports_version(self):
        """install xonsh output contains a version number."""
        result = run_cli("install", "--shell", "xonsh", timeout=120)
        assert result.returncode == 0
        import re

        assert re.search(r"\d+\.\d+\.\d+", result.stdout)

    @pytest.mark.skipif(not HAS_XONSH, reason="xonsh not installed")
    def test_xonsh_install_idempotent(self):
        """Second install run reports 'up to date'."""
        run_cli("install", "--shell", "xonsh", timeout=120)
        result = run_cli("install", "--shell", "xonsh", timeout=120)
        assert result.returncode == 0
        assert "up to date" in result.stdout.lower()


class TestCleanInstall:
    @pytest.mark.skipif(HAS_NU, reason="nu already installed -- run before shell install step")
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="winget installs don't update PATH in the current process",
    )
    def test_nushell_clean_install(self):
        """On a system without nu, install should report success."""
        result = run_cli("install", "--shell", "nushell", timeout=120)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(s in output for s in ("installed successfully", "is up to date", "upgraded"))

    @pytest.mark.skipif(
        HAS_XONSH, reason="xonsh already installed -- run before shell install step"
    )
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="uv tool installs don't update PATH in the current process on Windows",
    )
    def test_xonsh_clean_install(self):
        """On a system without xonsh, install should report success."""
        result = run_cli("install", "--shell", "xonsh", timeout=120)
        assert result.returncode == 0
        output = result.stdout.lower()
        assert any(s in output for s in ("installed successfully", "is up to date", "upgraded"))
