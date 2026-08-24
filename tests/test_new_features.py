"""Tests for doctor, backup, dry-run, profiles, status, benchmark and the init wizard."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from core.cli import app
from tests._helpers import apply_isolation_patches as _apply_patches

runner = CliRunner()


class TestDoctor:
    """Tests for core/doctor.py."""

    def test_doctor_check_shell_binaries(self):
        """Mock is_available and verify _check_shell_binaries results."""
        from core.doctor import _check_shell_binaries

        with (
            patch("core.doctor.is_available", return_value=False),
            patch("core.doctor.load_settings", return_value={"shells": {"xonsh": True}}),
        ):
            results = _check_shell_binaries()

        assert len(results) == 2
        names = [r.name for r in results]
        assert "Nushell binary" in names
        assert "xonsh binary" in names
        for r in results:
            assert r.status == "fail"

    def test_doctor_check_shell_binaries_available(self):
        """When shells are available, check returns pass with version info."""
        from core.doctor import _check_shell_binaries

        mock_result = MagicMock()
        mock_result.stdout = "0.99.0\n"

        with (
            patch("core.doctor.is_available", return_value=True),
            patch("core.doctor.subprocess.run", return_value=mock_result),
            patch("core.doctor.load_settings", return_value={"shells": {"xonsh": True}}),
        ):
            results = _check_shell_binaries()

        assert len(results) == 2
        for r in results:
            assert r.status == "pass"
            assert "0.99.0" in r.message

    def test_doctor_check_integration_tools(self):
        """Mock tool availability and verify _check_integration_tools results."""
        from core.doctor import _check_integration_tools

        with patch("core.doctor.is_available", return_value=False):
            results = _check_integration_tools()

        assert len(results) > 0
        for r in results:
            assert r.status == "warn"

    def test_doctor_install_hint_is_platform_correct(self):
        """The fix shows the detected manager's command, never a wrong-platform one."""
        from core.doctor import _check_integration_tools

        with (
            patch("core.doctor.is_available", return_value=False),
            patch("core.install.is_available", return_value=False),
            patch("core.install.detect_package_manager", return_value="apt"),
            patch("core.doctor.is_integration_enabled", return_value=True),
            patch("core.doctor.load_settings", return_value={}),
        ):
            results = _check_integration_tools()

        hints = [r.fix for r in results if r.fix]
        # atuin has an apt install path, so its fix uses the apt/curl command...
        assert any("atuin" in h.lower() for h in hints)
        # ...and no fix ever suggests winget when apt is the detected manager.
        for h in hints:
            assert "winget" not in h, f"winget shown when apt detected: {h}"

    def test_doctor_check_integration_tools_all_available(self, tmp_path: Path):
        """When all tools are available, all results should pass or warn about init files."""
        from core.doctor import _check_integration_tools

        # Create fake init files so the check passes
        for f in ("oh-my-posh.nu", "zoxide.nu", "atuin.nu", "carapace.nu", "mise.nu"):
            (tmp_path / f).write_text("# init\n", encoding="utf-8")

        with (
            patch("core.doctor.is_available", return_value=True),
            patch("core.doctor.get_config_dir", return_value=tmp_path),
        ):
            results = _check_integration_tools()

        assert len(results) > 0
        for r in results:
            assert r.status == "pass"

    def test_doctor_check_config_valid(self, tmp_project: Path):
        """Use tmp_project with valid config, verify config validation passes."""
        from core.doctor import _check_config_valid

        results = _check_config_valid(tmp_project)

        assert len(results) == 1
        # tmp_project from conftest has valid config files -- no failures
        assert results[0].status == "pass"

    def test_doctor_check_config_valid_missing_aliases(self, tmp_path: Path):
        """Config validation reports failure when aliases.toml is missing."""
        from core.doctor import _check_config_valid

        # Empty project dir -- no config files at all
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        results = _check_config_valid(tmp_path)

        assert len(results) >= 1
        assert any(r.status == "fail" for r in results)

    def test_doctor_check_deploy_hashes_fresh(self, tmp_project: Path, tmp_path: Path):
        """Deploy then check hashes -- should match (pass)."""
        from core.doctor import _check_deploy_hash
        from core.merge import deploy

        config_dir = tmp_path / "nu_cfg"
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.merge.get_config_dir", return_value=config_dir))
            stack.enter_context(patch("core.config.get_config_dir", return_value=config_dir))
            deploy("nushell", config_dir=config_dir, project_dir=tmp_project)

        with patch("core.doctor.get_config_dir", return_value=config_dir):
            result = _check_deploy_hash("nushell", tmp_project)

        assert result.status == "pass"
        assert "up to date" in result.message

    def test_doctor_check_deploy_hashes_stale(self, tmp_project: Path, tmp_path: Path):
        """Deploy then modify source, check hashes -- should detect stale (warn)."""
        from core.doctor import _check_deploy_hash
        from core.merge import deploy

        config_dir = tmp_path / "nu_cfg"
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.merge.get_config_dir", return_value=config_dir))
            stack.enter_context(patch("core.config.get_config_dir", return_value=config_dir))
            deploy("nushell", config_dir=config_dir, project_dir=tmp_project)

        # Modify a source file to change the project hash
        template = tmp_project / "shells" / "nushell" / "config.nu.template"
        template.write_text("# modified template\n", encoding="utf-8")

        with patch("core.doctor.get_config_dir", return_value=config_dir):
            result = _check_deploy_hash("nushell", tmp_project)

        assert result.status == "warn"
        assert "stale" in result.message

    def test_doctor_check_deploy_hashes_computes_hash_once(self, tmp_project: Path):
        """_check_deploy_hashes hashes the project once, not once per shell."""
        from core.doctor import _check_deploy_hashes

        with (
            patch("core.doctor._compute_project_hash", return_value="h") as mock_hash,
            patch("core.doctor.get_config_dir", return_value=tmp_project / "none"),
        ):
            results = _check_deploy_hashes(tmp_project, shells=["nushell", "xonsh"])

        assert len(results) == 2
        assert mock_hash.call_count == 1

    def test_doctor_run_produces_results(self, tmp_project: Path):
        """run_doctor returns a non-empty list of CheckResults."""
        from core.doctor import run_doctor

        with (
            patch("core.doctor.is_available", return_value=False),
            patch("core.doctor.get_project_dir", return_value=tmp_project),
            patch("core.doctor.get_config_dir", return_value=tmp_project / "fake_cfg"),
            patch("core.doctor.subprocess.run", side_effect=FileNotFoundError),
            # startup-time check lives in core.benchmark -- short-circuit it so the
            # test never spawns real nu/xonsh processes.
            patch("core.benchmark._shell_binary", return_value=None),
        ):
            results = run_doctor(project_dir=tmp_project)

        assert len(results) > 0
        # Every result should be a CheckResult
        from core.doctor import CheckResult

        for r in results:
            assert isinstance(r, CheckResult)
            assert r.status in ("pass", "info", "warn", "fail")

    def test_doctor_print_report(self):
        """print_doctor_report does not crash with valid input."""
        from core.doctor import CheckResult, print_doctor_report

        results = [
            CheckResult(name="Test check", status="pass", message="OK"),
            CheckResult(name="Test warn", status="warn", message="Hmm", fix="Do something"),
            CheckResult(name="Test fail", status="fail", message="Bad", fix="Fix it"),
        ]

        print_doctor_report(results)

    def test_doctor_print_report_empty(self):
        """print_doctor_report handles empty result list."""
        from core.doctor import print_doctor_report

        print_doctor_report([])

    def test_doctor_print_report_ascii_fallback(self):
        """ASCII-safe status labels are used when stdout is not UTF-capable."""
        from types import SimpleNamespace

        from core.doctor import _status_icons

        with patch("core.doctor.sys.stdout", SimpleNamespace(encoding="cp1252")):
            assert _status_icons()["pass"] == "[green]OK[/green]"


