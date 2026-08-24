"""Tests for the utils module."""

import os
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from core.utils import (
    atomic_write_text,
    escape_nushell_path,
    escape_python_path,
    get_home_dir,
    get_nushell_config_dir,
    get_os,
    get_project_dir,
    get_xonsh_config_dir,
    is_available,
    is_windows,
    log_error,
    log_step,
    log_success,
    resolve_tool_path,
)


def test_atomic_write_text_replaces_content_without_leaving_temp_files(tmp_path):
    target = tmp_path / "config.nu"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert list(tmp_path.glob(".config.nu.*.tmp")) == []


def test_get_os_returns_known_value():
    assert get_os() in ("windows", "macos", "linux")


def test_is_available_for_python():
    assert is_available("python") or is_available("python3")


def test_is_available_false_for_nonsense():
    assert not is_available("zzz_not_a_real_tool_999")


def test_is_available_mise_fallback():
    """is_available should find tools via 'mise which' when not in PATH."""
    import subprocess

    with (
        patch("core.utils.shutil.which", return_value=None),
        patch("core.utils.get_project_dir", return_value=Path("/tmp/my-shell")),
        patch(
            "core.mise.shutil.which",
            side_effect=lambda t: "/usr/bin/mise" if t == "mise" else None,
        ),
        patch("core.mise.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mise", "which", "carapace"],
            returncode=0,
            stdout="/home/user/.local/share/mise/installs/aqua/carapace\n",
            stderr="",
        )
        assert is_available("carapace") is True
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == Path("/tmp/my-shell")
        assert (
            str(Path("/tmp/my-shell"))
            in mock_run.call_args.kwargs["env"]["MISE_TRUSTED_CONFIG_PATHS"]
        )


def test_is_available_mise_fallback_not_found():
    """is_available returns False when mise doesn't manage the tool either."""
    import subprocess

    with (
        patch("core.utils.shutil.which", return_value=None),
        patch("core.utils.get_project_dir", return_value=Path("/tmp/my-shell")),
        patch(
            "core.mise.shutil.which",
            side_effect=lambda t: "/usr/bin/mise" if t == "mise" else None,
        ),
        patch("core.mise.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["mise", "which", "nonexistent"],
            returncode=1,
            stdout="",
            stderr="",
        )
        assert is_available("nonexistent") is False


def test_resolve_tool_path_returns_path_for_python():
    assert resolve_tool_path("python") or resolve_tool_path("python3")


def test_resolve_tool_path_none_for_nonsense():
    assert resolve_tool_path("zzz_not_a_real_tool_999") is None


def test_resolve_tool_path_none_without_path_or_mise():
    with (
        patch("core.utils.shutil.which", return_value=None),
        patch("core.utils.mise_binary", return_value=None),
    ):
        assert resolve_tool_path("sometool") is None


def test_resolve_tool_path_mise_fallback_without_project_dir():
    """A RuntimeError from get_project_dir must not break the mise lookup."""
    with (
        patch("core.utils.shutil.which", return_value=None),
        patch("core.utils.mise_binary", return_value="/usr/bin/mise"),
        patch("core.utils.get_project_dir", side_effect=RuntimeError("no project")),
        patch("core.utils.mise_which", return_value="/mise/tool"),
    ):
        assert resolve_tool_path("sometool") == "/mise/tool"


def test_get_home_dir_exists():
    home = get_home_dir()
    assert home.is_dir()


def test_get_home_dir_prefers_home_env(monkeypatch):
    override = Path("/tmp/my-shell-home")
    monkeypatch.setenv("HOME", str(override))
    monkeypatch.setenv("USERPROFILE", "/tmp/other-home")
    assert get_home_dir() == override


def test_get_home_dir_falls_back_to_userprofile(monkeypatch):
    override = Path("/tmp/my-shell-userprofile")
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(override))
    assert get_home_dir() == override


def test_get_project_dir_via_env(tmp_path, monkeypatch):
    """MY_SHELL_DIR env var should be respected."""
    # Create marker file so validation passes
    marker = tmp_path / "shells" / "nushell"
    marker.mkdir(parents=True)
    (marker / "env.nu.template").write_text("", encoding="utf-8")

    monkeypatch.setenv("MY_SHELL_DIR", str(tmp_path))
    assert get_project_dir() == tmp_path


