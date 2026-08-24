"""Tests for the alias renderer."""

import copy
import re
import tomllib
from pathlib import Path

import pytest

from core.config import load_aliases
from core.merge import generate_xonsh_config
from core.render import render_aliases, render_content, render_nushell, render_xonsh


def test_render_nushell_creates_file(tmp_project: Path):
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "# Nushell Aliases" in content
    assert "DO NOT EDIT" in content


def test_render_rejects_non_table_section(tmp_project: Path):
    """A scalar top-level key fails with a clear message, not an opaque AttributeError."""
    (tmp_project / "config" / "aliases.toml").write_text(
        'version = "1.0"\n\n[navigation]\n".." = "cd .."\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="must be a table"):
        render_aliases("nushell", project_dir=tmp_project)


def test_render_content_nushell_no_write(tmp_project: Path):
    """render_content returns nushell content without writing a file."""
    content = render_content("nushell", project_dir=tmp_project)
    assert "# Nushell Aliases" in content
    assert "alias g = git" in content
    assert not (tmp_project / "shells" / "nushell" / "aliases.nu").exists()


def test_render_content_xonsh_no_write(tmp_project: Path):
    """render_content returns xonsh content without writing a file."""
    content = render_content("xonsh", project_dir=tmp_project)
    assert "# Xonsh Aliases" in content
    assert not (tmp_project / "shells" / "xonsh" / "aliases.xsh").exists()


def test_render_nushell_contains_aliases(tmp_project: Path):
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    # Navigation
    assert "alias .. = cd .." in content
    assert "alias ... = cd ../.." in content

    # Modern replacements
    assert "alias grep = rg" in content

    # Git
    assert "alias g = git" in content
    assert "alias gs = git status" in content
    assert "alias glog = git log --oneline --graph --decorate" in content


def test_render_nushell_contains_wrappers(tmp_project: Path):
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    assert "Wrapper commands" in content
    assert "use" in content and "wrappers.nu" in content
    assert "_run_wrapper" in content
    assert "export def --wrapped fd" in content
    assert "fdfind" in content
    assert "export def --wrapped bat" in content
    assert "batcat" in content


def test_render_nushell_system_info_uses_nushell_command(tmp_project: Path):
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    assert "alias meminfo = sys mem" in content
    assert "alias cpuinfo = sys cpu" in content


def test_render_xonsh_creates_file(tmp_project: Path):
    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "# Xonsh Aliases" in content
    assert "DO NOT EDIT" in content


def test_render_xonsh_contains_aliases(tmp_project: Path):
    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    # Navigation: should use os.chdir
    assert "os.chdir('..')" in content

    # Modern replacements
    assert "aliases['grep'] = ['rg']" in content

    # Git: multi-word = string alias
    assert "aliases['gs'] = 'git status'" in content
    assert "aliases['glog'] = 'git log --oneline --graph --decorate'" in content

    # Single-word = list alias
    assert "aliases['g'] = ['git']" in content


def test_render_xonsh_contains_wrappers(tmp_project: Path):
    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    assert "make_wrapper(" in content
    assert "aliases['fd'] = make_wrapper('fd', 'fdfind'" in content
    assert "aliases['bat'] = make_wrapper('bat', 'batcat'" in content


def test_render_xonsh_system_info_uses_python_fn(tmp_project: Path):
    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    # meminfo should use the xonsh_fn handler
    assert "_platform_meminfo" in content
    assert "aliases['cpuinfo'] = _platform_cpuinfo" in content


def test_render_nushell_valid_syntax(tmp_project: Path):
    """Verify the generated Nushell aliases form valid syntax (basic check)."""
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("alias "):
            # Every alias line should have '='
            assert " = " in stripped, f"Malformed alias: {stripped}"


def test_render_xonsh_valid_python_syntax(tmp_project: Path):
    """Verify the generated xonsh aliases are valid Python (basic compile check)."""
    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    # The generated aliases.xsh should be pure Python (no xonsh $ syntax)
    compile(content, str(output), "exec")


def test_render_unsupported_shell_raises(tmp_project: Path):
    with pytest.raises(ValueError, match="Unsupported shell"):
        render_aliases("fish", project_dir=tmp_project)


def test_render_nushell_does_not_mutate_config(tmp_project: Path):
    """Calling render_nushell should not mutate the alias dicts passed to it."""
    aliases_config = load_aliases(tmp_project)
    wrappers = aliases_config.pop("wrappers", {})
    alias_groups_copy = copy.deepcopy(aliases_config)
    wrappers_copy = copy.deepcopy(wrappers)
    render_nushell(aliases_config, wrappers)
    assert aliases_config == alias_groups_copy
    assert wrappers == wrappers_copy


def test_render_creates_parent_dirs(tmp_project: Path):
    """Output path with non-existent parents should be created."""
    output = tmp_project / "deep" / "nested" / "dir" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    assert output.exists()


def test_render_xonsh_clear_aliases_use_clear_screen_fn(tmp_project: Path):
    """Clear aliases should import and use the _clear_screen function from alias_fns."""
    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    result = output.read_text(encoding="utf-8")
    assert "from alias_fns import" in result
    assert "_clear_screen" in result
    assert "aliases['c'] = _clear_screen" in result
    assert "aliases['cls'] = _clear_screen" in result


def test_render_nushell_skips_xonsh_only_aliases(tmp_project: Path):
    """Aliases with only xonsh_fn (no nushell key) should be skipped, not emit 'None'."""
    aliases_path = tmp_project / "config" / "aliases.toml"
    content = aliases_path.read_text(encoding="utf-8")
    content += '\n[xonsh_only]\nspecial = { xonsh_fn = "clear_screen", comment = "xonsh only" }\n'
    aliases_path.write_text(content, encoding="utf-8")

    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    result = output.read_text(encoding="utf-8")

    assert "alias special" not in result
    # But the xonsh output should contain it
    xonsh_output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=xonsh_output, project_dir=tmp_project)
    xonsh_result = xonsh_output.read_text(encoding="utf-8")
    assert "aliases['special'] = _clear_screen" in xonsh_result


