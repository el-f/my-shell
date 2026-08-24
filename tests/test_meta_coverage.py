"""Meta tests that ensure test fixtures stay in sync with real configuration.

Pure Python introspection -- no subprocess calls, no shell binaries.
Catches drift between real config files and test fixture data,
and consistency contracts across the codebase.
"""

import ast
import re
import tempfile
import textwrap
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest


def _project_root() -> Path:
    """Walk up from __file__ to find pyproject.toml."""
    path = Path(__file__).resolve().parent
    while path != path.parent:
        if (path / "pyproject.toml").exists():
            return path
        path = path.parent
    raise RuntimeError("Could not find project root")


def _parse_xonsh_commands(path: Path) -> list[str]:
    """Find command functions with signature (args, stdin=None) in a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    commands = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("_"):
            continue
        params = node.args.args
        if len(params) < 2:
            continue
        if params[1].arg == "stdin":
            commands.append(node.name)
    return commands


_ERROR_PATH_PATTERNS = frozenset(
    {
        "_no_",
        "_missing_",
        "_invalid_",
        "_nonexistent_",
        "_not_found",
        "_out_of_range",
    }
)


def _has_happy_path_test(cmd_name: str, test_dir: Path) -> bool:
    """Check if a command has at least one non-error-path test."""
    cmd = cmd_name.lstrip("_")
    needle = f"_{cmd}_"

    for test_file in sorted(test_dir.glob("test_*.py")):
        tree = ast.parse(test_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if needle not in node.name:
                continue
            if not any(p in node.name for p in _ERROR_PATH_PATTERNS):
                return True
    return False


def _extract_string(node: ast.expr) -> str | None:
    """Extract a string value from an AST node, handling textwrap.dedent()."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Call)
        and node.args
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dedent"
        and isinstance(node.args[0], ast.Constant)
    ):
        return textwrap.dedent(node.args[0].value)
    return None


def _read_fixture_toml(conftest_path: Path, filename: str) -> str | None:
    """Extract inline TOML content written to `filename` in conftest.py."""
    tree = ast.parse(conftest_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "write_text"):
            continue
        if not node.args:
            continue
        # Match pattern: (something / "filename").write_text(...)
        value = func.value
        if (
            isinstance(value, ast.BinOp)
            and isinstance(value.right, ast.Constant)
            and value.right.value == filename
        ):
            return _extract_string(node.args[0])
    return None


