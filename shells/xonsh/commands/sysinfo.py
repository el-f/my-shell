"""System information command for xonsh (port of shells/nushell/commands/sysinfo.nu)."""

import os
import platform
import shutil
import subprocess

from _box import (
    _box_footer,
    _box_header,
    _box_row,
    _box_row_raw,
    _box_width,
    _ensure_unicode_stdout,
)


def _is_available(tool):
    return shutil.which(tool) is not None


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _sysinfo(args, stdin=None):
    """Show system information and my-shell setup overview.

    Usage:
        sysinfo
    """
    _ensure_unicode_stdout()
    width = _box_width()
    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"

    # ── System section ──
    os_name = platform.system()
    arch = platform.machine()
    hostname = platform.node()

    if os_name == "Windows":
        os_release = platform.release()
        os_display = f"Windows {os_release} ({arch})"
    elif os_name == "Darwin":
        mac_ver = platform.mac_ver()[0]
        os_display = f"macOS {mac_ver} ({arch})" if mac_ver else f"macOS ({arch})"
    else:
        os_display = f"{os_name} ({arch})"

    # A live xonsh session exposes XONSH_VERSION in-process; the subprocess is the outside-xonsh path.
    xonsh_version = "unknown"
    try:
        import builtins

        xonsh_version = builtins.__xonsh__.env.get("XONSH_VERSION", "unknown")
    except AttributeError:
        try:
            result = subprocess.run(
                ["xonsh", "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.stdout.strip():
                xonsh_version = result.stdout.strip().split()[-1]
        except FileNotFoundError:
            xonsh_version = "unknown"

    terminal = os.environ.get("TERM_PROGRAM", "")
    if not terminal:
        if os.environ.get("WT_SESSION"):
            terminal = "Windows Terminal"
        else:
            terminal = "unknown"

    if os_name == "Windows":
        pkg_mgr = (
            "winget"
            if _is_available("winget")
            else (
                "scoop" if _is_available("scoop") else "choco" if _is_available("choco") else "none"
            )
        )
    elif os_name == "Darwin":
        pkg_mgr = "brew" if _is_available("brew") else "none"
    else:
        pkg_mgr = (
            "apt"
            if _is_available("apt")
            else "dnf"
            if _is_available("dnf")
            else "pacman"
            if _is_available("pacman")
            else "none"
        )

    print(_box_header("System", width))
    print(_box_row("OS", os_display, width))
    print(_box_row("Host", hostname, width))
    print(_box_row("Shell", f"xonsh {xonsh_version}", width))
    print(_box_row("Terminal", terminal, width))
    print(_box_row("Package Mgr", pkg_mgr, width))
    print(_box_footer(width))
    print()

    # ── my-shell section ──
    version = os.environ.get("MY_SHELL_VERSION", "unknown")
    project_dir = os.environ.get("MY_SHELL_DIR", "unknown")
    prompt = (
        "oh-my-posh"
        if _is_available("oh-my-posh")
        else "starship"
        if _is_available("starship")
        else "default"
    )

    print(_box_header("my-shell", width))
    print(_box_row("Version", version, width))
    print(_box_row("Project", project_dir, width))
    print(_box_row("Prompt", prompt, width))
    print(_box_footer(width))
    print()

    # ── Tools section ──
    tools = [
        "eza",
        "fzf",
        "fd",
        "rg",
        "bat",
        "zoxide",
        "yazi",
        "delta",
        "procs",
        "sd",
        "jq",
        "gron",
        "lefthook",
        "just",
        "atuin",
        "pueue",
        "dust",
        "duf",
        "lazygit",
        "tldr",
    ]

    found = [t for t in tools if _is_available(t)]
    missing = [t for t in tools if not _is_available(t)]

    print(_box_header("Tools", width))

    if found:
        for chunk in _chunks(found, 6):
            line = f"  {GREEN}\u2713{RESET} {'  '.join(chunk)}"
            print(_box_row_raw(line, width))

    if missing:
        for chunk in _chunks(missing, 6):
            line = f"  {RED}\u2717{RESET} {'  '.join(chunk)}"
            print(_box_row_raw(line, width))
    else:
        print(_box_row_raw(f"  {GREEN}\u2713{RESET} (none missing)", width))

    print(_box_footer(width))
    print()

    # ── Completions section ──
    has_carapace = shutil.which("carapace") is not None
    print(_box_header("Completions", width))
    if has_carapace:
        print(_box_row_raw(f"  {GREEN}\u2713{RESET} carapace (1000+ tools)", width))
    else:
        print(_box_row_raw(f"  {RED}\u2717{RESET} carapace (install carapace-bin)", width))
    print(_box_footer(width))