def test_alias_fns_compile():
    """alias_fns.py should be valid Python."""
    source_path = Path(__file__).parent.parent / "shells" / "xonsh" / "commands" / "alias_fns.py"
    source = source_path.read_text(encoding="utf-8")
    compile(source, "alias_fns.py", "exec")


def test_alias_fns_psutil_in_try():
    """psutil imports in alias_fns.py should be inside try blocks for graceful fallback."""
    source_path = Path(__file__).parent.parent / "shells" / "xonsh" / "commands" / "alias_fns.py"
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "def _platform_meminfo" in line:
            body = "\n".join(lines[i + 1 : i + 5])
            assert "try:" in body
            assert "import psutil" in body
            break
    else:
        pytest.fail("_platform_meminfo not found in alias_fns.py")


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
def test_invalid_wrapper_name_raises(renderer):
    """Wrapper names with invalid characters should raise ValueError."""
    wrappers = {
        "bad;name": {
            "preferred": "foo",
            "fallback": "bar",
            "error": "not found",
        }
    }
    with pytest.raises(ValueError, match=r"Wrapper name.*invalid"):
        renderer({}, wrappers)


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
def test_invalid_preferred_raises(renderer):
    """Wrapper preferred value with shell metacharacters should raise ValueError."""
    wrappers = {
        "myutil": {
            "preferred": "foo; rm -rf /",
            "fallback": "bar",
            "error": "not found",
        }
    }
    with pytest.raises(ValueError, match="invalid preferred"):
        renderer({}, wrappers)


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
def test_invalid_fallback_raises(renderer):
    """Wrapper fallback value with shell metacharacters should raise ValueError."""
    wrappers = {
        "myutil": {
            "preferred": "foo",
            "fallback": "bar && evil",
            "error": "not found",
        }
    }
    with pytest.raises(ValueError, match="invalid fallback"):
        renderer({}, wrappers)


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
def test_missing_required_wrapper_field_raises(renderer):
    """Wrapper missing a required field should raise ValueError."""
    wrappers = {
        "myutil": {
            "preferred": "foo",
            "fallback": "bar",
            # missing "error"
        }
    }
    with pytest.raises(ValueError, match="missing required field"):
        renderer({}, wrappers)


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
@pytest.mark.parametrize(
    "alias_def",
    [
        {"command": "ls -la", "comment": "list files\nsource /tmp/evil.nu"},
        {"command": "ls -la\nsource /tmp/evil.nu"},
    ],
    ids=["comment", "command"],
)
def test_newline_in_alias_raises(renderer, alias_def):
    """A newline would end the generated line and turn the rest into code."""
    with pytest.raises(ValueError, match="control character"):
        renderer({"listing": {"ll": alias_def}}, {})


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
def test_newline_in_alias_name_raises(renderer):
    with pytest.raises(ValueError, match="control character"):
        renderer({"listing": {"ll\nsource /tmp/evil.nu": "ls -la"}}, {})


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
def test_newline_in_group_name_raises(renderer):
    """Group names are written into a comment line; a newline escapes it into code."""
    with pytest.raises(ValueError, match="control character"):
        renderer({"listing\nsource /tmp/evil.nu": {"ll": "ls -la"}}, {})