def test_xonsh_commands_have_happy_path_tests():
    """Each xonsh command function should have at least one success-path test."""
    root = _project_root()
    commands_dir = root / "shells" / "xonsh" / "commands"
    test_dir = root / "tests"

    # Commands that genuinely can't be happy-path tested
    allowlist = {
        "_fh",  # Requires xonsh builtins (__xonsh__.history)
        "_clear_screen",  # Alias helper (tested via render + compile checks)
        "_platform_meminfo",  # Alias helper (tested via render + compile checks)
        "_platform_cpuinfo",  # Alias helper (tested via render + compile checks)
    }

    all_commands = []
    for py_file in sorted(commands_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        all_commands.extend(_parse_xonsh_commands(py_file))

    missing = []
    for cmd in all_commands:
        if cmd in allowlist:
            continue
        if not _has_happy_path_test(cmd, test_dir):
            missing.append(cmd)

    assert not missing, (
        f"Xonsh commands without happy-path tests: {missing}. "
        f"Add tests or update the allowlist with justification."
    )


def test_fixture_plugins_match_real_config():
    """Fixture plugins.toml must include all real plugins (or have them allowlisted)."""
    root = _project_root()

    with open(root / "config" / "plugins.toml", "rb") as f:
        real = tomllib.load(f)

    fixture_text = _read_fixture_toml(root / "tests" / "conftest.py", "plugins.toml")
    assert fixture_text is not None, "Could not extract plugins.toml from conftest.py"
    fixture = tomllib.loads(fixture_text)

    real_names = set(real.get("plugins", {}).keys())
    fixture_names = set(fixture.get("plugins", {}).keys())

    # Intentionally trimmed in fixture for faster tests
    trimmed = {
        "nu_plugin_query",
        "nu_plugin_clipboard",
        "nu_plugin_highlight",
        "nu_plugin_compress",
    }

    missing = real_names - fixture_names - trimmed
    assert not missing, f"Plugins in real config but not in fixture or allowlist: {missing}"

    unexpected = fixture_names - real_names
    assert not unexpected, f"Plugins in fixture but not in real config: {unexpected}"


def test_fixture_creates_all_nushell_command_modules():
    """conftest's tmp_project fixture must create every real nushell command module.

    The alias/plugin drift tests don't cover shell-dir drift: a new
    shells/nushell/commands/*.nu module could silently fall out of the fixture.
    """
    root = _project_root()
    # wrappers.nu is sourced via generated aliases.nu, not created standalone in the fixture.
    real = {f.name for f in (root / "shells" / "nushell" / "commands").glob("*.nu")} - {
        "wrappers.nu"
    }

    conftest = (root / "tests" / "conftest.py").read_text(encoding="utf-8")
    fixture = set(re.findall(r'"commands"\s*/\s*"([\w.]+\.nu)"', conftest))

    missing = real - fixture
    assert not missing, (
        f"nushell command modules on disk but missing from the tmp_project fixture: {missing}"
    )


def test_fixture_alias_groups_match_real_config():
    """Every alias group in real config must exist in the fixture and vice versa."""
    root = _project_root()

    with open(root / "config" / "aliases.toml", "rb") as f:
        real = tomllib.load(f)

    fixture_text = _read_fixture_toml(root / "tests" / "conftest.py", "aliases.toml")
    assert fixture_text is not None, "Could not extract aliases.toml from conftest.py"
    fixture = tomllib.loads(fixture_text)

    real_groups = set(real.keys())
    fixture_groups = set(fixture.keys())

    missing = real_groups - fixture_groups
    assert not missing, f"Alias groups in real config but not in fixture: {missing}"

    unexpected = fixture_groups - real_groups
    assert not unexpected, f"Alias groups in fixture but not in real config: {unexpected}"


# Structural / guardian tests


def _parse_nu_tool_list(path: Path) -> list[str]:
    """Extract tool names from nushell `let tools = [...]` block."""
    content = path.read_text(encoding="utf-8")
    match = re.search(r"let tools = \[(.*?)\]", content, re.DOTALL)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _parse_python_tool_list(path: Path) -> list[str]:
    """Extract tool names from a Python `tools = [...]` assignment using AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "tools"
                and isinstance(node.value, ast.List)
            ):
                return [
                    elt.value
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    return []


def _parse_nu_command_names(path: Path) -> list[str]:
    """Extract exported command names from a .nu file."""
    content = path.read_text(encoding="utf-8")
    return re.findall(r"export\s+def\s+(?:--\w+\s+)*(\w+)", content)


def _validate_nu_syntax(content: str) -> None:
    """Basic nushell syntax validation: balanced braces and export-def closures."""
    opens = content.count("{")
    closes = content.count("}")
    assert opens == closes, f"Unbalanced braces: {opens} '{{' vs {closes} '}}'"

    defs = re.findall(r"export def\s+", content)
    blocks = re.findall(r"export def\s+.*?\{", content, re.DOTALL)
    assert len(defs) == len(blocks), (
        f"Found {len(defs)} 'export def' but {len(blocks)} opening braces"
    )


def test_sysinfo_tool_lists_match_across_shells():
    """Sysinfo tool checklist must be identical in nushell and xonsh."""
    root = _project_root()
    nu_tools = _parse_nu_tool_list(root / "shells" / "nushell" / "commands" / "sysinfo.nu")
    xonsh_tools = _parse_python_tool_list(root / "shells" / "xonsh" / "commands" / "sysinfo.py")

    assert nu_tools, "Could not parse nushell tool list"
    assert xonsh_tools, "Could not parse xonsh tool list"
    assert nu_tools == xonsh_tools, (
        f"Sysinfo tool lists differ:\n  nushell: {nu_tools}\n  xonsh:  {xonsh_tools}"
    )


def test_rendered_aliases_syntax_is_valid():
    """Aliases rendered from real config must be syntactically valid."""
    from core.config import load_aliases
    from core.render import render_nushell, render_xonsh

    root = _project_root()
    aliases_config = load_aliases(root)
    wrappers = aliases_config.get("wrappers", {})
    alias_groups = {k: v for k, v in aliases_config.items() if k != "wrappers"}

    # Xonsh output must be valid Python
    xonsh_output = render_xonsh(alias_groups, wrappers)
    ast.parse(xonsh_output)

    # Nushell output must have balanced braces
    nu_output = render_nushell(alias_groups, wrappers)
    _validate_nu_syntax(nu_output)


def test_cross_shell_command_names_match():
    """Nushell and xonsh must export the same set of commands."""
    root = _project_root()
    nu_dir = root / "shells" / "nushell" / "commands"
    xonsh_dir = root / "shells" / "xonsh" / "commands"

    # Helper modules that provide shared utilities, not user-facing commands
    nu_helpers = {"wrappers.nu"}
    xonsh_helpers = {"alias_fns.py"}

    nu_commands: set[str] = set()
    for nu_file in sorted(nu_dir.glob("*.nu")):
        if nu_file.name in nu_helpers:
            continue
        module_name = nu_file.stem
        for name in _parse_nu_command_names(nu_file):
            # In nushell, `export def main` in a module → command name is the module name
            nu_commands.add(module_name if name == "main" else name)

    xonsh_commands: set[str] = set()
    for py_file in sorted(xonsh_dir.glob("*.py")):
        if py_file.name == "__init__.py" or py_file.name in xonsh_helpers:
            continue
        for cmd in _parse_xonsh_commands(py_file):
            xonsh_commands.add(cmd.lstrip("_"))

    assert nu_commands == xonsh_commands, (
        f"Command parity mismatch:\n"
        f"  nushell only: {nu_commands - xonsh_commands}\n"
        f"  xonsh only:   {xonsh_commands - nu_commands}"
    )


def test_generated_config_sources_all_nu_modules():
    """Every .nu module in commands/ must be sourced in generated nushell config."""
    from core.config import _DEFAULT_SETTINGS
    from core.merge import generate_nushell_config

    root = _project_root()
    # Modules sourced indirectly (e.g. via generated aliases.nu, not config.nu)
    indirect_modules = {"wrappers.nu"}
    nu_modules = sorted(
        f.name
        for f in (root / "shells" / "nushell" / "commands").glob("*.nu")
        if f.name not in indirect_modules
    )

    # Use default settings (all groups enabled) so local overrides don't mask modules
    default_settings = {k: dict(v) for k, v in _DEFAULT_SETTINGS.items()}
    with (
        tempfile.TemporaryDirectory() as tmp,
        patch("core.merge.load_settings", return_value=default_settings),
    ):
        config = generate_nushell_config(config_dir=Path(tmp), project_dir=root)

    for module in nu_modules:
        assert module in config, (
            f"Module '{module}' exists on disk but is not referenced in generated nushell config"
        )


def test_version_write_read_roundtrip():
    """Version written by generate_*_config must be extractable by _get_deployed_version."""
    from core.merge import (
        _get_deployed_version,
        _get_version,
        generate_nushell_config,
        generate_xonsh_config,
    )

    root = _project_root()
    version = _get_version(root)
    if version == "unknown":
        pytest.skip("git not available or no commits -- cannot test roundtrip")

    # Nushell roundtrip
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = generate_nushell_config(config_dir=tmp_path, project_dir=root)
        (tmp_path / "config.nu").write_text(config, encoding="utf-8")

        extracted = _get_deployed_version("nushell", tmp_path)
        assert extracted == version, (
            f"Nushell version roundtrip failed: wrote {version!r}, read {extracted!r}"
        )

    # Xonsh roundtrip: _get_deployed_version reads get_home_dir() / ".xonshrc"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = generate_xonsh_config(config_dir=tmp_path, project_dir=root)
        (tmp_path / ".xonshrc").write_text(config, encoding="utf-8")

        with patch("core.merge.get_home_dir", return_value=tmp_path):
            extracted = _get_deployed_version("xonsh", tmp_path)
        assert extracted == version, (
            f"Xonsh version roundtrip failed: wrote {version!r}, read {extracted!r}"
        )


# Core & config guardian tests


def _get_function_defs(path: Path) -> set[str]:
    """Get all function definition names from a Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _parse_nu_env_vars(path: Path) -> dict[str, str]:
    """Extract `$env.VAR = "value"` and `$env.VAR = ($env.VAR? | default "value")` from a nushell template."""
    content = path.read_text(encoding="utf-8")
    plain = re.findall(r'^\$env\.(\w+)\s*=\s*"([^"]*)"', content, re.MULTILINE)
    fallback = re.findall(
        r'^\$env\.(\w+)\s*=\s*\(\$env\.\1\?\s*\|\s*default\s+"([^"]*)"\)', content, re.MULTILINE
    )
    return dict(plain) | dict(fallback)


def _parse_xonsh_env_vars(path: Path) -> dict[str, str]:
    """Extract `$VAR = 'value'` and the `if 'VAR' not in ${...}` fallback from an xonsh template."""
    content = path.read_text(encoding="utf-8")
    plain = re.findall(r"^\$(\w+)\s*=\s*'([^']*)'", content, re.MULTILINE)
    fallback = re.findall(
        r"^if '(\w+)' not in \$\{\.\.\.\}:\n\s+\$\1\s*=\s*'([^']*)'", content, re.MULTILINE
    )
    return dict(plain) | dict(fallback)


def test_xonsh_config_imports_match_real_functions():
    """Hardcoded imports in generate_xonsh_config must reference real functions."""
    from core.merge import generate_xonsh_config

    root = _project_root()
    with tempfile.TemporaryDirectory() as tmp:
        config = generate_xonsh_config(config_dir=Path(tmp), project_dir=root)

    commands_dir = root / "shells" / "xonsh" / "commands"

    # Only consider modules that actually live in the commands directory
    command_modules = {f.stem for f in commands_dir.glob("*.py") if f.name != "__init__.py"}

    # Extract "from X import Y1, Y2" and "aliases['name'] = func" from generated config
    import_pattern = re.compile(r"from (\w+) import ([\w, ]+)")
    alias_pattern = re.compile(r"aliases\['(\w+)'\]\s*=\s*(\w+)")

    imports: dict[str, list[str]] = {}
    for match in import_pattern.finditer(config):
        module = match.group(1)
        if module not in command_modules:
            continue  # skip stdlib/third-party imports
        names = [n.strip() for n in match.group(2).split(",")]
        imports[module] = names

    aliases_map: dict[str, str] = {}
    for match in alias_pattern.finditer(config):
        aliases_map[match.group(1)] = match.group(2)

    assert imports, "No imports found in generated xonsh config"

    errors = []

    # Every imported name must exist as a function definition in the module
    imported_names: set[str] = set()
    for module, names in imports.items():
        module_path = commands_dir / f"{module}.py"
        if not module_path.exists():
            errors.append(f"Module {module}.py does not exist on disk")
            continue

        real_defs = _get_function_defs(module_path)
        for name in names:
            imported_names.add(name)
            if name not in real_defs:
                errors.append(f"{module}.{name} is imported but not defined")

    # Every alias target must be one of the imported names
    for alias_name, func_name in aliases_map.items():
        if func_name not in imported_names:
            errors.append(
                f"aliases['{alias_name}'] = {func_name}, but {func_name} was not imported"
            )

    assert not errors, "Import/alias mismatches in generated xonsh config:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def test_default_plugins_match_config_file():
    """DEFAULT_PLUGINS fallback dict must stay in sync with plugins.toml."""
    from core.plugins import DEFAULT_PLUGINS

    root = _project_root()
    with open(root / "config" / "plugins.toml", "rb") as f:
        toml_plugins = tomllib.load(f).get("plugins", {})

    # Keys must be identical
    default_keys = set(DEFAULT_PLUGINS.keys())
    toml_keys = set(toml_plugins.keys())
    assert default_keys == toml_keys, (
        f"Plugin key mismatch:\n"
        f"  DEFAULT_PLUGINS only: {default_keys - toml_keys}\n"
        f"  TOML only: {toml_keys - default_keys}"
    )

    # Values must match field-by-field
    errors = []
    for key in DEFAULT_PLUGINS:
        for field in ("crate", "description"):
            default_val = DEFAULT_PLUGINS[key].get(field)
            toml_val = toml_plugins[key].get(field)
            if default_val != toml_val:
                errors.append(
                    f"Plugin '{key}' field '{field}': "
                    f"DEFAULT_PLUGINS={default_val!r} vs TOML={toml_val!r}"
                )

    assert not errors, "DEFAULT_PLUGINS ↔ plugins.toml drift:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def test_command_group_names_parity():
    """The 5 command-group names must match across both generators + settings.toml.

    merge.py names the groups in the nushell generator AND in XONSH_COMMAND_GROUPS,
    and settings.toml [commands] lists them a third time. Renaming or adding a group
    in one place without the others silently mis-toggles it (is_command_group_enabled
    reads a key that no longer matches).
    """
    from core.merge import XONSH_COMMAND_GROUPS

    root = _project_root()
    with open(root / "config" / "settings.toml", "rb") as f:
        settings_groups = set(tomllib.load(f).get("commands", {}).keys())

    tree = ast.parse((root / "core" / "merge.py").read_text(encoding="utf-8"))
    cmd_group_sets = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_cmd_groups" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            names = {
                elt.elts[0].value
                for elt in node.value.elts
                if isinstance(elt, ast.Tuple) and elt.elts and isinstance(elt.elts[0], ast.Constant)
            }
            cmd_group_sets.append(names)

    assert len(cmd_group_sets) == 1, (
        f"expected 1 _cmd_groups in merge.py, found {len(cmd_group_sets)}"
    )
    nu_groups = cmd_group_sets[0]
    xonsh_groups = {group for group, _module, _funcs in XONSH_COMMAND_GROUPS}
    assert nu_groups == settings_groups, (
        f"nushell _cmd_groups vs settings.toml differ: {nu_groups ^ settings_groups}"
    )
    assert xonsh_groups == settings_groups, (
        f"XONSH_COMMAND_GROUPS vs settings.toml differ: {xonsh_groups ^ settings_groups}"
    )


