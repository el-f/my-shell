"""Tests for config validation module."""

import textwrap
from pathlib import Path

from core.validate import (
    ValidationError,
    validate_aliases,
    validate_aliases_local,
    validate_all,
    validate_and_report,
    validate_plugins,
    validate_plugins_local,
    validate_profiles,
    validate_settings,
    validate_settings_local,
)


def _write_profiles(tmp_path: Path, body: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "profiles.toml").write_text(body, encoding="utf-8")


class TestValidateProfiles:
    def test_missing_file_is_ok(self, tmp_path: Path):
        assert validate_profiles(tmp_path) == []

    def test_valid_profile_passes(self, tmp_path: Path):
        _write_profiles(
            tmp_path,
            '[profiles.custom]\nintegrations = ["zoxide"]\ncommands = ["navigation"]\n',
        )
        assert validate_profiles(tmp_path) == []

    def test_valid_inherits_passes(self, tmp_path: Path):
        _write_profiles(
            tmp_path,
            '[profiles.base]\nintegrations = ["zoxide"]\n[profiles.child]\ninherits = "base"\n',
        )
        assert validate_profiles(tmp_path) == []

    def test_unknown_integration_warns(self, tmp_path: Path):
        _write_profiles(tmp_path, '[profiles.custom]\nintegrations = ["not-a-tool"]\n')
        errors = validate_profiles(tmp_path)
        assert any(e.is_warning and "not-a-tool" in e.message for e in errors)

    def test_unknown_command_warns(self, tmp_path: Path):
        _write_profiles(tmp_path, '[profiles.custom]\ncommands = ["ghost-cmd"]\n')
        errors = validate_profiles(tmp_path)
        assert any(e.is_warning and "ghost-cmd" in e.message for e in errors)

    def test_bad_inherits_errors(self, tmp_path: Path):
        _write_profiles(tmp_path, '[profiles.custom]\ninherits = "ghost"\n')
        errors = validate_profiles(tmp_path)
        assert any(not e.is_warning and "ghost" in e.message for e in errors)

    def test_non_list_integrations_errors(self, tmp_path: Path):
        _write_profiles(tmp_path, '[profiles.custom]\nintegrations = "zoxide"\n')
        errors = validate_profiles(tmp_path)
        assert any(not e.is_warning and "list" in e.message.lower() for e in errors)

    def test_profiles_not_a_table_errors(self, tmp_path: Path):
        _write_profiles(tmp_path, 'profiles = "oops"\n')
        errors = validate_profiles(tmp_path)
        assert any("Must be a table" in e.message for e in errors)

    def test_invalid_toml_syntax_errors(self, tmp_path: Path):
        _write_profiles(tmp_path, "[profiles.custom\n")
        errors = validate_profiles(tmp_path)
        assert any("Invalid TOML syntax" in e.message for e in errors)

    def test_profile_entry_not_a_table_errors(self, tmp_path: Path):
        _write_profiles(tmp_path, "[profiles]\ncustom = 5\n")
        errors = validate_profiles(tmp_path)
        assert any("Profile must be a table" in e.message for e in errors)

    def test_unknown_profile_key_warns(self, tmp_path: Path):
        _write_profiles(tmp_path, "[profiles.custom]\nbogus = 1\n")
        errors = validate_profiles(tmp_path)
        assert any(e.is_warning and "bogus" in e.message for e in errors)

    def test_real_config_profiles_are_valid(self):
        """The shipped config/profiles.toml must pass validation."""
        from core.utils import get_project_dir

        assert validate_profiles(get_project_dir()) == []


class TestValidationError:
    def test_str_error(self):
        err = ValidationError("aliases.toml", "git.g", "bad value")
        assert str(err) == "[error] aliases.toml: git.g: bad value"

    def test_str_warning(self):
        err = ValidationError("aliases.toml", "git.g", "unknown key", is_warning=True)
        assert str(err) == "[warn] aliases.toml: git.g: unknown key"


