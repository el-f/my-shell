"""Tests for nushell plugin management."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.plugins import (
    DEFAULT_PLUGINS,
    _get_nu_version,
    _get_rustc_version,
    generate_plugin_use_statements,
    get_cargo_bin_dir,
    install_plugin,
    install_plugins,
    is_plugin_installed,
    load_plugin_list,
    register_plugin,
    register_plugins,
)


def test_load_plugin_list_from_toml(tmp_project: Path):
    plugins = load_plugin_list(tmp_project)
    assert "nu_plugin_gstat" in plugins
    assert plugins["nu_plugin_gstat"]["crate"] == "nu_plugin_gstat"


def test_load_plugin_list_fallback_no_file(tmp_path: Path):
    """When plugins.toml doesn't exist, falls back to DEFAULT_PLUGINS."""
    plugins = load_plugin_list(tmp_path)
    assert plugins == DEFAULT_PLUGINS


def test_load_plugin_list_local_override(tmp_project: Path):
    """plugins.local.toml merges into base."""
    local = tmp_project / "config" / "plugins.local.toml"
    local.write_text(
        '[plugins.nu_plugin_custom]\ncrate = "nu_plugin_custom"\ndescription = "Custom plugin"\n',
        encoding="utf-8",
    )
    plugins = load_plugin_list(tmp_project)
    assert "nu_plugin_custom" in plugins
    assert "nu_plugin_gstat" in plugins


def test_get_cargo_bin_dir():
    d = get_cargo_bin_dir()
    assert d.name == "bin"
    assert d.parent.name == ".cargo"


def test_is_plugin_installed_true(tmp_path: Path):
    with (
        patch("core.plugins.get_cargo_bin_dir", return_value=tmp_path),
        patch("core.plugins.is_windows", return_value=False),
    ):
        (tmp_path / "nu_plugin_gstat").write_text("fake", encoding="utf-8")
        assert is_plugin_installed("nu_plugin_gstat") is True


def test_is_plugin_installed_false(tmp_path: Path):
    with (
        patch("core.plugins.get_cargo_bin_dir", return_value=tmp_path),
        patch("core.plugins.is_windows", return_value=False),
    ):
        assert is_plugin_installed("nu_plugin_gstat") is False


def test_is_plugin_installed_windows(tmp_path: Path):
    with (
        patch("core.plugins.get_cargo_bin_dir", return_value=tmp_path),
        patch("core.plugins.is_windows", return_value=True),
    ):
        (tmp_path / "nu_plugin_gstat.exe").write_text("fake", encoding="utf-8")
        assert is_plugin_installed("nu_plugin_gstat") is True


def test_get_rustc_version_parses():
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="rustc 1.85.0 (abc 2025-01-01)", stderr=""
    )
    with patch("core.plugins.subprocess.run", return_value=fake):
        assert _get_rustc_version() == (1, 85, 0)


def test_get_rustc_version_old():
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="rustc 1.62.0 (abc 2022-06-30)", stderr=""
    )
    with patch("core.plugins.subprocess.run", return_value=fake):
        assert _get_rustc_version() == (1, 62, 0)


def test_get_rustc_version_not_found():
    with patch("core.plugins.subprocess.run", side_effect=FileNotFoundError):
        assert _get_rustc_version() is None


def test_get_rustc_version_bad_output():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="something weird", stderr="")
    with patch("core.plugins.subprocess.run", return_value=fake):
        assert _get_rustc_version() is None


def test_get_nu_version_parses():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="0.109.1\n", stderr="")
    with patch("core.plugins.subprocess.run", return_value=fake):
        assert _get_nu_version() == "0.109.1"


def test_get_nu_version_not_found():
    with patch("core.plugins.subprocess.run", side_effect=FileNotFoundError):
        assert _get_nu_version() is None


def test_get_nu_version_bad_output():
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="not a version", stderr="")
    with patch("core.plugins.subprocess.run", return_value=fake):
        assert _get_nu_version() is None


# install_plugin


def _mock_popen(returncode=0, stderr_text=""):
    """Create a mock Popen object with communicate()."""
    mock = MagicMock()
    mock.communicate.return_value = (None, stderr_text)
    mock.returncode = returncode
    return mock


