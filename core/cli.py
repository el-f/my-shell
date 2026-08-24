"""Typer CLI entry point."""

import sys
from enum import StrEnum

import typer

app = typer.Typer(
    name="my-shell",
    help="Cross-platform shell configuration manager.",
    add_completion=True,
    pretty_exceptions_short=True,
)

plugins_app = typer.Typer(help="Manage nushell plugins.")
app.add_typer(plugins_app, name="plugins")

profiles_app = typer.Typer(help="Manage configuration profiles.")
app.add_typer(profiles_app, name="profiles")


# Commands that already surface or fix staleness -- no nudge needed before them.
_NUDGE_SKIP = {"setup", "deploy", "update", "status", "doctor", "init"}


def _maybe_nudge_stale() -> None:
    """Interactive-only, once/hour: warn when the deployed config is stale.

    tty-gated so pipes / CI / tests never trigger it (no cache writes, no cost).
    Never raises -- a reminder must not break a command.
    """
    try:
        if not sys.stdout.isatty():
            return

        import time

        from .utils import get_cache_dir

        stamp = get_cache_dir() / "last-stale-nudge"
        now = time.time()
        if stamp.exists():
            try:
                if now - float(stamp.read_text(encoding="utf-8").strip()) < 3600:
                    return
            except OSError, ValueError:
                pass

        # Record the check time up front so a slow/failed check still throttles.
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(str(now), encoding="utf-8")
        except OSError:
            pass

        from .merge import get_deploy_statuses

        stale = [s.shell for s in get_deploy_statuses(["nushell", "xonsh"]) if s.stale]
        if stale:
            from .utils import log_warn

            log_warn(
                f"my-shell config changed since last deploy ({', '.join(stale)}). "
                "Run `my-shell setup` to update."
            )
    except Exception:
        pass  # a nudge must never break a command


@app.callback()
def main(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug output"),
) -> None:
    """Cross-platform shell configuration manager."""
    if verbose:
        from .utils import set_verbose

        set_verbose(True)

    if ctx.invoked_subcommand not in _NUDGE_SKIP:
        _maybe_nudge_stale()


class Shell(StrEnum):
    """Target shell for setup and deploy commands."""

    nushell = "nushell"
    xonsh = "xonsh"
    all = "all"


def _shells(shell: Shell) -> list[str]:
    if shell == Shell.all:
        return ["nushell", "xonsh"]
    return [shell.value]


def _enabled_shells(shell: Shell, *, include_xonsh: bool = False) -> list[str]:
    """Respect configured opt-outs for the implicit `all` target.

    Naming a shell explicitly remains an override. `--install-xonsh` also opts
    xonsh into a full setup, even when the local setting is false.
    """
    if shell != Shell.all:
        return [shell.value]

    from .config import load_settings

    enabled = load_settings().get("shells", {})
    return [
        name
        for name in ("nushell", "xonsh")
        if enabled.get(name, True) or (name == "xonsh" and include_xonsh)
    ]


@app.command()
def install(
    shell: Shell = typer.Option(Shell.all, help="Shell to install"),
) -> None:
    """Install shell binaries (nushell, xonsh)."""
    from .install import install_shell
    from .utils import log_error

    for s in _shells(shell):
        try:
            install_shell(s)
        except Exception as exc:
            log_error(f"Install failed for {s}: {exc}")
            raise typer.Exit(1) from exc