def test_env_template_uses_normalized_os_name():
    """env.nu.template must detect the OS via $nu.os-info.name, not `sys host`.

    `sys host | get name` returns the distro pretty-name on Linux (e.g. "Ubuntu"),
    so `str contains -i "linux"` is dead there -- the XDG block never ran. The
    normalized $nu.os-info.name is "linux"/"macos"/"windows".
    """
    root = _project_root()
    tmpl = (root / "shells" / "nushell" / "env.nu.template").read_text(encoding="utf-8")
    assert "sys host | get name" not in tmpl, "use $nu.os-info.name, not sys host name"
    assert "$nu.os-info.name" in tmpl


def test_command_help_is_derived_not_hardcoded():
    """`commands` in BOTH shells must DERIVE its list, never keep a static table.

    A hand-maintained table drifts -- a new command silently missing from the
    help (that is how `pq` was once missed). Both shells now build the list from
    the actual command modules; this fitness function fails if either regresses
    to a hardcoded list. Runtime correctness of the derive is covered by the e2e
    tests in test_e2e_shell.py and the unit test in test_xonsh_commands.py.
    """
    root = _project_root()

    xonsh_src = (root / "shells" / "xonsh" / "commands" / "commands.py").read_text(encoding="utf-8")
    assert "_CUSTOM_COMMANDS" not in xonsh_src, "commands.py must not hardcode a command dict"
    assert "_iter_custom_commands" in xonsh_src, "commands.py should derive the list at runtime"

    nu_src = (root / "shells" / "nushell" / "commands" / "commands.nu").read_text(encoding="utf-8")
    assert "scope modules" in nu_src, "commands.nu should derive the list from scope modules"
    assert not re.search(r'\["\w+"\s+"', nu_src), (
        "commands.nu must not hardcode a [name description] table"
    )


