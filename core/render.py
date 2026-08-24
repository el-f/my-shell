"""Multi-shell alias renderer.

Reads config/aliases.toml and outputs shell-specific alias files.
"""

import re
from pathlib import Path

from .config import load_aliases
from .utils import (
    atomic_write_text,
    escape_nushell_path,
    get_project_dir,
    log_debug,
    log_success,
    log_warn,
)

# ── Public API ─────────────────────────────────────────────────────


def _load_alias_groups(project_dir: Path) -> tuple[dict, dict]:
    """Load aliases.toml and split it into (alias_groups, wrappers).

    Validates that every non-wrapper section is a table.
    """
    aliases_config = load_aliases(project_dir)
    wrappers = aliases_config.get("wrappers", {})
    alias_groups = {k: v for k, v in aliases_config.items() if k != "wrappers"}
    for group_name, group_aliases in alias_groups.items():
        if not isinstance(group_aliases, dict):
            raise SystemExit(
                f"[fail] aliases.toml: section '{group_name}' must be a table, "
                f"got {type(group_aliases).__name__}"
            )
    return alias_groups, wrappers


def _render_for_shell(shell: str, alias_groups: dict, wrappers: dict, project_dir: Path) -> str:
    """Render alias file content for *shell*."""
    if shell == "nushell":
        return render_nushell(alias_groups, wrappers, project_dir=project_dir)
    if shell == "xonsh":
        return render_xonsh(alias_groups, wrappers, project_dir=project_dir)
    raise ValueError(f"Unsupported shell: {shell}")


def render_content(shell: str, project_dir: Path | None = None) -> str:
    """Render alias file content for *shell* without writing it (`render --preview`)."""
    root = project_dir or get_project_dir()
    alias_groups, wrappers = _load_alias_groups(root)
    return _render_for_shell(shell, alias_groups, wrappers, root)


