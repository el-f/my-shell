"""Health check module (chezmoi-style doctor command).

Runs health checks against the my-shell installation and reports
pass/warn/fail status with actionable fix suggestions.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import get_config_dir, is_integration_enabled, load_settings
from .merge import (
    _compute_project_hash,
    _get_deployed_hash,
    _python_candidates,
    foreign_owner_warnings,
)
from .mise import resolve_command
from .plugins import is_plugin_installed, load_plugin_list
from .registry import INTEGRATION_TOOLS, OPTIONAL_TOOLS, ToolInfo
from .utils import (
    get_home_dir,
    get_project_dir,
    is_available,
    is_windows,
    log_error,
    log_step,
)
from .validate import validate_all


@dataclass
class CheckResult:
    """Result of a single doctor check."""

    name: str
    status: str  # "pass", "warn", "fail"
    message: str
    fix: str | None = None


def _status_icons() -> dict[str, str]:
    """Use ASCII-safe status markers when stdout is not UTF-capable."""
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in encoding:
        return {
            "pass": "[green]\u2714[/green]",
            "info": "[blue]\u2139[/blue]",
            "warn": "[yellow]\u26a0[/yellow]",
            "fail": "[red]\u2718[/red]",
        }
    return {
        "pass": "[green]OK[/green]",
        "info": "[blue]i[/blue]",
        "warn": "[yellow]![/yellow]",
        "fail": "[red]X[/red]",
    }


def _read_text(path: Path) -> str | None:
    """Return UTF-8 text from *path*, or None when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _configured_shells(project_dir: Path | None = None) -> list[str]:
    """Return the shells currently enabled in settings."""
    settings = load_settings(project_dir)
    shells = ["nushell"]
    if settings.get("shells", {}).get("xonsh", False):
        shells.append("xonsh")
    return shells


# ── Individual checks ─────────────────────────────────────────────