@pytest.mark.parametrize("renderer", [render_nushell, render_xonsh])
@pytest.mark.parametrize(
    "bad_name",
    ["ll = ls; rm -rf /", "ll$(whoami)", "ll`id`", "ll|cat", 'll"x'],
)
def test_shell_metacharacter_in_alias_name_raises(renderer, bad_name):
    with pytest.raises(ValueError, match="not a valid alias name"):
        renderer({"listing": {bad_name: "ls -la"}}, {})


def test_invalid_xonsh_fn_raises():
    """Invalid xonsh_fn identifier should raise ValueError."""
    aliases = {
        "test_group": {
            "myalias": {"xonsh_fn": "not-a-valid-identifier", "comment": "bad"},
        }
    }
    with pytest.raises(ValueError, match="invalid xonsh_fn"):
        render_xonsh(aliases, {})


def test_nushell_error_msg_escapes_dollar_and_backslash():
    """Nushell error messages should escape $, \\, and \" properly."""
    wrappers = {
        "myutil": {
            "preferred": "foo",
            "fallback": "bar",
            "error": r'Cost is $5 and path is C:\dir "quoted"',
        }
    }
    content = render_nushell({}, wrappers)
    # $ should be escaped as \$
    assert "\\$5" in content
    # backslash should be escaped as \\
    assert "C:\\\\dir" in content
    # quote should be escaped as \"
    assert '\\"quoted\\"' in content


def test_nushell_error_msg_leaves_backticks_alone():
    """Nushell has no \\` escape -- escaping a backtick makes the file unparseable."""
    wrappers = {
        "myutil": {
            "preferred": "foo",
            "fallback": "bar",
            "error": "install `bat` first",
        }
    }
    content = render_nushell({}, wrappers)
    assert "\\`" not in content
    assert "install `bat` first" in content


def test_render_nushell_alias_completeness(tmp_project: Path):
    """Every non-wrapper, non-xonsh-only alias from TOML must appear in output."""
    aliases_path = tmp_project / "config" / "aliases.toml"
    with open(aliases_path, "rb") as f:
        config = tomllib.load(f)

    expected_names = set()
    for group_name, group_aliases in config.items():
        if group_name == "wrappers":
            continue
        for name, value in group_aliases.items():
            if isinstance(value, dict):
                has_command = "command" in value
                has_nushell = "nushell" in value
                if not has_command and not has_nushell:
                    continue
            expected_names.add(name)

    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    actual_names = set(re.findall(r"^alias (\S+) = ", content, re.MULTILINE))
    assert actual_names == expected_names


