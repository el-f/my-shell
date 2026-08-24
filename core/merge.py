"""Configuration generation and deployment.

Implements the three-layer architecture:
  Layer 1: Base template (managed by my-shell, auto-updated)
  Layer 2: My-shell enhancements (managed, selectively applied)
  Layer 3: User customizations (never overwritten)
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    get_config_dir,
    is_command_group_enabled,
    is_integration_deferred,
    is_integration_enabled,
    load_settings,
)
from .mise import mise_shims_dir, resolve_command
from .plugins import generate_plugin_use_statements, load_plugin_list
from .registry import (
    INTEGRATION_TOOLS,
    OPTIONAL_TOOLS,
    XONTRIB_PACKAGES,
    XONTRIB_PACKAGES_WINDOWS,
)
from .render import render_aliases
from .utils import (
    _canonical,
    _is_path_under,
    atomic_write_text,
    escape_nushell_path,
    escape_python_path,
    get_home_dir,
    get_os,
    get_project_dir,
    guard_test_write,
    is_available,
    is_windows,
    log_debug,
    log_error,
    log_header,
    log_info,
    log_step,
    log_success,
    log_warn,
)


@dataclass
class DeployResult:
    """Structured data collected during a deploy operation."""

    shell: str
    config_dir: Path
    project_dir: Path
    version: str
    old_version: str | None
    files_created: list[str] = field(default_factory=list)
    files_preserved: list[str] = field(default_factory=list)
    alias_count: int = 0
    aliases_added: list[str] = field(default_factory=list)
    aliases_removed: list[str] = field(default_factory=list)
    plugin_count: int = 0  # nushell only
    xontrib_count: int = 0  # xonsh only
    integrations: dict[str, bool] = field(default_factory=dict)  # tool -> available
    first_deploy: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# Alias names as rendered by render.py; nushell names can be punctuation, xonsh excludes wrappers.
_ALIAS_NAME_RE = {
    "nushell": re.compile(r"(?m)^alias (\S+) ="),
    "xonsh": re.compile(r"(?m)^aliases\['([^']+)'\]\s*=(?!\s*make_wrapper\()"),
}


def _aliases_file(shell: str, project_dir: Path) -> Path:
    """Path render_aliases writes the rendered alias file to for *shell*."""
    name = "aliases.nu" if shell == "nushell" else "aliases.xsh"
    return project_dir / "shells" / shell / name


def _parse_alias_names(shell: str, path: Path) -> set[str]:
    """Alias names defined in a rendered alias file (empty set if absent)."""
    if not path.exists():
        return set()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(_ALIAS_NAME_RE[shell].findall(content))


def _get_version(project_dir: Path) -> str:
    """Get the commit datetime of HEAD as the my-shell version string."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except FileNotFoundError, subprocess.TimeoutExpired:
        return "unknown"


def _get_deployed_field(shell: str, config_dir: Path, field: str) -> str | None:
    """Read a MY_SHELL_<field> value from a deployed config file."""
    if shell == "nushell":
        config_path = config_dir / "config.nu"
        pattern = rf"\$env\.MY_SHELL_{field}\s*=\s*'([^']*)'"
    elif shell == "xonsh":
        # The xonsh marker lives in ~/.xonshrc, not in config_dir.
        config_path = get_home_dir() / ".xonshrc"
        pattern = rf"\$MY_SHELL_{field}\s*=\s*'([^']*)'"
    else:
        return None

    if not config_path.exists():
        return None

    try:
        content = config_path.read_text(encoding="utf-8")
        match = re.search(pattern, content)
        return match.group(1) if match else None
    except OSError:
        log_debug(f"Could not read {config_path} for {field}")
        return None


_STUB_MARKER = "# my-shell:stub"

_SOURCED_PATH_RE = re.compile(
    r"""^\s*(?:source|use)\s+(?:'([^']+)'|"((?:[^"\\]|\\.)*)")""", re.MULTILINE
)


def _unescape_nu_double_quoted(value: str) -> str:
    """Undo the backslash escaping `escape_nushell_path` applies inside double quotes."""
    return re.sub(r"\\(.)", r"\1", value)


_XONSH_PATH_RE = re.compile(r"^\s*\w+\s*=\s*Path\('((?:[^'\\]|\\.)*)'\)", re.MULTILINE)


def _xonsh_targets_intact() -> bool:
    """True when every file the deployed ~/.xonshrc loads is still on disk.

    Each `source` there is guarded by .exists(), so a moved repo degrades silently
    instead of erroring -- the hash marker alone would keep reporting "up to date".
    """
    xonshrc = get_home_dir() / ".xonshrc"
    try:
        content = xonshrc.read_text(encoding="utf-8")
    except OSError:
        return False

    for match in _XONSH_PATH_RE.finditer(content):
        sourced = Path(re.sub(r"\\(.)", r"\1", match.group(1)))
        if not sourced.exists():
            log_debug(f"xonsh: deployed config loads a missing file: {sourced}")
            return False
    return True


def _deploy_targets_intact(shell: str, config_dir: Path) -> bool:
    """True when everything the deployed config needs is still on disk.

    The hash marker alone is not proof the last deploy finished: an interrupted
    run, or a file deleted afterwards, leaves the marker with missing targets.
    """
    if shell == "xonsh":
        return _xonsh_targets_intact()

    config_nu = config_dir / "config.nu"
    if not config_nu.exists() or not (config_dir / "env.nu").exists():
        return False

    try:
        content = config_nu.read_text(encoding="utf-8")
    except OSError:
        return False

    for match in _SOURCED_PATH_RE.finditer(content):
        single, double = match.group(1), match.group(2)
        sourced = Path(single if single else _unescape_nu_double_quoted(double))
        if not sourced.exists():
            log_debug(f"nushell: deployed config sources a missing file: {sourced}")
            return False
    return True


def _overwritten_targets(shell: str, config_dir: Path) -> list[Path]:
    """The files deploy replaces outright. user-custom files are never touched."""
    if shell == "xonsh":
        return [get_home_dir() / ".xonshrc"]
    return [config_dir / "config.nu", config_dir / "env.nu"]


_DOTFILE_MANAGERS = (
    ("chezmoi", ["chezmoi", "managed", "--path-style", "absolute"], False),
    ("yadm", ["yadm", "ls-files", "--full-name"], True),
)


def _dotfile_manager_claims() -> dict[str, str]:
    """Canonical path -> name of the installed dotfile manager that owns it."""
    claims: dict[str, str] = {}
    for name, argv, home_relative in _DOTFILE_MANAGERS:
        if not shutil.which(name):
            continue
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log_debug(f"{name} is installed but did not answer ({exc}); skipping its claims")
            continue
        if proc.returncode != 0:
            log_debug(f"{name} exited {proc.returncode}; skipping its claims")
            continue
        home = get_home_dir()
        for line in proc.stdout.splitlines():
            entry = line.strip()
            if entry:
                claims.setdefault(_canonical(home / entry if home_relative else entry), name)
    return claims


def foreign_owner_warnings(shell: str, config_dir: Path) -> list[str]:
    """Report files deploy overwrites that something else already owns.

    Two ways this goes wrong silently. A symlinked target is written *through*, so
    my-shell's output lands in someone else's repo. A chezmoi- or yadm-managed
    target keeps my-shell's output only until that tool's next apply reverts it.
    """
    claims = _dotfile_manager_claims()
    warnings: list[str] = []
    for target in _overwritten_targets(shell, config_dir):
        if target.is_symlink():
            warnings.append(
                f"{target} is a symlink to {os.readlink(target)} -- my-shell writes through it"
            )
        elif owner := claims.get(_canonical(target)):
            warnings.append(
                f"{target} is managed by {owner} -- its next apply reverts my-shell's output"
            )
    return warnings


def _get_deployed_version(shell: str, config_dir: Path) -> str | None:
    """Read the MY_SHELL_VERSION from an existing deployed config file."""
    return _get_deployed_field(shell, config_dir, "VERSION")


