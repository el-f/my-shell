"""Custom commands listing for xonsh (port of shells/nushell/commands/commands.nu)."""

import importlib
import inspect
import os
import tomllib

from _box import (
    _box_footer,
    _box_header,
    _box_row,
    _box_row_raw,
    _box_width,
    _ensure_unicode_stdout,
)


def _iter_custom_commands():
    """(alias, description) for every command across the command modules.

    A command is a module-level `_name(args, stdin=...)` function -- the shape
    xonshrc registers as an alias. Derived from the modules (not a hand-kept
    table), so a new `def _x(args, stdin=None)` appears here automatically.
    Imported helpers (e.g. _require_tool) and non-command utilities are skipped.
    """
    cmd_dir = os.path.dirname(__file__)
    for fname in sorted(os.listdir(cmd_dir)):
        if not fname.endswith(".py") or fname.startswith("_") or fname == "alias_fns.py":
            continue
        modname = fname[:-3]
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if getattr(fn, "__module__", None) != modname:
                continue  # helper imported from another module
            params = list(inspect.signature(fn).parameters)
            if len(params) >= 2 and params[1] == "stdin":
                doc = (fn.__doc__ or "").strip()
                yield name.lstrip("_"), (doc.splitlines()[0].strip() if doc else "")


def _load_aliases():
    """Load aliases from config/aliases.toml using $MY_SHELL_DIR."""
    my_shell_dir = os.environ.get("MY_SHELL_DIR", "")
    if not my_shell_dir:
        return None
    aliases_path = os.path.join(my_shell_dir, "config", "aliases.toml")
    if not os.path.isfile(aliases_path):
        return None
    with open(aliases_path, "rb") as f:
        return tomllib.load(f)


def _format_section_title(name):
    """Convert section key like 'modern_replacements' to 'Modern Replacements'."""
    return name.replace("_", " ").title()


def _commands(args, stdin=None):
    """Show all custom commands and aliases.

    Usage:
        commands
    """
    _ensure_unicode_stdout()
    width = _box_width()

    # ── Custom Commands section (derived from the modules, not a hand-kept list) ──
    print(_box_header("Custom Commands", width))
    for cmd, desc in sorted(_iter_custom_commands()):
        print(_box_row(cmd, desc, width))
    print(_box_footer(width))
    print()

    # ── Aliases from config/aliases.toml ──
    data = _load_aliases()
    if data is None:
        print(_box_header("Aliases", width))
        print(_box_row_raw("  \033[33maliases.toml not found\033[0m", width))
        print(_box_footer(width))
        return

    # Skip non-alias sections (e.g. wrappers)
    skip_sections = {"wrappers"}

    for section, entries in data.items():
        if section in skip_sections:
            continue
        if not isinstance(entries, dict):
            continue

        title = _format_section_title(section)
        print(_box_header(f"Aliases: {title}", width))

        for alias, value in entries.items():
            if isinstance(value, str):
                # Simple string alias: alias = "command"
                print(_box_row(alias, value, width))
            elif isinstance(value, dict):
                # Dict alias: alias = { command = "...", comment = "..." }
                cmd = value.get("command", value.get("xonsh_fn", ""))
                comment = value.get("comment", "")
                display = f"{cmd} ({comment})" if cmd and comment else (comment or cmd)
                print(_box_row(alias, display, width))

        print(_box_footer(width))
        print()

    # ── Wrappers section ──
    wrappers = data.get("wrappers", {})
    if wrappers:
        print(_box_header("Tool Wrappers", width))
        for name, info in wrappers.items():
            if isinstance(info, dict):
                preferred = info.get("preferred", "?")
                fallback = info.get("fallback", "?")
                print(_box_row(name, f"{preferred} \u2192 {fallback}", width))
        print(_box_footer(width))