def render_aliases(
    shell: str, output_path: Path | None = None, project_dir: Path | None = None
) -> int:
    """Render aliases for *shell* and write to *output_path*.

    Returns the number of aliases rendered.
    """
    root = project_dir or get_project_dir()
    alias_groups, wrappers = _load_alias_groups(root)
    content = _render_for_shell(shell, alias_groups, wrappers, root)

    default_output = {
        "nushell": root / "shells" / "nushell" / "aliases.nu",
        "xonsh": root / "shells" / "xonsh" / "aliases.xsh",
    }[shell]

    target = output_path or default_output
    alias_count = sum(len(v) for v in alias_groups.values() if isinstance(v, dict))
    log_debug(f"Rendering {alias_count} aliases for {shell} -> {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_text(target, content)
    except PermissionError:
        raise SystemExit(f"[fail] Permission denied writing {target}") from None

    log_success(f"Rendered {alias_count} aliases for {shell}")
    return alias_count


# ── Nushell renderer ──────────────────────────────────────────────


def render_nushell(aliases: dict, wrappers: dict, project_dir: Path | None = None) -> str:
    """Emit Nushell alias syntax from the aliases dict."""
    lines: list[str] = [
        "# Nushell Aliases -- generated from config/aliases.toml, DO NOT EDIT",
    ]

    for group_name, group_aliases in aliases.items():
        _reject_control_chars(group_name)
        lines.append("")
        lines.append(f"# {_title(group_name)}")

        for name, value in group_aliases.items():
            command, comment = _parse_alias_value(value, shell="nushell")
            if command is None:
                if isinstance(value, dict) and "xonsh" not in value and "xonsh_fn" not in value:
                    log_warn(f"Alias '{name}' has no 'command' or 'nushell' key -- skipped")
                continue
            _reject_control_chars(name, command, comment)
            _reject_unsafe_alias_name(name)
            comment_str = f"  # {comment}" if comment else ""
            lines.append(f"alias {name} = {command}{comment_str}")

    # Wrappers
    if wrappers:
        _validate_wrapper_fields(wrappers)
        lines.append("")
        lines.append("# Wrapper commands (fallback binaries)")

        root = project_dir or get_project_dir()
        wrappers_nu = escape_nushell_path(root / "shells" / "nushell" / "commands" / "wrappers.nu")
        lines.append(f'use "{wrappers_nu}" _run_wrapper')
        lines.append("")

        for wrapper_name, cfg in wrappers.items():
            preferred = cfg["preferred"]
            fallback = cfg["fallback"]
            # Nushell has no \` escape; a bare backtick inside "..." is already literal.
            error_msg = cfg["error"].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")

            lines.append(
                f"export def --wrapped {wrapper_name} [...args] "
                f'{{ _run_wrapper "{preferred}" "{fallback}" "{error_msg}" ...$args }}'
            )
            lines.append("")

    return "\n".join(lines) + "\n"


# ── Xonsh renderer ────────────────────────────────────────────────


def _alias_fn_names(project_dir: Path | None = None) -> list[str]:
    """The `xonsh_fn` values alias_fns.py actually defines, without the leading _."""
    root = project_dir or get_project_dir()
    source = (root / "shells" / "xonsh" / "commands" / "alias_fns.py").read_text(encoding="utf-8")
    return sorted(m.group(1) for m in re.finditer(r"^def _(\w+)\(", source, re.MULTILINE))


def render_xonsh(aliases: dict, wrappers: dict, project_dir: Path | None = None) -> str:
    """Emit xonsh alias syntax from the aliases dict."""
    fn_names = _alias_fn_names(project_dir)
    imported = ", ".join([f"_{fn}" for fn in fn_names] + ["make_wrapper"])
    lines: list[str] = [
        "# Xonsh Aliases -- generated from config/aliases.toml, DO NOT EDIT",
        "",
        "import os",
        "",
        f"from alias_fns import {imported}",
        "",
    ]

    wrapper_names = set(wrappers.keys()) if wrappers else set()

    # Wrappers first (so aliases can reference them for Debian bat/batcat, fd/fdfind)
    if wrappers:
        _validate_wrapper_fields(wrappers)
        lines.append("# Wrapper commands (fallback binaries)")
        lines.append("")

        for wrapper_name, cfg in wrappers.items():
            preferred = cfg["preferred"]
            fallback = cfg["fallback"]
            error_msg = cfg["error"]

            lines.append(
                f"aliases[{wrapper_name!r}] = make_wrapper("
                f"{preferred!r}, {fallback!r}, {error_msg!r})"
            )

        lines.append("")

    for group_name, group_aliases in aliases.items():
        _reject_control_chars(group_name)
        lines.append(f"# {_title(group_name)}")

        for name, value in group_aliases.items():
            command, comment = _parse_alias_value(value, shell="xonsh")
            _reject_control_chars(name, command or "", comment)
            _reject_unsafe_alias_name(name)
            comment_str = f"  # {comment}" if comment else ""

            if command is None:
                # xonsh_fn: reference function imported from alias_fns
                fn_name = value.get("xonsh_fn", "") if isinstance(value, dict) else ""
                if fn_name:
                    if not fn_name.isidentifier():
                        raise ValueError(
                            f"Alias '{name}' has invalid xonsh_fn: {fn_name!r}. "
                            f"Must be a valid Python identifier."
                        )
                    if fn_name not in fn_names:
                        # An un-imported name is a NameError that aborts the whole rc.
                        raise ValueError(
                            f"Alias '{name}' has unknown xonsh_fn: {fn_name!r}. "
                            f"alias_fns.py defines: {', '.join(fn_names)}"
                        )
                    lines.append(f"aliases[{name!r}] = _{fn_name}{comment_str}")
                elif isinstance(value, dict) and "nushell" not in value:
                    log_warn(
                        f"Alias '{name}' has no 'command', 'xonsh', or 'xonsh_fn' key -- skipped"
                    )
                continue

            # Navigation aliases: cd-based
            if command.startswith("cd "):
                target_dir = command[3:]
                lines.append(
                    f"aliases[{name!r}] = lambda args, stdin=None: "
                    f"os.chdir({target_dir!r}){comment_str}"
                )
            # Single-word command: reference wrapper if available, else list alias
            elif " " not in command:
                if command in wrapper_names:
                    lines.append(f"aliases[{name!r}] = aliases[{command!r}]{comment_str}")
                else:
                    lines.append(f"aliases[{name!r}] = [{command!r}]{comment_str}")
            # Multi-word command: string alias
            else:
                lines.append(f"aliases[{name!r}] = {command!r}{comment_str}")

        lines.append("")

    return "\n".join(lines) + "\n"


# ── Helpers ────────────────────────────────────────────────────────

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ALIAS_NAME_RE = re.compile(r"[A-Za-z0-9_.:+-]+")


def _reject_control_chars(name: str, *values: str) -> None:
    """Reject a newline in an alias name/command/comment.

    Each is written straight into a generated shell file, so a newline would end
    the line and turn the rest of the value into code that every shell runs.
    """
    for value in (name, *values):
        if _CONTROL_RE.search(value):
            raise ValueError(
                f"Alias {name!r} contains a control character in {value!r}. "
                f"Newlines and control characters are not allowed."
            )


def _reject_unsafe_alias_name(name: str) -> None:
    """An alias name lands unquoted in generated shell code, so keep it to a safe charset."""
    if not _ALIAS_NAME_RE.fullmatch(name):
        raise ValueError(
            f"{name!r} is not a valid alias name. Use letters, digits, and any of _ . : + - only."
        )


def _validate_wrapper_fields(wrappers: dict) -> None:
    """Validate wrapper names, required fields, and preferred/fallback values."""
    for name, cfg in wrappers.items():
        if not _IDENTIFIER_RE.match(name):
            raise ValueError(
                f"Wrapper name {name!r} is invalid. "
                f"Only alphanumeric characters, hyphens, and underscores are allowed."
            )
        for field in ("preferred", "fallback", "error"):
            if field not in cfg:
                raise ValueError(f"Wrapper '{name}' is missing required field: {field!r}.")
        for field in ("preferred", "fallback"):
            value = cfg[field]
            if not _IDENTIFIER_RE.match(value):
                raise ValueError(
                    f"Wrapper '{name}' has invalid {field}: {value!r}. "
                    f"Only alphanumeric characters, hyphens, and underscores are allowed."
                )


def _title(snake: str) -> str:
    """Convert snake_case section name to Title Case."""
    return snake.replace("_", " ").title()


def _parse_alias_value(value: str | dict, shell: str) -> tuple[str | None, str]:
    """Parse an alias value which can be a plain string or a dict with overrides.

    Returns (command, comment). command is None when the shell should use a
    special handler (e.g. xonsh_fn).
    """
    if isinstance(value, str):
        return value, ""

    # Dict form: may have 'command', 'nushell', 'xonsh', 'xonsh_fn', 'comment'
    comment = value.get("comment", "")

    # Shell-specific override
    if shell in value:
        return value[shell], comment

    # xonsh function override
    if shell == "xonsh" and "xonsh_fn" in value:
        return None, comment

    # Generic command
    if "command" in value:
        return value["command"], comment

    return None, comment