def test_default_profiles_match_config_file():
    """_DEFAULT_PROFILES fallback dict must stay in sync with config/profiles.toml."""
    from core.profiles import _DEFAULT_PROFILES

    root = _project_root()
    with open(root / "config" / "profiles.toml", "rb") as f:
        toml_profiles = tomllib.load(f).get("profiles", {})

    assert toml_profiles == _DEFAULT_PROFILES, (
        "_DEFAULT_PROFILES (profiles.py) drifted from config/profiles.toml:\n"
        f"  code:  {_DEFAULT_PROFILES}\n"
        f"  toml:  {toml_profiles}"
    )


def test_detect_tools_match_sysinfo_tools():
    """Sysinfo lists every detected CLI tool except tools shown in dedicated rows."""
    from core.registry import DETECT_TOOLS

    root = _project_root()

    detect_tools = set(DETECT_TOOLS)
    sysinfo_tools = set(
        _parse_python_tool_list(root / "shells" / "xonsh" / "commands" / "sysinfo.py")
    )

    assert detect_tools, "DETECT_TOOLS registry is empty"
    assert sysinfo_tools, "Could not parse sysinfo.py tool list"

    assert sysinfo_tools == detect_tools - {"oh-my-posh", "carapace", "mise"}


_SHARED_ENV_VARS = frozenset(
    {
        "FZF_DEFAULT_OPTS",
        "BAT_THEME",
        "LESS",
        "CLICOLOR",
        "LANG",
        "PYTHONDONTWRITEBYTECODE",
    }
)


