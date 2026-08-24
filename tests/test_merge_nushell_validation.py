"""Nushell syntax validation tests requiring the real `nu` binary.

These tests parse nushell templates and command modules to catch syntax errors
before deployment. Marked @e2e because they need the nushell interpreter.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HAS_NU = shutil.which("nu") is not None

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not HAS_NU, reason="nu not installed")]

# Derived from disk so a new command module is validated automatically (no hand-list to drift).
_NU_COMMAND_MODULES = sorted(
    f.name
    for f in (Path(__file__).resolve().parent.parent / "shells" / "nushell" / "commands").glob(
        "*.nu"
    )
)


def test_nushell_config_template_valid():
    """config.nu.template should load without config errors."""
    template = Path(__file__).resolve().parent.parent / "shells" / "nushell" / "config.nu.template"
    result = subprocess.run(
        ["nu", "--config", str(template), "-c", "exit"],
        capture_output=True,
        text=True,
    )
    for pattern in [
        "unknown_config_option",
        "invalid_config",
        "type_mismatch",
        "invalid_value",
        "nu::parser::",
    ]:
        assert pattern not in result.stderr, f"nushell config error ({pattern}):\n{result.stderr}"


@pytest.mark.parametrize("module", _NU_COMMAND_MODULES)
def test_nushell_command_module_valid(module: str):
    """Each command module should parse without errors."""
    commands_dir = Path(__file__).resolve().parent.parent / "shells" / "nushell" / "commands"
    result = subprocess.run(
        ["nu", "-c", f"use {module}"],
        capture_output=True,
        text=True,
        cwd=str(commands_dir),
    )
    assert "nu::parser::" not in result.stderr, f"{module} has parse errors:\n{result.stderr}"


def test_nushell_init_error_detection_matches_real_nu(tmp_path: Path):
    """The deploy-time guard's error detection must match the real `nu --ide-check`
    output format -- proves the mocked unit tests aren't testing a wrong assumption.
    A broken init is flagged; a clean one is not."""
    from core.merge import _nushell_init_has_errors

    broken = tmp_path / "broken.nu"
    broken.write_text("job spawn -t atuin { true }\n", encoding="utf-8")
    clean = tmp_path / "clean.nu"
    clean.write_text("job spawn { true } | ignore\n", encoding="utf-8")

    assert _nushell_init_has_errors(broken) is True
    assert _nushell_init_has_errors(clean) is False