class TestValidateAliases:
    def test_valid_aliases_passes(self, tmp_project: Path):
        """A well-formed aliases.toml from the fixture should produce no errors."""
        errors = validate_aliases(tmp_project)
        assert errors == []

    def test_unknown_keys_in_alias_dict_produce_warnings(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = { command = "git", bogus_key = true }
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        assert len(errors) == 1
        assert errors[0].is_warning is True
        assert "bogus_key" in errors[0].message

    def test_missing_required_keys_in_wrappers_produce_errors(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [wrappers.fd]
                preferred = "fd"
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Missing required keys" in hard[0].message
        assert "error" in hard[0].message
        assert "fallback" in hard[0].message

    def test_invalid_alias_type_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = 42
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "must be string or table" in hard[0].message
        assert "int" in hard[0].message

    def test_section_not_a_table_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            'git = "not a table"\n',
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        assert len(errors) == 1
        assert not errors[0].is_warning
        assert "Section must be a table" in errors[0].message

    def test_wrapper_not_a_table_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [wrappers]
                fd = "not a table"
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Wrapper must be a table" in hard[0].message

    def test_alias_dict_without_definition_key_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = { comment = "just a comment, no command" }
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "must have" in hard[0].message

    def test_file_not_found(self, tmp_path: Path):
        errors = validate_aliases(tmp_path)
        assert len(errors) == 1
        assert "File not found" in errors[0].message
        assert errors[0].file == "aliases.toml"

    def test_malformed_toml(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            "[broken\nnot = valid",
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        assert len(errors) == 1
        assert "Invalid TOML syntax" in errors[0].message

    def test_string_alias_passes(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = "git"
                gs = "git status"
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        assert errors == []

    def test_unknown_keys_in_wrapper_produce_warnings(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [wrappers.fd]
                preferred = "fd"
                fallback = "fdfind"
                error = "fd not found"
                extra_key = "unexpected"
            """),
            encoding="utf-8",
        )

        errors = validate_aliases(tmp_path)
        warnings = [e for e in errors if e.is_warning]
        assert len(warnings) == 1
        assert "extra_key" in warnings[0].message


class TestValidateSettings:
    def test_valid_settings_passes(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [oh-my-posh]
                theme = "jblab_2021"

                [integrations]
                zoxide = true
                atuin = true
                carapace = true

                [commands]
                navigation = true
                fuzzy = true

                [backup]
                max_count = 5
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        assert errors == []

    def test_unknown_section_produces_warning(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [mystery_section]
                foo = "bar"
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        assert len(errors) == 1
        assert errors[0].is_warning is True
        assert "Unknown section" in errors[0].message
        assert "mystery_section" in errors[0].message

    def test_non_bool_shell_value_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [shells]
                nushell = "yes"
                mystery = true
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        assert any(not e.is_warning and "shells.nushell" in str(e) for e in errors)
        assert not any("shells.mystery" in str(e) and "boolean" in e.message for e in errors)

    def test_invalid_integration_type_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [integrations]
                zoxide = "yes"
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be boolean or table" in hard[0].message
        assert "str" in hard[0].message

    def test_missing_file_returns_no_errors(self, tmp_path: Path):
        """settings.toml is optional; absence is not an error."""
        errors = validate_settings(tmp_path)
        assert errors == []

    def test_section_not_a_table_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            'integrations = "not a table"\n',
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Section must be a table" in hard[0].message

    def test_unknown_keys_in_section_produce_warnings(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [integrations]
                zoxide = true
                unknown_tool = true
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        warnings = [e for e in errors if e.is_warning]
        assert len(warnings) == 1
        assert "unknown_tool" in warnings[0].message

    def test_invalid_theme_type_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [oh-my-posh]
                theme = 123
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be a string" in hard[0].message

    def test_invalid_backup_max_count_type_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [backup]
                max_count = "five"
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be an integer" in hard[0].message

    def test_integration_dict_form_valid(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [integrations]
                zoxide = { enabled = true, defer = true }
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        assert errors == []

    def test_integration_dict_form_unknown_sub_keys(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [integrations]
                zoxide = { enabled = true, bogus = true }
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        warnings = [e for e in errors if e.is_warning]
        assert len(warnings) == 1
        assert "bogus" in warnings[0].message

    def test_malformed_toml(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            "[broken\nnot = valid",
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        assert len(errors) == 1
        assert "Invalid TOML syntax" in errors[0].message

    def test_shells_non_bool_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [shells]
                nushell = "yes"
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be a boolean" in hard[0].message
        assert "str" in hard[0].message

    def test_fonts_nerd_font_non_string_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [fonts]
                nerd_font = 123
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be a string" in hard[0].message

    def test_fonts_auto_install_non_bool_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [fonts]
                auto_install = "yes"
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be a boolean" in hard[0].message

    def test_backup_max_count_less_than_one_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [backup]
                max_count = 0
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be >= 1" in hard[0].message

    def test_fonts_valid_passes(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [fonts]
                nerd_font = "FiraCode Nerd Font"
                auto_install = true
            """),
            encoding="utf-8",
        )

        errors = validate_settings(tmp_path)
        assert errors == []


class TestValidatePlugins:
    def test_valid_plugins_passes(self, tmp_project: Path):
        """plugins.toml from the fixture should pass validation."""
        errors = validate_plugins(tmp_project)
        assert errors == []

    def test_missing_required_fields_produce_errors(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            textwrap.dedent("""\
                [plugins.my_plugin]
                version = "0.1.0"
            """),
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Missing required keys" in hard[0].message
        assert "crate" in hard[0].message
        assert "description" in hard[0].message

    def test_invalid_crate_name_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            textwrap.dedent("""\
                [plugins.my_plugin]
                crate = "-bad-start"
                description = "My plugin"
            """),
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        assert any(not e.is_warning and "Crate name" in e.message for e in errors)

    def test_unknown_plugin_keys_produce_warnings(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            textwrap.dedent("""\
                [plugins.my_plugin]
                crate = "my_plugin"
                description = "My plugin"
                extra_key = "unexpected"
            """),
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        warnings = [e for e in errors if e.is_warning]
        assert len(warnings) == 1
        assert "extra_key" in warnings[0].message

    def test_missing_file_returns_no_errors(self, tmp_path: Path):
        """plugins.toml is optional; absence is not an error."""
        errors = validate_plugins(tmp_path)
        assert errors == []

    def test_plugin_entry_not_a_table_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            textwrap.dedent("""\
                [plugins]
                my_plugin = "not a table"
            """),
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Plugin entry must be a table" in hard[0].message

    def test_plugins_section_not_a_table_produces_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            'plugins = "not a table"\n',
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        assert len(errors) == 1
        assert "Must be a table" in errors[0].message

    def test_malformed_toml(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            "[broken\nnot = valid",
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        assert len(errors) == 1
        assert "Invalid TOML syntax" in errors[0].message

    def test_valid_plugin_with_all_keys(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.toml").write_text(
            textwrap.dedent("""\
                [plugins.my_plugin]
                crate = "my_plugin"
                description = "My plugin"
                version = "0.1.0"
            """),
            encoding="utf-8",
        )

        errors = validate_plugins(tmp_path)
        assert errors == []


class TestValidateSettingsLocal:
    """Tests for validate_settings_local()."""

    def test_no_local_file_returns_empty(self, tmp_path: Path):
        """Missing settings.local.toml is fine."""
        errors = validate_settings_local(tmp_path)
        assert errors == []

    def test_valid_local_overrides(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text(
            textwrap.dedent("""\
                [integrations]
                atuin = false
            """),
            encoding="utf-8",
        )
        errors = validate_settings_local(tmp_path)
        assert errors == []

    def test_unknown_section_warning(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text(
            textwrap.dedent("""\
                [unknown_section]
                foo = "bar"
            """),
            encoding="utf-8",
        )
        errors = validate_settings_local(tmp_path)
        assert len(errors) == 1
        assert errors[0].is_warning
        assert "Unknown section" in errors[0].message
        assert errors[0].file == "settings.local.toml"

    def test_unknown_keys_warning(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text(
            textwrap.dedent("""\
                [integrations]
                nonexistent_tool = true
            """),
            encoding="utf-8",
        )
        errors = validate_settings_local(tmp_path)
        assert len(errors) == 1
        assert errors[0].is_warning
        assert "Unknown keys" in errors[0].message

    def test_invalid_toml_syntax(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text("not valid toml [[[", encoding="utf-8")
        errors = validate_settings_local(tmp_path)
        assert len(errors) == 1
        assert "Invalid TOML syntax" in errors[0].message

    def test_backup_max_count_zero_is_an_error(self, tmp_path: Path):
        """max_count = 0 makes each deploy delete every backup, including the new one."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text(
            "[backup]\nmax_count = 0\n", encoding="utf-8"
        )
        errors = validate_settings_local(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 1
        assert "Must be >= 1" in hard[0].message
        assert hard[0].file == "settings.local.toml"

    def test_value_types_are_checked(self, tmp_path: Path):
        """The local file gets the same per-key type checks as settings.toml."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text(
            '[shells]\nxonsh = "yes"\n\n[oh-my-posh]\ntheme = 42\n', encoding="utf-8"
        )
        errors = validate_settings_local(tmp_path)
        hard = [e for e in errors if not e.is_warning]
        assert len(hard) == 2

    def test_non_table_section_error(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.local.toml").write_text(
            'integrations = "not a table"\n',
            encoding="utf-8",
        )
        errors = validate_settings_local(tmp_path)
        assert len(errors) == 1
        assert not errors[0].is_warning
        assert "must be a table" in errors[0].message

    def test_included_in_validate_all(self, tmp_path: Path):
        """validate_all should include local file errors."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text("[git]\ng = 'git'\n", encoding="utf-8")
        (config_dir / "settings.local.toml").write_text(
            "[bogus]\nfoo = 1\n",
            encoding="utf-8",
        )
        errors = validate_all(tmp_path)
        local_errors = [e for e in errors if e.file == "settings.local.toml"]
        assert len(local_errors) >= 1


class TestValidateAll:
    def test_combines_all_results(self, tmp_path: Path):
        """validate_all should aggregate errors from all three validators."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # aliases.toml with an error (invalid type)
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = 42
            """),
            encoding="utf-8",
        )

        # settings.toml with a warning (unknown section)
        (config_dir / "settings.toml").write_text(
            textwrap.dedent("""\
                [unknown_section]
                foo = "bar"
            """),
            encoding="utf-8",
        )

        # plugins.toml with an error (missing required keys)
        (config_dir / "plugins.toml").write_text(
            textwrap.dedent("""\
                [plugins.bad_plugin]
                version = "0.1.0"
            """),
            encoding="utf-8",
        )

        errors = validate_all(tmp_path)

        files = {e.file for e in errors}
        assert "aliases.toml" in files
        assert "settings.toml" in files
        assert "plugins.toml" in files

        hard = [e for e in errors if not e.is_warning]
        warnings = [e for e in errors if e.is_warning]
        assert len(hard) >= 2  # alias type error + plugin missing keys
        assert len(warnings) >= 1  # settings unknown section

    def test_all_valid_returns_empty(self, tmp_project: Path):
        """A fully valid project should produce no errors."""
        errors = validate_all(tmp_project)
        assert errors == []


class TestValidateAndReport:
    def test_returns_true_when_no_hard_errors(self, tmp_project: Path):
        """With a valid project, validate_and_report should return True."""
        result = validate_and_report(tmp_project)
        assert result is True

    def test_returns_true_with_warnings_only(self, tmp_path: Path):
        """Warnings alone should not cause a False return."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Valid aliases with a warning (unknown key in alias dict)
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = { command = "git", bogus = true }
            """),
            encoding="utf-8",
        )

        result = validate_and_report(tmp_path)
        assert result is True

    def test_returns_false_with_hard_errors(self, tmp_path: Path):
        """Hard errors should cause validate_and_report to return False."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # aliases.toml with a hard error (invalid type)
        (config_dir / "aliases.toml").write_text(
            textwrap.dedent("""\
                [git]
                g = 42
            """),
            encoding="utf-8",
        )

        result = validate_and_report(tmp_path)
        assert result is False

    def test_returns_false_when_aliases_missing(self, tmp_path: Path):
        """Missing aliases.toml is a hard error (file not found)."""
        result = validate_and_report(tmp_path)
        assert result is False


class TestNonExistentFiles:
    def test_aliases_not_found_is_error(self, tmp_path: Path):
        errors = validate_aliases(tmp_path)
        assert len(errors) == 1
        assert not errors[0].is_warning
        assert "File not found" in errors[0].message

    def test_settings_not_found_is_ok(self, tmp_path: Path):
        errors = validate_settings(tmp_path)
        assert errors == []

    def test_plugins_not_found_is_ok(self, tmp_path: Path):
        errors = validate_plugins(tmp_path)
        assert errors == []

    def test_nonexistent_project_dir(self, tmp_path: Path):
        fake_dir = tmp_path / "does_not_exist"
        errors = validate_aliases(fake_dir)
        assert len(errors) == 1
        assert "File not found" in errors[0].message


class TestLocalOverrideFiles:
    """aliases.local.toml and plugins.local.toml reach the renderer, so they get checked."""

    def test_missing_local_files_are_ok(self, tmp_path: Path):
        assert validate_aliases_local(tmp_path) == []
        assert validate_plugins_local(tmp_path) == []

    def test_bad_alias_value_in_local_file_is_an_error(self, tmp_path: Path):
        """Without this, render crashes with an AttributeError instead of a clear message."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.local.toml").write_text("[git]\nst = 42\n", encoding="utf-8")

        errors = validate_aliases_local(tmp_path)
        assert len(errors) == 1
        assert not errors[0].is_warning
        assert errors[0].file == "aliases.local.toml"
        assert "must be string or table" in errors[0].message

    def test_plugin_missing_crate_in_local_file_is_an_error(self, tmp_path: Path):
        """Without this, install_plugins crashes with a KeyError."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.local.toml").write_text(
            '[plugins.nu_plugin_x]\ndescription = "no crate"\n', encoding="utf-8"
        )

        errors = validate_plugins_local(tmp_path)
        assert len(errors) == 1
        assert "Missing required keys: crate" in errors[0].message

    def test_plugin_name_outside_the_character_set_is_an_error(self, tmp_path: Path):
        """A plugin name becomes a filesystem path, so traversal must be rejected."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "plugins.local.toml").write_text(
            '[plugins."../../../tmp/evil"]\ncrate = "x"\ndescription = "y"\n', encoding="utf-8"
        )

        errors = validate_plugins_local(tmp_path)
        assert any("Plugin name must start with" in e.message for e in errors)

    def test_local_files_are_included_in_validate_all(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "aliases.toml").write_text("[git]\ng = 'git'\n", encoding="utf-8")
        (config_dir / "aliases.local.toml").write_text("[git]\nst = 42\n", encoding="utf-8")
        (config_dir / "plugins.local.toml").write_text(
            '[plugins.nu_plugin_x]\ndescription = "no crate"\n', encoding="utf-8"
        )

        files = {e.file for e in validate_all(tmp_path)}
        assert "aliases.local.toml" in files
        assert "plugins.local.toml" in files


class TestThemeNameSafety:
    """The theme name is written into generated shell code."""

    def test_newline_in_theme_is_rejected(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # TOML's \n escape is valid TOML and yields a real newline in the value.
        (config_dir / "settings.toml").write_text(
            '[oh-my-posh]\ntheme = "jblab_2021\\nimport os"\n', encoding="utf-8"
        )
        errors = validate_settings(tmp_path)
        assert any(not e.is_warning and "oh-my-posh.theme" in str(e) for e in errors)

    def test_normal_theme_name_passes(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "settings.toml").write_text(
            '[oh-my-posh]\ntheme = "jblab_2021"\n', encoding="utf-8"
        )
        assert validate_settings(tmp_path) == []


def test_escape_python_path_escapes_newlines():
    from core.utils import escape_python_path

    assert "\n" not in escape_python_path("theme\nimport os")