def test_env_var_values_match_across_shell_templates():
    """Shared env vars must have identical values in nushell and xonsh templates."""
    root = _project_root()

    nu_vars = _parse_nu_env_vars(root / "shells" / "nushell" / "env.nu.template")
    xonsh_vars = _parse_xonsh_env_vars(root / "shells" / "xonsh" / "xonshrc_base.xsh")

    errors = []
    for var in sorted(_SHARED_ENV_VARS):
        nu_val = nu_vars.get(var)
        xonsh_val = xonsh_vars.get(var)

        if nu_val is None:
            errors.append(f"${var} not found in env.nu.template")
        elif xonsh_val is None:
            errors.append(f"${var} not found in xonshrc_base.xsh")
        elif nu_val != xonsh_val:
            errors.append(f"${var}: nushell={nu_val!r} vs xonsh={xonsh_val!r}")

    assert not errors, "Env var parity violations:\n" + "\n".join(f"  - {e}" for e in errors)


def test_version_comes_from_package_metadata():
    """core.__version__ must not be a second hand-kept copy of the pyproject version."""
    import core

    root = _project_root()
    with open(root / "pyproject.toml", "rb") as f:
        declared = tomllib.load(f)["project"]["version"]

    assert core.__version__ == declared


def test_version_falls_back_when_the_package_is_not_installed():
    """A plain checkout that was never installed must still import."""
    import importlib
    import importlib.metadata
    from unittest.mock import patch

    import core

    try:
        with patch(
            "importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError
        ):
            importlib.reload(core)
        assert core.__version__ == "0+unknown"
    finally:
        importlib.reload(core)


