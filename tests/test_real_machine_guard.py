"""A test run must never write to the developer's own shell config.

These tests pin the guard that makes that structurally impossible rather than
a convention every future test has to remember.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.utils import RealMachineWriteError, guard_test_write

# A real home is never inside the temp tree, so simulating one there proves nothing.
FAKE_HOME = Path("C:/Users/nobody") if Path("C:/").exists() else Path("/home/nobody")


def test_allows_writes_under_the_temp_dir(tmp_path: Path):
    guard_test_write(tmp_path / "nushell" / "config.nu", "deploy")


def test_allows_writes_outside_home_entirely():
    guard_test_write(Path(tempfile.gettempdir()) / "somewhere" / "config.nu", "deploy")


def test_allows_writes_outside_home_and_temp(monkeypatch):
    monkeypatch.setattr("core.utils.get_home_dir", lambda: FAKE_HOME)

    guard_test_write(FAKE_HOME.parent.parent / "opt" / "elsewhere" / "config.nu", "deploy")


def test_allows_a_temp_dir_that_lives_inside_home(monkeypatch, tmp_path: Path):
    """CI runners put temp under the profile: C:/Users/runneradmin/AppData/Local/Temp."""
    monkeypatch.setattr("core.utils.get_home_dir", lambda: Path.home())

    guard_test_write(tmp_path / "nushell" / "config.nu", "deploy")


def test_refuses_a_write_into_the_real_home(monkeypatch):
    fake_home = FAKE_HOME
    monkeypatch.setattr("core.utils.get_home_dir", lambda: fake_home)

    with pytest.raises(RealMachineWriteError) as excinfo:
        guard_test_write(fake_home / "AppData" / "Roaming" / "nushell" / "config.nu", "deploy")

    message = str(excinfo.value)
    assert "deploy" in message
    assert str(fake_home) in message


def test_names_the_opt_out_so_the_message_is_actionable(monkeypatch):
    fake_home = FAKE_HOME
    monkeypatch.setattr("core.utils.get_home_dir", lambda: fake_home)

    with pytest.raises(RealMachineWriteError, match="MY_SHELL_ALLOW_REAL_WRITES"):
        guard_test_write(fake_home / ".xonshrc", "deploy")


def test_opt_out_env_var_re_enables_the_write(monkeypatch):
    fake_home = FAKE_HOME
    monkeypatch.setattr("core.utils.get_home_dir", lambda: fake_home)
    monkeypatch.setenv("MY_SHELL_ALLOW_REAL_WRITES", "1")

    guard_test_write(fake_home / ".xonshrc", "deploy")


def test_is_inert_outside_a_test_run(monkeypatch):
    """A real user running `my-shell setup` must always be able to write."""
    fake_home = FAKE_HOME
    monkeypatch.setattr("core.utils.get_home_dir", lambda: fake_home)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    guard_test_write(fake_home / "AppData" / "Roaming" / "nushell" / "config.nu", "deploy")


def test_deploy_wires_the_guard_before_writing(real_project_dir: Path):
    from core.merge import deploy

    with (
        patch("core.utils.get_home_dir", return_value=FAKE_HOME),
        pytest.raises(RealMachineWriteError),
    ):
        deploy(
            "nushell",
            config_dir=FAKE_HOME / ".config" / "nushell",
            project_dir=real_project_dir,
        )


def test_xonshrc_writer_wires_the_guard(tmp_project: Path, tmp_path: Path):
    from core.merge import _deploy_xonsh

    config_dir = tmp_path / "xonsh"
    config_dir.mkdir()
    with (
        patch("core.merge.get_home_dir", return_value=FAKE_HOME),
        patch("core.utils.get_home_dir", return_value=FAKE_HOME),
        patch("core.merge._install_xontribs", return_value=0),
        pytest.raises(RealMachineWriteError),
    ):
        _deploy_xonsh(config_dir, tmp_project)


def test_xonsh_uninstall_guards_the_home_rc(tmp_path: Path):
    """A safe temp config dir must not authorize deleting the real home rc file."""
    from core.uninstall import uninstall_shell

    config_dir = tmp_path / "xonsh"
    config_dir.mkdir()
    with (
        patch("core.uninstall.get_home_dir", return_value=FAKE_HOME),
        patch("core.utils.get_home_dir", return_value=FAKE_HOME),
        patch("core.backup.backup_before_deploy"),
        patch("core.uninstall._is_my_shell_file", return_value=True),
        patch.object(
            Path,
            "exists",
            autospec=True,
            side_effect=lambda path: path == FAKE_HOME / ".xonshrc",
        ),
        pytest.raises(RealMachineWriteError),
    ):
        uninstall_shell("xonsh", config_dir)


def test_restore_writer_wires_the_guard(tmp_path: Path):
    from core.backup import restore_backup

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "config.nu").write_text("old", encoding="utf-8")
    with (
        patch("core.utils.get_home_dir", return_value=FAKE_HOME),
        pytest.raises(RealMachineWriteError),
    ):
        restore_backup(backup, FAKE_HOME / ".config" / "nushell", "nushell")


def test_font_writer_wires_the_guard():
    from core.fonts import _install_via_github

    with (
        patch("core.fonts.get_os", return_value="macos"),
        patch("core.fonts.get_home_dir", return_value=FAKE_HOME),
        patch("core.utils.get_home_dir", return_value=FAKE_HOME),
        pytest.raises(RealMachineWriteError),
    ):
        _install_via_github("meslo")