def test_install_plugin_success():
    mock_proc = _mock_popen(returncode=0)
    with patch("core.plugins.subprocess.Popen", return_value=mock_proc):
        assert install_plugin("nu_plugin_gstat") is True


def test_install_plugin_failure():
    mock_proc = _mock_popen(returncode=1, stderr_text="some error")
    with patch("core.plugins.subprocess.Popen", return_value=mock_proc):
        assert install_plugin("nu_plugin_gstat") is False


def test_install_plugin_with_version():
    """install_plugin passes --version to cargo when specified."""
    mock_proc = _mock_popen(returncode=0)
    with patch("core.plugins.subprocess.Popen", return_value=mock_proc) as mock_popen:
        assert install_plugin("nu_plugin_gstat", version="~0.109") is True
    cmd = mock_popen.call_args[0][0]
    assert "--version" in cmd
    assert "~0.109" in cmd


def test_install_plugin_ends_options_before_the_crate_name():
    """`--` stops cargo from reading a crate name that starts with a dash as a flag."""
    mock_proc = _mock_popen(returncode=0)
    with patch("core.plugins.subprocess.Popen", return_value=mock_proc) as mock_popen:
        install_plugin("nu_plugin_gstat")
    cmd = mock_popen.call_args[0][0]
    assert cmd[-2:] == ["--", "nu_plugin_gstat"]


@pytest.mark.parametrize(
    "plugins_toml",
    [
        '[plugins."../../../tmp/evil"]\ncrate = "x"\ndescription = "y"\n',
        '[plugins.nu_plugin_x]\ncrate = "--git"\ndescription = "y"\n',
    ],
    ids=["name", "crate"],
)
def test_load_plugin_list_rejects_values_outside_the_character_set(
    tmp_project: Path, plugins_toml: str
):
    """A plugin name becomes a path; a crate name becomes a cargo argument."""
    (tmp_project / "config" / "plugins.toml").write_text(plugins_toml, encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid"):
        load_plugin_list(tmp_project)


def test_install_plugin_rejects_incompatible_latest_fallback():
    """A missing Nu-matched version must not fall back to an incompatible latest."""
    call_count = 0

    def _mock_popen_fn(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_popen(
            returncode=101,
            stderr_text="error: could not find `nu_plugin_clipboard` in registry `crates-io` with version `~0.109`",
        )

    with patch("core.plugins.subprocess.Popen", side_effect=_mock_popen_fn):
        assert install_plugin("nu_plugin_clipboard", version="~0.109") is False
    assert call_count == 1


def test_install_plugin_version_fallback_also_fails():
    """A failed versioned install returns False."""
    mock_proc = _mock_popen(
        returncode=101,
        stderr_text="error: could not find `nu_plugin_bad` in registry",
    )
    with patch("core.plugins.subprocess.Popen", return_value=mock_proc):
        assert install_plugin("nu_plugin_bad", version="~0.109") is False


def test_install_plugin_build_failure_no_retry():
    """Compilation errors must not trigger a version-fallback retry."""
    call_count = 0

    def _mock_popen_fn(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_popen(
            returncode=1,
            stderr_text=(
                "error[E0432]: unresolved import\n"
                "error: could not compile `nu-plugin-core` (lib) due to 1 previous error\n"
                "error: failed to compile `nu_plugin_query v0.110.0`\n"
            ),
        )

    with patch("core.plugins.subprocess.Popen", side_effect=_mock_popen_fn):
        assert install_plugin("nu_plugin_query", version="~0.110") is False
    assert call_count == 1  # must NOT retry on build failure


def test_install_plugin_compilation_error_no_retry():
    """Compilation errors produce a generic error and don't retry."""
    mock_proc = _mock_popen(
        returncode=1,
        stderr_text=(
            "error[E0432]: unresolved import "
            "`interprocess::local_socket::traits::ListenerNonblockingMode`\n"
            "error: could not compile `nu-plugin-core` (lib)\n"
            "error: failed to compile `nu_plugin_query v0.110.0`\n"
        ),
    )
    with (
        patch("core.plugins.subprocess.Popen", return_value=mock_proc),
        patch("core.plugins.log_error") as mock_err,
    ):
        assert install_plugin("nu_plugin_query", version="~0.110") is False

    error_msg = mock_err.call_args[0][0]
    assert "compilation error" in error_msg


def test_install_plugin_cargo_not_found():
    """install_plugin handles FileNotFoundError (cargo missing)."""
    with patch("core.plugins.subprocess.Popen", side_effect=FileNotFoundError):
        assert install_plugin("nu_plugin_gstat") is False


def test_install_plugin_timeout_kills_and_reaps_process():
    """The timeout applies while cargo is running, not after stderr closes."""
    mock_proc = _mock_popen()
    mock_proc.communicate.side_effect = [
        subprocess.TimeoutExpired(["cargo", "install"], 600),
        (None, "partial cargo output\n"),
    ]
    with patch("core.plugins.subprocess.Popen", return_value=mock_proc):
        assert install_plugin("nu_plugin_gstat") is False

    mock_proc.kill.assert_called_once()
    assert mock_proc.communicate.call_count == 2


def test_install_plugins_no_cargo(tmp_project: Path):
    with (
        patch("core.plugins.is_available", return_value=False),
        patch("core.plugins.log_error") as mock_err,
    ):
        install_plugins(tmp_project)
    assert "cargo" in mock_err.call_args[0][0].lower()


def test_install_plugins_rust_too_old(tmp_project: Path):
    """install_plugins returns early with clear error if Rust is too old."""
    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 62, 0)),
        patch("core.plugins.install_plugin") as mock_install,
        patch("core.plugins.log_error") as mock_err,
    ):
        install_plugins(tmp_project)

    mock_install.assert_not_called()
    err_msg = mock_err.call_args[0][0]
    assert "1.62.0" in err_msg
    assert "rustup update" in err_msg