class TestBackup:
    """Tests for core/backup.py."""

    def test_backup_creates_timestamped_dir(self, tmp_project: Path, tmp_path: Path):
        """backup_before_deploy creates a timestamped backup directory."""
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# existing config\n", encoding="utf-8")

        with patch("core.backup.get_project_dir", return_value=tmp_project):
            backup_dir = backup_before_deploy("nushell", config_dir, project_dir=tmp_project)

        assert backup_dir is not None
        assert backup_dir.exists()
        assert backup_dir.is_dir()
        # The backup dir should be inside .my-shell-backup
        assert ".my-shell-backup" in str(backup_dir)

    def test_backup_copies_files(self, tmp_project: Path, tmp_path: Path):
        """Existing config files are copied into the backup directory."""
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# config content\n", encoding="utf-8")
        (config_dir / "env.nu").write_text("# env content\n", encoding="utf-8")
        (config_dir / "oh-my-posh.nu").write_text("# omp init\n", encoding="utf-8")

        with patch("core.backup.get_project_dir", return_value=tmp_project):
            backup_dir = backup_before_deploy("nushell", config_dir, project_dir=tmp_project)

        assert backup_dir is not None
        assert (backup_dir / "config.nu").exists()
        assert (backup_dir / "env.nu").exists()
        assert (backup_dir / "oh-my-posh.nu").exists()
        assert (backup_dir / "config.nu").read_text(encoding="utf-8") == "# config content\n"
        assert (backup_dir / "env.nu").read_text(encoding="utf-8") == "# env content\n"
        assert (backup_dir / "oh-my-posh.nu").read_text(encoding="utf-8") == "# omp init\n"

    def test_backup_prunes_old(self, tmp_project: Path, tmp_path: Path):
        """Old backups are pruned when exceeding max_count."""
        from core.backup import _prune_backups

        backup_root = tmp_path / ".my-shell-backup"
        backup_root.mkdir()

        # Create 7 backup directories
        for i in range(7):
            d = backup_root / f"2024-01-0{i + 1}T00-00-00"
            d.mkdir()
            (d / "config.nu").write_text(f"# backup {i}\n", encoding="utf-8")

        _prune_backups(backup_root, max_count=3)

        remaining = list(backup_root.iterdir())
        assert len(remaining) == 3
        # Should keep the 3 newest (highest names lexicographically)
        names = sorted(d.name for d in remaining)
        assert names == [
            "2024-01-05T00-00-00",
            "2024-01-06T00-00-00",
            "2024-01-07T00-00-00",
        ]

    def test_pre_my_shell_snapshot_survives_pruning(self, tmp_project: Path, tmp_path: Path):
        """The user's pre-my-shell config is kept forever, not evicted by the ring."""
        from core.backup import backup_before_deploy, list_backups

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        config_nu = config_dir / "config.nu"
        config_nu.write_text("# ORIGINAL user config\n", encoding="utf-8")

        with patch("core.backup.get_project_dir", return_value=tmp_project):
            for i in range(8):
                backup_before_deploy("nushell", config_dir, project_dir=tmp_project)
                config_nu.write_text(f"# Generated by my-shell {i}\n", encoding="utf-8")

        pre = config_dir / ".my-shell-backup" / "pre-my-shell"
        assert pre.is_dir()
        assert (pre / "config.nu").read_text(encoding="utf-8") == "# ORIGINAL user config\n"
        # It is offered for restore, but never as the default (newest) choice.
        assert list_backups(config_dir)[-1] == pre

    def test_generated_layer_three_placeholders_do_not_create_pre_adoption_snapshot(
        self, tmp_project: Path, tmp_path: Path
    ):
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "user-custom.nu").write_text(
            "# Your custom Nushell configurations go here\n", encoding="utf-8"
        )

        backup = backup_before_deploy("nushell", config_dir, project_dir=tmp_project)

        assert backup is not None
        assert not (config_dir / ".my-shell-backup" / "pre-my-shell").exists()

    def test_backup_dirs_unique_within_one_second(self, tmp_project: Path, tmp_path: Path):
        """Two deploys in the same second must not write into one directory."""
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        config_nu = config_dir / "config.nu"
        config_nu.write_text("# ORIGINAL user config\n", encoding="utf-8")

        with patch("core.backup.get_project_dir", return_value=tmp_project):
            first = backup_before_deploy("nushell", config_dir, project_dir=tmp_project)
            config_nu.write_text("# Generated by my-shell\n", encoding="utf-8")
            second = backup_before_deploy("nushell", config_dir, project_dir=tmp_project)

        assert first != second
        assert (first / "config.nu").read_text(encoding="utf-8") == "# ORIGINAL user config\n"

    def test_prune_keeps_one_backup_when_max_count_is_zero(self, tmp_path: Path):
        """max_count = 0 must not delete the backup that was just taken."""
        from core.backup import _prune_backups

        backup_root = tmp_path / ".my-shell-backup"
        backup_root.mkdir()
        (backup_root / "2024-01-01T00-00-00-000000").mkdir()

        _prune_backups(backup_root, max_count=0)

        assert len(list(backup_root.iterdir())) == 1

    def test_restore_never_overwrites_user_custom(self, tmp_path: Path):
        """Layer 3 is never overwritten, so a rollback cannot lose the user's own code."""
        from core.backup import restore_backup

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# current\n", encoding="utf-8")
        (config_dir / "user-custom.nu").write_text("# 200 lines of mine\n", encoding="utf-8")

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / "config.nu").write_text("# old\n", encoding="utf-8")
        (backup_dir / "user-custom.nu").write_text("# placeholder\n", encoding="utf-8")

        restore_backup(backup_dir, config_dir, "nushell")

        assert (config_dir / "config.nu").read_text(encoding="utf-8") == "# old\n"
        assert (config_dir / "user-custom.nu").read_text(
            encoding="utf-8"
        ) == "# 200 lines of mine\n"

    def test_backup_nothing_to_backup(self, tmp_project: Path, tmp_path: Path):
        """Returns None when there are no existing config files to back up."""
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        # No config files exist

        with patch("core.backup.get_project_dir", return_value=tmp_project):
            result = backup_before_deploy("nushell", config_dir, project_dir=tmp_project)

        assert result is None

    def test_list_backups_empty(self, tmp_path: Path):
        """Returns empty list when no backups exist."""
        from core.backup import list_backups

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()

        result = list_backups(config_dir)
        assert result == []

    def test_list_backups_ordered(self, tmp_path: Path):
        """Returns backups ordered newest first."""
        from core.backup import list_backups

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        backup_root = config_dir / ".my-shell-backup"
        backup_root.mkdir()

        # Create backups in arbitrary order
        for name in ["2024-01-01T00-00-00", "2024-01-03T00-00-00", "2024-01-02T00-00-00"]:
            (backup_root / name).mkdir()

        result = list_backups(config_dir)
        assert len(result) == 3
        # Newest first
        assert result[0].name == "2024-01-03T00-00-00"
        assert result[1].name == "2024-01-02T00-00-00"
        assert result[2].name == "2024-01-01T00-00-00"

    def test_restore_backup(self, tmp_path: Path):
        """restore_backup restores files to the config directory correctly."""
        from core.backup import restore_backup

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / "config.nu").write_text("# restored config\n", encoding="utf-8")
        (backup_dir / "env.nu").write_text("# restored env\n", encoding="utf-8")

        restore_backup(backup_dir, config_dir, "nushell")

        assert (config_dir / "config.nu").exists()
        assert (config_dir / "env.nu").exists()
        assert (config_dir / "config.nu").read_text(encoding="utf-8") == "# restored config\n"
        assert (config_dir / "env.nu").read_text(encoding="utf-8") == "# restored env\n"

    def test_restore_backup_missing_dir(self, tmp_path: Path):
        """restore_backup raises FileNotFoundError for missing backup dir."""
        from core.backup import restore_backup

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        nonexistent = tmp_path / "no_such_backup"

        with pytest.raises(FileNotFoundError):
            restore_backup(nonexistent, config_dir, "nushell")


class TestDryRun:
    """Tests for core/dry_run.py."""

    def test_dry_run_nushell(self, tmp_project: Path, tmp_path: Path):
        """show_dry_run_diff doesn't crash for nushell."""
        from core.dry_run import show_dry_run_diff

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, tmp_path / "home")
            stack.enter_context(patch("core.dry_run.get_config_dir", return_value=config_dir))
            stack.enter_context(patch("core.dry_run.get_project_dir", return_value=tmp_project))
            stack.enter_context(patch("core.config.get_project_dir", return_value=tmp_project))
            stack.enter_context(patch("core.merge.get_config_dir", return_value=config_dir))
            show_dry_run_diff("nushell", project_dir=tmp_project)

    def test_dry_run_xonsh(self, tmp_project: Path, tmp_path: Path):
        """show_dry_run_diff doesn't crash for xonsh."""
        from core.dry_run import show_dry_run_diff

        config_dir = tmp_path / "xonsh_cfg"
        config_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir(exist_ok=True)

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.dry_run.get_config_dir", return_value=config_dir))
            stack.enter_context(patch("core.dry_run.get_project_dir", return_value=tmp_project))
            stack.enter_context(patch("core.dry_run.get_home_dir", return_value=fake_home))
            stack.enter_context(patch("core.config.get_project_dir", return_value=tmp_project))
            stack.enter_context(patch("core.merge.get_config_dir", return_value=config_dir))
            show_dry_run_diff("xonsh", project_dir=tmp_project)

    def test_dry_run_tool_init_diff_previews_change(self, tmp_path: Path, capsys):
        """The nushell tool-init preview diffs the rendered init against the deployed file."""
        from core.dry_run import _show_tool_init_diffs
        from core.merge import ToolInitSpec

        spec = ToolInitSpec(
            "zoxide",
            ["zoxide", "init", "nushell"],
            tmp_path / "zoxide.nu",
            regex_post_process=[("old", "new")],
        )
        (tmp_path / "zoxide.nu").write_text("OLD INIT\n", encoding="utf-8")

        with (
            patch("core.dry_run.load_settings", return_value={}),
            patch("core.dry_run._nushell_tool_init_specs", return_value=[spec]),
            patch("core.dry_run._render_tool_init", return_value="NEW INIT\n") as render,
        ):
            _show_tool_init_diffs(tmp_path, tmp_path)

        out = capsys.readouterr().out
        assert "zoxide.nu" in out
        assert "NEW INIT" in out
        assert render.call_args.kwargs["regex_post_process"] == [("old", "new")]

    def test_dry_run_tool_init_skips_unavailable(self, tmp_path: Path, capsys):
        """A tool that isn't installed is noted and skipped, not diffed."""
        from core.dry_run import _show_tool_init_diffs
        from core.merge import ToolInitSpec

        spec = ToolInitSpec("atuin", ["atuin", "init", "nu"], tmp_path / "atuin.nu")
        with (
            patch("core.dry_run.load_settings", return_value={}),
            patch("core.dry_run._nushell_tool_init_specs", return_value=[spec]),
            patch("core.dry_run._render_tool_init", return_value=None),
        ):
            _show_tool_init_diffs(tmp_path, tmp_path)

        out = capsys.readouterr().out
        assert "atuin not available" in out