def test_every_xonsh_integration_guards_its_load_time_exec():
    """An unguarded exec during rc load aborts the WHOLE ~/.xonshrc, silently
    disabling every integration after it plus the user's own user-custom.xsh."""
    import re

    from core.utils import get_project_dir

    init_files = sorted(
        (get_project_dir() / "shells" / "xonsh" / "integrations").glob("*/init.xsh")
    )
    assert init_files, "no xonsh integration inits found -- invariant would be vacuous"

    def _lines_with_indent(source: str, pattern: str) -> list[tuple[int, int]]:
        return [
            (m.start(), len(m.group("i")))
            for m in re.finditer(rf"^(?P<i>[ \t]*){pattern}", source, re.MULTILINE)
        ]

    unguarded = []
    for path in init_files:
        source = path.read_text(encoding="utf-8")
        defs = _lines_with_indent(source, r"def ")
        tries = _lines_with_indent(source, r"try:")
        for pos, indent in _lines_with_indent(source, r"(?:execx|exec)\("):
            # Inside a def, the failure hits that command, not shell startup.
            if any(d_pos < pos and d_indent < indent for d_pos, d_indent in defs):
                continue
            if not any(t_pos < pos and t_indent < indent for t_pos, t_indent in tries):
                unguarded.append(f"{path.parent.name}:{source[:pos].count(chr(10)) + 1}")
    assert not unguarded, f"load-time exec without try/except: {unguarded}"
