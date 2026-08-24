"""Prompt-specific doctor checks."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.doctor import _check_nushell_prompt_contract, _check_project_mise_backends


def test_baked_path_extraction_handles_quoted_paths_with_spaces():
    from core.doctor import _baked_path_references

    path = "/Users/Test User/Library/Application Support/carapace/bin"
    assert _baked_path_references(f'let bin = "{path}"') == {path}


def _prompt_settings() -> dict:
    """Minimal settings fixture for prompt doctor checks."""
    return {
        "integrations": {
            "oh-my-posh": True,
            "mise": True,
        },
        "oh-my-posh": {
            "theme": "jblab_2021",
        },
    }


def _make_project_dir() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Create a minimal project dir with a managed local oh-my-posh theme."""
    tempdir = tempfile.TemporaryDirectory()
    project_dir = Path(tempdir.name)
    theme_dir = project_dir / "shells" / "shared" / "oh-my-posh" / "themes"
    theme_dir.mkdir(parents=True)
    (theme_dir / "jblab_2021.omp.json").write_text('{"segments":[]}', encoding="utf-8")
    return tempdir, project_dir


def test_doctor_warns_on_deprecated_mise_backends():
    """Project mise.toml should not use deprecated ubi backends."""
    with tempfile.TemporaryDirectory() as raw_dir:
        project_dir = Path(raw_dir)
        (project_dir / "mise.toml").write_text(
            '"ubi:atuinsh/atuin" = "latest"\n',
            encoding="utf-8",
        )

        results = _check_project_mise_backends(project_dir)

        assert len(results) == 1
        assert results[0].status == "warn"
        assert "ubi:atuinsh/atuin" in results[0].message


def test_doctor_accepts_non_deprecated_mise_backends():
    """Project mise.toml should pass when ubi backends are gone."""
    with tempfile.TemporaryDirectory() as raw_dir:
        project_dir = Path(raw_dir)
        (project_dir / "mise.toml").write_text(
            '"github:atuinsh/atuin" = "latest"\n',
            encoding="utf-8",
        )

        results = _check_project_mise_backends(project_dir)

        assert len(results) == 1
        assert results[0].status == "pass"


def test_doctor_flags_nushell_prompt_regressions():
    """Doctor flags a mise.nu that hooks pre_prompt and an omp.nu written without --config."""
    project_tempdir, project_dir = _make_project_dir()
    try:
        with tempfile.TemporaryDirectory() as raw_config_dir:
            config_dir = Path(raw_config_dir) / "nushell"
            config_dir.mkdir()

            (config_dir / "mise.nu").write_text(
                "add-hook hooks.pre_prompt $mise_hook\n",
                encoding="utf-8",
            )
            (config_dir / "config.nu").write_text(
                "# generated without trust export\n",
                encoding="utf-8",
            )
            (config_dir / "oh-my-posh.nu").write_text(
                '# generated without local theme pin\n"--no-status=true"\n',
                encoding="utf-8",
            )

            theme_path = (
                project_dir / "shells" / "shared" / "oh-my-posh" / "themes" / "jblab_2021.omp.json"
            )
            theme_path.write_text(
                '{"segments":[{"template":" CONFIG URL FETCH FAILED "}]}',
                encoding="utf-8",
            )

            with (
                patch("core.doctor.get_config_dir", return_value=config_dir),
                patch("core.doctor.load_settings", return_value=_prompt_settings()),
            ):
                results = _check_nushell_prompt_contract(project_dir)

            by_name = {result.name: result for result in results}
            assert by_name["Nushell mise hook"].status == "fail"
            assert by_name["Nushell mise trust"].status == "fail"
            assert by_name["Nushell prompt theme"].status == "fail"
            assert by_name["Nushell prompt status"].status == "fail"
            assert by_name["Managed theme file"].status == "fail"
    finally:
        project_tempdir.cleanup()


def test_doctor_accepts_healthy_nushell_prompt_contract():
    """Doctor should pass when the deployed prompt wiring is healthy."""
    project_tempdir, project_dir = _make_project_dir()
    try:
        with tempfile.TemporaryDirectory() as raw_config_dir:
            config_dir = Path(raw_config_dir) / "nushell"
            config_dir.mkdir()

            theme_path = (
                project_dir / "shells" / "shared" / "oh-my-posh" / "themes" / "jblab_2021.omp.json"
            )
            theme_path_nu = str(theme_path).replace("\\", "/")

            (config_dir / "mise.nu").write_text(
                "# my-shell: only refresh mise on directory changes\n"
                "add-hook hooks.env_change.PWD $mise_hook\n",
                encoding="utf-8",
            )
            (config_dir / "config.nu").write_text(
                "$env.MISE_TRUSTED_CONFIG_PATHS = 'C:/Projects/my-shell'\n",
                encoding="utf-8",
            )
            (config_dir / "oh-my-posh.nu").write_text(
                f'"--config={theme_path_nu}"\n',
                encoding="utf-8",
            )
            theme_path.write_text('{"segments":[]}', encoding="utf-8")

            with (
                patch("core.doctor.get_config_dir", return_value=config_dir),
                patch("core.doctor.load_settings", return_value=_prompt_settings()),
            ):
                results = _check_nushell_prompt_contract(project_dir)

            assert results
            assert all(result.status == "pass" for result in results)
    finally:
        project_tempdir.cleanup()