@app.command()
def setup(
    shell: Shell | None = typer.Option(
        None, help="Target shell (default: shells enabled in settings)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-deploy even if version unchanged"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview what would be done without making changes"
    ),
    profile: str = typer.Option(
        None, "--profile", "-p", help="Configuration profile to apply (e.g. minimal, full)"
    ),
    install_xonsh: bool = typer.Option(
        False, "--install-xonsh", help="Install xonsh even if not enabled in settings"
    ),
) -> None:
    """Full setup: render aliases + deploy configs."""
    from .merge import deploy
    from .utils import log_error, log_header

    if profile:
        from .profiles import apply_profile

        apply_profile(profile)

    selected_shell = shell or Shell.all
    # An explicit `--shell all` means exactly that. Only the omitted/default
    # target follows settings, which lets installers avoid xonsh unless the
    # user enabled it without making the explicit CLI option misleading.
    include_xonsh = install_xonsh or shell == Shell.all
    target_shells = _enabled_shells(selected_shell, include_xonsh=include_xonsh)

    if not dry_run:
        from .install import install_all_tools, install_shells_for_setup

        try:
            install_shells_for_setup(
                install_xonsh_override=install_xonsh,
                shells=target_shells,
            )
        except Exception as exc:
            log_error(f"Shell install failed: {exc}")
            raise typer.Exit(1) from exc
        install_all_tools()

        # Validate the (global) config once, not once per shell.
        from .merge import _preflight_validate
        from .utils import get_project_dir

        _preflight_validate(get_project_dir())

    for s in target_shells:
        log_header(f"  Setting up {s}")

        if dry_run:
            from .dry_run import show_dry_run_diff

            show_dry_run_diff(s)
            continue

        try:
            deploy(s, force=force, validate=False)
        except Exception as exc:
            log_error(f"Setup failed for {s}: {exc}")
            raise typer.Exit(1) from exc
        print()


@app.command()
def render(
    shell: Shell = typer.Option(Shell.all, help="Target shell"),
    preview: bool = typer.Option(
        False, "--preview", "-p", help="Print the rendered aliases instead of writing them"
    ),
) -> None:
    """Render aliases only (no deployment)."""
    from .utils import log_error

    if preview:
        from rich.console import Console
        from rich.syntax import Syntax

        from .render import render_content

        console = Console()
        for s in _shells(shell):
            try:
                content = render_content(s)
            except Exception as exc:
                log_error(f"Render failed for {s}: {exc}")
                raise typer.Exit(1) from exc
            lexer = "python" if s == "xonsh" else "bash"
            console.print(f"\n[bold cyan]# {s} aliases[/bold cyan]")
            console.print(Syntax(content, lexer, theme="monokai", line_numbers=False))
        return

    from .render import render_aliases

    for s in _shells(shell):
        try:
            render_aliases(s)
        except Exception as exc:
            log_error(f"Render failed for {s}: {exc}")
            raise typer.Exit(1) from exc


@app.command(name="deploy")
def deploy_cmd(
    shell: Shell | None = typer.Option(
        None, help="Target shell (default: shells enabled in settings)"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-deploy even if version unchanged"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview what would be done without making changes"
    ),
) -> None:
    """Deploy configs only."""
    from .merge import deploy
    from .utils import log_error

    if not dry_run:
        # Validate the (global) config once, not once per shell.
        from .merge import _preflight_validate
        from .utils import get_project_dir

        _preflight_validate(get_project_dir())

    selected_shell = shell or Shell.all
    target_shells = _enabled_shells(selected_shell, include_xonsh=shell == Shell.all)
    for s in target_shells:
        if dry_run:
            from .dry_run import show_dry_run_diff

            show_dry_run_diff(s)
            continue
        try:
            deploy(s, force=force, validate=False)
        except Exception as exc:
            log_error(f"Deploy failed for {s}: {exc}")
            raise typer.Exit(1) from exc


@app.command(name="install-tools")
def install_tools_cmd(
    tool: str = typer.Argument(
        default=None,
        help="Tool to install. Omit to install all; run `my-shell detect` for the list.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show the install commands without running them"
    ),
) -> None:
    """Install integration and optional tools through the available package manager."""
    from .install import (
        install_all_tools,
        install_tool,
        preview_install_all,
        resolve_install_command,
    )
    from .utils import log_error, log_info

    if dry_run:
        if tool:
            cmd = resolve_install_command(tool)
            log_info(f"{tool}: {' '.join(cmd) if cmd else 'no install method for this platform'}")
        else:
            preview_install_all()
        return

    try:
        if tool:
            install_tool(tool)
        else:
            install_all_tools()
    except Exception as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc


