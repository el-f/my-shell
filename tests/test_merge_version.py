"""Tests for _get_version() and _get_deployed_version() logic."""

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from core.merge import (
    _get_deployed_version,
    _get_version,
    get_deploy_status,
    get_deploy_statuses,
)


def test_get_version_returns_git_datetime(tmp_path: Path):
    """_get_version should return a git commit datetime string from a real repo."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    version = _get_version(tmp_path)
    assert version != "unknown"
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}", version)


def test_get_version_returns_unknown_for_non_repo(tmp_path: Path):
    """_get_version should return 'unknown' when not in a git repo."""
    version = _get_version(tmp_path)
    assert version == "unknown"


def test_get_version_file_not_found():
    """_get_version returns 'unknown' when git is not installed."""
    with patch("core.merge.subprocess.run", side_effect=FileNotFoundError):
        assert _get_version(Path("/fake")) == "unknown"


def test_get_deployed_version_nushell(tmp_path: Path):
    """Should extract version from existing nushell config."""
    config_dir = tmp_path / "nushell-config"
    config_dir.mkdir()
    (config_dir / "config.nu").write_text(
        "$env.MY_SHELL_VERSION = '2026-01-15 10:30:00 +0200'\n",
        encoding="utf-8",
    )
    assert _get_deployed_version("nushell", config_dir) == "2026-01-15 10:30:00 +0200"


def test_get_deployed_version_nushell_no_file(tmp_path: Path):
    """Should return None when no config exists yet."""
    assert _get_deployed_version("nushell", tmp_path) is None


def test_get_deployed_version_nushell_no_version_line(tmp_path: Path):
    """Should return None when config exists but has no version line."""
    config_dir = tmp_path / "nushell-config"
    config_dir.mkdir()
    (config_dir / "config.nu").write_text("# no version here\n", encoding="utf-8")
    assert _get_deployed_version("nushell", config_dir) is None


def test_get_deployed_version_xonsh(tmp_path: Path):
    """Should extract version from existing xonsh config."""
    (tmp_path / ".xonshrc").write_text(
        "$MY_SHELL_VERSION = '2026-02-01 09:00:00 +0000'\n",
        encoding="utf-8",
    )
    with patch("core.merge.get_home_dir", return_value=tmp_path):
        assert _get_deployed_version("xonsh", tmp_path) == "2026-02-01 09:00:00 +0000"


def test_get_deployed_version_unsupported_shell(tmp_path: Path):
    """Should return None for unknown shell types."""
    assert _get_deployed_version("fish", tmp_path) is None


def test_get_deployed_version_oserror(tmp_path: Path):
    """_get_deployed_version returns None when read_text raises OSError."""
    config_dir = tmp_path / "nu-cfg"
    config_dir.mkdir()
    config_file = config_dir / "config.nu"
    config_file.write_text("$env.MY_SHELL_VERSION = '1.0'", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        assert _get_deployed_version("nushell", config_dir) is None


def test_get_deploy_status_not_deployed(tmp_path: Path):
    """No deployed config -> deployed False, and 'stale' is False (absent != stale)."""
    st = get_deploy_status(
        "nushell",
        config_dir=tmp_path / "empty",
        project_dir=tmp_path,
        current_version="v1",
        current_hash="abc123",
    )
    assert st.deployed is False
    assert st.stale is False


def test_get_deploy_status_fresh_and_stale(tmp_path: Path):
    """A matching hash is fresh; a differing hash is stale."""
    cfg = tmp_path / "nu"
    cfg.mkdir()
    (cfg / "config.nu").write_text(
        "$env.MY_SHELL_VERSION = 'v1'\n$env.MY_SHELL_HASH = 'abc123'\n",
        encoding="utf-8",
    )

    fresh = get_deploy_status(
        "nushell",
        config_dir=cfg,
        project_dir=tmp_path,
        current_version="v1",
        current_hash="abc123",
    )
    assert fresh.deployed is True
    assert fresh.stale is False
    assert fresh.deployed_version == "v1"
    assert fresh.deployed_hash == "abc123"

    stale = get_deploy_status(
        "nushell",
        config_dir=cfg,
        project_dir=tmp_path,
        current_version="v2",
        current_hash="def456",
    )
    assert stale.deployed is True
    assert stale.stale is True


def test_category_hashes_split_from_combined(tmp_path: Path):
    """Per-category hashes are distinct from each other; combined covers all dirs."""
    from core.merge import _compute_category_hashes, _compute_project_hash

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.toml").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "shells").mkdir()
    (tmp_path / "shells" / "b.nu").write_text("echo hi\n", encoding="utf-8")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "c.py").write_text("y = 2\n", encoding="utf-8")

    cats = _compute_category_hashes(tmp_path)
    assert set(cats) == {"CONFIG", "SHELLS", "CORE"}
    assert len({cats["CONFIG"], cats["SHELLS"], cats["CORE"]}) == 3  # all different

    combined = _compute_project_hash(tmp_path)
    # Editing only config/ changes the combined hash and the CONFIG category,
    # but leaves SHELLS/CORE untouched.
    (tmp_path / "config" / "a.toml").write_text("x = 99\n", encoding="utf-8")
    cats2 = _compute_category_hashes(tmp_path)
    assert cats2["CONFIG"] != cats["CONFIG"]
    assert cats2["SHELLS"] == cats["SHELLS"]
    assert cats2["CORE"] == cats["CORE"]
    assert _compute_project_hash(tmp_path) != combined


def test_get_deployed_category_hashes_tolerant(tmp_path: Path):
    """Reads per-category hashes; an old deploy without them yields an empty dict."""
    from core.merge import _get_deployed_category_hashes

    cfg = tmp_path / "nu"
    cfg.mkdir()
    (cfg / "config.nu").write_text(
        "$env.MY_SHELL_HASH = 'abc'\n"
        "$env.MY_SHELL_HASH_CONFIG = 'c1'\n"
        "$env.MY_SHELL_HASH_SHELLS = 's1'\n"
        "$env.MY_SHELL_HASH_CORE = 'k1'\n",
        encoding="utf-8",
    )
    assert _get_deployed_category_hashes("nushell", cfg) == {
        "CONFIG": "c1",
        "SHELLS": "s1",
        "CORE": "k1",
    }

    old = tmp_path / "old"
    old.mkdir()
    (old / "config.nu").write_text("$env.MY_SHELL_HASH = 'abc'\n", encoding="utf-8")
    assert _get_deployed_category_hashes("nushell", old) == {}


def test_changed_categories():
    """Only differing categories are named; an old deploy (no data) names none."""
    from core.merge import _changed_categories

    deployed = {"CONFIG": "c1", "SHELLS": "s1", "CORE": "k1"}
    current = {"CONFIG": "c2", "SHELLS": "s1", "CORE": "k1"}
    assert _changed_categories(deployed, current) == ["config"]
    assert _changed_categories({}, current) == []  # old deploy -> generic message


def test_generated_config_has_category_hashes(tmp_project: Path):
    """The rendered nushell + xonsh configs carry all three category hashes."""
    from core.merge import generate_nushell_config, generate_xonsh_config

    nu = generate_nushell_config(tmp_project / "nu", tmp_project)
    for cat in ("CONFIG", "SHELLS", "CORE"):
        assert f"$env.MY_SHELL_HASH_{cat} = '" in nu

    xo = generate_xonsh_config(tmp_project / "xo", tmp_project)
    for cat in ("CONFIG", "SHELLS", "CORE"):
        assert f"$MY_SHELL_HASH_{cat} = '" in xo


def test_deploy_names_changed_category(tmp_project: Path, capsys):
    """Re-deploy after a config-only edit names 'config' as the changed category."""
    from core.merge import deploy

    cfg = tmp_project / "nu_cat"
    with patch("core.merge.is_available", return_value=False):
        deploy("nushell", config_dir=cfg, project_dir=tmp_project)
        capsys.readouterr()  # drop first-deploy output
        # Change only a config/ file, then re-deploy (hash differs -> not skipped).
        (tmp_project / "config" / "aliases.toml").write_text(
            '[extra]\nzz = "echo hi"\n', encoding="utf-8"
        )
        deploy("nushell", config_dir=cfg, project_dir=tmp_project)

    out = capsys.readouterr().out
    assert "config changed since last deploy" in out


def test_get_deploy_statuses_computes_current_once(tmp_path: Path):
    """The current version + hash are computed once, not once per shell."""
    with (
        patch("core.merge._get_version", return_value="v") as mock_ver,
        patch("core.merge._compute_project_hash", return_value="h") as mock_hash,
        patch("core.merge.get_config_dir", return_value=tmp_path / "none"),
        patch("core.merge.get_home_dir", return_value=tmp_path),
        patch("core.merge.get_project_dir", return_value=tmp_path),
    ):
        statuses = get_deploy_statuses(["nushell", "xonsh"])

    assert len(statuses) == 2
    assert mock_ver.call_count == 1
    assert mock_hash.call_count == 1