def test_doctor_flags_dead_baked_paths(tmp_path: Path):
    """A deployed init referencing a deleted absolute exe path must fail."""
    from core.doctor import _check_deployed_path_liveness

    config_dir = tmp_path / "nu-config"
    config_dir.mkdir()
    (config_dir / "mise.nu").write_text(
        '^"C:\\definitely\\gone\\mise.EXE" hook-env -s nu\n',
        encoding="utf-8",
    )

    with (
        patch("core.doctor.get_config_dir", return_value=config_dir),
        patch("core.doctor.load_settings", return_value=_prompt_settings()),
    ):
        results = _check_deployed_path_liveness()

    mise_results = [r for r in results if "mise.nu" in r.name]
    assert mise_results and mise_results[0].status == "fail"


def test_doctor_passes_live_baked_paths(tmp_path: Path):
    import os

    from core.doctor import _check_deployed_path_liveness

    config_dir = tmp_path / "nu-config"
    config_dir.mkdir()
    if os.name == "nt":
        real_exe = tmp_path / "mise.exe"
        real_exe.write_text("", encoding="utf-8")
        exe_ref = str(real_exe).replace("\\", "\\\\")
    else:
        exe_ref = "/usr/bin/env"
    (config_dir / "mise.nu").write_text(f'^"{exe_ref}" hook-env -s nu\n', encoding="utf-8")

    with (
        patch("core.doctor.get_config_dir", return_value=config_dir),
        patch("core.doctor.load_settings", return_value=_prompt_settings()),
    ):
        results = _check_deployed_path_liveness()

    mise_results = [r for r in results if "mise.nu" in r.name]
    assert mise_results and mise_results[0].status == "pass"


def test_doctor_ignores_unpopulated_generated_bin_directory(tmp_path: Path):
    """A tool-owned PATH directory may not exist until that tool first uses it."""
    from core.doctor import _check_deployed_path_liveness

    config_dir = tmp_path / "nu-config"
    config_dir.mkdir()
    generated_bin = tmp_path / "carapace" / "bin"
    (config_dir / "mise.nu").write_text(
        f'$env.PATH = ($env.PATH | prepend "{generated_bin}")\n',
        encoding="utf-8",
    )

    with (
        patch("core.doctor.get_config_dir", return_value=config_dir),
        patch("core.doctor.load_settings", return_value=_prompt_settings()),
    ):
        results = _check_deployed_path_liveness()

    assert not [r for r in results if r.status == "fail"]


def test_doctor_flags_failure_stub_as_unhealthy(tmp_path: Path):
    """An integration whose init file is a deploy-failure stub must not pass."""
    from core.doctor import _check_integration_tools

    config_dir = tmp_path / "nu-config"
    config_dir.mkdir()
    (config_dir / "mise.nu").write_text(
        "# Generated by my-shell\n# mise: init failed during deploy (fix and re-run)\n",
        encoding="utf-8",
    )

    with (
        patch("core.doctor.get_config_dir", return_value=config_dir),
        patch("core.doctor.load_settings", return_value=_prompt_settings()),
        patch("core.doctor.is_available", return_value=True),
    ):
        results = _check_integration_tools()

    mise_results = [r for r in results if r.name == "Integration: mise"]
    assert mise_results and mise_results[0].status == "warn"
    assert "stub" in mise_results[0].message


def test_doctor_nushell_runtime_flags_string_path():
    """A deployed config that degrades PATH to a string must fail the runtime check."""
    import subprocess as sp

    from core.doctor import _check_nushell_runtime

    proc = sp.CompletedProcess(args=["nu"], returncode=0, stdout="string\n1\n", stderr="")
    with (
        patch("core.benchmark._shell_binary", return_value="nu"),
        patch("core.doctor.subprocess.run", return_value=proc),
    ):
        result = _check_nushell_runtime()

    assert result.status == "fail"
    assert "PATH degraded" in result.message


def test_doctor_nushell_runtime_passes_list_path():
    import subprocess as sp

    from core.doctor import _check_nushell_runtime

    proc = sp.CompletedProcess(args=["nu"], returncode=0, stdout="list<string>\n42\n", stderr="")
    run = MagicMock(return_value=proc)
    with (
        patch("core.benchmark._shell_binary", return_value="nu"),
        patch("core.doctor.subprocess.run", run),
    ):
        result = _check_nushell_runtime()

    assert result.status == "pass"
    assert "CMD_DURATION_MS = 42" in run.call_args.args[0][-1]
    assert "PROMPT_COMMAND" in run.call_args.args[0][-1]


def test_doctor_nushell_runtime_fails_on_startup_warning():
    """A zero-exit shell is not healthy when every startup writes a warning."""
    import subprocess as sp

    from core.doctor import _check_nushell_runtime

    proc = sp.CompletedProcess(
        args=["nu"],
        returncode=0,
        stdout="list<string>\n42\n",
        stderr="Warning: nu::parser::deprecated\n",
    )
    with (
        patch("core.benchmark._shell_binary", return_value="nu"),
        patch("core.doctor.subprocess.run", return_value=proc),
    ):
        result = _check_nushell_runtime()

    assert result.status == "fail"
    assert "startup diagnostics" in result.message