class TestProfiles:
    """Tests for core/profiles.py."""

    def test_load_default_profiles(self, tmp_path: Path):
        """Returns default profiles when no profiles.toml file exists."""
        from core.profiles import load_profiles

        profiles = load_profiles(tmp_path)

        assert "minimal" in profiles
        assert "full" in profiles
        assert "zoxide" in profiles["minimal"]["integrations"]

    def test_load_profiles_from_file(self, tmp_path: Path):
        """Loads profiles from config/profiles.toml when it exists."""
        from core.profiles import load_profiles

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profiles.toml").write_text(
            '[profiles.custom]\nintegrations = ["zoxide"]\ncommands = ["navigation"]\n',
            encoding="utf-8",
        )

        profiles = load_profiles(tmp_path)
        assert "custom" in profiles
        assert profiles["custom"]["integrations"] == ["zoxide"]

    def test_load_profiles_merges_over_defaults(self, tmp_path: Path):
        """A user-defined profile ADDS to the built-ins, it does not replace them."""
        from core.profiles import load_profiles

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profiles.toml").write_text(
            '[profiles.custom]\nintegrations = ["zoxide"]\ncommands = ["navigation"]\n',
            encoding="utf-8",
        )

        profiles = load_profiles(tmp_path)
        assert "custom" in profiles
        assert {"minimal", "full"} <= set(profiles)

    def test_load_profiles_user_overrides_one_builtin(self, tmp_path: Path):
        """A user profile named like a built-in replaces just that one; others stay."""
        from core.profiles import load_profiles

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profiles.toml").write_text(
            '[profiles.minimal]\nintegrations = ["atuin"]\ncommands = []\n',
            encoding="utf-8",
        )

        profiles = load_profiles(tmp_path)
        assert profiles["minimal"]["integrations"] == ["atuin"]
        assert "full" in profiles

    def test_load_profiles_non_table_raises(self, tmp_path: Path):
        """A non-table 'profiles' value is a hard error, not a silent bad state."""
        from core.profiles import load_profiles

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "profiles.toml").write_text('profiles = "oops"\n', encoding="utf-8")

        with pytest.raises(ValueError, match="must be a table"):
            load_profiles(tmp_path)

    def test_apply_profile_minimal(self, tmp_project: Path):
        """apply_profile with 'minimal' writes settings.local.toml."""
        from core.profiles import apply_profile

        apply_profile("minimal", project_dir=tmp_project)

        local_path = tmp_project / "config" / "settings.local.toml"
        assert local_path.exists()
        content = local_path.read_text(encoding="utf-8")
        assert "zoxide = true" in content
        # minimal profile does NOT enable oh-my-posh
        assert "oh-my-posh = false" in content

    def test_apply_profile_full(self, tmp_project: Path):
        """apply_profile with 'full' enables all integrations."""
        from core.profiles import apply_profile

        apply_profile("full", project_dir=tmp_project)

        local_path = tmp_project / "config" / "settings.local.toml"
        assert local_path.exists()
        content = local_path.read_text(encoding="utf-8")
        assert "oh-my-posh = true" in content
        assert "zoxide = true" in content
        assert "atuin = true" in content

    def test_apply_profile_keeps_other_user_settings(self, tmp_project: Path):
        """Applying a profile rewrites two sections; every other setting survives."""
        import tomllib

        from core.profiles import apply_profile

        local_path = tmp_project / "config" / "settings.local.toml"
        local_path.write_text(
            "[oh-my-posh]\n"
            'theme = "my_custom_theme"\n'
            "\n"
            "[fonts]\n"
            'nerd_font = "firacode"\n'
            "auto_install = false\n"
            "\n"
            "[backup]\n"
            "max_count = 30\n"
            "\n"
            "[integrations]\n"
            "atuin = false\n",
            encoding="utf-8",
        )

        apply_profile("full", project_dir=tmp_project)

        data = tomllib.loads(local_path.read_text(encoding="utf-8"))
        assert data["oh-my-posh"]["theme"] == "my_custom_theme"
        assert data["fonts"] == {"nerd_font": "firacode", "auto_install": False}
        assert data["backup"]["max_count"] == 30
        assert data["integrations"]["atuin"] is True  # rewritten by the profile

    def test_apply_profile_header_stays_first(self, tmp_project: Path):
        """active_profile() reads the first line, so the header must not be pushed down."""
        from core.profiles import active_profile, apply_profile

        local_path = tmp_project / "config" / "settings.local.toml"
        local_path.write_text('[oh-my-posh]\ntheme = "mine"\n', encoding="utf-8")

        apply_profile("minimal", project_dir=tmp_project)

        assert active_profile(tmp_project) == "minimal"

    def test_apply_profile_unknown_raises(self, tmp_project: Path):
        """ValueError is raised for an unknown profile name."""
        from core.profiles import apply_profile

        with pytest.raises(ValueError, match="Unknown profile"):
            apply_profile("nonexistent_profile", project_dir=tmp_project)

    def test_profile_inheritance(self, tmp_project: Path):
        """A profile that inherits another gets the parent's integrations."""
        from core.profiles import _resolve_profile, load_profiles

        profiles = load_profiles(tmp_project)
        profiles["child"] = {"inherits": "full"}
        resolved = _resolve_profile(profiles, "child")

        assert "oh-my-posh" in resolved["integrations"]
        assert "zoxide" in resolved["integrations"]
        assert "atuin" in resolved["integrations"]

    def test_profile_inheritance_override(self, tmp_path: Path):
        """Child profile overrides parent keys."""
        from core.profiles import _resolve_profile

        profiles = {
            "base": {
                "integrations": ["zoxide", "atuin"],
                "commands": ["navigation"],
            },
            "child": {
                "inherits": "base",
                "commands": ["navigation", "fuzzy"],
            },
        }

        resolved = _resolve_profile(profiles, "child")
        # commands overridden by child
        assert resolved["commands"] == ["navigation", "fuzzy"]
        # integrations inherited from base
        assert resolved["integrations"] == ["zoxide", "atuin"]

    def test_profile_circular_inheritance(self):
        """Circular inheritance is detected and raises ValueError."""
        from core.profiles import _resolve_profile

        profiles = {
            "a": {"inherits": "b"},
            "b": {"inherits": "a"},
        }

        with pytest.raises(ValueError, match="Circular"):
            _resolve_profile(profiles, "a")

    def test_unknown_profile_lists_known_names(self, tmp_project: Path):
        """An unknown profile error names the known profiles to guide the user."""
        from core.profiles import _resolve_profile, load_profiles

        profiles = load_profiles(tmp_project)
        with pytest.raises(ValueError, match=r"Known profiles:.*full"):
            _resolve_profile(profiles, "ghost")


class TestCLIStatus:
    """CLI tests for the status command."""

    def test_cli_status(self, tmp_project: Path, tmp_path: Path):
        """Invoke status command, check it runs without error."""
        config_dir = tmp_path / "nu_cfg"
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.merge.get_config_dir", return_value=config_dir))
            stack.enter_context(patch("core.config.get_config_dir", return_value=config_dir))
            stack.enter_context(patch("core.utils.get_project_dir", return_value=tmp_project))
            result = runner.invoke(app, ["status", "--shell", "nushell"])

        assert result.exit_code == 0
        # Config dir does not exist -> status must report the not-deployed state,
        # not just echo the shell name in the header.
        assert "nushell" in result.output.lower()
        assert "not deployed yet" in result.output.lower()


class TestCLIDoctor:
    """CLI tests for the doctor command."""

    def test_cli_doctor(self, tmp_project: Path):
        """Invoke doctor command, verify it runs."""
        from core.doctor import CheckResult

        mock_results = [
            CheckResult(name="Test", status="pass", message="All good"),
        ]

        with (
            patch("core.doctor.run_doctor", return_value=mock_results),
            patch("core.doctor.print_doctor_report") as mock_print,
        ):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        mock_print.assert_called_once_with(mock_results)

    def test_cli_doctor_exits_on_failure(self):
        """Doctor exits with code 1 when there are failures."""
        from core.doctor import CheckResult

        mock_results = [
            CheckResult(name="Broken", status="fail", message="Bad"),
        ]

        with (
            patch("core.doctor.run_doctor", return_value=mock_results),
            patch("core.doctor.print_doctor_report"),
        ):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 1