_HASH_DIRS = ("shells", "config", "core")
_HASH_EXCLUDE_SUFFIXES = {".pyc"}
_HASH_EXCLUDE_NAMES = {"__pycache__", "aliases.nu", "aliases.xsh"}

# (settings group, module under shells/xonsh/commands, [(function, alias)])
XONSH_COMMAND_GROUPS: tuple[tuple[str, str, list[tuple[str, str]]], ...] = (
    ("navigation", "navigation", [("_fj", "fj"), ("_y", "y")]),
    ("fuzzy", "fuzzy", [("_fx", "fx"), ("_fh", "fh"), ("_fk", "fk")]),
    (
        "utilities",
        "utilities",
        [("_port", "port"), ("_clip", "clip"), ("_trash", "trash"), ("_pq", "pq")],
    ),
    ("sysinfo", "sysinfo", [("_sysinfo", "sysinfo")]),
    ("commands", "commands", [("_commands", "commands")]),
)


def expected_xonsh_aliases(settings: dict) -> list[str]:
    """Alias names the deployed rc must register, given the enabled command groups."""
    return [
        alias
        for group, _module, funcs in XONSH_COMMAND_GROUPS
        if is_command_group_enabled(settings, group)
        for _fn, alias in funcs
    ]


def _hash_source_files(project_dir: Path, subdirs: tuple[str, ...]) -> str:
    """SHA-256 (first 12 hex) over source files under *subdirs*.

    Excludes __pycache__, .pyc, and generated alias files. Files are sorted
    globally across the given subdirs so the digest is order-stable.
    """
    hasher = hashlib.sha256()
    files: list[str] = []
    for sub in subdirs:
        dir_path = project_dir / sub
        if not dir_path.is_dir():
            continue
        for path in dir_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in _HASH_EXCLUDE_SUFFIXES:
                continue
            if path.name in _HASH_EXCLUDE_NAMES:
                continue
            if "__pycache__" in path.parts:
                continue
            # Normalise to forward slashes for cross-platform consistency
            files.append(path.relative_to(project_dir).as_posix())
    for rel in sorted(files):
        hasher.update(rel.encode())
        hasher.update((project_dir / rel).read_bytes())
    return hasher.hexdigest()[:12]


def _compute_project_hash(project_dir: Path) -> str:
    """Combined hash of all source dirs (shells/, config/, core/).

    This is the source of truth for stale/skip detection.
    """
    return _hash_source_files(project_dir, _HASH_DIRS)


def _compute_category_hashes(project_dir: Path) -> dict[str, str]:
    """Per-category source hashes, keyed CONFIG/SHELLS/CORE.

    Additive metadata so a skip/redeploy can name which category changed. The
    combined hash above still decides whether to redeploy.
    """
    return {sub.upper(): _hash_source_files(project_dir, (sub,)) for sub in _HASH_DIRS}


def _get_deployed_hash(shell: str, config_dir: Path) -> str | None:
    """Read the MY_SHELL_HASH from an existing deployed config file."""
    return _get_deployed_field(shell, config_dir, "HASH")


def _clear_deployed_hash(shell: str, config_dir: Path) -> None:
    """Blank the deployed hash so the next deploy redeploys instead of skipping."""
    config_path = config_dir / "config.nu" if shell == "nushell" else get_home_dir() / ".xonshrc"
    if not config_path.exists():
        return
    prefix = r"\$env\.MY_SHELL" if shell == "nushell" else r"\$MY_SHELL"
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return
    blanked = re.sub(rf"({prefix}_HASH[A-Z_]*\s*=\s*)'[^']*'", r"\1''", content)
    if blanked != content:
        atomic_write_text(config_path, blanked)


def _hash_env_lines(assign: str, project_hash: str, category_hashes: dict[str, str]) -> str:
    """Render the combined + per-category MY_SHELL_HASH assignments.

    *assign* is the shell's var prefix, e.g. "$env.MY_SHELL" (nushell) or
    "$MY_SHELL" (xonsh).
    """
    lines = [f"{assign}_HASH = '{project_hash}'"]
    lines += [f"{assign}_HASH_{cat} = '{h}'" for cat, h in category_hashes.items()]
    return "\n".join(lines)


def _get_deployed_category_hashes(shell: str, config_dir: Path) -> dict[str, str]:
    """Read the deployed per-category hashes. Empty if the deploy predates them."""
    found = {}
    for cat in (sub.upper() for sub in _HASH_DIRS):
        value = _get_deployed_field(shell, config_dir, f"HASH_{cat}")
        if value is not None:
            found[cat] = value
    return found


def _changed_categories(deployed: dict[str, str], current: dict[str, str]) -> list[str]:
    """Categories whose hash differs. Empty when the deploy has no category hashes."""
    if not deployed:
        return []  # old deploy -- caller falls back to a generic message
    return [cat.lower() for cat, cur in current.items() if deployed.get(cat) != cur]


@dataclass
class DeployStatus:
    """Deployed-vs-current comparison for one shell (drives `status`/`version`)."""

    shell: str
    deployed: bool
    deployed_version: str | None
    current_version: str
    deployed_hash: str | None
    current_hash: str

    @property
    def stale(self) -> bool:
        """Deployed, but the project changed since -- a re-deploy is needed."""
        return self.deployed and self.deployed_hash != self.current_hash


def get_deploy_status(
    shell: str,
    config_dir: Path | None = None,
    project_dir: Path | None = None,
    *,
    current_version: str | None = None,
    current_hash: str | None = None,
) -> DeployStatus:
    """Compare the deployed config against the current project for one shell.

    Pass current_version/current_hash to reuse a value already computed for a
    sibling shell instead of walking the tree again.
    """
    myshell = project_dir or get_project_dir()
    cfg = config_dir or get_config_dir(shell)
    cur_ver = current_version if current_version is not None else _get_version(myshell)
    cur_hash = current_hash if current_hash is not None else _compute_project_hash(myshell)
    deployed_version = _get_deployed_version(shell, cfg)
    return DeployStatus(
        shell=shell,
        deployed=deployed_version is not None,
        deployed_version=deployed_version,
        current_version=cur_ver,
        deployed_hash=_get_deployed_hash(shell, cfg),
        current_hash=cur_hash,
    )


def get_deploy_statuses(shells: list[str], project_dir: Path | None = None) -> list[DeployStatus]:
    """Deploy status for several shells, computing the current version+hash once."""
    myshell = project_dir or get_project_dir()
    cur_ver = _get_version(myshell)
    cur_hash = _compute_project_hash(myshell)
    return [
        get_deploy_status(s, project_dir=myshell, current_version=cur_ver, current_hash=cur_hash)
        for s in shells
    ]


def _render_nushell_mise_trust_block() -> str:
    """Set `MISE_TRUSTED_CONFIG_PATHS` so my-shell can trust its own repo config."""
    return (
        "let _trusted_paths = ($env.MISE_TRUSTED_CONFIG_PATHS? | default '' "
        "| split row (char esep) | where {|path| $path != '' })\n"
        "if ($env.MY_SHELL_DIR not-in $_trusted_paths) {\n"
        "    $env.MISE_TRUSTED_CONFIG_PATHS = "
        "($env.MISE_TRUSTED_CONFIG_PATHS? | default '' | split row (char esep) "
        "| where {|path| $path != '' } | append $env.MY_SHELL_DIR | str join (char esep))\n"
        "}\n"
    )


def _render_nushell_mise_shims_block() -> str:
    """Re-add mise shims after activation, because `mise activate` rewrites PATH."""
    return (
        "let _mise_shims = if ($env.MISE_DATA_DIR? | default '' | is-not-empty) "
        "{ ($env.MISE_DATA_DIR | path join 'shims') } "
        "else if (sys host | get name | str contains -i 'windows') "
        "{ ($env.LOCALAPPDATA | path join 'mise' 'shims') } "
        "else { ($env.HOME | path join '.local' 'share' 'mise' 'shims') }\n"
        "if ($_mise_shims | path exists) and ($_mise_shims not-in ($env.PATH | default [])) "
        "{ $env.PATH = ($env.PATH | prepend $_mise_shims) }\n"
    )


