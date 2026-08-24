"""Enhanced --dry-run with diff preview.

Renders new config in memory and diffs against currently deployed files.
Uses delta if available, else Rich diff, else plain unified diff.
"""

import difflib
from pathlib import Path

from .config import get_config_dir, load_settings
from .merge import (
    _compute_project_hash,
    _get_version,
    _nushell_tool_init_specs,
    _render_tool_init,
    generate_nushell_config,
    generate_nushell_env,
    generate_xonsh_config,
)
from .utils import get_home_dir, get_project_dir, log_header, log_info, log_step


def _read_file(path: Path) -> str:
    """Read a file's content, returning empty string if it doesn't exist."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _unified_diff(old: str, new: str, filename: str) -> str:
    """Generate a unified diff between old and new content."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"deployed/{filename}",
        tofile=f"new/{filename}",
    )
    return "".join(diff)


def _print_diff(diff_text: str) -> None:
    """Print a diff with color coding."""
    if not diff_text.strip():
        log_info("  (no changes)")
        return

    try:
        from rich.console import Console
        from rich.syntax import Syntax

        console = Console()
        syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
        console.print(syntax)
    except ImportError:
        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                print(f"\033[32m{line}\033[0m")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"\033[31m{line}\033[0m")
            elif line.startswith("@@"):
                print(f"\033[36m{line}\033[0m")
            else:
                print(line)


def show_dry_run_diff(shell: str, project_dir: Path | None = None) -> None:
    """Show what would change if we deployed now."""
    root = project_dir or get_project_dir()
    config_dir = get_config_dir(shell)

    log_step(f"[dry-run] Diff preview for {shell}")

    # Computed once here; every generate_* call would otherwise re-walk the tree.
    version = _get_version(root)
    project_hash = _compute_project_hash(root)

    if shell == "nushell":
        new_config = generate_nushell_config(
            config_dir, root, version=version, project_hash=project_hash
        )
        new_env = generate_nushell_env(config_dir, root, version=version, project_hash=project_hash)

        old_config = _read_file(config_dir / "config.nu")
        old_env = _read_file(config_dir / "env.nu")

        log_header("  config.nu")
        diff = _unified_diff(old_config, new_config, "config.nu")
        _print_diff(diff)

        log_header("  env.nu")
        diff = _unified_diff(old_env, new_env, "env.nu")
        _print_diff(diff)

        _show_tool_init_diffs(config_dir, root)

    elif shell == "xonsh":
        new_config = generate_xonsh_config(
            config_dir, root, version=version, project_hash=project_hash
        )
        xonshrc_path = get_home_dir() / ".xonshrc"
        old_config = _read_file(xonshrc_path)

        log_header("  .xonshrc")
        diff = _unified_diff(old_config, new_config, ".xonshrc")
        _print_diff(diff)

    else:
        raise ValueError(f"Unsupported shell: {shell}")


def _show_tool_init_diffs(config_dir: Path, project_dir: Path) -> None:
    """Diff each enabled Nushell integration init against what would be generated.

    Renders each tool's init via its own init subprocess (read-only) and diffs it
    against the deployed file. Nothing is written. Tools that aren't installed are
    noted and skipped.
    """
    settings = load_settings(project_dir)
    for spec in _nushell_tool_init_specs(config_dir, project_dir, settings):
        log_header(f"  {spec.output_path.name}")
        new_init = _render_tool_init(
            spec.tool_name,
            spec.cmd,
            project_dir=project_dir,
            post_process=spec.post_process,
            regex_post_process=spec.regex_post_process,
            prepend_lines=spec.prepend_lines,
            append_lines=spec.append_lines,
        )
        if new_init is None:
            log_info(f"  ({spec.tool_name} not available -- init skipped)")
            continue
        old_init = _read_file(spec.output_path)
        _print_diff(_unified_diff(old_init, new_init, spec.output_path.name))