class TestBenchmark:
    """Tests for core/benchmark.py."""

    def test_benchmark_result_dataclass(self):
        """BenchmarkResult can be instantiated with expected fields."""
        from core.benchmark import BenchmarkResult

        br = BenchmarkResult(
            shell="nushell",
            mean_ms=42.5,
            stddev_ms=3.2,
            min_ms=38.0,
            max_ms=47.0,
            runs=5,
            used_hyperfine=False,
        )

        assert br.shell == "nushell"
        assert br.mean_ms == 42.5
        assert br.stddev_ms == 3.2
        assert br.min_ms == 38.0
        assert br.max_ms == 47.0
        assert br.runs == 5
        assert br.used_hyperfine is False

    def test_benchmark_result_no_stddev(self):
        """BenchmarkResult allows None for stddev_ms (single run)."""
        from core.benchmark import BenchmarkResult

        br = BenchmarkResult(
            shell="xonsh",
            mean_ms=100.0,
            stddev_ms=None,
            min_ms=100.0,
            max_ms=100.0,
            runs=1,
            used_hyperfine=False,
        )

        assert br.stddev_ms is None
        assert br.runs == 1

    def test_shell_binary_found(self):
        """_shell_binary returns binary name when available."""
        from core.benchmark import _shell_binary

        with patch("core.benchmark.is_available", return_value=True):
            result = _shell_binary("nushell")
        assert result == "nu"

    def test_shell_binary_not_found(self):
        """_shell_binary returns None when binary is not available."""
        from core.benchmark import _shell_binary

        with patch("core.benchmark.is_available", return_value=False):
            result = _shell_binary("nushell")
        assert result is None

    def test_shell_binary_unknown_shell(self):
        """_shell_binary returns None for an unknown shell."""
        from core.benchmark import _shell_binary

        result = _shell_binary("fish")
        assert result is None

    def test_benchmark_basic(self):
        """_benchmark_basic times startup and computes exact mean/min/max/stddev."""
        from core.benchmark import _benchmark_basic

        mock_result = MagicMock()
        mock_result.returncode = 0

        # perf_counter is called (start, end) per run; 3 runs -> elapsed 10/20/15 ms
        perf_values = [0.0, 0.010, 0.0, 0.020, 0.0, 0.015]

        with (
            patch("core.benchmark.subprocess.run", return_value=mock_result),
            patch("core.benchmark.time.perf_counter", side_effect=perf_values),
        ):
            result = _benchmark_basic("nushell", "nu", runs=3)

        assert result.shell == "nushell"
        assert result.runs == 3
        assert result.used_hyperfine is False
        assert result.mean_ms == 15.0
        assert result.min_ms == 10.0
        assert result.max_ms == 20.0
        assert result.stddev_ms == pytest.approx(5.0, abs=0.01)

    def test_benchmark_basic_failure(self):
        """_benchmark_basic raises when the shell exits non-zero (broken startup)."""
        from core.benchmark import _benchmark_basic

        mock_result = MagicMock()
        mock_result.returncode = 1

        with (
            patch("core.benchmark.subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="exited 1"),
        ):
            _benchmark_basic("nushell", "nu", runs=3)

    def test_benchmark_detailed_failure(self):
        """_benchmark_detailed raises when the empty-config shell exits non-zero."""
        from core.benchmark import _benchmark_detailed

        mock_result = MagicMock()
        mock_result.returncode = 1

        with (
            patch("core.benchmark.subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="empty config"),
        ):
            _benchmark_detailed("nushell", "nu", runs=1, use_hyperfine=False)

    def test_benchmark_hyperfine(self):
        """_benchmark_hyperfine parses hyperfine JSON output correctly."""
        import json

        from core.benchmark import _benchmark_hyperfine

        hf_data = {
            "results": [
                {
                    "mean": 0.05,
                    "stddev": 0.002,
                    "min": 0.045,
                    "max": 0.060,
                }
            ]
        }
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(hf_data)

        with patch("core.benchmark.subprocess.run", return_value=mock_result):
            result = _benchmark_hyperfine("nushell", "nu", runs=5)

        assert result.shell == "nushell"
        assert result.used_hyperfine is True
        assert result.mean_ms == 50.0
        assert result.stddev_ms == 2.0
        assert result.min_ms == 45.0
        assert result.max_ms == 60.0

    def test_benchmark_hyperfine_quotes_a_binary_path_with_spaces(self):
        """Without -N and quoting, the shell splits `C:/Program Files/nu/nu` into two args."""
        import json

        from core.benchmark import _benchmark_hyperfine

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {"results": [{"mean": 0.05, "stddev": 0.002, "min": 0.045, "max": 0.060}]}
        )

        with patch("core.benchmark.subprocess.run", return_value=mock_result) as mock_run:
            _benchmark_hyperfine("nushell", "C:/Program Files/nu/nu", runs=1)

        argv = mock_run.call_args[0][0]
        assert "-N" in argv
        assert argv[-1] == "'C:/Program Files/nu/nu' -c exit"

    def test_benchmark_hyperfine_failure(self):
        """_benchmark_hyperfine raises RuntimeError on non-zero exit."""
        from core.benchmark import _benchmark_hyperfine

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error happened"

        with (
            patch("core.benchmark.subprocess.run", return_value=mock_result),
            pytest.raises(RuntimeError, match="hyperfine failed"),
        ):
            _benchmark_hyperfine("nushell", "nu")

    def test_print_benchmark_results(self):
        """print_benchmark_results doesn't crash with valid input."""
        from core.benchmark import BenchmarkResult, print_benchmark_results

        results = [
            BenchmarkResult("nushell", 42.5, 3.2, 38.0, 47.0, 5, False),
            BenchmarkResult("xonsh", 100.0, None, 100.0, 100.0, 1, False),
        ]
        print_benchmark_results(results)

    def test_run_benchmark_no_shells_available(self):
        """run_benchmark returns empty list when no shells are available."""
        from core.benchmark import run_benchmark

        with patch("core.benchmark.is_available", return_value=False):
            results = run_benchmark(shells=["nushell"])

        assert results == []

    def test_run_benchmark_basic_path(self):
        """run_benchmark uses basic timing when hyperfine is not available."""
        from core.benchmark import run_benchmark

        mock_result = MagicMock()
        mock_result.returncode = 0

        def mock_is_available(name):
            return name == "nu"

        with (
            patch("core.benchmark.is_available", side_effect=mock_is_available),
            patch("core.benchmark.subprocess.run", return_value=mock_result),
        ):
            results = run_benchmark(shells=["nushell"])

        assert len(results) == 1
        assert results[0].shell == "nushell"
        assert results[0].used_hyperfine is False

    def test_run_benchmark_timeout(self):
        """run_benchmark handles subprocess timeout gracefully."""
        import subprocess as _sp

        from core.benchmark import run_benchmark

        def mock_is_available(name):
            return name == "nu"

        with (
            patch("core.benchmark.is_available", side_effect=mock_is_available),
            patch("core.benchmark.subprocess.run", side_effect=_sp.TimeoutExpired("nu", 30)),
        ):
            results = run_benchmark(shells=["nushell"])

        assert results == []

    def test_run_benchmark_exception(self):
        """run_benchmark handles generic exceptions gracefully."""
        from core.benchmark import run_benchmark

        def mock_is_available(name):
            return name == "nu"

        with (
            patch("core.benchmark.is_available", side_effect=mock_is_available),
            patch("core.benchmark.subprocess.run", side_effect=OSError("boom")),
        ):
            results = run_benchmark(shells=["nushell"])

        assert results == []

    def test_benchmark_detailed_basic(self):
        """_benchmark_detailed returns 2 results (empty + my-shell config)."""
        from core.benchmark import _benchmark_detailed

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("core.benchmark.subprocess.run", return_value=mock_result):
            results = _benchmark_detailed("nushell", "nu", runs=2, use_hyperfine=False)

        assert len(results) == 2
        assert "empty config" in results[0].shell
        assert "my-shell config" in results[1].shell


class TestInitWizard:
    """Tests for core/init_wizard.py."""

    def test_detect_shells(self):
        """_detect_shells returns dict with correct keys."""
        from core.init_wizard import _detect_shells

        with patch("core.init_wizard.is_available", return_value=False):
            result = _detect_shells()

        assert "nushell" in result
        assert "xonsh" in result
        assert result["nushell"] is False
        assert result["xonsh"] is False

    def test_detect_shells_found(self):
        """_detect_shells detects available shells."""
        from core.init_wizard import _detect_shells

        with patch("core.init_wizard.is_available", return_value=True):
            result = _detect_shells()

        assert result["nushell"] is True
        assert result["xonsh"] is True

    def test_detect_tools(self):
        """_detect_tools returns dict with tool names."""
        from core.init_wizard import _detect_tools

        with patch("core.init_wizard.is_available", return_value=False):
            result = _detect_tools()

        assert len(result) > 0
        for v in result.values():
            assert v is False

    def test_list_omp_themes(self, tmp_project: Path):
        """_list_omp_themes finds theme files."""
        from core.init_wizard import _list_omp_themes

        result = _list_omp_themes(tmp_project)
        assert "jblab_2021" in result

    def test_list_omp_themes_empty(self, tmp_path: Path):
        """_list_omp_themes returns empty list when no themes dir."""
        from core.init_wizard import _list_omp_themes

        result = _list_omp_themes(tmp_path)
        assert result == []

    def test_init_wizard_no_shells(self, tmp_project: Path):
        """Wizard exits early when no shells are detected."""
        from core.init_wizard import run_init_wizard

        with (
            patch("core.init_wizard.is_available", return_value=False),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
        ):
            run_init_wizard(project_dir=tmp_project)

    def test_init_wizard_user_declines(self, tmp_project: Path):
        """Wizard returns when user declines to configure."""
        from core.init_wizard import run_init_wizard

        def mock_is_available(name):
            return name == "nu"

        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
            patch("core.init_wizard.typer.confirm", return_value=False),
        ):
            run_init_wizard(project_dir=tmp_project)

        # settings.local.toml should NOT have been created
        local_path = tmp_project / "config" / "settings.local.toml"
        assert not local_path.exists()

    def test_init_wizard_declines_config_write(self, tmp_project: Path):
        """Answering no at the 'Write config?' step leaves settings.local.toml unwritten."""
        from core.init_wizard import run_init_wizard

        def mock_is_available(name):
            return name == "nu"

        # configure=yes, write=NO (no oh-my-posh, so no theme/font prompts in between)
        confirm_calls = iter([True, False])
        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
            patch("core.init_wizard.typer.confirm", side_effect=confirm_calls),
        ):
            run_init_wizard(project_dir=tmp_project)

        assert not (tmp_project / "config" / "settings.local.toml").exists()

    def test_init_wizard_full_run(self, tmp_project: Path):
        """Wizard writes settings.local.toml when user accepts."""
        from core.init_wizard import run_init_wizard

        def mock_is_available(name):
            return name in ("nu", "xonsh")

        confirm_calls = iter([True, True, False])  # configure, write, run_setup=False

        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
            patch("core.init_wizard.typer.confirm", side_effect=confirm_calls),
        ):
            run_init_wizard(project_dir=tmp_project)

        local_path = tmp_project / "config" / "settings.local.toml"
        assert local_path.exists()
        content = local_path.read_text(encoding="utf-8")
        assert "[integrations]" in content
        assert "[commands]" in content

    def test_init_wizard_with_omp_theme_selection(self, tmp_project: Path):
        """Wizard prompts for OMP theme when oh-my-posh is available."""
        from core.init_wizard import run_init_wizard

        def mock_is_available(name):
            return name in ("nu", "oh-my-posh")

        confirm_calls = iter([True, True, False])  # configure, write, run_setup=False

        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
            patch("core.init_wizard.typer.confirm", side_effect=confirm_calls),
            patch("core.init_wizard.typer.prompt", return_value="jblab_2021"),
        ):
            run_init_wizard(project_dir=tmp_project)

        local_path = tmp_project / "config" / "settings.local.toml"
        content = local_path.read_text(encoding="utf-8")
        assert 'theme = "jblab_2021"' in content