def test_install_plugins_rust_ok_proceeds(tmp_project: Path):
    """install_plugins proceeds when Rust version meets minimum."""
    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 85, 0)),
        patch("core.plugins._get_nu_version", return_value=None),
        patch("core.plugins.is_plugin_installed", return_value=True),
        patch("core.plugins.log_success") as mock_success,
    ):
        install_plugins(tmp_project)

    summary = mock_success.call_args_list[-1][0][0]
    assert "already present" in summary


def test_install_plugins_rust_none_proceeds(tmp_project: Path):
    """install_plugins proceeds if rustc version can't be detected (None)."""
    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=None),
        patch("core.plugins._get_nu_version", return_value=None),
        patch("core.plugins.is_plugin_installed", return_value=True),
        patch("core.plugins.log_success") as mock_success,
    ):
        install_plugins(tmp_project)

    summary = mock_success.call_args_list[-1][0][0]
    assert "already present" in summary


def test_install_plugins_skips_installed(tmp_project: Path):
    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 85, 0)),
        patch("core.plugins._get_nu_version", return_value=None),
        patch("core.plugins.is_plugin_installed", return_value=True),
        patch("core.plugins.install_plugin") as mock_install,
    ):
        install_plugins(tmp_project)

    mock_install.assert_not_called()


def test_install_plugins_passes_nu_version(tmp_project: Path):
    """install_plugins auto-detects Nu version and passes version spec."""
    install_calls = []

    def _mock_install(crate, version=None):
        install_calls.append((crate, version))
        return True

    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 85, 0)),
        patch("core.plugins._get_nu_version", return_value="0.109.1"),
        patch("core.plugins.is_plugin_installed", return_value=False),
        patch("core.plugins.install_plugin", side_effect=_mock_install),
    ):
        install_plugins(tmp_project)

    # All plugins in tmp_project start with nu_plugin_, so all should get version
    for crate, ver in install_calls:
        assert ver == "~0.109", f"{crate} should have version ~0.109, got {ver}"