def _integration_missing_comment(tool_name: str) -> str:
    """Human-readable comment for a missing integration init file."""
    info = INTEGRATION_TOOLS[tool_name]
    detail = info.nushell_missing_status
    if info.setup_hint:
        detail = f"{detail} ({info.setup_hint})"
    return f"# {info.display_name}: {detail}\n"


def _integration_failed_comment(tool_name: str) -> str:
    """Human-readable comment for an integration init that failed to generate."""
    info = INTEGRATION_TOOLS[tool_name]
    hint = info.setup_hint or "fix the tool/runtime issue and re-run my-shell setup"
    return f"# {info.display_name}: init failed during deploy ({hint})\n"


def _write_nushell_integration_stub(
    tool_name: str,
    output_path: Path,
    *,
    failed: bool,
) -> None:
    """Write a deterministic Nushell init stub when a tool init cannot be generated."""
    comment = (
        _integration_failed_comment(tool_name)
        if failed
        else _integration_missing_comment(tool_name)
    )
    atomic_write_text(output_path, f"# Generated by my-shell\n{_STUB_MARKER}\n{comment}")
    log_success(f"Created placeholder: {output_path}")


def _stubbed_integrations_now_available(config_dir: Path, project_dir: Path) -> list[str]:
    """Enabled integrations still stubbed on disk whose tool is now installed.

    Their init never regenerates on its own: installing the tool does not change
    the source hash, so the deploy would skip and leave the stub forever.
    """
    settings = load_settings(project_dir)
    ready = []
    for name, info in INTEGRATION_TOOLS.items():
        init_file = info.nushell_init_file
        if not init_file or not is_integration_enabled(settings, name):
            continue
        path = config_dir / init_file
        content = _read_text_or_none(path)
        if content and _STUB_MARKER in content and is_available(info.binary):
            ready.append(name)
    return ready


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _nushell_init_has_errors(output_path: Path, *, project_dir: Path | None = None) -> bool:
    """Parse-check a generated Nushell init file with `nu --ide-check`.

    Tools generate init code against their own idea of the nushell version;
    a mismatch (e.g. atuin emitting a flag a newer nushell removed) produces a
    file that runs the init command fine but errors on every shell startup.
    This catches that class before it reaches the user's prompt.

    Returns True only when nushell reports a parser/compile Error. If nushell is
    unavailable or the check itself cannot run, returns False -- we never discard
    a file we cannot prove is broken.
    """
    if not is_available("nu"):
        return False
    try:
        invocation = resolve_command(
            ["nu", "--ide-check", "100", str(output_path)], project_dir=project_dir
        )
        result = subprocess.run(
            invocation.args,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=invocation.cwd,
            env=invocation.env,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log_debug(f"nushell parse-check skipped for {output_path.name}: {e}")
        return False
    return '"severity":"Error"' in result.stdout


def _ensure_nushell_tool_init(
    tool_name: str,
    cmd: list[str],
    output_path: Path,
    *,
    project_dir: Path | None = None,
    post_process: list[tuple[str, str]] | None = None,
    regex_post_process: list[tuple[str, str]] | None = None,
    prepend_lines: list[str] | None = None,
    append_lines: list[str] | None = None,
    timeout: int = 30,
    warnings: list[str] | None = None,
) -> bool:
    """Generate a Nushell init file, falling back to a managed stub on failure."""
    generated = _generate_tool_init(
        tool_name,
        cmd,
        output_path,
        project_dir=project_dir,
        post_process=post_process,
        regex_post_process=regex_post_process,
        prepend_lines=prepend_lines,
        append_lines=append_lines,
        timeout=timeout,
        warnings=warnings,
    )
    if generated:
        if _nushell_init_has_errors(output_path, project_dir=project_dir):
            log_warn(
                f"{tool_name}: generated Nushell init has parse errors "
                "(tool/shell version mismatch) -- writing safe stub instead"
            )
            _write_nushell_integration_stub(tool_name, output_path, failed=True)
            return False
        return True

    _write_nushell_integration_stub(tool_name, output_path, failed=is_available(tool_name))
    return False


# ── Nushell config generation ─────────────────────────────────────


def generate_nushell_config(
    config_dir: Path | None = None,
    project_dir: Path | None = None,
    version: str | None = None,
    project_hash: str | None = None,
    category_hashes: dict[str, str] | None = None,
) -> str:
    """Generate config.nu content with literal paths (Nushell 0.108+ compatible)."""
    myshell_dir = project_dir or get_project_dir()
    cfg_dir = config_dir or get_config_dir("nushell")

    settings = load_settings(myshell_dir)

    config_template = escape_nushell_path(myshell_dir / "shells" / "nushell" / "config.nu.template")
    aliases_file = escape_nushell_path(myshell_dir / "shells" / "nushell" / "aliases.nu")
    user_custom = escape_nushell_path(cfg_dir / "user-custom.nu")
    escaped_myshell = escape_nushell_path(myshell_dir)

    command_modules: list[tuple[str, str]] = []
    _cmd_groups = [
        ("navigation", "navigation.nu"),
        ("fuzzy", "fuzzy.nu"),
        ("utilities", "utilities.nu"),
        ("sysinfo", "sysinfo.nu"),
        ("commands", "commands.nu"),
    ]
    for group_name, filename in _cmd_groups:
        if not is_command_group_enabled(settings, group_name):
            continue
        cmd_path = myshell_dir / "shells" / "nushell" / "commands" / filename
        if cmd_path.exists():
            command_modules.append((group_name, escape_nushell_path(cmd_path)))

    commands_section = "# Custom commands\n"
    for _group_name, escaped_path in command_modules:
        commands_section += f'use "{escaped_path}" *\n'

    mise_env_setup = (
        "# Trust the my-shell repo for mise lookups/hooks\n" + _render_nushell_mise_trust_block()
        if is_integration_enabled(settings, "mise")
        else ""
    )

    integration_sections = ""
    for tool_name, info in INTEGRATION_TOOLS.items():
        init_file = info.nushell_init_file
        init_path = escape_nushell_path(cfg_dir / init_file)
        has_init = (cfg_dir / init_file).exists()
        enabled = is_integration_enabled(settings, tool_name)
        # Nushell `source` runs at parse time, so a deferred load cannot export its commands.
        deferred_note = (
            " (defer ignored: Nushell sources at parse time)"
            if is_integration_deferred(settings, tool_name)
            else ""
        )

        if not enabled:
            integration_sections += f"# {info.display_name}: Disabled in settings\n"
        elif not has_init:
            integration_sections += _integration_missing_comment(tool_name)
        else:
            integration_sections += (
                f"# {info.display_name} {info.shell_comment_label}{deferred_note}\n"
                f'source "{init_path}"\n'
            )
            if tool_name == "mise":
                integration_sections += _render_nushell_mise_shims_block()

    plugin_section = generate_plugin_use_statements(myshell_dir)

    version = version or _get_version(myshell_dir)
    project_hash = project_hash or _compute_project_hash(myshell_dir)
    category_hashes = category_hashes or _compute_category_hashes(myshell_dir)
    hash_lines = _hash_env_lines("$env.MY_SHELL", project_hash, category_hashes)

    return f"""\
# Generated by my-shell -- edit {cfg_dir / "user-custom.nu"} instead

# Set my-shell directory
$env.MY_SHELL_DIR = "{escaped_myshell}"
$env.MY_SHELL_VERSION = '{version}'
{hash_lines}

{mise_env_setup}

# LAYER 1: Base Template
source "{config_template}"

# LAYER 2: My-Shell Enhancements

{commands_section}
# Aliases
source "{aliases_file}"

# Tool Integrations

{integration_sections}
# Nushell Plugins
{plugin_section}
# LAYER 3: User Customizations

try {{
    source "{user_custom}"
}}
"""


def generate_nushell_env(
    config_dir: Path | None = None,
    project_dir: Path | None = None,
    version: str | None = None,
    project_hash: str | None = None,
    category_hashes: dict[str, str] | None = None,
) -> str:
    """Generate env.nu content with literal paths."""
    myshell_dir = project_dir or get_project_dir()
    cfg_dir = config_dir or get_config_dir("nushell")

    env_template = escape_nushell_path(myshell_dir / "shells" / "nushell" / "env.nu.template")
    user_env = escape_nushell_path(cfg_dir / "user-env.nu")
    escaped_myshell = escape_nushell_path(myshell_dir)

    version = version or _get_version(myshell_dir)
    project_hash = project_hash or _compute_project_hash(myshell_dir)
    category_hashes = category_hashes or _compute_category_hashes(myshell_dir)
    hash_lines = _hash_env_lines("$env.MY_SHELL", project_hash, category_hashes)

    return f"""\
# Nushell Environment - Generated by my-shell

# Set my-shell directory
$env.MY_SHELL_DIR = "{escaped_myshell}"
$env.MY_SHELL_VERSION = '{version}'
{hash_lines}

# Source my-shell environment template
source "{env_template}"

# Carapace completion bridges
$env.CARAPACE_BRIDGES = 'zsh,fish,bash,inshellisense'

# User environment customizations
try {{
    source "{user_env}"
}}
"""


# ── Xonsh config generation ──────────────────────────────────────


def _xontrib_names(packages: list[str]) -> list[str]:
    """Derive xontrib load names from package names (drop the xontrib- prefix)."""
    return [p.removeprefix("xontrib-").replace("-", "_") for p in packages]


def generate_xonsh_config(
    config_dir: Path | None = None,
    project_dir: Path | None = None,
    version: str | None = None,
    project_hash: str | None = None,
    category_hashes: dict[str, str] | None = None,
) -> str:
    """Generate xonshrc content with the three-layer architecture."""
    myshell_dir = project_dir or get_project_dir()
    cfg_dir = config_dir or get_config_dir("xonsh")

    template_path = escape_python_path(myshell_dir / "shells" / "xonsh" / "xonshrc_base.xsh")
    aliases_path = escape_python_path(myshell_dir / "shells" / "xonsh" / "aliases.xsh")
    commands_dir = escape_python_path(myshell_dir / "shells" / "xonsh" / "commands")
    user_custom = escape_python_path(cfg_dir / "user-custom.xsh")
    escaped_myshell = escape_python_path(myshell_dir)

    version = version or _get_version(myshell_dir)
    project_hash = project_hash or _compute_project_hash(myshell_dir)
    category_hashes = category_hashes or _compute_category_hashes(myshell_dir)
    hash_lines = _hash_env_lines("$MY_SHELL", project_hash, category_hashes)

    settings = load_settings(myshell_dir)
    # Escape like every other interpolated value in this single-quoted-string context
    omp_theme = escape_python_path(settings["oh-my-posh"]["theme"])

    # Build command imports based on enable/disable settings
    command_imports: list[str] = []
    for group_name, module_name, funcs in XONSH_COMMAND_GROUPS:
        if not is_command_group_enabled(settings, group_name):
            continue
        imports = ", ".join(f[0] for f in funcs)
        aliases_lines = "\n    ".join(f"aliases['{f[1]}'] = {f[0]}" for f in funcs)
        command_imports.append(
            f"try:\n"
            f"    from {module_name} import {imports}\n"
            f"    {aliases_lines}\n"
            f"except ImportError as _import_err:\n"
            f"    print(f'my-shell: commands unavailable ({module_name}): {{_import_err}}', file=sys.stderr)"
        )
    commands_section = "\n\n".join(command_imports)

    mise_trust_setup = ""
    if is_integration_enabled(settings, "mise"):
        mise_trust_setup = (
            "# Trust the my-shell repo for mise lookups/hooks\n"
            "_my_shell_dir = os.environ.get('MY_SHELL_DIR', '')\n"
            "if not _my_shell_dir and '__xonsh__' in globals():\n"
            "    _my_shell_dir = __xonsh__.env.get('MY_SHELL_DIR', '')\n"
            "_mise_trusted = [path for path in os.environ.get('MISE_TRUSTED_CONFIG_PATHS', '').split(os.pathsep) if path]\n"
            "if _my_shell_dir and _my_shell_dir not in _mise_trusted:\n"
            "    _mise_trusted.append(_my_shell_dir)\n"
            "os.environ['MISE_TRUSTED_CONFIG_PATHS'] = os.pathsep.join(dict.fromkeys(_mise_trusted))\n"
            "$MISE_TRUSTED_CONFIG_PATHS = os.environ['MISE_TRUSTED_CONFIG_PATHS']\n"
        )

    integration_sections = ""
    temp_vars: list[str] = ["_tmpl", "_aliases_file", "_cmd_dir"]
    if mise_trust_setup:
        temp_vars.extend(("_my_shell_dir", "_mise_trusted"))

    for tool_name, info in INTEGRATION_TOOLS.items():
        init_path = escape_python_path(
            myshell_dir / "shells" / "xonsh" / "integrations" / info.xonsh_init_dir / "init.xsh"
        )
        var_name = f"_{tool_name.replace('-', '_')}_init"
        enabled = is_integration_enabled(settings, tool_name)
        deferred = is_integration_deferred(settings, tool_name)

        if not enabled:
            integration_sections += f"# {info.display_name}: Disabled in settings\n\n"
        elif deferred:
            integration_sections += (
                f"# {info.display_name} (deferred)\n"
                f"def _load_{tool_name.replace('-', '_')}(**kwargs):\n"
                f"    {var_name} = Path('{init_path}')\n"
                f"    if {var_name}.exists():\n"
                f"        source @({var_name})\n"
                f"    events.on_pre_prompt.remove(_load_{tool_name.replace('-', '_')})\n"
                f"events.on_pre_prompt(_load_{tool_name.replace('-', '_')})\n\n"
            )
        else:
            temp_vars.append(var_name)
            integration_sections += (
                f"# {info.display_name}\n"
                f"{var_name} = Path('{init_path}')\n"
                f"if {var_name}.exists():\n"
                f"    source @({var_name})\n\n"
            )

    temp_vars.append("_user_custom")
    cleanup = ", ".join(temp_vars)

    return f"""\
# Generated by my-shell -- edit {cfg_dir / "user-custom.xsh"} instead

import os
import sys
from pathlib import Path

# Set my-shell directory
$MY_SHELL_DIR = '{escaped_myshell}'
$MY_SHELL_VERSION = '{version}'
{hash_lines}
$MY_SHELL_OMP_THEME = '{omp_theme}'

{mise_trust_setup}

# LAYER 1: Base Template
_tmpl = Path('{template_path}')
if _tmpl.exists():
    source @(_tmpl)

# LAYER 2: My-Shell Enhancements

# Add commands to Python path
_cmd_dir = '{commands_dir}'
if _cmd_dir not in sys.path:
    sys.path.insert(0, _cmd_dir)

# Custom commands
{commands_section}

# Aliases
_aliases_file = Path('{aliases_path}')
if _aliases_file.exists():
    source @(_aliases_file)

# Suppress SyntaxWarning from tokenize_output (xontrib-output-search dependency)
import warnings as _warnings
_warnings.filterwarnings("ignore", message=".*invalid escape sequence.*", category=SyntaxWarning)
del _warnings

# Xontribs (essential extensions)
try:
    xontrib load {" ".join(_xontrib_names(XONTRIB_PACKAGES))}
except Exception as _xontrib_err:
    print(f"[my-shell] Warning: xontrib load failed: {{_xontrib_err}}", file=sys.stderr)

import platform as _plat
if _plat.system() == 'Windows':
    try:
        xontrib load {" ".join(_xontrib_names(XONTRIB_PACKAGES_WINDOWS))}
    except Exception as _xontrib_win_err:
        print(f"[my-shell] Warning: Windows xontrib load failed: {{_xontrib_win_err}}", file=sys.stderr)
del _plat

# Tool Integrations

{integration_sections}
# LAYER 3: User Customizations

_user_custom = Path('{user_custom}')
if _user_custom.exists():
    source @(_user_custom)

# Cleanup temp vars
del {cleanup}
"""


# ── Pre-flight and summary ────────────────────────────────────────


_CHECK = "\033[32m+\033[0m"
_CROSS = "\033[2m-\033[0m"


def _print_pre_flight(shell: str) -> dict[str, bool]:
    """Print pre-flight tool availability check. Returns tool status dict."""
    settings = load_settings()
    shell_bin = "nu" if shell == "nushell" else "xonsh"
    tools_to_check: dict[str, bool] = {shell_bin: is_available(shell_bin)}
    for name in INTEGRATION_TOOLS:
        if not is_integration_enabled(settings, name):
            continue  # Skip disabled integrations
        tools_to_check[name] = is_available(name)
    for tool in OPTIONAL_TOOLS:
        tools_to_check[tool] = is_available(tool)

    available = [t for t, ok in tools_to_check.items() if ok]
    missing = [t for t, ok in tools_to_check.items() if not ok]

    log_header(f"  Pre-flight check ({shell})")
    parts = []
    for tool, ok in tools_to_check.items():
        mark = _CHECK if ok else _CROSS
        parts.append(f"{tool} {mark}")
    log_info("  ".join(parts))
    if missing:
        log_warn(f"{len(available)} found, {len(missing)} missing")

    return tools_to_check


def _print_summary(result: DeployResult) -> None:
    """Print a polished post-deploy summary using Rich."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console(highlight=False)
    shell = result.shell
    shell_bin = "nu" if shell == "nushell" else "xonsh"
    lines: list[str] = []

    lines.append(f"[green]+[/] {shell} deployed successfully")
    if result.first_deploy:
        lines.append("[yellow]* First deploy -- welcome to my-shell![/]")
    lines.append("")

    lines.append(f"Config dir    {result.config_dir}")
    ver = result.version.rsplit(" ", 1)[0] if " " in result.version else result.version
    if result.old_version and result.old_version != result.version:
        old_ver = (
            result.old_version.rsplit(" ", 1)[0]
            if " " in result.old_version
            else result.old_version
        )
        lines.append(f"Version       {old_ver} -> {ver}")
    else:
        lines.append(f"Version       {ver}")
    lines.append("")

    alias_line = f"Rendered      {result.alias_count} aliases"
    delta = []
    if result.aliases_added:
        delta.append(f"[green]+{len(result.aliases_added)}[/]")
    if result.aliases_removed:
        delta.append(f"[red]-{len(result.aliases_removed)}[/]")
    if delta:
        alias_line += f"  ({' '.join(delta)})"
    lines.append(alias_line)
    if shell == "nushell":
        lines.append(f"Plugins       {result.plugin_count} loaded")
    else:
        lines.append(f"Xontribs      {result.xontrib_count} loaded")

    integration_parts = []
    for tool, ok in result.integrations.items():
        mark = "[green]+[/]" if ok else "[dim]-[/]"
        integration_parts.append(f"{tool} {mark}")
    if integration_parts:
        lines.append(f"Integrations  {'  '.join(integration_parts)}")
    lines.append("")

    if result.files_created:
        lines.append("[bold]Files created[/]")
        names = [Path(f).name for f in result.files_created]
        for i in range(0, len(names), 3):
            chunk = names[i : i + 3]
            lines.append(f"  {' '.join(chunk)}")
    if result.files_preserved:
        lines.append("[bold]Files preserved[/] [dim](your settings)[/]")
        names = [Path(f).name for f in result.files_preserved]
        lines.append(f"  {' '.join(names)}")
    lines.append("")

    lines.append("[bold]Next steps[/]")
    lines.append(f"  ->Start your shell: {shell_bin}")
    if shell == "nushell":
        lines.append(f"  ->Customize: edit {result.config_dir / 'user-custom.nu'}")
        lines.append("  ->Install plugins: uv run my-shell plugins setup")
    else:
        lines.append(f"  ->Customize: edit {result.config_dir / 'user-custom.xsh'}")

    if result.warnings:
        lines.append("")
        lines.append("[bold yellow]Warnings[/]")
        for w in result.warnings:
            lines.append(f"  [yellow]![/] {w}")

    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            border_style="yellow" if result.warnings else "green",
            padding=(1, 2),
        )
    )


# ── Deployment ────────────────────────────────────────────────────


def _preflight_validate(project_dir: Path) -> None:
    """Validate config before we render or write anything.

    Warnings are printed but do not stop the deploy. Any hard error (bad TOML,
    unknown section, wrong type) stops the deploy with a field-path message, so
    the user fixes the source file instead of getting a broken render.
    """
    from .validate import validate_all

    errors = validate_all(project_dir)
    for w in (e for e in errors if e.is_warning):
        log_warn(str(w))
    hard = [e for e in errors if not e.is_warning]
    if hard:
        for e in hard:
            log_error(str(e))
        raise SystemExit(f"Config has {len(hard)} error(s). Fix the file(s) above, then re-run.")


def _verify_global_xonsh_runtime() -> None:
    """Refuse to claim success when the deployed ~/.xonshrc cannot reach a prompt."""
    from .doctor import _check_xonsh_runtime

    check = _check_xonsh_runtime()
    if check.status == "fail":
        raise RuntimeError(f"deployed xonsh verification failed: {check.message}")


def _verify_global_nushell_runtime(project_dir: Path, config_dir: Path) -> None:
    """Refuse to claim success when the deployed Nushell config is noisy or broken."""
    from .doctor import _check_nushell_prompt_contract, _check_nushell_runtime

    checks = [
        _check_nushell_runtime(config_dir),
        *_check_nushell_prompt_contract(project_dir, config_dir=config_dir),
    ]
    failures = [check for check in checks if check.status == "fail"]
    if failures:
        details = "; ".join(f"{check.name}: {check.message}" for check in failures)
        raise RuntimeError(f"deployed Nushell verification failed: {details}")


def deploy(
    shell: str,
    config_dir: Path | None = None,
    project_dir: Path | None = None,
    *,
    force: bool = False,
    validate: bool = True,
) -> DeployResult:
    """Deploy configuration for a specific shell.

    A multi-shell caller pre-flights once and passes validate=False, so the same
    global-config warnings are not printed per shell.
    """
    myshell_dir = project_dir or get_project_dir()
    cfg_dir = config_dir or get_config_dir(shell)
    guard_test_write(cfg_dir, f"deploy {shell} config")

    if validate:
        _preflight_validate(myshell_dir)

    old_version = _get_deployed_version(shell, cfg_dir)
    first_deploy = old_version is None

    new_version = _get_version(myshell_dir)
    new_hash = _compute_project_hash(myshell_dir)
    new_category_hashes = _compute_category_hashes(myshell_dir)
    old_hash = _get_deployed_hash(shell, cfg_dir)
    newly_installed = (
        _stubbed_integrations_now_available(cfg_dir, myshell_dir) if shell == "nushell" else []
    )
    if newly_installed:
        log_info(f"{shell}: re-deploying, now installed: {', '.join(newly_installed)}")
    if (
        not force
        and not newly_installed
        and old_hash
        and old_hash == new_hash
        and _deploy_targets_intact(shell, cfg_dir)
    ):
        if shell == "nushell" and config_dir is None:
            _verify_global_nushell_runtime(myshell_dir, cfg_dir)
        log_info(f"{shell}: config unchanged, skipping. Use --force to re-deploy.")
        return DeployResult(
            shell=shell,
            config_dir=cfg_dir,
            project_dir=myshell_dir,
            version=new_version,
            old_version=old_version,
        )

    # Name which category changed; needs per-category hashes from the previous deploy.
    if old_hash and old_hash != new_hash:
        changed = _changed_categories(
            _get_deployed_category_hashes(shell, cfg_dir), new_category_hashes
        )
        if changed:
            log_info(f"{shell}: {', '.join(changed)} changed since last deploy -- re-deploying.")

    log_step(f"Deploying {shell} configuration...")
    log_debug(f"Config dir: {cfg_dir}")
    log_debug(f"Project dir: {myshell_dir}")

    # Checked before the backup: copy2 follows a symlink the same way write_text does.
    deploy_warnings: list[str] = foreign_owner_warnings(shell, cfg_dir)
    for warning in deploy_warnings:
        log_warn(warning)

    from .backup import backup_before_deploy

    backup_before_deploy(shell, cfg_dir, myshell_dir)

    tool_status = _print_pre_flight(shell)

    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot alias names before deploy re-renders them.
    aliases_path = _aliases_file(shell, myshell_dir)
    aliases_before = _parse_alias_names(shell, aliases_path) if shell in _ALIAS_NAME_RE else set()

    if shell == "nushell":
        files_created, files_preserved, alias_count, plugin_count = _deploy_nushell(
            cfg_dir,
            myshell_dir,
            version=new_version,
            project_hash=new_hash,
            category_hashes=new_category_hashes,
            warnings=deploy_warnings,
        )
    elif shell == "xonsh":
        files_created, files_preserved, alias_count, xontrib_count = _deploy_xonsh(
            cfg_dir,
            myshell_dir,
            version=new_version,
            project_hash=new_hash,
            category_hashes=new_category_hashes,
        )
        plugin_count = 0
    else:
        raise ValueError(f"Unsupported shell: {shell}")

    if config_dir is None:
        try:
            if shell == "nushell":
                _verify_global_nushell_runtime(myshell_dir, cfg_dir)
            else:
                _verify_global_xonsh_runtime()
        except RuntimeError:
            # The hash is written inside the deployed config, so it is already on disk.
            _clear_deployed_hash(shell, cfg_dir)
            raise

    aliases_after = _parse_alias_names(shell, aliases_path)

    _deploy_delta_gitconfig(myshell_dir)

    from .fonts import ensure_nerd_font

    ensure_nerd_font(myshell_dir)

    if old_version and old_version != new_version:
        log_success(f"Version: {old_version} -> {new_version}")
    elif old_version:
        log_success(f"Version: {new_version} (unchanged)")
    else:
        log_success(f"Version: {new_version} (first deploy)")

    log_success(f"Configuration deployed for {shell}")

    result = DeployResult(
        shell=shell,
        config_dir=cfg_dir,
        project_dir=myshell_dir,
        version=new_version,
        old_version=old_version,
        files_created=files_created,
        files_preserved=files_preserved,
        alias_count=alias_count,
        aliases_added=sorted(aliases_after - aliases_before),
        aliases_removed=sorted(aliases_before - aliases_after),
        plugin_count=plugin_count,
        xontrib_count=xontrib_count if shell == "xonsh" else 0,
        integrations={
            name: tool_status.get(name, False)
            for name in INTEGRATION_TOOLS
            if name in tool_status  # only include enabled tools
        },
        first_deploy=first_deploy,
        warnings=deploy_warnings,
    )

    _print_summary(result)

    return result


def _strip_transient_path_entries(path_value: str) -> str:
    """Drop the running venv's dirs from PATH -- tool init output bakes this PATH."""
    roots: list[str] = []
    if sys.prefix != sys.base_prefix:
        roots.append(sys.prefix)
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        roots.append(virtual_env)
    if not roots:
        return path_value
    canonical_roots = [_canonical(root) for root in roots]
    kept = [
        entry
        for entry in path_value.split(os.pathsep)
        if entry and not any(_is_path_under(_canonical(entry), root) for root in canonical_roots)
    ]
    return os.pathsep.join(kept)


def _render_tool_init(
    tool_name: str,
    cmd: list[str],
    *,
    project_dir: Path | None = None,
    post_process: list[tuple[str, str]] | None = None,
    regex_post_process: list[tuple[str, str]] | None = None,
    prepend_lines: list[str] | None = None,
    append_lines: list[str] | None = None,
    timeout: int = 30,
    warnings: list[str] | None = None,
) -> str | None:
    """Run a tool's init command and return its processed output (no write).

    Runs the tool's own `init` subprocess and applies the post-process, prepend,
    and append steps, but writes nothing. Returns None when the tool is missing
    or the init fails, so the real deploy (which writes) and the dry-run preview
    (which diffs) share one renderer.

    A regex_post_process miss is expected (format-conditional) and stays silent; a
    post_process miss is appended to *warnings* so the deploy summary can surface it.
    """
    log_debug(f"{tool_name}: {'found' if is_available(tool_name) else 'not found'}")
    if not is_available(tool_name):
        return None

    try:
        invocation = resolve_command(cmd, project_dir=project_dir)
        log_debug(
            f"{tool_name}: running {invocation.args!r}"
            + (f" cwd={invocation.cwd}" if invocation.cwd else "")
        )
        env = {**(invocation.env if invocation.env is not None else os.environ)}
        # Empty XDG_CONFIG_HOME is not equivalent to an unset variable for
        # every integration. Atuin treats it as a relative directory and writes
        # `./atuin/config.toml` into the invocation cwd while generating init
        # output. Preserve a real override, but remove the empty sentinel often
        # used by tests and launchers to request the platform default.
        if not env.get("XDG_CONFIG_HOME"):
            env.pop("XDG_CONFIG_HOME", None)
        path_key = next((k for k in env if k.upper() == "PATH"), None)
        if path_key:
            env[path_key] = _strip_transient_path_entries(env[path_key])
        result = subprocess.run(
            invocation.args,
            capture_output=True,
            text=True,
            # Tools emit UTF-8; the Windows ANSI code page mangles non-ASCII paths.
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=timeout,
            cwd=invocation.cwd,
            env=env,
        )
        output = result.stdout
        if post_process:
            for old, new in post_process:
                if old not in output:
                    # A pattern that matches nothing means the tool changed its init output.
                    message = (
                        f"{tool_name}: post-process pattern not found "
                        f"(tool output changed?): {old[:60]!r}"
                    )
                    log_warn(message)
                    if warnings is not None:
                        warnings.append(message)
                output = output.replace(old, new)
        if regex_post_process:
            for pattern, replacement in regex_post_process:
                output = re.sub(pattern, replacement, output)
        if prepend_lines:
            output = "\n".join(prepend_lines) + "\n\n" + output
        if append_lines:
            output += "\n" + "\n".join(append_lines) + "\n"
        # Marker: uninstall only deletes files my-shell wrote.
        return f"# Generated by my-shell\n{output}"
    except subprocess.TimeoutExpired:
        log_error(f"{tool_name} init timed out after {timeout}s")
        return None
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"{tool_name} init returned non-zero exit code (may need initial setup): {e}"
        if stderr:
            detail += f"\n{stderr}"
        log_warn(detail)
        return None
    except FileNotFoundError as e:
        log_error(f"Failed to generate {tool_name} init: {e}")
        return None


def _generate_tool_init(
    tool_name: str,
    cmd: list[str],
    output_path: Path,
    *,
    project_dir: Path | None = None,
    post_process: list[tuple[str, str]] | None = None,
    regex_post_process: list[tuple[str, str]] | None = None,
    prepend_lines: list[str] | None = None,
    append_lines: list[str] | None = None,
    timeout: int = 30,
    warnings: list[str] | None = None,
) -> bool:
    """Render a tool init file and write it. Returns True on success."""
    output = _render_tool_init(
        tool_name,
        cmd,
        project_dir=project_dir,
        post_process=post_process,
        regex_post_process=regex_post_process,
        prepend_lines=prepend_lines,
        append_lines=append_lines,
        timeout=timeout,
        warnings=warnings,
    )
    if output is None:
        return False
    atomic_write_text(output_path, output)
    log_success(f"Created: {output_path}")
    return True


@dataclass
class ToolInitSpec:
    """One Nushell integration init file: how to generate it and where it goes.

    Shared by the real deploy (which writes via _ensure_nushell_tool_init) and
    the dry-run preview (which renders via _render_tool_init and diffs).
    """

    tool_name: str
    cmd: list[str]
    output_path: Path
    post_process: list[tuple[str, str]] | None = None
    # (pattern, replacement) rewrites; a non-matching pattern is older output, not an error.
    regex_post_process: list[tuple[str, str]] | None = None
    prepend_lines: list[str] | None = None
    append_lines: list[str] | None = None


def _nushell_tool_init_specs(
    config_dir: Path, project_dir: Path, settings: dict
) -> list[ToolInitSpec]:
    """Init specs for every enabled Nushell integration, in deploy order."""
    specs: list[ToolInitSpec] = []

    if is_integration_enabled(settings, "oh-my-posh"):
        omp_theme_name = settings["oh-my-posh"]["theme"]
        theme_path = (
            project_dir
            / "shells"
            / "shared"
            / "oh-my-posh"
            / "themes"
            / f"{omp_theme_name}.omp.json"
        )
        theme = str(theme_path) if theme_path.exists() else omp_theme_name
        # Forward slashes for Nushell; escape any double-quote for the "--config=..." strings
        theme_nu = theme.replace("\\", "/").replace('"', '\\"')
        # oh-my-posh changed the expression from `$execution_time < 0` to
        # `$no_status` in v30. Remove the entire argument independent of the
        # expression so the first prompt renders its status segment either way.
        omp_regex_rewrites = [
            (r'(?m)^\s*\$"--no-status=[^"]*"\r?\n?', ""),
            (
                r"(?m)^(\s*)\$clear = \(history \| is-empty\) or "
                r'\(\(history \| last 1 \| get 0\.command\) == "clear"\)\s*$',
                r"\g<1>let _history = (history | last 1)\n"
                r"\g<1>$clear = if (($_history | length) == 0) { true } "
                r'else { (($_history | last 1 | get 0.command) == "clear") }',
            ),
            (
                r"match \$env\.CMD_DURATION_MS \{",
                'match ($env.CMD_DURATION_MS? | default "0823") {',
            ),
            (
                r"(?m)^(\s*)if\s+\$env\.CMD_DURATION_MS\s*!=\s*['\"]0823['\"]\s*\{\s*$",
                r"\g<1>if (($env.CMD_DURATION_MS? | default 823 | into int) != 823) {",
            ),
            (
                r"(?m)^(\s*)\$execution_time\s*=\s*\$env\.CMD_DURATION_MS\s*$",
                r"\g<1>$execution_time = ($env.CMD_DURATION_MS? | default 823 | into int)",
            ),
        ]
        shims = mise_shims_dir()
        omp_shim = next(
            (p for p in (shims / "oh-my-posh.exe", shims / "oh-my-posh") if p.exists()),
            None,
        )
        if omp_shim:
            omp_regex_rewrites.append(
                (
                    r'(?i)"[^"]*/mise/installs/[^"]*?oh-my-posh(?:\.exe)?"',
                    f'"{omp_shim.as_posix()}"',
                )
            )
        specs.append(
            ToolInitSpec(
                "oh-my-posh",
                ["oh-my-posh", "init", "nu", "--print", "--config", theme],
                config_dir / "oh-my-posh.nu",
                # omp bakes the exe path it was invoked as; a mise installs path dies on every omp upgrade, the shim path never moves
                regex_post_process=omp_regex_rewrites,
                prepend_lines=[
                    "# my-shell: prevent double-init from standalone oh-my-posh vendor/autoload",
                    'let _vendor_omp = ($nu.default-config-dir | path join "vendor" "autoload" "oh-my-posh.nu")',
                    "if ($_vendor_omp | path exists) { rm $_vendor_omp }",
                ],
                post_process=[
                    # oh-my-posh v29+ caches by session id; --config at print time beats it
                    (
                        "--save-cache\n",
                        f'--save-cache\n            "--config={theme_nu}"\n',
                    ),
                    (
                        "print secondary\n",
                        f'print secondary\n        "--config={theme_nu}"\n',
                    ),
                ],
            )
        )

    if is_integration_enabled(settings, "zoxide"):
        specs.append(
            ToolInitSpec(
                "zoxide",
                ["zoxide", "init", "nushell"],
                config_dir / "zoxide.nu",
                append_lines=[
                    "# my-shell: also override cd with zoxide",
                    "alias cd = __zoxide_z",
                    "alias cdi = __zoxide_zi",
                ],
            )
        )

    if is_integration_enabled(settings, "atuin"):
        specs.append(
            ToolInitSpec(
                "atuin",
                ["atuin", "init", "nu"],
                config_dir / "atuin.nu",
                regex_post_process=[
                    # atuin names both its ctrl-r and up-arrow bindings "atuin"; nu drops one.
                    (
                        r"name: atuin\n(\s+modifier: none\n\s+keycode: up\b)",
                        r"name: atuin_up\1",
                    ),
                ],
            )
        )

    if is_integration_enabled(settings, "carapace"):
        specs.append(
            ToolInitSpec(
                "carapace", ["carapace", "_carapace", "nushell"], config_dir / "carapace.nu"
            )
        )

    if is_integration_enabled(settings, "mise"):
        specs.append(
            ToolInitSpec(
                "mise",
                ["mise", "activate", "nu"],
                config_dir / "mise.nu",
                post_process=[
                    (
                        "  add-hook hooks.pre_prompt $mise_hook\n",
                        "  # my-shell: only refresh mise on directory changes\n",
                    ),
                ],
                # mise bakes deploy-day PATH (raw string in 2026.7, set,PATH CSV row in 2026.8); shims + startup PWD hook cover startup
                regex_post_process=[
                    # mise 2026.8 still emits a Nushell command deprecated in
                    # nu 0.114. Keep generated startup files warning-free.
                    (r"\bstr upcase\b", "str uppercase"),
                    (r"(?m)^\s*\$env\.PATH = r#'.*'#\r?\n", ""),
                    (r"(?m)^(\s*')?set,PATH,.*\r?\n", r"\1"),
                ],
                append_lines=[
                    "# my-shell: mise >= 2026.7 assigns $env.PATH as one raw string; nu < 0.114 keeps it a string",
                    "export-env {",
                    '  if ($env.PATH | describe) == "string" {',
                    "    $env.PATH = ($env.PATH | split row (char esep))",
                    "  }",
                    "}",
                ],
            )
        )

    return specs


def _deploy_nushell(
    config_dir: Path,
    project_dir: Path,
    version: str | None = None,
    project_hash: str | None = None,
    category_hashes: dict[str, str] | None = None,
    warnings: list[str] | None = None,
) -> tuple[list[str], list[str], int, int]:
    """Deploy Nushell configuration files.

    Returns (files_created, files_preserved, alias_count, plugin_count).
    """
    files_created: list[str] = []
    files_preserved: list[str] = []

    alias_count = render_aliases("nushell", project_dir=project_dir)

    settings = load_settings(project_dir)

    for spec in _nushell_tool_init_specs(config_dir, project_dir, settings):
        _ensure_nushell_tool_init(
            spec.tool_name,
            spec.cmd,
            spec.output_path,
            project_dir=project_dir,
            post_process=spec.post_process,
            regex_post_process=spec.regex_post_process,
            prepend_lines=spec.prepend_lines,
            append_lines=spec.append_lines,
            warnings=warnings,
        )
        files_created.append(str(spec.output_path))

    # oh-my-posh 29.x writes here directly, causing double-init
    vendor_omp = config_dir / "vendor" / "autoload" / "oh-my-posh.nu"
    if vendor_omp.exists():
        vendor_omp.unlink()
        log_step("Removed vendor/autoload/oh-my-posh.nu (prevents double-init)")

    # Clean up init files for disabled integrations (e.g. after profile switch)
    for tool_name, info in INTEGRATION_TOOLS.items():
        if not is_integration_enabled(settings, tool_name):
            orphan = config_dir / info.nushell_init_file
            if orphan.exists():
                orphan.unlink()
                log_step(f"Removed {info.nushell_init_file} (disabled in settings)")

    env_content = generate_nushell_env(
        config_dir,
        project_dir,
        version=version,
        project_hash=project_hash,
        category_hashes=category_hashes,
    )
    env_path = config_dir / "env.nu"
    atomic_write_text(env_path, env_content)
    log_success(f"Created: {env_path}")
    files_created.append(str(env_path))

    for placeholder_name, placeholder_content in [
        (
            "user-env.nu",
            "# Your custom Nushell environment settings go here\n"
            "# This file will never be overwritten by my-shell updates\n",
        ),
        (
            "user-custom.nu",
            "# Your custom Nushell configurations go here\n"
            "# This file will never be overwritten by my-shell updates\n",
        ),
    ]:
        placeholder_path = config_dir / placeholder_name
        if placeholder_path.exists():
            files_preserved.append(str(placeholder_path))
        else:
            _create_placeholder(placeholder_path, placeholder_content)
            files_created.append(str(placeholder_path))

    # Written last: it carries the hash an interrupted deploy must not record.
    config_content = generate_nushell_config(
        config_dir,
        project_dir,
        version=version,
        project_hash=project_hash,
        category_hashes=category_hashes,
    )
    config_path = config_dir / "config.nu"
    atomic_write_text(config_path, config_content)
    log_success(f"Created: {config_path}")
    files_created.append(str(config_path))

    plugins = load_plugin_list(project_dir)
    from .plugins import is_plugin_installed

    plugin_count = sum(1 for name in plugins if is_plugin_installed(name))

    _warn_stale_config("nushell", config_dir)

    return files_created, files_preserved, alias_count, plugin_count


def _deploy_delta_gitconfig(project_dir: Path) -> None:
    """Configure git to use delta as its pager, when the user opted in.

    These writes land in the global ~/.gitconfig, outside the shell config
    my-shell manages, so `[git] manage_global_config` must be true first.
    """
    settings = load_settings(project_dir)
    if not settings.get("git", {}).get("manage_global_config", False):
        return
    if not is_available("delta"):
        return
    git_configs = [
        ["git", "config", "--global", "core.pager", "delta"],
        ["git", "config", "--global", "interactive.diffFilter", "delta --color-only"],
        ["git", "config", "--global", "delta.navigate", "true"],
        ["git", "config", "--global", "delta.side-by-side", "true"],
        ["git", "config", "--global", "merge.conflictStyle", "zdiff3"],
    ]
    try:
        for cmd in git_configs:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=10)
        log_success("Configured git to use delta as pager")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        log_error(f"Failed to configure delta git pager: {e}")


