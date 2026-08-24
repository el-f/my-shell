"""Deploy must not silently fight another dotfile manager over the same file."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.merge import foreign_owner_warnings


def _symlink_supported() -> bool:
    with tempfile.TemporaryDirectory() as d:
        try:
            Path(d, "link").symlink_to(Path(d, "target"))
        except OSError, NotImplementedError:
            return False
        return True


requires_symlink = pytest.mark.skipif(
    not _symlink_supported(), reason="creating symlinks is not permitted here"
)


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _no_managers():
    return patch("core.merge.shutil.which", return_value=None)


def _manager(name: str, stdout: str, returncode: int = 0):
    """Pretend *name* is the only installed dotfile manager."""
    return (
        patch(
            "core.merge.shutil.which", side_effect=lambda b: "/usr/bin/" + b if b == name else None
        ),
        patch("core.merge.subprocess.run", return_value=_completed(stdout, returncode)),
    )


def test_clean_targets_produce_no_warning(tmp_path):
    (tmp_path / "config.nu").write_text("# mine", encoding="utf-8")
    (tmp_path / "env.nu").write_text("# mine", encoding="utf-8")

    with _no_managers():
        assert foreign_owner_warnings("nushell", tmp_path) == []


def test_missing_targets_produce_no_warning(tmp_path):
    with _no_managers():
        assert foreign_owner_warnings("nushell", tmp_path) == []


@requires_symlink
def test_symlinked_target_warns_and_names_the_link_target(tmp_path):
    elsewhere = tmp_path / "dotfiles" / "config.nu"
    elsewhere.parent.mkdir()
    elsewhere.write_text("# theirs", encoding="utf-8")
    (tmp_path / "config.nu").symlink_to(elsewhere)

    with _no_managers():
        warnings = foreign_owner_warnings("nushell", tmp_path)

    assert len(warnings) == 1
    assert "symlink" in warnings[0]
    assert str(elsewhere) in warnings[0]


def test_chezmoi_managed_target_warns(tmp_path):
    target = tmp_path / "config.nu"
    target.write_text("# mine", encoding="utf-8")
    which, run = _manager("chezmoi", f"{tmp_path / 'other'}\n{target}\n")

    with which, run:
        warnings = foreign_owner_warnings("nushell", tmp_path)

    assert len(warnings) == 1
    assert "chezmoi" in warnings[0]
    assert str(target) in warnings[0]


def test_yadm_paths_are_resolved_against_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".xonshrc").write_text("# mine", encoding="utf-8")
    which, run = _manager("yadm", ".xonshrc\n.gitconfig\n")

    with which, run, patch("core.merge.get_home_dir", return_value=home):
        warnings = foreign_owner_warnings("xonsh", tmp_path)

    assert len(warnings) == 1
    assert "yadm" in warnings[0]


def test_manager_that_errors_is_ignored(tmp_path):
    (tmp_path / "config.nu").write_text("# mine", encoding="utf-8")
    which, run = _manager("chezmoi", f"{tmp_path / 'config.nu'}\n", returncode=1)

    with which, run:
        assert foreign_owner_warnings("nushell", tmp_path) == []


def test_manager_that_hangs_is_ignored(tmp_path):
    (tmp_path / "config.nu").write_text("# mine", encoding="utf-8")

    with (
        patch("core.merge.shutil.which", return_value="/usr/bin/chezmoi"),
        patch("core.merge.subprocess.run", side_effect=subprocess.TimeoutExpired("chezmoi", 15)),
    ):
        assert foreign_owner_warnings("nushell", tmp_path) == []


def test_deploy_surfaces_the_warning(tmp_path, real_project_dir):
    """The guard is dead code unless deploy calls it and carries the result."""
    from core.config import _DEFAULT_SETTINGS
    from core.merge import deploy

    config_dir = tmp_path / "nushell"
    default_settings = {k: dict(v) for k, v in _DEFAULT_SETTINGS.items()}
    with (
        patch("core.merge.is_available", return_value=False),
        patch("core.merge.load_settings", return_value=default_settings),
        patch("core.merge.foreign_owner_warnings", return_value=["chezmoi owns config.nu"]),
    ):
        result = deploy("nushell", config_dir=config_dir, project_dir=real_project_dir)

    assert "chezmoi owns config.nu" in result.warnings


@requires_symlink
def test_deploy_warns_on_a_real_symlinked_config(tmp_path, real_project_dir):
    from core.config import _DEFAULT_SETTINGS
    from core.merge import deploy

    config_dir = tmp_path / "nushell"
    config_dir.mkdir()
    elsewhere = tmp_path / "dotfiles" / "config.nu"
    elsewhere.parent.mkdir()
    elsewhere.write_text("# theirs", encoding="utf-8")
    (config_dir / "config.nu").symlink_to(elsewhere)

    default_settings = {k: dict(v) for k, v in _DEFAULT_SETTINGS.items()}
    with (
        patch("core.merge.is_available", return_value=False),
        patch("core.merge.load_settings", return_value=default_settings),
        _no_managers(),
    ):
        result = deploy("nushell", config_dir=config_dir, project_dir=real_project_dir)

    assert any("symlink" in w for w in result.warnings)


def test_doctor_reports_the_conflict(tmp_path):
    from core.doctor import _check_config_ownership

    with (
        patch("core.doctor.foreign_owner_warnings", return_value=["chezmoi owns config.nu"]),
        patch("core.doctor.get_config_dir", return_value=tmp_path),
    ):
        results = _check_config_ownership(shells=["nushell"])

    assert len(results) == 1
    assert results[0].status == "warn"
    assert "chezmoi" in results[0].message
    assert results[0].fix


def test_doctor_stays_quiet_when_nothing_conflicts(tmp_path):
    from core.doctor import _check_config_ownership

    with (
        patch("core.doctor.foreign_owner_warnings", return_value=[]),
        patch("core.doctor.get_config_dir", return_value=tmp_path),
    ):
        assert _check_config_ownership(shells=["nushell", "xonsh"]) == []


@requires_symlink
def test_deploy_writes_through_a_symlink_instead_of_replacing_it(tmp_path):
    """foreign_owner_warnings promises "my-shell writes through it" -- honour that."""
    from core.utils import atomic_write_text

    real = tmp_path / "dotfiles" / "config.nu"
    real.parent.mkdir()
    real.write_text("old", encoding="utf-8")
    link = tmp_path / "config.nu"
    link.symlink_to(real)

    atomic_write_text(link, "new")

    assert link.is_symlink(), "the dotfiles manager's link was destroyed"
    assert real.read_text(encoding="utf-8") == "new"