@app.command(name="install-fonts")
def install_fonts_cmd(
    font: str = typer.Argument(
        default=None,
        help="Font to install (meslo, firacode). Defaults to settings.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Install even on headless systems (no DISPLAY)"
    ),
) -> None:
    """Install a Nerd Font for oh-my-posh prompt theming."""
    from .fonts import NERD_FONTS, font_preview, install_nerd_font
    from .utils import get_project_dir, log_error, log_info

    if font is None:
        from .config import load_settings

        settings = load_settings(get_project_dir())
        font = settings.get("fonts", {}).get("nerd_font", "meslo")

    if font not in NERD_FONTS:
        log_error(f"Unknown font: {font}. Available: {', '.join(NERD_FONTS)}")
        raise typer.Exit(1)

    if not install_nerd_font(font, force=force):
        raise typer.Exit(1)

    log_info(font_preview(font))


@app.command()
def detect() -> None:
    """Print system detection info."""
    from .detect import print_detection_info

    print_detection_info()


@plugins_app.command(name="install")
def plugins_install() -> None:
    """Install all missing nushell plugins via cargo."""
    from .plugins import install_plugins
    from .utils import get_project_dir

    if not install_plugins(get_project_dir()):
        raise typer.Exit(1)


@plugins_app.command(name="register")
def plugins_register() -> None:
    """Register all installed plugins with nushell."""
    from .plugins import register_plugins
    from .utils import get_project_dir

    if not register_plugins(get_project_dir()):
        raise typer.Exit(1)


@plugins_app.command(name="status")
def plugins_status() -> None:
    """Show plugin install/registration status."""
    from rich.console import Console
    from rich.table import Table

    from .plugins import get_cargo_bin_dir, is_plugin_installed, load_plugin_list
    from .utils import get_project_dir, is_available

    project_dir = get_project_dir()
    plugins = load_plugin_list(project_dir)
    console = Console(highlight=False)

    cargo_ok = "[green]available[/]" if is_available("cargo") else "[red]NOT FOUND[/]"
    nu_ok = "[green]available[/]" if is_available("nu") else "[red]NOT FOUND[/]"
    console.print(f"  cargo: {cargo_ok}")
    console.print(f"  nu:    {nu_ok}")
    console.print(f"  cargo bin dir: {get_cargo_bin_dir()}")

    table = Table(title="Nushell Plugins", show_header=True, header_style="bold")
    table.add_column("Status", justify="center", width=10)
    table.add_column("Plugin", style="cyan")
    table.add_column("Description")
    for name, info in plugins.items():
        status = "[green]installed[/]" if is_plugin_installed(name) else "[yellow]missing[/]"
        table.add_row(status, name, info.get("description", ""))
    console.print(table)


@plugins_app.command(name="setup")
def plugins_setup() -> None:
    """Install + register all plugins in one step."""
    from .plugins import install_plugins, register_plugins
    from .utils import get_project_dir

    project_dir = get_project_dir()
    installed = install_plugins(project_dir)
    registered = register_plugins(project_dir)
    if not installed or not registered:
        raise typer.Exit(1)


@profiles_app.command(name="list")
def profiles_list() -> None:
    """List available profiles and mark the one currently applied."""
    from rich.console import Console
    from rich.table import Table

    from .profiles import _resolve_profile, active_profile, load_profiles
    from .utils import get_project_dir

    project_dir = get_project_dir()
    profiles = load_profiles(project_dir)
    active = active_profile(project_dir)

    table = Table(title="Profiles", show_header=True, header_style="bold")
    table.add_column("Active", justify="center", width=6)
    table.add_column("Name", style="cyan")
    table.add_column("Integrations")
    table.add_column("Commands")

    for name in profiles:
        resolved = _resolve_profile(profiles, name)
        mark = "[green]*[/]" if name == active else ""
        integrations = ", ".join(resolved.get("integrations", [])) or "(all)"
        commands = ", ".join(resolved.get("commands", [])) or "(all)"
        table.add_row(mark, name, integrations, commands)

    console = Console()
    console.print(table)
    if active:
        console.print(f"  Active profile: [green]{active}[/]")
    else:
        console.print("  No profile applied (using settings.toml defaults).")


