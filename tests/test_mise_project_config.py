"""Tests for repo-level mise behavior and generated shell hooks."""

import tomllib


def test_repo_mise_settings_disable_auto_install(real_project_dir):
    """Entering the repo must not trigger mise installs from prompt hooks."""
    with open(real_project_dir / "mise.toml", "rb") as handle:
        data = tomllib.load(handle)

    settings = data["settings"]
    assert settings["auto_install"] is False
    assert settings["exec_auto_install"] is False
    assert settings["not_found_auto_install"] is False
    # Nested form: flat task_run_auto_install is deprecated and mise 2027.2.0 removes it.
    assert settings["task"]["run_auto_install"] is False
    assert "task_run_auto_install" not in settings


def test_xonsh_mise_hook_avoids_pre_prompt(real_project_dir):
    """Xonsh mise integration should refresh on chdir, not on every prompt."""
    content = (
        real_project_dir / "shells" / "xonsh" / "integrations" / "mise" / "init.xsh"
    ).read_text(encoding="utf-8")

    assert "events.on_chdir(_mise_hook)" in content
    assert "events.on_pre_prompt(_mise_hook)" not in content