def test_install_plugins_explicit_version_override(tmp_project: Path):
    """Explicit version in config takes priority over auto-detected Nu version."""
    # Add version field to one plugin
    config_path = tmp_project / "config" / "plugins.toml"
    config_path.write_text(
        "[plugins.nu_plugin_gstat]\n"
        'crate = "nu_plugin_gstat"\n'
        'description = "Git status"\n'
        'version = "0.100.0"\n',
        encoding="utf-8",
    )

    install_calls = []

    def _mock_install(crate, version=None):
        install_calls.append((crate, version))
        return True

    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 85, 0)),
        patch("core.plugins._get_nu_version", return_value="0.109.1"),
        patch("core.plugins.is_plugin_installed", return_value=False),
        patch("core.plugins.install_plugin", side_effect=_mock_install),
    ):
        install_plugins(tmp_project)

    # Explicit version should override auto-detected
    assert install_calls[0] == ("nu_plugin_gstat", "0.100.0")


def test_install_plugins_counts_failures(tmp_project: Path):
    """When install_plugin returns False, it is not counted as installed."""
    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 85, 0)),
        patch("core.plugins._get_nu_version", return_value=None),
        patch("core.plugins.is_plugin_installed", return_value=False),
        patch("core.plugins.install_plugin", return_value=False),
        patch("core.plugins.log_success") as mock_success,
    ):
        success = install_plugins(tmp_project)

    # Final summary should show 0 installed
    summary_call = mock_success.call_args_list[-1]
    assert "0 installed" in summary_call[0][0]
    assert "failed" in summary_call[0][0]
    assert success is False


def test_register_plugin_success(tmp_path: Path):
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with (
        patch("core.plugins.get_cargo_bin_dir", return_value=tmp_path),
        patch("core.plugins.is_windows", return_value=False),
        patch("core.plugins.subprocess.run", return_value=fake_result),
    ):
        (tmp_path / "nu_plugin_gstat").write_text("fake", encoding="utf-8")
        assert register_plugin("nu_plugin_gstat") is True


def test_register_plugin_missing_binary(tmp_path: Path):
    with (
        patch("core.plugins.get_cargo_bin_dir", return_value=tmp_path),
        patch("core.plugins.is_windows", return_value=False),
    ):
        assert register_plugin("nu_plugin_gstat") is False


def test_register_plugins_no_nu(tmp_project: Path):
    with (
        patch("core.plugins.is_available", return_value=False),
        patch("core.plugins.log_error") as mock_err,
    ):
        register_plugins(tmp_project)
    assert "nu" in mock_err.call_args[0][0].lower()


def test_generate_plugin_use_statements_none_installed(tmp_project: Path):
    with (
        patch("core.plugins.is_plugin_installed", return_value=False),
    ):
        result = generate_plugin_use_statements(tmp_project)
    assert "No nushell plugins installed" in result


def test_generate_plugin_use_statements_some_installed(tmp_project: Path):
    def mock_installed(name: str) -> bool:
        return name == "nu_plugin_gstat"

    with patch("core.plugins.is_plugin_installed", side_effect=mock_installed):
        result = generate_plugin_use_statements(tmp_project)
    assert "plugin use gstat" in result
    assert "plugin use formats" not in result


def test_load_plugin_list_local_override_non_dict_skipped(tmp_project: Path):
    """Non-dict values in plugins.local.toml are skipped."""
    local = tmp_project / "config" / "plugins.local.toml"
    local.write_text(
        '[plugins]\nnu_plugin_gstat = "not a dict"\n',
        encoding="utf-8",
    )
    plugins = load_plugin_list(tmp_project)
    # The original dict should remain unchanged
    assert isinstance(plugins["nu_plugin_gstat"], dict)
    assert plugins["nu_plugin_gstat"]["crate"] == "nu_plugin_gstat"


def test_register_plugin_subprocess_error(tmp_path: Path):
    """register_plugin returns False when subprocess raises."""
    (tmp_path / "nu_plugin_gstat").write_text("fake", encoding="utf-8")

    with (
        patch("core.plugins.get_cargo_bin_dir", return_value=tmp_path),
        patch("core.plugins.is_windows", return_value=False),
        patch(
            "core.plugins.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "nu"),
        ),
    ):
        assert register_plugin("nu_plugin_gstat") is False