def test_get_project_dir_walks_up_from_source(monkeypatch):
    """When MY_SHELL_DIR is empty, should find project root via file walk-up."""
    monkeypatch.setenv("MY_SHELL_DIR", "")
    result = get_project_dir()
    assert (result / "shells" / "nushell" / "env.nu.template").exists()


def test_get_nushell_config_dir_is_path():
    d = get_nushell_config_dir()
    assert isinstance(d, Path)
    assert "nushell" in str(d).lower()


def test_get_xonsh_config_dir_is_path():
    d = get_xonsh_config_dir()
    assert isinstance(d, Path)
    assert "xonsh" in str(d).lower()


def test_get_xonsh_config_dir_linux():
    """On Linux (no XDG override), returns ~/.config/xonsh."""
    with (
        patch("core.utils.is_windows", return_value=False),
        patch("core.utils.get_os", return_value="linux"),
        patch.dict(os.environ, {}, clear=False),
    ):
        # Remove XDG_CONFIG_HOME if set
        os.environ.pop("XDG_CONFIG_HOME", None)
        d = get_xonsh_config_dir()
    assert d == get_home_dir() / ".config" / "xonsh"


def test_get_xonsh_config_dir_windows():
    """On Windows, returns %APPDATA%/xonsh."""
    import os

    with (
        patch("core.utils.is_windows", return_value=True),
        patch.dict(os.environ, {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}),
    ):
        d = get_xonsh_config_dir()
    # Normalize separators for cross-platform comparison
    assert str(d).replace("\\", "/") == "C:/Users/test/AppData/Roaming/xonsh"