def _check_shell_binary(shell_bin: str, display_name: str) -> CheckResult:
    """Check that a shell binary is installed and detect its version."""
    if not is_available(shell_bin):
        return CheckResult(
            name=f"{display_name} binary",
            status="fail",
            message=f"{shell_bin} not found in PATH",
            fix=(
                f"Install {display_name}: https://www.nushell.sh/book/installation.html"
                if shell_bin == "nu"
                else f"Install {display_name}: `pip install xonsh` or `uv tool install xonsh`"
            ),
        )

    try:
        result = subprocess.run(
            [shell_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = result.stdout.strip()
        return CheckResult(
            name=f"{display_name} binary",
            status="pass",
            message=f"{shell_bin} {version}",
        )
    except subprocess.TimeoutExpired, FileNotFoundError, OSError:
        return CheckResult(
            name=f"{display_name} binary",
            status="warn",
            message=f"{shell_bin} found but version detection failed",
        )


def _check_shell_binaries() -> list[CheckResult]:
    """Check that shell binaries (nu, xonsh) are installed.

    Always checks nushell. Only checks xonsh if enabled in settings.
    """
    results = [_check_shell_binary("nu", "Nushell")]

    settings = load_settings()
    xonsh_enabled = settings.get("shells", {}).get("xonsh", False)
    if xonsh_enabled:
        results.append(_check_shell_binary("xonsh", "xonsh"))
    elif is_available("xonsh"):
        # Launchable but unmanaged: the user gets a bare shell with none of their aliases.
        results.append(
            CheckResult(
                name="xonsh binary",
                status="warn",
                message="xonsh is installed but my-shell does not manage it",
                fix=(
                    "Set `xonsh = true` under `[shells]` in config/settings.local.toml, "
                    "then run `my-shell setup --shell xonsh`"
                ),
            )
        )
    else:
        results.append(
            CheckResult(
                name="xonsh binary",
                status="info",
                message="xonsh not enabled in settings",
            )
        )
    return results


def _install_hint(info: ToolInfo) -> str | None:
    """Return the same install command that `install-tools` would execute."""
    import shlex

    from .install import resolve_install_command

    cmd = resolve_install_command(info.name)
    return shlex.join(cmd) if cmd else None


def _check_integration_tools() -> list[CheckResult]:
    """Check availability of integration tools, respecting profile settings."""
    results: list[CheckResult] = []
    nu_config_dir = get_config_dir("nushell")
    settings = load_settings()
    for name, info in INTEGRATION_TOOLS.items():
        binary = info.binary

        # Skip check for integrations disabled by profile
        if not is_integration_enabled(settings, name):
            results.append(
                CheckResult(
                    name=f"Integration: {name}",
                    status="info",
                    message=f"{name} disabled in settings",
                )
            )
            continue

        if is_available(binary):
            # Also check if init file exists for deployed nushell config
            init_file = info.nushell_init_file
            init_path = (nu_config_dir / init_file) if init_file else None
            if init_path is not None and not init_path.exists():
                results.append(
                    CheckResult(
                        name=f"Integration: {name}",
                        status="warn",
                        message=f"{binary} available but init file missing ({init_file})",
                        fix="Run `my-shell setup` to generate init files",
                    )
                )
            elif init_path is not None and "init failed during deploy" in init_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                results.append(
                    CheckResult(
                        name=f"Integration: {name}",
                        status="warn",
                        message=f"{binary} available but its init is a failure stub ({init_file})",
                        fix="Run `my-shell setup --force` and check the deploy warnings",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name=f"Integration: {name}",
                        status="pass",
                        message=f"{binary} is available",
                    )
                )
        else:
            cmd = _install_hint(info)
            fix = (
                f"{cmd}  (or run `my-shell install-tools`)"
                if cmd
                else f"Run `my-shell install-tools` or install {name} manually"
            )
            results.append(
                CheckResult(
                    name=f"Integration: {name}",
                    status="warn",
                    message=f"{binary} not found in PATH",
                    fix=fix,
                )
            )

    for tool, info in OPTIONAL_TOOLS.items():
        if is_available(info.binary):
            results.append(
                CheckResult(
                    name=f"Optional tool: {tool}",
                    status="pass",
                    message=f"{tool} is available",
                )
            )
        else:
            cmd = _install_hint(info)
            results.append(
                CheckResult(
                    name=f"Optional tool: {tool}",
                    status="warn",
                    message=f"{tool} not found in PATH",
                    fix=cmd if cmd else f"Install {tool} for enhanced functionality",
                )
            )

    return results


def _check_config_valid(project_dir: Path) -> list[CheckResult]:
    """Validate that config files are valid TOML with correct schemas."""
    errors = validate_all(project_dir)

    if not errors:
        return [
            CheckResult(
                name="Config validation",
                status="pass",
                message="All config files are valid",
            )
        ]

    results: list[CheckResult] = []
    warnings = [e for e in errors if e.is_warning]
    hard_errors = [e for e in errors if not e.is_warning]

    if hard_errors:
        messages = "; ".join(str(e) for e in hard_errors)
        results.append(
            CheckResult(
                name="Config validation",
                status="fail",
                message=f"{len(hard_errors)} error(s): {messages}",
                fix="Fix the reported errors in your config/ TOML files",
            )
        )

    if warnings:
        messages = "; ".join(str(e) for e in warnings)
        results.append(
            CheckResult(
                name="Config validation (warnings)",
                status="warn",
                message=f"{len(warnings)} warning(s): {messages}",
            )
        )

    return results


def _check_deploy_hash(
    shell: str, project_dir: Path, current_hash: str | None = None
) -> CheckResult:
    """Check whether the deployed config hash matches the current project hash."""
    config_dir = get_config_dir(shell)
    deployed_hash = _get_deployed_hash(shell, config_dir)

    if deployed_hash is None:
        return CheckResult(
            name=f"{shell} deploy status",
            status="warn",
            message=f"{shell} has not been deployed yet",
            fix=f"Run `my-shell setup --shell {shell}` to deploy",
        )

    if current_hash is None:
        current_hash = _compute_project_hash(project_dir)
    if deployed_hash == current_hash:
        return CheckResult(
            name=f"{shell} deploy status",
            status="pass",
            message=f"Deployed config is up to date (hash: {current_hash})",
        )

    return CheckResult(
        name=f"{shell} deploy status",
        status="warn",
        message=f"Deployed config is stale (deployed: {deployed_hash}, current: {current_hash})",
        fix=f"Run `my-shell setup --shell {shell}` or `my-shell setup --force` to re-deploy",
    )


def _check_deploy_hashes(
    project_dir: Path,
    *,
    shells: list[str] | None = None,
) -> list[CheckResult]:
    """Check stale deploy detection for the selected shells."""
    target_shells = shells or ["nushell", "xonsh"]
    # Hash the project once, not once per shell.
    current_hash = _compute_project_hash(project_dir)
    return [_check_deploy_hash(shell, project_dir, current_hash) for shell in target_shells]


def _check_user_custom_file(shell: str) -> CheckResult:
    """Check that the user-custom file exists for a shell."""
    config_dir = get_config_dir(shell)

    if shell == "nushell":
        custom_file = config_dir / "user-custom.nu"
        display_path = str(custom_file)
    elif shell == "xonsh":
        custom_file = config_dir / "user-custom.xsh"
        display_path = str(custom_file)
    else:
        return CheckResult(
            name=f"{shell} user-custom",
            status="fail",
            message=f"Unknown shell: {shell}",
        )

    if custom_file.exists():
        return CheckResult(
            name=f"{shell} user-custom",
            status="pass",
            message=f"Found: {display_path}",
        )

    return CheckResult(
        name=f"{shell} user-custom",
        status="warn",
        message=f"Not found: {display_path}",
        fix=f"Run `my-shell setup --shell {shell}` to create the placeholder file",
    )


def _check_user_custom_files(*, shells: list[str] | None = None) -> list[CheckResult]:
    """Check user-custom files for the selected shells."""
    target_shells = shells or ["nushell", "xonsh"]
    return [_check_user_custom_file(shell) for shell in target_shells]


def _check_config_ownership(*, shells: list[str] | None = None) -> list[CheckResult]:
    """Report deploy targets another dotfile manager already owns."""
    return [
        CheckResult(
            name=f"{shell} config ownership",
            status="warn",
            message=warning,
            fix="Let one tool own the file: unmanage it there, or stop deploying this shell.",
        )
        for shell in (shells or ["nushell", "xonsh"])
        for warning in foreign_owner_warnings(shell, get_config_dir(shell))
    ]


def _check_nerd_font(project_dir: Path) -> CheckResult:
    """Check whether the configured Nerd Font is installed."""
    from .fonts import NERD_FONTS, is_headless_linux, is_nerd_font_installed

    settings = load_settings(project_dir)
    fonts_cfg = settings.get("fonts", {})
    font_key = fonts_cfg.get("nerd_font", "meslo")

    meta = NERD_FONTS.get(font_key)
    if not meta:
        return CheckResult(
            name="Nerd Font",
            status="warn",
            message=f"Unknown font '{font_key}' in settings",
            fix=f"Set fonts.nerd_font to one of: {', '.join(NERD_FONTS)}",
        )

    if is_nerd_font_installed(font_key):
        return CheckResult(
            name="Nerd Font",
            status="pass",
            message=f"{meta['display']} is installed",
        )

    # Deploy skips font install on headless Linux; report the same decision.
    if is_headless_linux():
        return CheckResult(
            name="Nerd Font",
            status="info",
            message=f"{meta['display']} not found (no graphical environment, install skipped)",
        )

    return CheckResult(
        name="Nerd Font",
        status="warn",
        message=f"{meta['display']} not found",
        fix="Run `my-shell install-fonts` to install it",
    )


def _check_cargo_rust() -> list[CheckResult]:
    """Check that Cargo and Rust are available for plugin compilation."""
    results: list[CheckResult] = []

    if is_available("cargo"):
        results.append(
            CheckResult(
                name="Cargo",
                status="pass",
                message="cargo is available",
            )
        )
    else:
        results.append(
            CheckResult(
                name="Cargo",
                status="info",
                message="cargo not found in PATH (only needed for optional nushell plugins)",
                fix="Install Rust: https://rustup.rs/",
            )
        )

    if is_available("rustc"):
        try:
            invocation = resolve_command(["rustc", "--version"], project_dir=get_project_dir())
            result = subprocess.run(
                invocation.args,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=invocation.cwd,
                env=invocation.env,
            )
            version = result.stdout.strip()
            results.append(
                CheckResult(
                    name="Rust compiler",
                    status="pass",
                    message=version,
                )
            )
        except subprocess.TimeoutExpired, FileNotFoundError, OSError:
            results.append(
                CheckResult(
                    name="Rust compiler",
                    status="warn",
                    message="rustc found but version detection failed",
                )
            )
    else:
        results.append(
            CheckResult(
                name="Rust compiler",
                status="info",
                message="rustc not found in PATH (only needed for optional nushell plugins)",
                fix="Install Rust: https://rustup.rs/",
            )
        )

    return results


def _check_python_environment(*, enabled: bool = True) -> CheckResult:
    """Check Python availability for xonsh."""
    if not enabled:
        return CheckResult(
            name="Python (for xonsh)",
            status="info",
            message="xonsh not enabled in settings",
        )

    for candidate in _python_candidates():
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return CheckResult(
                    name="Python (for xonsh)",
                    status="pass",
                    message=f"{candidate}: {version}",
                )
        except subprocess.TimeoutExpired, FileNotFoundError, OSError:
            continue

    return CheckResult(
        name="Python (for xonsh)",
        status="fail",
        message="No Python interpreter found (python3/python)",
        fix="Install Python 3.10+ for xonsh support",
    )


def _check_path_sanity() -> list[CheckResult]:
    """Check that important directories are present in PATH."""
    results: list[CheckResult] = []
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    path_dirs_lower = [p.lower() for p in path_dirs]

    # Check cargo bin directory
    cargo_bin = str(get_home_dir() / ".cargo" / "bin")
    cargo_bin_lower = cargo_bin.lower()
    found = (
        any(cargo_bin_lower == p for p in path_dirs_lower)
        if is_windows()
        else cargo_bin in path_dirs
    )
    if found:
        results.append(
            CheckResult(
                name="PATH: cargo bin",
                status="pass",
                message=f"{cargo_bin} is in PATH",
            )
        )
    else:
        results.append(
            CheckResult(
                name="PATH: cargo bin",
                status="warn",
                message=f"{cargo_bin} is not in PATH",
                fix=f"Add {cargo_bin} to your PATH for nushell plugins",
            )
        )

    # Check common directories based on platform
    if is_windows():
        common_dirs = [
            (str(get_home_dir() / ".local" / "bin"), "~/.local/bin (uv/pipx tools)"),
            (
                str(get_home_dir() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links"),
                "WinGet links",
            ),
        ]
    else:
        common_dirs = [
            (str(get_home_dir() / ".local" / "bin"), "~/.local/bin (user binaries)"),
            ("/usr/local/bin", "/usr/local/bin (system packages)"),
        ]

    for dir_path, description in common_dirs:
        dir_lower = dir_path.lower()
        found = (
            any(dir_lower == p for p in path_dirs_lower) if is_windows() else dir_path in path_dirs
        )
        if found:
            results.append(
                CheckResult(
                    name=f"PATH: {description}",
                    status="pass",
                    message=f"{dir_path} is in PATH",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"PATH: {description}",
                    status="warn",
                    message=f"{dir_path} is not in PATH",
                    fix=f"Consider adding {dir_path} to your PATH",
                )
            )

    return results


def _check_duplicate_shells() -> list[CheckResult]:
    """Check for duplicate shell installations."""
    from .duplicates import detect_duplicate_shells

    reports = detect_duplicate_shells()
    results: list[CheckResult] = []

    for report in reports:
        if report.has_duplicates:
            paths = ", ".join(str(inst.path) for inst in report.installations)
            results.append(
                CheckResult(
                    name=f"Duplicate {report.display_name} installations",
                    status="warn",
                    message=f"Found {len(report.installations)} installations: {paths}",
                    fix="Run `my-shell setup` to review and remove stale installation(s)",
                )
            )

    return results


def _check_tool_sources() -> list[CheckResult]:
    """Warn when a tool is installed by more than one package manager."""
    import shutil

    from .duplicates import classify_install_source, detect_multi_source_tools

    results: list[CheckResult] = []
    for report in detect_multi_source_tools():
        listing = "; ".join(
            f"{src}: {', '.join(str(p) for p in paths)}"
            for src, paths in sorted(report.sources.items())
        )
        keep = "mise" if "mise" in report.sources else sorted(report.sources)[0]
        active = shutil.which(report.tool_name)
        # A shadowed extra copy is harmless as long as PATH picks the mise one.
        mise_wins = (
            "mise" in report.sources
            and active is not None
            and classify_install_source(Path(active)) == "mise"
        )
        results.append(
            CheckResult(
                name=f"Multiple install sources: {report.tool_name}",
                status="info" if mise_wins else "warn",
                message=f"Installed by {len(report.sources)} package managers -- {listing}"
                + (" (PATH picks the mise copy)" if mise_wins else ""),
                fix=f"Keep the {keep} copy and uninstall the rest",
            )
        )
    return results


def _check_vendor_autoload_conflicts() -> list[CheckResult]:
    """Check for vendor/autoload files that conflict with my-shell managed integrations."""
    config_dir = get_config_dir("nushell")
    vendor_dir = config_dir / "vendor" / "autoload"

    if not vendor_dir.is_dir():
        return []

    # Managed integration init files that would conflict with vendor/autoload copies
    managed_files = {
        info.nushell_init_file for info in INTEGRATION_TOOLS.values() if info.nushell_init_file
    }

    results: list[CheckResult] = []
    for nu_file in vendor_dir.glob("*.nu"):
        if nu_file.name in managed_files:
            results.append(
                CheckResult(
                    name=f"Vendor autoload conflict: {nu_file.name}",
                    status="warn",
                    message=f"{nu_file} shadows my-shell managed init",
                    fix="Run `my-shell deploy` to fix (removes conflicting vendor file)",
                )
            )

    return results


def _check_project_mise_backends(project_dir: Path) -> list[CheckResult]:
    """Warn when the project still uses deprecated mise backend syntaxes."""
    mise_toml = project_dir / "mise.toml"
    content = _read_text(mise_toml)
    if content is None:
        return [
            CheckResult(
                name="Project mise config",
                status="warn",
                message=f"Could not read {mise_toml}",
                fix="Make sure the project mise.toml exists and is readable",
            )
        ]

    deprecated = sorted(set(re.findall(r'"(ubi:[^"]+)"', content)))
    if deprecated:
        return [
            CheckResult(
                name="Project mise backends",
                status="warn",
                message=f"Deprecated backend(s): {', '.join(deprecated)}",
                fix="Replace `ubi:` backends with `github:` or another supported backend",
            )
        ]

    return [
        CheckResult(
            name="Project mise backends",
            status="pass",
            message="No deprecated `ubi:` backends found in mise.toml",
        )
    ]


def _check_nushell_prompt_contract(
    project_dir: Path, *, config_dir: Path | None = None
) -> list[CheckResult]:
    """Validate prompt-related invariants for deployed Nushell config."""
    results: list[CheckResult] = []
    settings = load_settings(project_dir)
    config_dir = config_dir or get_config_dir("nushell")

    if is_integration_enabled(settings, "mise"):
        mise_init = config_dir / "mise.nu"
        mise_content = _read_text(mise_init)
        if mise_content is None:
            results.append(
                CheckResult(
                    name="Nushell mise hook",
                    status="warn",
                    message=f"Could not read {mise_init}",
                    fix="Run `my-shell setup --shell nushell --force`",
                )
            )
        elif "hooks.pre_prompt" in mise_content:
            results.append(
                CheckResult(
                    name="Nushell mise hook",
                    status="fail",
                    message="mise is still wired into hooks.pre_prompt",
                    fix="Re-deploy Nushell so mise only refreshes on directory changes",
                )
            )
        elif "hooks.env_change.PWD" not in mise_content:
            results.append(
                CheckResult(
                    name="Nushell mise hook",
                    status="warn",
                    message="mise PWD change hook is missing from mise.nu",
                    fix="Run `my-shell setup --shell nushell --force`",
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Nushell mise hook",
                    status="pass",
                    message="mise only refreshes on directory changes",
                )
            )

        config_nu = config_dir / "config.nu"
        config_content = _read_text(config_nu)
        if config_content is None:
            results.append(
                CheckResult(
                    name="Nushell mise trust",
                    status="warn",
                    message=f"Could not read {config_nu}",
                    fix="Run `my-shell setup --shell nushell --force`",
                )
            )
        elif "MISE_TRUSTED_CONFIG_PATHS" not in config_content:
            results.append(
                CheckResult(
                    name="Nushell mise trust",
                    status="fail",
                    message="config.nu does not export MISE_TRUSTED_CONFIG_PATHS",
                    fix="Re-deploy Nushell so my-shell can trust its own repo config",
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Nushell mise trust",
                    status="pass",
                    message="config.nu exports MISE_TRUSTED_CONFIG_PATHS",
                )
            )

    if is_integration_enabled(settings, "oh-my-posh"):
        theme_name = settings["oh-my-posh"]["theme"]
        theme_path = (
            project_dir / "shells" / "shared" / "oh-my-posh" / "themes" / f"{theme_name}.omp.json"
        )
        theme_path_nu = str(theme_path).replace("\\", "/")

        omp_init = config_dir / "oh-my-posh.nu"
        omp_content = _read_text(omp_init)
        if omp_content is None:
            results.append(
                CheckResult(
                    name="Nushell prompt theme",
                    status="warn",
                    message=f"Could not read {omp_init}",
                    fix="Run `my-shell setup --shell nushell --force`",
                )
            )
        elif "Not installed" in omp_content or "init failed during deploy" in omp_content:
            results.append(
                CheckResult(
                    name="Nushell prompt theme",
                    status="warn",
                    message="oh-my-posh integration is a managed stub",
                    fix="Install oh-my-posh and re-run `my-shell setup --shell nushell --force`",
                )
            )
        elif theme_path.exists() and theme_path_nu not in omp_content:
            results.append(
                CheckResult(
                    name="Nushell prompt theme",
                    status="fail",
                    message="oh-my-posh.nu is not pinned to the local managed theme file",
                    fix="Re-deploy Nushell so the generated prompt uses the project theme file",
                )
            )
        else:
            target = (
                f"local theme {theme_path_nu}"
                if theme_path.exists()
                else f"built-in theme {theme_name}"
            )
            results.append(
                CheckResult(
                    name="Nushell prompt theme",
                    status="pass",
                    message=f"oh-my-posh init uses {target}",
                )
            )
        if omp_content is not None:
            if "--no-status" in omp_content:
                results.append(
                    CheckResult(
                        name="Nushell prompt status",
                        status="fail",
                        message="oh-my-posh.nu still suppresses the status segment on the initial prompt",
                        fix="Re-run `my-shell setup --shell nushell --force` to regenerate the prompt wrapper",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name="Nushell prompt status",
                        status="pass",
                        message="oh-my-posh init keeps the status segment visible",
                    )
                )

        if theme_path.exists():
            theme_content = _read_text(theme_path)
            if theme_content is None:
                results.append(
                    CheckResult(
                        name="Managed theme file",
                        status="warn",
                        message=f"Could not read {theme_path}",
                        fix="Restore the managed theme file under shells/shared/oh-my-posh/themes/",
                    )
                )
            elif "CONFIG URL FETCH FAILED" in theme_content:
                results.append(
                    CheckResult(
                        name="Managed theme file",
                        status="fail",
                        message="Managed theme still contains the broken CONFIG URL FETCH FAILED segment",
                        fix="Remove the broken status segment from the local theme file",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name="Managed theme file",
                        status="pass",
                        message=f"{theme_path.name} does not contain the broken status segment",
                    )
                )

    return results


def _rate_startup_ms(ms: float) -> str:
    return "pass" if ms < 300 else "warn" if ms <= 500 else "fail"


def _check_startup_time(shell: str) -> CheckResult:
    """Time the shell's startup and rate it: pass <300ms, warn 300-500, fail >500.

    A shell that is slow before any config is loaded (xonsh needs ~2 s to import
    itself on Windows) is rated on what my-shell adds instead.
    """
    from .benchmark import _benchmark_basic, _benchmark_empty_config, _shell_binary

    binary = _shell_binary(shell)
    if binary is None:
        return CheckResult(
            name=f"{shell} startup time",
            status="info",
            message=f"{shell} not installed -- skipped",
        )

    try:
        result = _benchmark_basic(shell, binary, runs=3)
    except Exception as e:
        # The shell is installed but won't run its deployed config -- that is a
        # broken shell, not a missing measurement.
        return CheckResult(
            name=f"{shell} startup time",
            status="fail",
            message=f"{shell} failed to start with the deployed config: {e}",
            fix="Run the shell manually to see the startup error, then `my-shell setup --force`",
        )

    ms = result.mean_ms
    status = _rate_startup_ms(ms)
    message = f"{ms} ms mean over {result.runs} runs"

    if status != "pass":
        try:
            baseline = _benchmark_empty_config(shell, binary, runs=3).mean_ms
        except Exception:
            baseline = None
        if baseline is not None:
            overhead = round(ms - baseline, 2)
            status = _rate_startup_ms(overhead)
            message = f"{ms} ms mean; {baseline} ms is {shell} itself, my-shell adds {overhead} ms"

    return CheckResult(
        name=f"{shell} startup time",
        status=status,
        message=message,
        fix=(
            None
            if status == "pass"
            else "Run `my-shell benchmark --detailed` to see the my-shell overhead"
        ),
    )


def _check_startup_times(*, shells: list[str] | None = None) -> list[CheckResult]:
    """Startup-time check for the selected shells."""
    target_shells = shells or ["nushell", "xonsh"]
    return [_check_startup_time(shell) for shell in target_shells]


_QUOTED_VALUE_RE = re.compile(r'"([^"\r\n]+)"|\'([^\'\r\n]+)\'')


def _baked_path_references(content: str) -> set[str]:
    """Extract quoted absolute executable/install paths, including paths with spaces."""
    refs: set[str] = set()
    for double_quoted, single_quoted in _QUOTED_VALUE_RE.findall(content):
        value = (double_quoted or single_quoted).replace("\\\\", "\\")
        normalized = value.replace("\\", "/")
        is_absolute = normalized.startswith("/") or bool(re.match(r"^[A-Za-z]:/", normalized))
        components = {part.lower() for part in normalized.split("/")}
        if is_absolute and (
            components & {"bin", "shims", "installs"} or normalized.lower().endswith(".exe")
        ):
            refs.add(value)
    return refs


def _check_deployed_path_liveness() -> list[CheckResult]:
    """Every absolute executable path baked into a deployed init must exist.

    Generated init files capture tool locations at deploy time; a tool that
    moves or upgrades afterwards leaves a dead path that fails only at runtime
    (or, worse, is guarded by a silent `return`).
    """
    results: list[CheckResult] = []
    nu_config_dir = get_config_dir("nushell")
    settings = load_settings()
    for name, info in INTEGRATION_TOOLS.items():
        init_file = info.nushell_init_file
        if not init_file or not is_integration_enabled(settings, name):
            continue
        path = nu_config_dir / init_file
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        refs = _baked_path_references(content)
        dead = sorted(
            ref
            for ref in refs
            if not Path(ref.replace("\\\\", "\\")).exists()
            # Tool init scripts may prepend a cache/bin directory before the
            # tool has ever populated it (carapace does this on a clean HOME).
            # Those optional PATH directories are not stale executables.
            and Path(ref.replace("\\\\", "\\")).name.lower() not in {"bin", "shims", "installs"}
        )
        if dead:
            results.append(
                CheckResult(
                    name=f"Baked paths: {init_file}",
                    status="fail",
                    message=f"{len(dead)} baked path(s) no longer exist, e.g. {dead[0]}",
                    fix="Run `my-shell setup --force` to regenerate init files",
                )
            )
        elif refs:
            results.append(
                CheckResult(
                    name=f"Baked paths: {init_file}",
                    status="pass",
                    message=f"all {len(refs)} baked absolute path(s) exist",
                )
            )
    return results


def _check_nushell_runtime(config_dir: Path | None = None) -> CheckResult:
    """Execute the deployed nushell config and assert PATH survives as a list.

    String-shaped PATH (or a near-empty one) means every external command
    lookup fails while shape-level checks stay green.
    """
    from .benchmark import _shell_binary

    binary = _shell_binary("nushell")
    if binary is None:
        return CheckResult(
            name="nushell runtime PATH",
            status="info",
            message="nushell not installed -- skipped",
        )

    probe = (
        "$env.CMD_DURATION_MS = 42; "
        "do $env.PROMPT_COMMAND | ignore; "
        "$env.PATH | describe | print; "
        "$env.PATH | length | print"
    )
    try:
        args = [binary, "-l"]
        if config_dir is not None:
            args.extend(
                [
                    "--config",
                    str(config_dir / "config.nu"),
                    "--env-config",
                    str(config_dir / "env.nu"),
                ]
            )
        args.extend(["-c", probe])
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return CheckResult(
            name="nushell runtime PATH",
            status="fail",
            message=f"could not run nushell with the deployed config: {e}",
            fix="Run `nu -l` manually to see the startup error",
        )

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    describe = lines[-2] if len(lines) >= 2 else ""
    length = lines[-1] if lines else "0"
    stderr = proc.stderr.strip()
    if proc.returncode != 0 or stderr:
        detail = stderr.splitlines()[0] if stderr else f"exit code {proc.returncode}"
        return CheckResult(
            name="nushell runtime PATH",
            status="fail",
            message=f"deployed config wrote startup diagnostics: {detail}",
            fix="Run `nu -l` to see the error, then `my-shell setup --force`",
        )
    if not describe.startswith("list<") or not length.isdigit() or int(length) < 3:
        return CheckResult(
            name="nushell runtime PATH",
            status="fail",
            message=f"PATH degraded after config load (type={describe or '?'}, entries={length})",
            fix="Run `my-shell setup --force` to regenerate mise.nu",
        )
    return CheckResult(
        name="nushell runtime PATH",
        status="pass",
        message=f"PATH is a {describe} with {length} entries after config load",
    )


def _check_xonsh_runtime(rc_path: Path | None = None) -> CheckResult:
    """Start xonsh with the deployed rc and confirm it still reaches a prompt.

    A load-time error in any sourced file aborts the rest of the rc, so xonsh
    exits 0 with the integrations silently missing.
    """
    from .benchmark import _shell_binary

    binary = _shell_binary("xonsh")
    if binary is None:
        return CheckResult(
            name="xonsh runtime",
            status="info",
            message="xonsh not installed -- skipped",
        )

    rc = rc_path if rc_path is not None else get_home_dir() / ".xonshrc"
    if not rc.exists():
        return CheckResult(
            name="xonsh runtime",
            status="warn",
            message="xonsh is installed but has no deployed rc -- it starts as a bare shell",
            fix="Run `my-shell setup --shell xonsh` to deploy it",
        )

    from .merge import expected_xonsh_aliases

    sentinel = "MY_SHELL_XONSH_OK"
    missing_marker = "MY_SHELL_MISSING:"
    expected = expected_xonsh_aliases(load_settings())
    probe = (
        f"print('{sentinel}'); "
        f"print('{missing_marker}' + ','.join(a for a in {expected!r} if a not in aliases))"
    )
    try:
        proc = subprocess.run(
            [binary, "--rc", str(rc), "-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return CheckResult(
            name="xonsh runtime",
            status="fail",
            message=f"could not run xonsh with the deployed rc: {e}",
            fix="Run `xonsh` manually to see the startup error",
        )

    if proc.returncode != 0 or sentinel not in proc.stdout:
        detail = (proc.stderr.strip().splitlines() or [f"exit code {proc.returncode}"])[0]
        return CheckResult(
            name="xonsh runtime",
            status="fail",
            message=f"deployed rc does not reach a prompt: {detail}",
            fix="Run `xonsh` to see the error, then `my-shell setup --shell xonsh --force`",
        )
    missing = next(
        (
            line.removeprefix(missing_marker).strip()
            for line in proc.stdout.splitlines()
            if line.startswith(missing_marker)
        ),
        "",
    )
    if missing:
        return CheckResult(
            name="xonsh runtime",
            status="fail",
            message=f"rc loads but these commands never registered: {missing}",
            fix="Run `xonsh` to see the import error, then `my-shell setup --shell xonsh --force`",
        )
    if proc.stderr.strip():
        return CheckResult(
            name="xonsh runtime",
            status="warn",
            message=f"rc loads but writes startup noise: {proc.stderr.strip().splitlines()[0]}",
            fix="Run `xonsh` to see the warning",
        )
    return CheckResult(
        name="xonsh runtime",
        status="pass",
        message="deployed rc reaches a prompt cleanly",
    )


def _check_plugin_versions() -> list[CheckResult]:
    """A plugin binary outlives the nushell it was built for; nu then refuses to load it."""
    from .plugins import _get_nu_version, registered_plugin_versions, stale_plugin_names

    registered = registered_plugin_versions()
    if not registered:
        return []

    stale = stale_plugin_names(registered, _get_nu_version())
    if not stale:
        return [
            CheckResult(
                name="Nushell plugin versions",
                status="pass",
                message=f"All {len(registered)} registered plugins match the running nushell",
            )
        ]
    return [
        CheckResult(
            name="Nushell plugin versions",
            status="warn",
            message=f"stale, built for an older nushell: {', '.join(sorted(stale))}",
            fix=(
                "Run `my-shell plugins setup` to rebuild the configured ones; "
                "for any other plugin, `cargo install <crate>` then `plugin add <path>`"
            ),
        )
    ]


def _check_plugins(project_dir: Path) -> list[CheckResult]:
    """Check status of configured nushell plugins."""
    plugins = load_plugin_list(project_dir)
    if not plugins:
        return [
            CheckResult(
                name="Nushell plugins",
                status="pass",
                message="No plugins configured",
            )
        ]

    results: list[CheckResult] = []
    installed_count = 0
    missing_names: list[str] = []

    for name in plugins:
        if is_plugin_installed(name):
            installed_count += 1
        else:
            missing_names.append(name)

    if not missing_names:
        results.append(
            CheckResult(
                name="Nushell plugins",
                status="pass",
                message=f"All {installed_count} configured plugins are installed",
            )
        )
        results.extend(_check_plugin_versions())
    else:
        results.append(
            CheckResult(
                name="Nushell plugins",
                status="info",
                message=f"{len(missing_names)} optional plugin(s) not installed: "
                f"{', '.join(missing_names)}",
                fix="Run `my-shell plugins setup` to install and register plugins",
            )
        )

    return results


# ── Main entry point ──────────────────────────────────────────────


def run_doctor(project_dir: Path | None = None) -> list[CheckResult]:
    """Run every health check. project_dir defaults to the detected repo root."""
    root = project_dir or get_project_dir()
    results: list[CheckResult] = []
    target_shells = _configured_shells(root)

    log_step("Running health checks...")

    results.extend(_check_shell_binaries())
    results.extend(_check_duplicate_shells())
    results.extend(_check_tool_sources())
    results.extend(_check_vendor_autoload_conflicts())
    results.extend(_check_project_mise_backends(root))
    results.extend(_check_integration_tools())
    results.extend(_check_nushell_prompt_contract(root))
    results.append(_check_nerd_font(root))
    results.extend(_check_config_valid(root))
    results.extend(_check_config_ownership(shells=target_shells))
    results.extend(_check_deploy_hashes(root, shells=target_shells))
    results.extend(_check_user_custom_files(shells=target_shells))
    results.extend(_check_cargo_rust())
    results.append(_check_python_environment(enabled="xonsh" in target_shells))
    if "xonsh" in target_shells:
        results.append(_check_xonsh_runtime())
    results.extend(_check_path_sanity())
    results.extend(_check_startup_times(shells=target_shells))
    results.extend(_check_deployed_path_liveness())
    if "nushell" in target_shells:
        results.append(_check_nushell_runtime())
    results.extend(_check_plugins(root))

    return results


def doctor_json(results: list[CheckResult]) -> str:
    """Serialize check results as JSON for CI / scripting (`doctor --json`)."""
    payload = {
        "results": [asdict(r) for r in results],
        "summary": {
            status: sum(1 for r in results if r.status == status)
            for status in ("pass", "warn", "fail", "info")
        },
    }
    return json.dumps(payload, indent=2)


def fix_doctor_issues() -> None:
    """Install tools that doctor reports as missing (`doctor --fix`).

    Reuses install_tool, which is idempotent (skips already-installed tools) and
    isolates per-tool failures so one bad install doesn't abort the rest.
    """
    from .config import is_integration_enabled, load_settings
    from .install import install_tool

    settings = load_settings()
    to_fix: list[str] = []
    for name, info in INTEGRATION_TOOLS.items():
        if is_integration_enabled(settings, name) and not is_available(info.binary):
            to_fix.append(name)
    to_fix.extend(name for name, info in OPTIONAL_TOOLS.items() if not is_available(info.binary))

    if not to_fix:
        log_step("Nothing to fix -- all configured tools are available.")
        return

    log_step(f"Installing {len(to_fix)} missing tool(s): {', '.join(to_fix)}")
    for name in to_fix:
        try:
            install_tool(name)
        except Exception as e:
            log_error(f"Could not install {name}: {e}")


# ── Rich report printer ──────────────────────────────────────────


def print_doctor_report(results: list[CheckResult]) -> None:
    """Print a formatted doctor report using Rich."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    STATUS_ICONS = _status_icons()

    STATUS_STYLE = {
        "pass": "green",
        "info": "blue",
        "warn": "yellow",
        "fail": "red",
    }

    table = Table(
        title="my-shell doctor",
        show_header=True,
        header_style="bold",
        border_style="dim",
        pad_edge=True,
    )
    table.add_column("Status", width=6, justify="center")
    table.add_column("Check", min_width=30)
    table.add_column("Details", ratio=1)

    for result in results:
        icon = STATUS_ICONS.get(result.status, "?")
        style = STATUS_STYLE.get(result.status, "")

        detail = result.message
        if result.fix:
            detail += f"\n[dim]Fix: {result.fix}[/dim]"

        table.add_row(icon, f"[{style}]{result.name}[/{style}]", detail)

    console.print()
    console.print(table)
    console.print()

    # Summary line
    pass_count = sum(1 for r in results if r.status == "pass")
    warn_count = sum(1 for r in results if r.status == "warn")
    fail_count = sum(1 for r in results if r.status == "fail")

    parts: list[str] = []
    parts.append(f"[green]{pass_count} passed[/green]")
    if warn_count:
        parts.append(f"[yellow]{warn_count} warnings[/yellow]")
    if fail_count:
        parts.append(f"[red]{fail_count} failed[/red]")

    console.print(f"  Summary: {', '.join(parts)}")

    if fail_count:
        console.print("\n  [red]Some checks failed. Review the fixes above.[/red]")
    elif warn_count:
        console.print(
            "\n  [yellow]Some warnings detected. Your setup may work but is not optimal.[/yellow]"
        )
    else:
        console.print(
            "\n  [green]All checks passed. Your my-shell installation is healthy.[/green]"
        )

    console.print()