@profiles_app.command(name="apply")
def profiles_apply(
    name: str = typer.Argument(..., help="Profile name to apply (e.g. minimal, full)"),
) -> None:
    """Apply a profile: writes config/settings.local.toml."""
    from .profiles import apply_profile
    from .utils import log_error

    try:
        apply_profile(name)
    except ValueError as exc:
        log_error(str(exc))
        raise typer.Exit(1) from exc


@app.command()
def status(
    shell: Shell = typer.Option(Shell.all, help="Target shell"),
) -> None:
    """Show deployment status (deployed vs current version)."""
    from .merge import get_deploy_statuses
    from .utils import get_project_dir, log_header, log_info, log_success, log_warn

    project_dir = get_project_dir()

    for st in get_deploy_statuses(_shells(shell), project_dir):
        log_header(f"  Status: {st.shell}")

        if not st.deployed:
            log_warn(f"{st.shell}: Not deployed yet")
            log_info(f"Run `my-shell setup --shell {st.shell}` to deploy")
            continue

        log_info(f"Deployed version: {st.deployed_version}")
        log_info(f"Current version:  {st.current_version}")
        log_info(f"Deployed hash:    {st.deployed_hash}")
        log_info(f"Current hash:     {st.current_hash}")

        if not st.stale:
            log_success(f"{st.shell}: Up to date")
        else:
            log_warn(f"{st.shell}: Re-deploy needed (config has changed)")
            # Plain setup redeploys when the source changed; --force is only for
            # re-deploying when nothing changed.
            log_info(f"Run `my-shell setup --shell {st.shell}` to update")
        print()