def test_get_xonsh_config_dir_windows_without_appdata():
    """On Windows with APPDATA unset, falls back to home/AppData/Roaming."""
    import os

    from core.utils import get_home_dir

    with (
        patch("core.utils.is_windows", return_value=True),
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("APPDATA", None)
        d = get_xonsh_config_dir()
    assert d == get_home_dir() / "AppData" / "Roaming" / "xonsh"


def test_escape_nushell_path_keeps_apostrophe_literal():
    """Nushell has no escape inside '...', so a path with an apostrophe stays untouched."""
    assert escape_nushell_path("it's a path") == "it's a path"


def test_escape_nushell_path_escapes_double_quote():
    assert escape_nushell_path('say "hi"') == 'say \\"hi\\"'


def test_escape_nushell_path_passthrough():
    assert escape_nushell_path("/simple/path") == "/simple/path"


def test_escape_python_path_handles_backslashes():
    result = escape_python_path(r"C:\Users\test")
    assert result == "C:\\\\Users\\\\test"


def test_escape_python_path_handles_quotes():
    result = escape_python_path("it's")
    assert result == "it\\'s"


def test_escape_python_path_backslash_and_quote_combined():
    """Both backslashes and quotes should be escaped correctly together."""
    result = escape_python_path(r"C:\Users\it's mine\file")
    assert result == "C:\\\\Users\\\\it\\'s mine\\\\file"


def test_escape_nushell_path_multiple_quotes():
    """Apostrophes are never doubled -- that idiom is SQL, not Nushell."""
    assert escape_nushell_path("it's Bob's") == "it's Bob's"


def test_escape_empty_strings():
    """Empty strings should pass through without error."""
    assert escape_nushell_path("") == ""
    assert escape_python_path("") == ""


def test_escape_python_path_with_Path_object():
    """Path objects should be accepted and return an escaped string."""
    result = escape_python_path(Path("C:/Users/test"))
    assert isinstance(result, str)
    expected = str(Path("C:/Users/test")).replace("\\", "\\\\").replace("'", "\\'")
    assert result == expected


def test_escape_nushell_path_with_Path_object():
    """A Path with no special character escapes to itself, with forward slashes."""
    assert escape_nushell_path(Path("C:/Users/test")) == "C:/Users/test"


def test_escape_nushell_path_escapes_quote_and_backslash():
    """Double-quoted Nushell strings escape only backslash and double quote."""
    assert escape_nushell_path('a"b') == 'a\\"b'
    # On Windows a backslash is a separator and becomes '/', so only test the escape off-Windows.
    if not is_windows():
        assert escape_nushell_path("a\\b") == "a\\\\b"


def test_is_windows():
    assert is_windows() == (platform.system().lower() == "windows")


def test_get_project_dir_raises_when_not_found(tmp_path, monkeypatch):
    """When MY_SHELL_DIR is empty and walk-up fails, should raise RuntimeError."""
    monkeypatch.setenv("MY_SHELL_DIR", "")
    # Point __file__ resolution to a tmp dir with no marker file
    import core.utils as _utils_mod

    monkeypatch.setattr(_utils_mod, "__file__", str(tmp_path / "core" / "utils.py"))
    with pytest.raises(RuntimeError):
        get_project_dir()


def test_get_os_darwin():
    """get_os returns 'macos' on Darwin."""
    with patch("core.utils.platform.system", return_value="Darwin"):
        assert get_os() == "macos"


def test_get_os_linux():
    """get_os returns 'linux' on Linux."""
    with patch("core.utils.platform.system", return_value="Linux"):
        assert get_os() == "linux"


def test_get_os_windows():
    """get_os returns 'windows' on Windows."""
    with patch("core.utils.platform.system", return_value="Windows"):
        assert get_os() == "windows"


def test_get_nushell_config_dir_linux():
    """On Linux (no XDG override), returns ~/.config/nushell."""
    with (
        patch("core.utils.is_windows", return_value=False),
        patch("core.utils.get_os", return_value="linux"),
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("XDG_CONFIG_HOME", None)
        d = get_nushell_config_dir()
    assert d == get_home_dir() / ".config" / "nushell"


def test_get_nushell_config_dir_windows():
    """On Windows without XDG_CONFIG_HOME, returns ~/AppData/Roaming/nushell."""
    with patch("core.utils.is_windows", return_value=True), patch.dict(os.environ, {}):
        # CI leaks XDG_CONFIG_HOME (mise-action sets it) and nushell honours it.
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("APPDATA", None)
        d = get_nushell_config_dir()
    assert d == get_home_dir() / "AppData" / "Roaming" / "nushell"


def test_get_nushell_config_dir_windows_uses_appdata():
    """On Windows, APPDATA should take precedence when it is set."""
    with (
        patch("core.utils.is_windows", return_value=True),
        patch.dict(os.environ, {"APPDATA": r"C:\Users\test\AppData\Roaming"}),
    ):
        os.environ.pop("XDG_CONFIG_HOME", None)
        d = get_nushell_config_dir()
    assert str(d).replace("\\", "/") == "C:/Users/test/AppData/Roaming/nushell"


def test_log_success(capsys):
    log_success("deployed config")
    out = capsys.readouterr().out
    assert "[ok] deployed config" in out


def test_log_error(capsys):
    log_error("something broke")
    err = capsys.readouterr().err
    assert "[fail] something broke" in err


def test_log_step(capsys):
    log_step("Setting up nushell")
    out = capsys.readouterr().out
    assert "Setting up nushell" in out


def test_nushell_config_dir_macos():
    """On macOS (no XDG override), returns ~/Library/Application Support/nushell."""
    with (
        patch("core.utils.is_windows", return_value=False),
        patch("core.utils.get_os", return_value="macos"),
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("XDG_CONFIG_HOME", None)
        d = get_nushell_config_dir()
    assert d == get_home_dir() / "Library" / "Application Support" / "nushell"


def test_nushell_config_dir_macos_xdg_override():
    """On macOS with XDG_CONFIG_HOME set, uses XDG path."""
    with (
        patch("core.utils.is_windows", return_value=False),
        patch("core.utils.get_os", return_value="macos"),
        patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg-test"}),
    ):
        d = get_nushell_config_dir()
    assert d == Path("/tmp/xdg-test/nushell")


def test_xonsh_config_dir_macos():
    """xonsh uses the XDG default on macOS too."""
    with (
        patch("core.utils.is_windows", return_value=False),
        patch("core.utils.get_os", return_value="macos"),
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("XDG_CONFIG_HOME", None)
        d = get_xonsh_config_dir()
    assert d == get_home_dir() / ".config" / "xonsh"


def test_xonsh_config_dir_macos_xdg_override():
    """On macOS with XDG_CONFIG_HOME set, uses XDG path."""
    with (
        patch("core.utils.is_windows", return_value=False),
        patch("core.utils.get_os", return_value="macos"),
        patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg-test"}),
    ):
        d = get_xonsh_config_dir()
    assert d == Path("/tmp/xdg-test/xonsh")


def test_download_bytes_non_tty_reads_all():
    """On a non-interactive stream, download_bytes reads the whole body at once."""
    from unittest.mock import MagicMock

    from core.utils import download_bytes

    resp = MagicMock()
    resp.read.return_value = b"payload-bytes"
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=resp):
        data = download_bytes("https://example.test/file", timeout=5)

    assert data == b"payload-bytes"


def test_download_bytes_tty_bad_content_length_reads_all():
    """A non-numeric Content-Length falls back to a single read instead of crashing."""
    from unittest.mock import MagicMock

    from core.utils import download_bytes

    resp = MagicMock()
    resp.headers = {"Content-Length": "not-a-number"}
    resp.read.return_value = b"payload-bytes"
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)

    with (
        patch("urllib.request.urlopen", return_value=resp),
        patch("sys.stdout.isatty", return_value=True),
    ):
        data = download_bytes("https://example.test/file", timeout=5)

    assert data == b"payload-bytes"


def test_download_bytes_tty_chunks_and_joins():
    """On a TTY with a known size, it chunk-reads with a progress bar and joins the full body."""
    from unittest.mock import MagicMock

    from core.utils import download_bytes

    resp = MagicMock()
    resp.headers = {"Content-Length": "13"}
    resp.read.side_effect = [b"payload-", b"bytes", b""]
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)

    with (
        patch("urllib.request.urlopen", return_value=resp),
        patch("sys.stdout.isatty", return_value=True),
    ):
        data = download_bytes("https://example.test/file", timeout=5)

    assert data == b"payload-bytes"


def test_get_home_dir_homedrive_homepath_fallback():
    from core.utils import get_home_dir

    env = {"HOME": "", "USERPROFILE": "", "HOMEDRIVE": "C:", "HOMEPATH": r"\Users\U"}
    with patch.dict(os.environ, env, clear=False):
        assert str(get_home_dir()).replace("/", "\\") == r"C:\Users\U"


def test_get_home_dir_pathlib_fallback():
    from core.utils import get_home_dir

    env = {"HOME": "", "USERPROFILE": "", "HOMEDRIVE": "", "HOMEPATH": ""}
    with patch.dict(os.environ, env, clear=False):
        assert get_home_dir() == Path.home()


def test_get_cache_dir_windows_localappdata():
    from core.utils import get_cache_dir

    with (
        patch("core.utils.is_windows", return_value=True),
        patch.dict(os.environ, {"LOCALAPPDATA": r"C:\LAD"}, clear=False),
    ):
        assert str(get_cache_dir()).replace("/", "\\") == r"C:\LAD\my-shell"


def test_get_cache_dir_windows_no_localappdata():
    from core.utils import get_cache_dir, get_home_dir

    with (
        patch("core.utils.is_windows", return_value=True),
        patch.dict(os.environ, {"LOCALAPPDATA": ""}, clear=False),
    ):
        assert get_cache_dir() == get_home_dir() / "AppData" / "Local" / "my-shell"


def test_get_cache_dir_xdg_cache_home():
    from core.utils import get_cache_dir

    with (
        patch("core.utils.is_windows", return_value=False),
        patch.dict(os.environ, {"XDG_CACHE_HOME": "/xdg-cache"}, clear=False),
    ):
        assert get_cache_dir() == Path("/xdg-cache") / "my-shell"


def test_nushell_config_dir_honours_xdg_on_windows():
    """Verified against nu 0.115.1 on Windows: XDG_CONFIG_HOME wins over %APPDATA%."""
    import os

    from core.utils import get_nushell_config_dir

    with (
        patch("core.utils.is_windows", return_value=True),
        patch.dict(os.environ, {"XDG_CONFIG_HOME": "C:/xdg", "APPDATA": "C:/roaming"}),
    ):
        assert get_nushell_config_dir() == Path("C:/xdg") / "nushell"
