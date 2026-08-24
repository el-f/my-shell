"""Platform, shell, and tool detection."""

import os
import platform

from .registry import DETECT_TOOLS
from .utils import (
    get_os,
    is_available,
    is_windows,
    resolve_tool_path,
)


def detect_distro() -> str:
    """Return Linux distribution family: 'debian', 'rhel', 'arch', 'unknown', or 'not-linux'."""
    if get_os() != "linux":
        return "not-linux"
    if is_available("apt"):
        return "debian"
    if is_available("yum"):
        return "rhel"
    if is_available("pacman"):
        return "arch"
    return "unknown"


def detect_current_shell() -> str:
    """Detect the current interactive shell."""
    # NU_VERSION is set on all platforms
    if os.environ.get("NU_VERSION"):
        return "nushell"

    if os.environ.get("XONSH_VERSION"):
        return "xonsh"

    shell_path = os.environ.get("SHELL", "")
    shell_basename = os.path.basename(shell_path)
    if shell_basename in ("nu", "nushell"):
        return "nushell"
    if "xonsh" in shell_basename:
        return "xonsh"
    if "zsh" in shell_basename:
        return "zsh"
    if "bash" in shell_basename:
        return "bash"

    if is_windows() and os.environ.get("PSModulePath"):
        return "powershell"

    return "unknown"


def detect_package_manager() -> str:
    """Detect the primary package manager."""
    os_name = get_os()
    if os_name == "windows":
        if is_available("winget"):
            return "winget"
        return "none"
    if os_name == "macos":
        if is_available("brew"):
            return "homebrew"
        return "none"
    if is_available("apt"):
        return "apt"
    if is_available("yum"):
        return "yum"
    if is_available("pacman"):
        return "pacman"
    return "none"


def detect_all() -> dict[str, str]:
    """Return detection info."""
    return {
        "os": get_os(),
        "architecture": platform.machine(),
        "distro": detect_distro(),
        "shell": detect_current_shell(),
        "package_manager": detect_package_manager(),
        "python_version": platform.python_version(),
    }


def print_detection_info() -> None:
    """Print detection results in a friendly format."""
    from rich.console import Console
    from rich.table import Table

    console = Console(highlight=False)
    info = detect_all()

    console.print("[bold]System Detection Results:[/]")
    console.print(f"  OS: {info['os']}")
    console.print(f"  Architecture: {info['architecture']}")
    if info["os"] == "linux":
        console.print(f"  Distribution: {info['distro']}")
    console.print(f"  Current Shell: {info['shell']}")
    console.print(f"  Package Manager: {info['package_manager']}")
    console.print(f"  Python: {info['python_version']}")
    console.print()

    tools = DETECT_TOOLS

    table = Table(title="Tool Status", show_header=False, show_edge=False, pad_edge=False)
    table.add_column("status", no_wrap=True)
    table.add_column("tool", no_wrap=True)
    table.add_column("path")
    for tool in tools:
        path = resolve_tool_path(tool)
        if path:
            table.add_row("  [green]\\[ok][/]", tool, f"[dim]{path}[/]")
        else:
            table.add_row("  [red]\\[missing][/]", tool, "")
    console.print(table)