@app.command()
def update() -> None:
    """Pull latest changes and re-deploy all shells."""
    import subprocess as _sp

    from .merge import deploy
    from .utils import get_project_dir, log_error, log_header, log_step, log_success

    project_dir = get_project_dir()

    log_step("Pulling latest changes...")
    try:
        result = _sp.run(
            ["git", "-C", str(project_dir), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log_error(f"git pull failed: {result.stderr.strip()}")
            raise typer.Exit(1)
        log_success(f"git pull: {result.stdout.strip()}")
    except FileNotFoundError:
        log_error("git not found in PATH")
        raise typer.Exit(1) from None
    except _sp.TimeoutExpired:
        log_error("git pull timed out")
        raise typer.Exit(1) from None

    log_step("Re-deploying all shells...")
    # Validate the (global) config once, not once per shell.
    from .merge import _preflight_validate

    _preflight_validate(project_dir)
    for s in _enabled_shells(Shell.all):
        log_header(f"  Deploying {s}")
        try:
            deploy(s, force=True, validate=False)
        except Exception as exc:
            log_error(f"Deploy failed for {s}: {exc}")
            raise typer.Exit(1) from exc
    log_success("Update complete")


@app.command()
def doctor(
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON (for CI)"),
    fix: bool = typer.Option(
        False, "--fix", help="Install tools that doctor reports as missing, then re-check"
    ),
) -> None:
    """Run health checks on your my-shell installation."""
    from .doctor import doctor_json, fix_doctor_issues, print_doctor_report, run_doctor

    if output_json:
        # run_doctor logs progress to stdout; swallow it so --json emits only JSON.
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            if fix:
                fix_doctor_issues()
            results = run_doctor()
        print(doctor_json(results))
    else:
        if fix:
            fix_doctor_issues()
        results = run_doctor()
        print_doctor_report(results)

    fail_count = sum(1 for r in results if r.status == "fail")
    if fail_count:
        raise typer.Exit(1)


@app.command()
def benchmark(
    shell: Shell = typer.Option(Shell.all, help="Shell to benchmark"),
    detailed: bool = typer.Option(
        False, "--detailed", "-d", help="Compare empty config vs my-shell config"
    ),
    runs: int = typer.Option(5, "--runs", "-n", min=1, help="Timing samples per shell"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress progress output"),
) -> None:
    """Measure shell startup time."""
    from .benchmark import run_benchmark
    from .utils import get_cache_dir

    run_benchmark(
        shells=_shells(shell),
        detailed=detailed,
        runs=runs,
        quiet=quiet,
        history_dir=get_cache_dir(),
    )


@app.command()
def rollback(
    shell: Shell = typer.Option(Shell.all, help="Target shell"),
    to: str = typer.Option(None, "--to", help="Backup timestamp to restore (from list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Restore a previous backup of shell configuration."""
    from .backup import list_backups, preview_restore, restore_backup
    from .config import get_config_dir
    from .utils import log_error, log_header, log_info, log_warn

    for s in _shells(shell):
        config_dir = get_config_dir(s)
        backups = list_backups(config_dir)

        if not backups:
            log_warn(f"{s}: No backups available")
            continue

        log_info(f"{s}: Available backups:")
        for i, backup in enumerate(backups):
            print(f"  [{i}] {backup.name}")

        # Select backup
        selected = None
        if to:
            for backup in backups:
                if backup.name == to:
                    selected = backup
                    break
            if selected is None:
                log_error(f"{s}: No backup matching '{to}'")
                raise typer.Exit(1)
        else:
            selected = backups[0]

        # Show what restoring this backup would change, then confirm (destructive).
        log_header(f"  {s}: rollback preview ({selected.name})")
        if not preview_restore(selected, config_dir, s):
            log_info(f"{s}: backup matches current config -- nothing to restore")
            continue
        if not yes and not typer.confirm(f"\n  Restore {s} from {selected.name}?", default=False):
            log_info(f"{s}: rollback cancelled")
            continue

        log_info(f"Restoring backup: {selected.name}")
        try:
            restore_backup(selected, config_dir, s)
        except Exception as exc:
            log_error(f"Rollback failed for {s}: {exc}")
            raise typer.Exit(1) from exc


@app.command(name="init")
def init_cmd() -> None:
    """Interactive setup wizard for my-shell."""
    if not sys.stdin.isatty():
        from .utils import log_error

        log_error("Interactive terminal required. Use 'my-shell setup --shell <shell>' instead.")
        raise typer.Exit(1)

    from .init_wizard import run_init_wizard

    run_init_wizard()


@app.command()
def version() -> None:
    """Show my-shell version and deployment status."""
    from . import __version__
    from .config import get_config_dir
    from .merge import _get_deployed_version, _get_version
    from .utils import get_project_dir

    project_dir = get_project_dir()
    current_version = _get_version(project_dir)

    print(f"my-shell {__version__}")
    print(f"Source version: {current_version}")

    for s in _shells(Shell.all):
        config_dir = get_config_dir(s)
        deployed = _get_deployed_version(s, config_dir)
        if deployed:
            print(f"{s}: deployed ({deployed})")
        else:
            print(f"{s}: not deployed")


@app.command(name="validate")
def validate_cmd() -> None:
    """Validate configuration files (aliases, settings, plugins)."""
    from .validate import validate_and_report

    ok = validate_and_report()
    if not ok:
        raise typer.Exit(1)


@app.command(name="config")
def config_show() -> None:
    """Show current merged configuration settings."""
    import json

    from .config import load_settings
    from .utils import get_project_dir

    settings = load_settings(get_project_dir())
    print(json.dumps(settings, indent=2))


@app.command()
def uninstall(
    shell: Shell = typer.Option(Shell.all, help="Target shell"),
    keep_custom: bool = typer.Option(True, help="Keep user-custom files"),
) -> None:
    """Remove deployed my-shell configuration files."""
    from .config import get_config_dir
    from .uninstall import uninstall_shell
    from .utils import log_header, log_warn

    if not keep_custom:
        typer.confirm(
            "This will permanently delete user-custom files. Continue?",
            abort=True,
        )

    for s in _shells(shell):
        log_header(f"  Uninstalling {s}")
        config_dir = get_config_dir(s)
        removed = uninstall_shell(s, config_dir, keep_custom=keep_custom)
        if not removed:
            log_warn(f"{s}: Nothing to remove")
        print()


if __name__ == "__main__":
    app()