class TestDryRunInternals:
    """Tests for dry_run.py internal functions."""

    def test_read_file_existing(self, tmp_path: Path):
        """_read_file returns content for existing file."""
        from core.dry_run import _read_file

        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")

        assert _read_file(f) == "hello"

    def test_read_file_missing(self, tmp_path: Path):
        """_read_file returns empty string for missing file."""
        from core.dry_run import _read_file

        assert _read_file(tmp_path / "nope.txt") == ""

    def test_unified_diff_identical(self):
        """_unified_diff returns empty string for identical content."""
        from core.dry_run import _unified_diff

        result = _unified_diff("same\n", "same\n", "test.nu")
        assert result == ""

    def test_unified_diff_changes(self):
        """_unified_diff returns diff for changed content."""
        from core.dry_run import _unified_diff

        result = _unified_diff("old line\n", "new line\n", "test.nu")
        assert "old line" in result
        assert "new line" in result
        assert "---" in result
        assert "+++" in result

    def test_print_diff_no_changes(self, capsys):
        """_print_diff prints 'no changes' for empty diff."""
        from core.dry_run import _print_diff

        _print_diff("")
        captured = capsys.readouterr()
        assert "no changes" in captured.out.lower()

    def test_print_diff_with_content(self):
        """_print_diff doesn't crash with diff content."""
        from core.dry_run import _print_diff

        diff_text = "--- a/test.nu\n+++ b/test.nu\n@@ -1 +1 @@\n-old\n+new\n"
        _print_diff(diff_text)

    def test_print_diff_no_rich(self):
        """_print_diff falls back when Rich is unavailable."""
        from core.dry_run import _print_diff

        diff_text = "--- a/test.nu\n+++ b/test.nu\n@@ -1 +1 @@\n-old\n+new\n"
        with patch.dict("sys.modules", {"rich": None, "rich.console": None, "rich.syntax": None}):
            _print_diff(diff_text)

    def test_show_dry_run_diff_unsupported_shell(self, tmp_project: Path):
        """show_dry_run_diff raises ValueError for unsupported shell names."""
        from core.dry_run import show_dry_run_diff

        with (
            patch("core.dry_run.get_project_dir", return_value=tmp_project),
            patch("core.dry_run.get_config_dir", return_value=tmp_project / "cfg"),
            pytest.raises(ValueError, match="Unsupported shell: fish"),
        ):
            show_dry_run_diff("fish", project_dir=tmp_project)

    def test_show_dry_run_diff_hashes_once(self, tmp_project: Path):
        """show_dry_run_diff computes version+hash once and threads them, so the
        generate_* calls never recompute (guards against dropping the kwargs)."""
        from core.dry_run import show_dry_run_diff

        with (
            patch("core.dry_run.get_config_dir", return_value=tmp_project / "cfg"),
            patch("core.dry_run._compute_project_hash", return_value="h") as dry_hash,
            patch("core.dry_run._get_version", return_value="v") as dry_ver,
            # generate_* fall back through the core.merge namespace; they must NOT be
            # reached, because show_dry_run_diff passes version=/project_hash=.
            patch("core.merge._compute_project_hash") as merge_hash,
            patch("core.merge._get_version") as merge_ver,
        ):
            show_dry_run_diff("nushell", project_dir=tmp_project)

        assert dry_hash.call_count == 1  # computed once at the top
        assert dry_ver.call_count == 1
        assert merge_hash.call_count == 0  # generate_* reuse the threaded values
        assert merge_ver.call_count == 0


class TestDoctorAdditional:
    """Additional doctor tests for uncovered check functions."""

    def test_check_user_custom_files(self, tmp_path: Path):
        """_check_user_custom_files reports for both shells."""
        from core.doctor import _check_user_custom_files

        with patch("core.doctor.get_config_dir", return_value=tmp_path):
            results = _check_user_custom_files()

        assert len(results) == 2
        names = [r.name for r in results]
        assert "nushell user-custom" in names
        assert "xonsh user-custom" in names

    def test_check_user_custom_file_exists(self, tmp_path: Path):
        """_check_user_custom_file passes when user-custom.nu exists."""
        from core.doctor import _check_user_custom_file

        (tmp_path / "user-custom.nu").write_text("# custom\n", encoding="utf-8")

        with patch("core.doctor.get_config_dir", return_value=tmp_path):
            result = _check_user_custom_file("nushell")

        assert result.status == "pass"

    def test_check_cargo_rust_both_available(self):
        """_check_cargo_rust passes when both cargo and rustc are available."""
        from core.doctor import _check_cargo_rust

        mock_result = MagicMock()
        mock_result.stdout = "rustc 1.80.0\n"
        mock_result.returncode = 0

        with (
            patch("core.doctor.is_available", return_value=True),
            patch("core.doctor.subprocess.run", return_value=mock_result),
        ):
            results = _check_cargo_rust()

        assert len(results) == 2
        for r in results:
            assert r.status == "pass"

    def test_check_cargo_rust_not_available(self):
        """Missing cargo/rustc is info: only optional plugins need them."""
        from core.doctor import _check_cargo_rust

        with patch("core.doctor.is_available", return_value=False):
            results = _check_cargo_rust()

        assert len(results) == 2
        for r in results:
            assert r.status == "info"
            assert "optional" in r.message

    def test_check_python_environment_found(self):
        """_check_python_environment passes when python is found."""
        from core.doctor import _check_python_environment

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Python 3.12.0\n"

        with patch("core.doctor.subprocess.run", return_value=mock_result):
            result = _check_python_environment()

        assert result.status == "pass"
        assert "3.12.0" in result.message

    def test_check_python_environment_not_found(self):
        """_check_python_environment fails when no python is found."""
        from core.doctor import _check_python_environment

        with patch("core.doctor.subprocess.run", side_effect=FileNotFoundError):
            result = _check_python_environment()

        assert result.status == "fail"

    def test_check_path_sanity(self):
        """_check_path_sanity returns results for expected directories."""
        from core.doctor import _check_path_sanity

        results = _check_path_sanity()
        assert len(results) >= 2
        # Each result should be a CheckResult with pass or warn
        for r in results:
            assert r.status in ("pass", "warn")

    def test_check_plugins_none_configured(self, tmp_path: Path):
        """_check_plugins returns pass when no plugins are configured."""
        from core.doctor import _check_plugins

        # Write an empty plugins.toml
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text("[plugins]\n", encoding="utf-8")

        with patch("core.doctor.load_plugin_list", return_value={}):
            results = _check_plugins(tmp_path)

        assert len(results) == 1
        assert results[0].status == "pass"

    def test_check_plugins_missing(self, tmp_project: Path):
        """_check_plugins reports missing optional plugins as info, not warn."""
        from core.doctor import _check_plugins

        with patch("core.doctor.is_plugin_installed", return_value=False):
            results = _check_plugins(tmp_project)

        assert len(results) >= 1
        assert any(r.status == "info" and "plugins setup" in (r.fix or "") for r in results)

    def test_check_plugins_all_installed(self, tmp_project: Path):
        """_check_plugins passes when all plugins are installed."""
        from core.doctor import _check_plugins

        with (
            patch("core.doctor.is_plugin_installed", return_value=True),
            patch("core.plugins.registered_plugin_versions", return_value=None),
        ):
            results = _check_plugins(tmp_project)

        assert len(results) == 1
        assert results[0].status == "pass"


class TestCLIUpdate:
    """CLI tests for the update command."""

    def test_cli_update_success(self, tmp_project: Path, tmp_path: Path):
        """update command succeeds when git pull and deploy work."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        mock_git = MagicMock()
        mock_git.returncode = 0
        mock_git.stdout = "Already up to date.\n"

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.utils.get_project_dir", return_value=tmp_project))
            stack.enter_context(patch("core.merge.get_config_dir", return_value=tmp_path / "cfg"))
            stack.enter_context(patch("core.config.get_config_dir", return_value=tmp_path / "cfg"))
            stack.enter_context(patch("subprocess.run", return_value=mock_git))
            stack.enter_context(patch("core.merge.deploy"))
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0

    def test_cli_update_git_not_found(self):
        """update command exits 1 when git is not found."""

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 1


class TestCLIBenchmark:
    """CLI tests for the benchmark command."""

    def test_cli_benchmark(self):
        """benchmark command runs without error."""
        from core.benchmark import BenchmarkResult

        mock_results = [
            BenchmarkResult("nushell", 42.5, 3.2, 38.0, 47.0, 5, False),
        ]

        with patch("core.benchmark.run_benchmark", return_value=mock_results):
            result = runner.invoke(app, ["benchmark", "--shell", "nushell"])

        assert result.exit_code == 0


class TestCLIRollback:
    """CLI tests for the rollback command."""

    def test_cli_rollback_no_backups(self, tmp_path: Path):
        """rollback warns when no backups are available."""
        with (
            patch("core.backup.list_backups", return_value=[]),
            patch("core.config.get_config_dir", return_value=tmp_path),
        ):
            result = runner.invoke(app, ["rollback", "--shell", "nushell"])

        assert result.exit_code == 0

    def test_cli_rollback_with_backup(self, tmp_path: Path):
        """rollback previews the diff, confirms, then restores the latest backup."""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / "config.nu").write_text("# backed up\n", encoding="utf-8")

        with (
            patch("core.backup.list_backups", return_value=[backup_dir]),
            patch("core.backup.restore_backup") as mock_restore,
            patch("core.config.get_config_dir", return_value=tmp_path),
        ):
            result = runner.invoke(app, ["rollback", "--shell", "nushell"], input="y\n")

        assert result.exit_code == 0
        mock_restore.assert_called_once_with(backup_dir, tmp_path, "nushell")

    def test_cli_rollback_declined_does_not_restore(self, tmp_path: Path):
        """Answering no at the confirm leaves the deployed config untouched."""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / "config.nu").write_text("# backed up\n", encoding="utf-8")

        with (
            patch("core.backup.list_backups", return_value=[backup_dir]),
            patch("core.backup.restore_backup") as mock_restore,
            patch("core.config.get_config_dir", return_value=tmp_path),
        ):
            result = runner.invoke(app, ["rollback", "--shell", "nushell"], input="n\n")

        assert result.exit_code == 0
        mock_restore.assert_not_called()

    def test_cli_rollback_with_to_option(self, tmp_path: Path):
        """rollback --to selects a specific backup by name."""
        backup1 = tmp_path / "2026-03-01T00-00-00"
        backup2 = tmp_path / "2026-03-02T00-00-00"
        backup1.mkdir()
        backup2.mkdir()
        (backup1 / "config.nu").write_text("# restore me\n", encoding="utf-8")

        with (
            patch("core.backup.list_backups", return_value=[backup2, backup1]),
            patch("core.backup.restore_backup") as mock_restore,
            patch("core.config.get_config_dir", return_value=tmp_path),
        ):
            result = runner.invoke(
                app,
                ["rollback", "--shell", "nushell", "--to", "2026-03-01T00-00-00"],
                input="y\n",
            )

        assert result.exit_code == 0
        mock_restore.assert_called_once_with(backup1, tmp_path, "nushell")

    def test_cli_rollback_with_to_not_found(self, tmp_path: Path):
        """rollback --to exits 1 when no matching backup found."""
        backup_dir = tmp_path / "2026-03-01T00-00-00"
        backup_dir.mkdir()

        with (
            patch("core.backup.list_backups", return_value=[backup_dir]),
            patch("core.config.get_config_dir", return_value=tmp_path),
        ):
            result = runner.invoke(app, ["rollback", "--shell", "nushell", "--to", "nonexistent"])

        assert result.exit_code == 1

    def test_cli_rollback_no_changes_skips_restore(self, tmp_path: Path):
        """When the backup matches the deployed config, rollback restores nothing."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (config_dir / "config.nu").write_text("# same\n", encoding="utf-8")
        (backup_dir / "config.nu").write_text("# same\n", encoding="utf-8")

        with (
            patch("core.backup.list_backups", return_value=[backup_dir]),
            patch("core.backup.restore_backup") as mock_restore,
            patch("core.config.get_config_dir", return_value=config_dir),
        ):
            result = runner.invoke(app, ["rollback", "--shell", "nushell"])

        assert result.exit_code == 0
        assert "nothing to restore" in result.output
        mock_restore.assert_not_called()