def _python_candidates() -> list[str]:
    """Return platform-appropriate Python executable names."""
    return ["python.exe", "python3.exe"] if is_windows() else ["python3", "python"]


def _find_xonsh_python() -> str | None:
    """Find the Python interpreter that has xonsh installed."""
    xonsh_path = shutil.which("xonsh")
    if not xonsh_path:
        return None
    # xonsh is a Python script; its sibling python/python3 shares the same env
    xonsh_dir = Path(xonsh_path).parent
    for name in _python_candidates():
        candidate = xonsh_dir / name
        if candidate.exists():
            return str(candidate)
    # Windows global install: Scripts/xonsh.exe, python.exe in parent
    if is_windows():
        candidate = xonsh_dir.parent / "python.exe"
        if candidate.exists():
            return str(candidate)
    # uv tool install: trampoline in ~/.local/bin, real venv elsewhere
    result = _find_xonsh_python_uv()
    if result:
        return result
    return None


def _find_xonsh_python_uv() -> str | None:
    """Find xonsh's Python inside a uv tool environment."""
    uv_path = shutil.which("uv")
    if not uv_path:
        return None
    try:
        result = subprocess.run(
            ["uv", "tool", "dir"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired:
        return None
    tools_dir = Path(result.stdout.strip())
    scripts = "Scripts" if is_windows() else "bin"
    for name in _python_candidates():
        candidate = tools_dir / "xonsh" / scripts / name
        if candidate.exists():
            return str(candidate)
    return None


def _install_xontribs() -> int:
    """Install xontrib packages into xonsh's Python environment.

    Returns the number of packages installed -- 0 when xonsh/python is missing
    or the install fails, so the deploy summary never claims installs that did
    not happen.
    """
    python = _find_xonsh_python()
    if not python:
        log_error("xonsh not found in PATH -- skipping xontrib install")
        return 0

    packages = list(XONTRIB_PACKAGES)
    if is_windows():
        packages.extend(XONTRIB_PACKAGES_WINDOWS)

    # Prefer `uv pip install --python` for uv tool environments (no pip inside venv)
    uv_python = _find_xonsh_python_uv()
    if uv_python and is_available("uv"):
        cmd = ["uv", "pip", "install", "--quiet", "--python", uv_python, *packages]
    else:
        cmd = [python, "-m", "pip", "install", "--quiet", *packages]

    log_step("Installing xontrib packages...")
    try:
        subprocess.run(cmd, check=True, timeout=120)
        log_success(f"Installed xontribs: {', '.join(packages)}")
        return len(packages)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        log_error(f"Failed to install xontribs (non-fatal): {e}")
        return 0


def _deploy_xonsh(
    config_dir: Path,
    project_dir: Path,
    version: str | None = None,
    project_hash: str | None = None,
    category_hashes: dict[str, str] | None = None,
) -> tuple[list[str], list[str], int, int]:
    """Deploy xonsh configuration files.

    Returns (files_created, files_preserved, alias_count, xontrib_count).
    """
    files_created: list[str] = []
    files_preserved: list[str] = []

    alias_count = render_aliases("xonsh", project_dir=project_dir)

    # Install xontrib packages -- count reflects what actually installed
    xontrib_count = _install_xontribs()

    user_custom_path = config_dir / "user-custom.xsh"
    if user_custom_path.exists():
        files_preserved.append(str(user_custom_path))
    else:
        _create_placeholder(
            user_custom_path,
            "# Your custom xonsh configurations go here\n"
            "# This file will never be overwritten by my-shell updates\n",
        )
        files_created.append(str(user_custom_path))

    xonshrc_content = generate_xonsh_config(
        config_dir,
        project_dir,
        version=version,
        project_hash=project_hash,
        category_hashes=category_hashes,
    )

    # Written last: it carries the hash an interrupted deploy must not record.
    home = get_home_dir()
    xonshrc_path = home / ".xonshrc"
    guard_test_write(xonshrc_path, "deploy .xonshrc")
    atomic_write_text(xonshrc_path, xonshrc_content)
    log_success(f"Created: {xonshrc_path}")
    files_created.append(str(xonshrc_path))

    _warn_stale_config("xonsh", config_dir)

    return files_created, files_preserved, alias_count, xontrib_count


def _warn_stale_config(shell: str, active_dir: Path) -> None:
    """Warn if a stale config exists at a different platform-expected location.

    On macOS, nushell can read from either ~/Library/Application Support/nushell
    (native default) or ~/.config/nushell (XDG). If we deployed to one and the
    other still has config files, the user may get confused by stale configs.
    """
    if get_os() != "macos":
        return

    home = get_home_dir()
    sentinel = "config.nu" if shell == "nushell" else "user-custom.xsh"

    library_dir = home / "Library" / "Application Support" / shell
    xdg_dir = home / ".config" / shell

    if str(active_dir).startswith(str(library_dir)):
        stale_dir = xdg_dir
    elif str(active_dir).startswith(str(xdg_dir)):
        stale_dir = library_dir
    else:
        return

    stale_file = stale_dir / sentinel
    if stale_file.exists():
        log_warn(
            f"Stale {shell} config found at {stale_dir} -- "
            f"{shell} reads from {active_dir}. "
            f"Remove the stale dir to avoid confusion."
        )


def _create_placeholder(path: Path, content: str) -> None:
    """Create a placeholder file if it doesn't already exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        log_success(f"Created: {path}")