def test_register_plugins_mixed(tmp_project: Path):
    """register_plugins only registers installed plugins."""
    call_log = []

    def _mock_installed(name):
        return name == "nu_plugin_gstat"

    def _mock_register(name):
        call_log.append(name)
        return True

    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins.is_plugin_installed", side_effect=_mock_installed),
        patch("core.plugins.register_plugin", side_effect=_mock_register),
        patch("core.plugins.log_success") as mock_success,
    ):
        register_plugins(tmp_project)

    # Only nu_plugin_gstat should be registered
    assert "nu_plugin_gstat" in call_log
    assert "nu_plugin_formats" not in call_log
    summary_call = mock_success.call_args_list[-1]
    assert "1 registered" in summary_call[0][0]
    assert "0 failed" in summary_call[0][0]


def test_register_plugins_counts_failures(tmp_project: Path):
    """register_plugins tracks and reports registration failures."""

    def _mock_installed(name):
        return name == "nu_plugin_gstat"

    def _mock_register(name):
        return False  # simulate failure

    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins.is_plugin_installed", side_effect=_mock_installed),
        patch("core.plugins.register_plugin", side_effect=_mock_register),
        patch("core.plugins.log_success") as mock_success,
    ):
        register_plugins(tmp_project)

    summary_call = mock_success.call_args_list[-1]
    assert "0 registered" in summary_call[0][0]
    assert "1 failed" in summary_call[0][0]


# Version drift: a plugin binary keeps working as a file long after nushell moves on.


@pytest.mark.parametrize(
    "reported,expected",
    [
        ("0.111.0", "0.111.0"),
        ("1.4.13+0.111.0", "0.111.0"),
        ("", ""),
    ],
)
def test_plugin_nu_version_strips_the_crate_version(reported, expected):
    from core.plugins import _plugin_nu_version

    assert _plugin_nu_version(reported) == expected


def test_stale_plugin_names_flags_a_minor_version_mismatch():
    from core.plugins import stale_plugin_names

    registered = {"gstat": "0.111.0", "formats": "0.114.1", "highlight": "1.4.13+0.111.0"}
    assert stale_plugin_names(registered, "0.114.1") == ["gstat", "highlight"]


def test_stale_plugin_names_is_empty_without_a_nu_version():
    from core.plugins import stale_plugin_names

    assert stale_plugin_names({"gstat": "0.111.0"}, None) == []


def test_registered_plugin_versions_parses_nu_output():
    from core.plugins import registered_plugin_versions

    payload = '[{"name":"gstat","version":"0.111.0"},{"name":"query","version":"0.114.1"}]'
    with patch("core.plugins.subprocess.run", return_value=MagicMock(returncode=0, stdout=payload)):
        assert registered_plugin_versions() == {"gstat": "0.111.0", "query": "0.114.1"}


def test_registered_plugin_versions_returns_none_when_nu_fails():
    from core.plugins import registered_plugin_versions

    with patch("core.plugins.subprocess.run", side_effect=OSError("no nu")):
        assert registered_plugin_versions() is None


def test_install_plugins_reinstalls_a_stale_plugin(tmp_project: Path):
    """Present-on-disk is not installed-and-working: rebuild when nushell moved on."""
    install_calls: list[tuple[str, str | None]] = []

    with (
        patch("core.plugins.is_available", return_value=True),
        patch("core.plugins._get_rustc_version", return_value=(1, 85, 0)),
        patch("core.plugins._get_nu_version", return_value="0.114.1"),
        patch("core.plugins.is_plugin_installed", return_value=True),
        patch("core.plugins.registered_plugin_versions", return_value={"gstat": "0.111.0"}),
        patch(
            "core.plugins.install_plugin",
            side_effect=lambda crate, version=None: install_calls.append((crate, version)) or True,
        ),
    ):
        install_plugins(tmp_project)

    assert [c for c, _ in install_calls] == ["nu_plugin_gstat"]


def test_install_plugin_names_a_too_old_rustc(tmp_project: Path):
    """cargo refuses the build; the summary line must not blame the version spec."""
    proc = MagicMock()
    proc.returncode = 101
    proc.communicate.return_value = (
        "",
        "error: cannot install package `nu_plugin_gstat 0.114.1`, it requires rustc 1.95.0 or newer",
    )
    with (
        patch("core.plugins.subprocess.Popen", return_value=proc),
        patch("core.plugins.log_error") as mock_error,
    ):
        assert install_plugin("nu_plugin_gstat", version="~0.114") is False

    assert "rustup update" in mock_error.call_args[0][0]