class TestCLIInit:
    """CLI tests for the init command."""

    def test_cli_init(self):
        """init command calls run_init_wizard."""
        from io import StringIO

        fake_stdin = StringIO()
        fake_stdin.isatty = lambda: True  # type: ignore[attr-defined]
        with (
            patch("core.cli.sys") as mock_sys,
            patch("core.init_wizard.run_init_wizard") as mock_wizard,
        ):
            mock_sys.stdin = fake_stdin
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        mock_wizard.assert_called_once()

    def test_cli_init_non_interactive(self):
        """init command exits 1 when stdin is not a TTY."""
        from io import StringIO

        fake_stdin = StringIO()
        fake_stdin.isatty = lambda: False  # type: ignore[attr-defined]
        with patch("core.cli.sys") as mock_sys:
            mock_sys.stdin = fake_stdin
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "Interactive terminal required" in result.output


class TestCLISetupDryRun:
    """CLI tests for setup --dry-run and --profile."""

    def test_setup_dry_run(self, tmp_project: Path, tmp_path: Path):
        """setup --dry-run calls show_dry_run_diff instead of deploy."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.utils.get_project_dir", return_value=tmp_project))
            mock_diff = stack.enter_context(patch("core.dry_run.show_dry_run_diff"))
            result = runner.invoke(app, ["setup", "--dry-run", "--shell", "nushell"])

        assert result.exit_code == 0
        mock_diff.assert_called_once_with("nushell")

    def test_setup_with_profile(self, tmp_project: Path, tmp_path: Path):
        """setup --profile applies the profile before deploying."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.utils.get_project_dir", return_value=tmp_project))
            stack.enter_context(patch("core.merge.get_config_dir", return_value=tmp_path / "cfg"))
            stack.enter_context(patch("core.config.get_config_dir", return_value=tmp_path / "cfg"))
            mock_apply = stack.enter_context(patch("core.profiles.apply_profile"))
            stack.enter_context(patch("core.merge.deploy"))
            result = runner.invoke(app, ["setup", "--profile", "minimal", "--shell", "nushell"])

        assert result.exit_code == 0
        mock_apply.assert_called_once_with("minimal")


class TestBackupXonsh:
    """Backup tests specific to xonsh paths."""

    def test_backup_xonsh_xonshrc(self, tmp_project: Path, tmp_path: Path):
        """backup_before_deploy backs up .xonshrc for xonsh."""
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "xonsh_cfg"
        config_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        xonshrc = fake_home / ".xonshrc"
        xonshrc.write_text("# xonsh config\n", encoding="utf-8")

        with (
            patch("core.backup.get_project_dir", return_value=tmp_project),
            patch("core.backup.get_home_dir", return_value=fake_home),
        ):
            result = backup_before_deploy("xonsh", config_dir, project_dir=tmp_project)

        assert result is not None
        assert (result / ".xonshrc").exists()

    def test_backup_unknown_shell(self, tmp_project: Path, tmp_path: Path):
        """backup_before_deploy returns None for unknown shell."""
        from core.backup import backup_before_deploy

        config_dir = tmp_path / "cfg"
        config_dir.mkdir()

        with patch("core.backup.get_project_dir", return_value=tmp_project):
            result = backup_before_deploy("fish", config_dir, project_dir=tmp_project)

        assert result is None

    def test_restore_backup_xonsh(self, tmp_path: Path):
        """restore_backup puts .xonshrc back in home directory."""
        from core.backup import restore_backup

        config_dir = tmp_path / "xonsh_cfg"
        config_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        (backup_dir / ".xonshrc").write_text("# restored\n", encoding="utf-8")

        with patch("core.backup.get_home_dir", return_value=fake_home):
            restore_backup(backup_dir, config_dir, "xonsh")

        assert (fake_home / ".xonshrc").exists()
        assert (fake_home / ".xonshrc").read_text(encoding="utf-8") == "# restored\n"


class TestConfigHelpers:
    """Tests for enable/disable config helpers."""

    def test_is_integration_enabled_default(self):
        """is_integration_enabled returns True when not specified."""
        from core.config import is_integration_enabled

        settings = {}
        assert is_integration_enabled(settings, "zoxide") is True

    def test_is_integration_enabled_bool_true(self):
        """is_integration_enabled returns True for bool True."""
        from core.config import is_integration_enabled

        settings = {"integrations": {"zoxide": True}}
        assert is_integration_enabled(settings, "zoxide") is True

    def test_is_integration_enabled_bool_false(self):
        """is_integration_enabled returns False for bool False."""
        from core.config import is_integration_enabled

        settings = {"integrations": {"zoxide": False}}
        assert is_integration_enabled(settings, "zoxide") is False

    def test_is_integration_enabled_dict_form(self):
        """is_integration_enabled handles dict form with 'enabled' key."""
        from core.config import is_integration_enabled

        settings = {"integrations": {"zoxide": {"enabled": True, "defer": True}}}
        assert is_integration_enabled(settings, "zoxide") is True

    def test_is_integration_deferred_true(self):
        """is_integration_deferred returns True for deferred integration."""
        from core.config import is_integration_deferred

        settings = {"integrations": {"carapace": {"enabled": True, "defer": True}}}
        assert is_integration_deferred(settings, "carapace") is True

    def test_is_integration_deferred_false(self):
        """is_integration_deferred returns False when not deferred."""
        from core.config import is_integration_deferred

        settings = {"integrations": {"carapace": True}}
        assert is_integration_deferred(settings, "carapace") is False

    def test_is_command_group_enabled_default(self):
        """is_command_group_enabled returns True by default."""
        from core.config import is_command_group_enabled

        settings = {}
        assert is_command_group_enabled(settings, "navigation") is True

    def test_is_command_group_enabled_false(self):
        """is_command_group_enabled returns False when disabled."""
        from core.config import is_command_group_enabled

        settings = {"commands": {"navigation": False}}
        assert is_command_group_enabled(settings, "navigation") is False


class TestBenchmarkDetailed:
    """Tests for _benchmark_detailed with hyperfine."""

    def test_benchmark_detailed_hyperfine(self):
        """_benchmark_detailed uses hyperfine when requested."""
        import json

        from core.benchmark import _benchmark_detailed

        hf_data = {"results": [{"mean": 0.03, "stddev": 0.001, "min": 0.025, "max": 0.035}]}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(hf_data)

        with patch("core.benchmark.subprocess.run", return_value=mock_result):
            results = _benchmark_detailed("nushell", "nu", runs=3, use_hyperfine=True)

        assert len(results) == 2
        assert all(r.used_hyperfine for r in results)

    def test_benchmark_detailed_xonsh(self):
        """_benchmark_detailed works for xonsh too."""
        from core.benchmark import _benchmark_detailed

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("core.benchmark.subprocess.run", return_value=mock_result):
            results = _benchmark_detailed("xonsh", "xonsh", runs=2, use_hyperfine=False)

        assert len(results) == 2

    def test_run_benchmark_hyperfine_path(self):
        """run_benchmark uses hyperfine path when available."""
        import json

        from core.benchmark import run_benchmark

        hf_data = {"results": [{"mean": 0.04, "stddev": 0.002, "min": 0.035, "max": 0.05}]}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(hf_data)

        def mock_is_available(name):
            return True  # both shell and hyperfine available

        with (
            patch("core.benchmark.is_available", side_effect=mock_is_available),
            patch("core.benchmark.subprocess.run", return_value=mock_result),
        ):
            results = run_benchmark(shells=["nushell"])

        assert len(results) == 1
        assert results[0].used_hyperfine is True

    def test_run_benchmark_detailed_path(self):
        """run_benchmark calls detailed when --detailed is True."""
        from core.benchmark import run_benchmark

        mock_result = MagicMock()
        mock_result.returncode = 0

        def mock_is_available(name):
            return name == "nu"

        with (
            patch("core.benchmark.is_available", side_effect=mock_is_available),
            patch("core.benchmark.subprocess.run", return_value=mock_result),
        ):
            results = run_benchmark(shells=["nushell"], detailed=True)

        assert len(results) == 2

    def test_run_benchmark_default_shells(self):
        """run_benchmark uses all shells when none specified."""
        from core.benchmark import run_benchmark

        with patch("core.benchmark.is_available", return_value=False):
            results = run_benchmark()

        assert results == []