def test_render_xonsh_alias_completeness(tmp_project: Path):
    """Every alias name from TOML must appear as aliases['name'] in xonsh output."""
    aliases_path = tmp_project / "config" / "aliases.toml"
    with open(aliases_path, "rb") as f:
        config = tomllib.load(f)

    expected_names = set()
    for group_name, group_aliases in config.items():
        if group_name == "wrappers":
            continue
        for name, value in group_aliases.items():
            if isinstance(value, dict):
                has_command = "command" in value
                has_xonsh = "xonsh" in value
                has_xonsh_fn = "xonsh_fn" in value
                if not has_command and not has_xonsh and not has_xonsh_fn:
                    continue
            expected_names.add(name)

    output = tmp_project / "shells" / "xonsh" / "aliases.xsh"
    render_aliases("xonsh", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    for name in expected_names:
        assert f"aliases['{name}']" in content, f"Alias '{name}' not found in xonsh output"


def test_render_nushell_group_headers_in_order(tmp_project: Path):
    """Every TOML group should appear as a # Title Case header in TOML order."""
    aliases_path = tmp_project / "config" / "aliases.toml"
    with open(aliases_path, "rb") as f:
        config = tomllib.load(f)

    expected_headers = []
    for group_name in config:
        if group_name == "wrappers":
            continue
        expected_headers.append(group_name.replace("_", " ").title())

    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    header_positions = []
    for header in expected_headers:
        pos = content.find(f"# {header}")
        assert pos != -1, f"Header '# {header}' not found in output"
        header_positions.append(pos)

    assert header_positions == sorted(header_positions), "Group headers are not in TOML order"


def test_render_nushell_wrapper_structure(tmp_project: Path):
    """Each wrapper should use the _run_wrapper helper with correct arguments."""
    aliases_path = tmp_project / "config" / "aliases.toml"
    with open(aliases_path, "rb") as f:
        config = tomllib.load(f)

    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    wrappers = config.get("wrappers", {})
    for name, cfg in wrappers.items():
        assert f"export def --wrapped {name}" in content, f"Wrapper '{name}' not found"
        assert f'_run_wrapper "{cfg["preferred"]}"' in content
        assert f'"{cfg["fallback"]}"' in content


def test_render_nushell_wrapper_delegates_to_run_wrapper(tmp_project: Path):
    """Each wrapper should delegate to _run_wrapper with preferred, fallback, error args."""
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    # Count expected wrappers from TOML
    aliases_path = tmp_project / "config" / "aliases.toml"
    with open(aliases_path, "rb") as f:
        config = tomllib.load(f)
    expected_count = len(config.get("wrappers", {}))

    # Extract each wrapper function block
    blocks = re.split(r"(?=export def --wrapped)", content)
    wrapper_blocks = [b for b in blocks if b.startswith("export def --wrapped")]
    assert len(wrapper_blocks) == expected_count, (
        f"Expected {expected_count} wrapper blocks, found {len(wrapper_blocks)}"
    )

    for block in wrapper_blocks:
        assert "_run_wrapper" in block, "Missing '_run_wrapper' call in wrapper block"
        assert "...$args" in block, "Missing '...$args' in wrapper block"


def test_render_nushell_balanced_braces(tmp_project: Path):
    """Generated nushell file should have balanced braces."""
    output = tmp_project / "shells" / "nushell" / "aliases.nu"
    render_aliases("nushell", output_path=output, project_dir=tmp_project)
    content = output.read_text(encoding="utf-8")

    assert content.count("{") == content.count("}"), "Unbalanced braces in nushell output"


def test_generated_xonshrc_python_portions_compile(tmp_project: Path):
    """Python portions of generated xonshrc should compile without errors."""
    config_dir = tmp_project / "xonsh-config"
    config_dir.mkdir()
    content = generate_xonsh_config(config_dir, tmp_project)

    # Strip xonsh-specific lines, replacing with pass to keep block structure
    python_lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("$"):
            continue
        if "source @(" in stripped:
            indent = len(line) - len(line.lstrip())
            python_lines.append(" " * indent + "pass")
            continue
        if stripped.startswith(("xontrib load", "xontrib ")):
            indent = len(line) - len(line.lstrip())
            python_lines.append(" " * indent + "pass")
            continue
        python_lines.append(line)

    python_code = "\n".join(python_lines)
    compile(python_code, "<xonshrc>", "exec")


def test_render_real_config_nushell():
    """Render the real config/aliases.toml for nushell -- every alias must appear."""
    from core.utils import get_project_dir

    project_dir = get_project_dir()

    aliases_config = load_aliases(project_dir)
    wrappers = aliases_config.get("wrappers", {})
    alias_groups = {k: v for k, v in aliases_config.items() if k != "wrappers"}

    content = render_nushell(alias_groups, wrappers)

    for group_name, group_aliases in alias_groups.items():
        for name, value in group_aliases.items():
            if isinstance(value, dict):
                has_command = "command" in value
                has_nushell = "nushell" in value
                if not has_command and not has_nushell:
                    continue
            assert f"alias {name} = " in content, (
                f"Alias '{name}' from group '{group_name}' not in nushell output"
            )

    for wrapper_name in wrappers:
        assert f"export def --wrapped {wrapper_name}" in content, (
            f"Wrapper '{wrapper_name}' not in nushell output"
        )


def test_render_real_config_xonsh_compiles():
    """Render the real config for xonsh -- output must be valid Python."""
    from core.utils import get_project_dir

    project_dir = get_project_dir()
    aliases_config = load_aliases(project_dir)
    wrappers = aliases_config.get("wrappers", {})
    alias_groups = {k: v for k, v in aliases_config.items() if k != "wrappers"}

    content = render_xonsh(alias_groups, wrappers)
    compile(content, "<real_aliases.xsh>", "exec")


def test_xonsh_wrapper_aliases_reference_wrapper():
    """Aliases whose command matches a wrapper name should reference the wrapper alias."""
    aliases = {"tools": {"find": {"command": "fd", "comment": "fd"}}}
    wrappers = {
        "fd": {"preferred": "fd", "fallback": "fdfind", "error": "fd not found"},
    }
    content = render_xonsh(aliases, wrappers)

    # Wrapper should be defined before aliases
    wrapper_pos = content.index("make_wrapper(")
    alias_pos = content.index("aliases['find']")
    assert wrapper_pos < alias_pos, "Wrappers must be rendered before aliases"

    # find should reference the fd wrapper, not the binary
    assert "aliases['find'] = aliases['fd']" in content
    # fd wrapper should use make_wrapper
    assert "aliases['fd'] = make_wrapper('fd', 'fdfind'" in content


def test_xonsh_non_wrapper_alias_uses_list():
    """Aliases whose command does NOT match a wrapper should use list form."""
    aliases = {"tools": {"g": {"command": "git", "comment": "git shorthand"}}}
    wrappers = {}
    content = render_xonsh(aliases, wrappers)
    assert "aliases['g'] = ['git']" in content


def test_unknown_xonsh_fn_raises():
    """An un-imported name is a NameError at rc load, which aborts the whole rc."""
    aliases = {"test_group": {"myalias": {"xonsh_fn": "not_a_real_fn", "comment": "bad"}}}
    with pytest.raises(ValueError, match="unknown xonsh_fn"):
        render_xonsh(aliases, {})


def test_xonsh_import_line_matches_alias_fns_module():
    """The generated import is derived from alias_fns.py, so it cannot drift."""
    from core.render import _alias_fn_names

    content = render_xonsh({}, {})
    for fn in _alias_fn_names():
        assert f"_{fn}" in content