class TestInitWizardSetup:
    """Tests for init wizard with setup execution."""

    def test_init_wizard_runs_setup(self, tmp_project: Path):
        """Wizard runs deploy for selected shells when user confirms setup."""
        from core.init_wizard import run_init_wizard

        def mock_is_available(name):
            return name == "nu"

        confirm_calls = iter([True, True, True])  # configure, write, run_setup=True

        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
            patch("core.init_wizard.typer.confirm", side_effect=confirm_calls),
            patch("core.merge.deploy") as mock_deploy,
        ):
            run_init_wizard(project_dir=tmp_project)

        mock_deploy.assert_called_once_with("nushell", force=True, validate=False)

    def test_init_wizard_setup_failure(self, tmp_project: Path):
        """Wizard handles deploy failure gracefully."""
        from core.init_wizard import run_init_wizard

        def mock_is_available(name):
            return name == "nu"

        confirm_calls = iter([True, True, True])

        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_project),
            patch("core.init_wizard.typer.confirm", side_effect=confirm_calls),
            patch("core.merge.deploy", side_effect=Exception("deploy failed")),
        ):
            run_init_wizard(project_dir=tmp_project)

    def test_init_wizard_no_omp_themes(self, tmp_path: Path):
        """Wizard handles missing OMP themes dir gracefully."""
        from core.init_wizard import run_init_wizard

        # Create minimal project structure
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text("[git]\ng = 'git'\n", encoding="utf-8")

        def mock_is_available(name):
            return name in ("nu", "oh-my-posh")

        confirm_calls = iter([True, True, False])

        with (
            patch("core.init_wizard.is_available", side_effect=mock_is_available),
            patch("core.init_wizard.get_project_dir", return_value=tmp_path),
            patch("core.init_wizard.typer.confirm", side_effect=confirm_calls),
            patch("core.init_wizard.typer.prompt", return_value="meslo"),
        ):
            run_init_wizard(project_dir=tmp_path)


class TestCLIDeployDryRun:
    """CLI tests for deploy --dry-run."""

    def test_deploy_dry_run(self, tmp_project: Path, tmp_path: Path):
        """deploy --dry-run calls show_dry_run_diff."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with ExitStack() as stack:
            _apply_patches(stack, tmp_project, fake_home)
            stack.enter_context(patch("core.utils.get_project_dir", return_value=tmp_project))
            mock_diff = stack.enter_context(patch("core.dry_run.show_dry_run_diff"))
            result = runner.invoke(app, ["deploy", "--dry-run", "--shell", "nushell"])

        assert result.exit_code == 0
        mock_diff.assert_called_once_with("nushell")


class TestDoctorEdgeCases:
    """Tests for doctor.py edge case branches."""

    def test_check_shell_binary_version_timeout(self):
        """_check_shell_binary returns warn when version detection times out."""
        import subprocess as _sp

        from core.doctor import _check_shell_binary

        with (
            patch("core.doctor.is_available", return_value=True),
            patch(
                "core.doctor.subprocess.run",
                side_effect=_sp.TimeoutExpired("nu", 10),
            ),
        ):
            result = _check_shell_binary("nu", "Nushell")

        assert result.status == "warn"
        assert "version detection failed" in result.message

    def test_check_config_valid_with_warnings(self, tmp_path: Path):
        """_check_config_valid reports warnings separately from errors."""
        from core.doctor import _check_config_valid

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Valid aliases + settings with unknown key (produces warning)
        (config_dir / "aliases.toml").write_text(
            '[git]\ng = "git"\n',
            encoding="utf-8",
        )
        (config_dir / "settings.toml").write_text(
            '[unknown_section]\nfoo = "bar"\n',
            encoding="utf-8",
        )

        results = _check_config_valid(tmp_path)
        assert len(results) >= 1
        assert any(r.status == "warn" for r in results)

    def test_check_cargo_rust_version_timeout(self):
        """_check_cargo_rust handles rustc version timeout."""
        import subprocess as _sp

        from core.doctor import _check_cargo_rust

        def mock_is_available(name):
            return True

        with (
            patch("core.doctor.is_available", side_effect=mock_is_available),
            patch(
                "core.doctor.subprocess.run",
                side_effect=_sp.TimeoutExpired("rustc", 10),
            ),
        ):
            results = _check_cargo_rust()

        # cargo should pass (is_available=True), rustc should warn (timeout)
        rustc_results = [r for r in results if "Rust compiler" in r.name]
        assert len(rustc_results) == 1
        assert rustc_results[0].status == "warn"

    def test_doctor_report_all_pass(self):
        """print_doctor_report shows green summary when all pass."""
        from core.doctor import CheckResult, print_doctor_report

        results = [
            CheckResult(name="Check 1", status="pass", message="OK"),
            CheckResult(name="Check 2", status="pass", message="OK"),
        ]
        print_doctor_report(results)

    def test_doctor_report_warnings_only(self):
        """print_doctor_report shows yellow summary with warnings only."""
        from core.doctor import CheckResult, print_doctor_report

        results = [
            CheckResult(name="Check 1", status="pass", message="OK"),
            CheckResult(name="Check 2", status="warn", message="Hmm"),
        ]
        print_doctor_report(results)


class TestCLIVersion:
    """CLI tests for the version command."""

    def test_version_output(self):
        """version command prints version string."""
        with (
            patch("core.merge._get_version", return_value="2025-01-01 12:00:00 +0000"),
            patch("core.merge._get_deployed_version", return_value=None),
            patch("core.config.get_config_dir", return_value=Path("/fake")),
        ):
            result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "my-shell" in result.output
        assert "1.0.0" in result.output

    def test_version_shows_deployed(self):
        """version command shows deployed status for each shell."""
        with (
            patch("core.merge._get_version", return_value="2025-01-01 12:00:00 +0000"),
            patch("core.merge._get_deployed_version", return_value="2025-01-01 12:00:00 +0000"),
            patch("core.config.get_config_dir", return_value=Path("/fake")),
        ):
            result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "deployed" in result.output

    def test_version_shows_not_deployed(self):
        """version command shows not-deployed when nothing deployed."""
        with (
            patch("core.merge._get_version", return_value="2025-01-01 12:00:00 +0000"),
            patch("core.merge._get_deployed_version", return_value=None),
            patch("core.config.get_config_dir", return_value=Path("/fake")),
        ):
            result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "not deployed" in result.output


class TestCLIValidate:
    """CLI tests for the validate command."""

    def test_validate_pass(self, tmp_project: Path):
        """validate returns 0 on valid config."""
        with patch("core.validate.get_project_dir", return_value=tmp_project):
            result = runner.invoke(app, ["validate"])

        assert result.exit_code == 0

    def test_validate_fail(self, tmp_path: Path):
        """validate returns 1 when config has errors."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Missing aliases.toml triggers a hard error
        with patch("core.validate.get_project_dir", return_value=tmp_path):
            result = runner.invoke(app, ["validate"])

        assert result.exit_code == 1


class TestCLIConfigShow:
    """CLI tests for the config show command."""

    def test_config_show(self, tmp_project: Path):
        """config command prints settings without error."""
        with patch("core.config.get_project_dir", return_value=tmp_project):
            result = runner.invoke(app, ["config"])

        assert result.exit_code == 0
        assert "oh-my-posh" in result.output


class TestCLIUninstall:
    """CLI tests for the uninstall command."""

    def test_uninstall_removes_files(self, tmp_path: Path):
        """uninstall removes deployed nushell config files."""
        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# Generated by my-shell\n", encoding="utf-8")
        (config_dir / "env.nu").write_text("# Generated by my-shell\n", encoding="utf-8")
        (config_dir / "user-custom.nu").write_text("# user\n", encoding="utf-8")

        with patch("core.config.get_config_dir", return_value=config_dir):
            result = runner.invoke(app, ["uninstall", "--shell", "nushell"])

        assert result.exit_code == 0
        assert not (config_dir / "config.nu").exists()
        assert not (config_dir / "env.nu").exists()

    def test_uninstall_keeps_hand_written_config(self, tmp_path: Path):
        """A config.nu my-shell never generated is kept, not deleted."""
        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# my own config\n", encoding="utf-8")

        with patch("core.config.get_config_dir", return_value=config_dir):
            result = runner.invoke(app, ["uninstall", "--shell", "nushell"])

        assert result.exit_code == 0
        assert (config_dir / "config.nu").read_text(encoding="utf-8") == "# my own config\n"

    def test_uninstall_keeps_custom(self, tmp_path: Path):
        """uninstall preserves user-custom files by default."""
        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# managed\n", encoding="utf-8")
        (config_dir / "user-custom.nu").write_text("# user\n", encoding="utf-8")

        with patch("core.config.get_config_dir", return_value=config_dir):
            result = runner.invoke(app, ["uninstall", "--shell", "nushell"])

        assert result.exit_code == 0
        assert (config_dir / "user-custom.nu").exists()

    def test_uninstall_removes_custom(self, tmp_path: Path):
        """uninstall --no-keep-custom removes user-custom files."""
        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# managed\n", encoding="utf-8")
        (config_dir / "user-custom.nu").write_text("# user\n", encoding="utf-8")

        with patch("core.config.get_config_dir", return_value=config_dir):
            result = runner.invoke(
                app, ["uninstall", "--shell", "nushell", "--no-keep-custom"], input="y\n"
            )

        assert result.exit_code == 0
        assert not (config_dir / "user-custom.nu").exists()

    def test_uninstall_nothing_to_remove(self, tmp_path: Path):
        """uninstall warns when nothing to remove."""
        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()

        with patch("core.config.get_config_dir", return_value=config_dir):
            result = runner.invoke(app, ["uninstall", "--shell", "nushell"])

        assert result.exit_code == 0

    def test_uninstall_xonsh(self, tmp_path: Path):
        """uninstall removes xonsh .xonshrc."""
        config_dir = tmp_path / "xonsh_cfg"
        config_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        xonshrc = fake_home / ".xonshrc"
        xonshrc.write_text("# Generated by my-shell\n", encoding="utf-8")

        with (
            patch("core.config.get_config_dir", return_value=config_dir),
            patch("core.uninstall.get_home_dir", return_value=fake_home),
            patch("core.backup.get_home_dir", return_value=fake_home),
        ):
            result = runner.invoke(app, ["uninstall", "--shell", "xonsh"])

        assert result.exit_code == 0
        assert not xonshrc.exists()


class TestUninstallModule:
    """Tests for core/uninstall.py."""

    def test_uninstall_nushell_managed_files(self, tmp_path: Path):
        """uninstall_shell removes config.nu/env.nu + every registry integration init."""
        from core.registry import INTEGRATION_TOOLS
        from core.uninstall import uninstall_shell

        # The managed set is derived from the registry -- assert against it, not a
        # hardcoded copy, so a new integration is covered automatically.
        expected = ["config.nu", "env.nu"] + [
            info.nushell_init_file for info in INTEGRATION_TOOLS.values() if info.nushell_init_file
        ]
        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        for fname in expected:
            (config_dir / fname).write_text("# Generated by my-shell\n", encoding="utf-8")

        removed = uninstall_shell("nushell", config_dir)
        assert len(removed) == len(expected)
        for fname in expected:
            assert not (config_dir / fname).exists()

    def test_uninstall_backs_up_before_removing(self, tmp_path: Path):
        """The removal is recoverable: a backup exists and still holds the old file."""
        from core.backup import list_backups
        from core.uninstall import uninstall_shell

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# Generated by my-shell\n", encoding="utf-8")

        uninstall_shell("nushell", config_dir)

        backups = list_backups(config_dir)
        assert backups
        assert (backups[0] / "config.nu").read_text(encoding="utf-8") == (
            "# Generated by my-shell\n"
        )

    def test_uninstall_keeps_files_my_shell_did_not_generate(self, tmp_path: Path):
        """A hand-written config.nu / env.nu is reported, not removed."""
        from core.uninstall import uninstall_shell

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# my own config\n", encoding="utf-8")
        (config_dir / "env.nu").write_text("# my own env\n", encoding="utf-8")

        removed = uninstall_shell("nushell", config_dir)

        assert removed == []
        assert (config_dir / "config.nu").exists()
        assert (config_dir / "env.nu").exists()

    def test_uninstall_nushell_keeps_custom(self, tmp_path: Path):
        """uninstall_shell keeps user-custom.nu by default."""
        from core.uninstall import uninstall_shell

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "config.nu").write_text("# managed\n", encoding="utf-8")
        (config_dir / "user-custom.nu").write_text("# user\n", encoding="utf-8")

        uninstall_shell("nushell", config_dir)
        assert (config_dir / "user-custom.nu").exists()

    def test_uninstall_nushell_removes_custom(self, tmp_path: Path):
        """uninstall_shell removes user-custom.nu when keep_custom=False."""
        from core.uninstall import uninstall_shell

        config_dir = tmp_path / "nu_cfg"
        config_dir.mkdir()
        (config_dir / "user-custom.nu").write_text("# user\n", encoding="utf-8")
        (config_dir / "user-env.nu").write_text("# user env\n", encoding="utf-8")

        removed = uninstall_shell("nushell", config_dir, keep_custom=False)
        assert not (config_dir / "user-custom.nu").exists()
        assert not (config_dir / "user-env.nu").exists()
        assert len(removed) == 2

    def test_uninstall_xonsh_removes_custom(self, tmp_path: Path):
        """uninstall_shell removes xonsh user-custom.xsh when keep_custom=False."""
        from core.uninstall import uninstall_shell

        config_dir = tmp_path / "xonsh_cfg"
        config_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        (fake_home / ".xonshrc").write_text("# Generated by my-shell\n", encoding="utf-8")
        (config_dir / "user-custom.xsh").write_text("# user\n", encoding="utf-8")

        with (
            patch("core.uninstall.get_home_dir", return_value=fake_home),
            patch("core.backup.get_home_dir", return_value=fake_home),
        ):
            removed = uninstall_shell("xonsh", config_dir, keep_custom=False)

        assert len(removed) == 2
        assert not (fake_home / ".xonshrc").exists()
        assert not (config_dir / "user-custom.xsh").exists()

    def test_uninstall_xonsh(self, tmp_path: Path):
        """uninstall_shell removes xonsh .xonshrc."""
        from core.uninstall import uninstall_shell

        config_dir = tmp_path / "xonsh_cfg"
        config_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        xonshrc = fake_home / ".xonshrc"
        xonshrc.write_text("# Generated by my-shell\n", encoding="utf-8")

        with (
            patch("core.uninstall.get_home_dir", return_value=fake_home),
            patch("core.backup.get_home_dir", return_value=fake_home),
        ):
            removed = uninstall_shell("xonsh", config_dir)

        assert len(removed) == 1
        assert not xonshrc.exists()

    def test_uninstall_unknown_shell(self, tmp_path: Path):
        """uninstall_shell returns empty list for unknown shell."""
        from core.uninstall import uninstall_shell

        removed = uninstall_shell("fish", tmp_path)
        assert removed == []


class TestDoctorInstallHints:
    """doctor surfaces the exact platform-correct install command it already knows."""

    def test_install_hint_uses_detected_manager(self):
        from core.doctor import _install_hint
        from core.registry import OPTIONAL_TOOLS

        with (
            patch("core.install.detect_package_manager", return_value="homebrew"),
            patch("core.install.is_available", return_value=False),
        ):
            assert _install_hint(OPTIONAL_TOOLS["fzf"]) == "brew install fzf"

    def test_install_hint_winget_is_copy_pasteable(self):
        from core.doctor import _install_hint
        from core.registry import INTEGRATION_TOOLS

        with (
            patch("core.install.detect_package_manager", return_value="winget"),
            patch("core.install.is_available", return_value=False),
        ):
            hint = _install_hint(INTEGRATION_TOOLS["atuin"])
        assert hint is not None
        assert hint.startswith("winget install")
        assert "Atuinsh.Atuin" in hint

    def test_install_hint_falls_back_to_cargo_when_manager_absent(self):
        from core.doctor import _install_hint
        from core.registry import OPTIONAL_TOOLS

        # pueue ships only homebrew + cargo; detected 'none' -> use cargo if present.
        with (
            patch("core.install.detect_package_manager", return_value="none"),
            patch("core.install.is_available", side_effect=lambda t: t == "cargo"),
        ):
            assert _install_hint(OPTIONAL_TOOLS["pueue"]) == "cargo install pueue"

    def test_install_hint_none_when_no_commands(self):
        from core.doctor import _install_hint
        from core.registry import ToolInfo

        assert _install_hint(ToolInfo(name="mystery")) is None

    def test_install_hint_none_when_manager_absent_and_no_fallback(self):
        from core.doctor import _install_hint
        from core.registry import INTEGRATION_TOOLS

        # oh-my-posh has only winget + homebrew (no cargo/mise); with neither
        # detected there is no command to offer -> None (keep the generic hint).
        with (
            patch("core.install.detect_package_manager", return_value="none"),
            patch("core.install.is_available", return_value=False),
        ):
            assert _install_hint(INTEGRATION_TOOLS["oh-my-posh"]) is None

    def test_doctor_missing_optional_tool_fix_is_exact_command(self):
        """End-to-end: a missing optional tool's fix carries the real command."""
        from core.doctor import _check_integration_tools

        with (
            patch("core.doctor.is_available", return_value=False),
            patch("core.install.is_available", return_value=False),
            patch("core.install.detect_package_manager", return_value="homebrew"),
            patch("core.doctor.is_integration_enabled", return_value=False),
            patch("core.doctor.load_settings", return_value={}),
        ):
            results = _check_integration_tools()

        fd = next(r for r in results if r.name == "Optional tool: fd")
        assert fd.status == "warn"
        assert fd.fix == "brew install fd"


class TestPreviewRestore:
    """preview_restore diffs a backup against the deployed config."""

    def test_preview_restore_detects_changes(self, tmp_path: Path):
        from core.backup import preview_restore

        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        backup_dir = tmp_path / "bak"
        backup_dir.mkdir()
        (config_dir / "config.nu").write_text("old\n", encoding="utf-8")
        (backup_dir / "config.nu").write_text("new\n", encoding="utf-8")

        assert preview_restore(backup_dir, config_dir, "nushell") is True

    def test_preview_restore_no_changes(self, tmp_path: Path):
        from core.backup import preview_restore

        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        backup_dir = tmp_path / "bak"
        backup_dir.mkdir()
        (config_dir / "config.nu").write_text("same\n", encoding="utf-8")
        (backup_dir / "config.nu").write_text("same\n", encoding="utf-8")

        assert preview_restore(backup_dir, config_dir, "nushell") is False


def test_is_my_shell_file_false_when_unreadable(tmp_path: Path):
    """An unreadable file is treated as not generated by my-shell, so it is kept."""
    from core.uninstall import _is_my_shell_file

    fpath = tmp_path / "config.nu"
    fpath.write_text("# Generated by my-shell\n", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=OSError("locked")):
        assert _is_my_shell_file(fpath) is False


def test_prune_backups_never_deletes_the_only_backup(tmp_path: Path):
    """max_count of 0 must not delete the backup that was just taken."""
    from core.backup import _prune_backups

    root = tmp_path / ".my-shell-backup"
    only = root / "2026-01-01T00-00-00-000000"
    only.mkdir(parents=True)

    _prune_backups(root, 0)

    assert only.exists()
