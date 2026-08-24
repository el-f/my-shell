"""Tests for xonsh command modules (navigation, fuzzy, utilities)."""

# Import xonsh command modules via importlib to avoid polluting sys.path
import importlib.util
import io
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_commands_dir = Path(__file__).resolve().parent.parent / "shells" / "xonsh" / "commands"


def _import_from_commands(module_name):
    spec = importlib.util.spec_from_file_location(module_name, _commands_dir / f"{module_name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod  # needed so patches like "navigation.shutil" work
    spec.loader.exec_module(mod)
    return mod


_box = _import_from_commands("_box")  # must load before sysinfo (it does `from _box import ...`)
_common = _import_from_commands("_common")  # must load before nav/fuzzy (`from _common import ...`)
_nav = _import_from_commands("navigation")
_fuz = _import_from_commands("fuzzy")
_util = _import_from_commands("utilities")
_sys = _import_from_commands("sysinfo")
_cmds = _import_from_commands("commands")

_find_fd = _nav._find_fd
_fj = _nav._fj
_y = _nav._y
_fx = _fuz._fx
_fh = _fuz._fh
_fk = _fuz._fk
_trash = _util._trash
_trash_freedesktop = _util._trash_freedesktop
_port = _util._port
_clip = _util._clip
_pq = _util._pq
_sysinfo = _sys._sysinfo
_commands = _cmds._commands
_iter_custom_commands = _cmds._iter_custom_commands
_valid_pid = _common._valid_pid
_kill_pid = _common._kill_pid


def test_valid_pid_rejects_zero_dash_and_nonnumeric():
    """A killable PID is a positive integer; 0/-/empty are rejected."""
    assert _valid_pid("1234") is True
    assert _valid_pid("0") is False
    assert _valid_pid("-") is False
    assert _valid_pid("") is False


def test_kill_pid_refuses_zero_no_subprocess(capsys):
    """_kill_pid('0') must NOT spawn kill/taskkill -- kill -9 0 hits the process group."""
    calls = []
    with patch("_common.subprocess.run", side_effect=lambda cmd, **k: calls.append(list(cmd))):
        _kill_pid("0")
    assert calls == [], f"kill spawned for PID 0: {calls}"
    assert "no killable PID" in capsys.readouterr().out


_box_header = _sys._box_header
_box_row = _sys._box_row
_box_row_raw = _sys._box_row_raw
_box_footer = _sys._box_footer


def test_commands_derives_list_not_helpers(capsys):
    """`commands` derives its list from the modules; every command shows, no helpers."""
    _commands([], None)
    out = capsys.readouterr().out
    for name in ("fj", "y", "fx", "fh", "fk", "port", "clip", "trash", "pq", "sysinfo", "commands"):
        assert name in out, f"'{name}' missing from commands output"
    for helper in ("_require_tool", "_find_fd", "_load_aliases", "_iter_custom_commands"):
        assert helper not in out, f"helper '{helper}' leaked into commands output"


def test_iter_custom_commands_excludes_helpers():
    """The derive yields the (args, stdin) commands, not module helpers."""
    names = {name for name, _desc in _iter_custom_commands()}
    assert {"fj", "fk", "port", "sysinfo", "commands"} <= names
    assert "require_tool" not in names
    assert "find_fd" not in names


# navigation.py


def test_find_fd_prefers_fd():
    """When both fd and fdfind exist, fd should be preferred."""
    with patch("navigation.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"):
        assert _find_fd() == "fd"


def test_find_fd_falls_back_to_fdfind():
    """When fd is missing but fdfind exists, fdfind should be returned."""
    with patch(
        "navigation.shutil.which", side_effect=lambda n: f"/usr/bin/{n}" if n == "fdfind" else None
    ):
        assert _find_fd() == "fdfind"


def test_find_fd_returns_none():
    """When neither fd nor fdfind exists, None should be returned."""
    with patch("navigation.shutil.which", return_value=None):
        assert _find_fd() is None


def test_fj_no_fd_prints_error(capsys):
    """fj should print error when fd is not found."""
    with (
        patch("navigation._find_fd", return_value=None),
        patch("navigation.subprocess.Popen") as mock_popen,
    ):
        _fj([], None)
    assert "fd not found" in capsys.readouterr().out
    mock_popen.assert_not_called()


def test_fj_no_fzf_prints_error(capsys):
    """fj should print error when fzf is not found."""
    with (
        patch("navigation._find_fd", return_value="fd"),
        patch("navigation.shutil.which", return_value=None),
        patch("navigation.subprocess.Popen") as mock_popen,
    ):
        _fj([], None)
    assert "fzf not found" in capsys.readouterr().out
    mock_popen.assert_not_called()


def test_y_no_yazi_prints_error(capsys):
    """y should print error when yazi is not found."""
    with (
        patch("navigation.shutil.which", return_value=None),
        patch("navigation.subprocess.run") as mock_run,
    ):
        _y([], None)
    assert "yazi not found" in capsys.readouterr().out
    mock_run.assert_not_called()


# utilities.py


def test_trash_no_args(capsys):
    """trash with no args should print usage."""
    _trash([], None)
    assert "Usage:" in capsys.readouterr().out


def test_trash_nonexistent_file(tmp_path, capsys):
    """trash with nonexistent file should print error."""
    _trash([str(tmp_path / "no_such_file.txt")], None)
    assert "does not exist" in capsys.readouterr().out


@pytest.mark.skipif(platform.system() == "Windows", reason="Freedesktop Trash is Linux/macOS")
def test_trash_freedesktop_creates_trashinfo(tmp_path, monkeypatch):
    """Freedesktop trash should create both file entry and .trashinfo metadata."""
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("hello")

    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    _trash_freedesktop(str(test_file))

    assert not test_file.exists()
    trash_files = fake_home / ".local" / "share" / "Trash" / "files"
    assert (trash_files / "test_file.txt").exists()

    trash_info = fake_home / ".local" / "share" / "Trash" / "info"
    trashinfo_path = trash_info / "test_file.txt.trashinfo"
    assert trashinfo_path.exists()

    info_content = trashinfo_path.read_text()
    assert "[Trash Info]" in info_content
    assert "Path=" in info_content
    assert str(test_file) in info_content or "test_file.txt" in info_content
    assert "DeletionDate=" in info_content
    assert re.search(r"DeletionDate=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", info_content)


@pytest.mark.skipif(platform.system() == "Windows", reason="Freedesktop Trash is Linux/macOS")
def test_trash_freedesktop_collision_handling(tmp_path, monkeypatch):
    """Trashing two files with the same name should use a counter suffix."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # Create and trash first file
    file1 = tmp_path / "file.txt"
    file1.write_text("first")
    _trash_freedesktop(str(file1))

    # Create and trash second file with same name
    file2 = tmp_path / "file.txt"
    file2.write_text("second")
    _trash_freedesktop(str(file2))

    assert not file2.exists()
    trash_files = fake_home / ".local" / "share" / "Trash" / "files"
    assert (trash_files / "file.txt").exists()
    assert (trash_files / "file.1.txt").exists()


def test_port_no_args(capsys):
    """port with no args should print usage."""
    _port([], None)
    assert "Usage:" in capsys.readouterr().out


def test_port_invalid_number(capsys):
    """port with non-numeric value should print error."""
    _port(["abc"], None)
    assert "Invalid port" in capsys.readouterr().out


@pytest.mark.parametrize("port", ["0", "65536", "70000"])
def test_port_out_of_range(port, capsys):
    """Ports outside 1-65535 should be rejected."""
    _port([port], None)
    assert "Invalid port" in capsys.readouterr().out


def test_clip_no_tool_linux(capsys):
    """On Linux with no clipboard tool, should print error message."""
    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.subprocess.run") as mock_run,
    ):
        _clip([], None)
    assert "No clipboard tool found" in capsys.readouterr().out
    mock_run.assert_not_called()


# fuzzy.py


@pytest.mark.parametrize("func", [_fx, _fh, _fk], ids=["fx", "fh", "fk"])
def test_fuzzy_no_fzf_prints_error(func, capsys):
    """Fuzzy commands should print error when fzf is not found."""
    with (
        patch("fuzzy.shutil.which", return_value=None),
        patch("fuzzy.subprocess.run") as mock_run,
    ):
        func([], None)
    assert "fzf not found" in capsys.readouterr().out
    mock_run.assert_not_called()


# _box.py


def test_ensure_unicode_stdout_switches_a_cp1252_stream():
    """`sysinfo > file` on Windows hands the command a cp1252 stdout."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    _box._ensure_unicode_stdout(stream)
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_ensure_unicode_stdout_leaves_a_utf8_stream_alone():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    _box._ensure_unicode_stdout(stream)
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_ensure_unicode_stdout_tolerates_a_stream_without_reconfigure():
    _box._ensure_unicode_stdout(io.StringIO())


def test_sysinfo_writes_box_characters_to_a_cp1252_stdout(monkeypatch):
    """The whole point: no UnicodeEncodeError when stdout cannot encode the box."""
    monkeypatch.setenv("MY_SHELL_VERSION", "2026-01-01 00:00:00 +0000")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    sys.stdout.flush()
    assert "─" in sys.stdout.buffer.getvalue().decode("utf-8")


# sysinfo.py


def test_sysinfo_prints_system_section(capsys, monkeypatch):
    """sysinfo should print a System section with OS info."""
    monkeypatch.setenv("MY_SHELL_VERSION", "2026-01-01 00:00:00 +0000")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "System" in output
    assert "OS" in output
    assert "Host" in output
    assert "Shell" in output


def test_sysinfo_prints_myshell_section(capsys, monkeypatch):
    """sysinfo should print a my-shell section with version."""
    monkeypatch.setenv("MY_SHELL_VERSION", "2026-02-16 12:00:00 +0200")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "my-shell" in output
    assert "2026-02-16 12:00:00 +0200" in output
    assert "Version" in output


def test_sysinfo_prints_tools_section(capsys, monkeypatch):
    """sysinfo should print a Tools section."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "Tools" in output


def test_sysinfo_shows_found_tools(capsys, monkeypatch):
    """sysinfo should show checkmarks for available tools."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    def fake_which(name):
        return f"/usr/bin/{name}" if name in ("fzf", "rg", "bat") else None

    with patch("sysinfo.shutil.which", side_effect=fake_which):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "\u2713" in output  # checkmark for found tools
    assert "\u2717" in output  # cross for missing tools
    assert "fzf" in output
    assert "rg" in output


def test_sysinfo_prints_completions_section_with_carapace(capsys, monkeypatch):
    """sysinfo should show carapace as available when installed."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    def fake_which(name):
        return "/usr/bin/carapace" if name == "carapace" else None

    with patch("sysinfo.shutil.which", side_effect=fake_which):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "Completions" in output
    assert "carapace" in output


def test_sysinfo_prints_completions_section_without_carapace(capsys, monkeypatch):
    """sysinfo should show carapace as missing when not installed."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "Completions" in output
    assert "carapace" in output
    assert "install carapace-bin" in output


def _strip_ansi(text):
    """Remove ANSI escape codes to get visual content."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def test_sysinfo_box_alignment():
    """All box lines (header, rows, footer) must have the same visual width."""
    width = 46
    titles = ["System", "my-shell", "Tools", "Completions"]
    for title in titles:
        header = _strip_ansi(_box_header(title, width))
        footer = _strip_ansi(_box_footer(width))
        row = _strip_ansi(_box_row("Label", "value", width))
        raw = _strip_ansi(_box_row_raw("  some content", width))
        assert len(header) == width, f"header '{title}': {len(header)} != {width}"
        assert len(footer) == width, f"footer: {len(footer)} != {width}"
        assert len(row) == width, f"row: {len(row)} != {width}"
        assert len(raw) == width, f"row_raw: {len(raw)} != {width}"


def test_sysinfo_box_header_corners_aligned():
    """Header ╮ and footer ╯ must be in the same column."""
    width = 46
    header = _strip_ansi(_box_header("System", width))
    footer = _strip_ansi(_box_footer(width))
    assert header[0] == "\u256d"  # ╭
    assert header[-1] == "\u256e"  # ╮
    assert footer[0] == "\u2570"  # ╰
    assert footer[-1] == "\u256f"  # ╯
    assert len(header) == len(footer)


def test_sysinfo_box_row_raw_ansi_alignment():
    """box_row_raw with ANSI content must have the same visual width as plain rows."""
    width = 46
    ansi_content = "  \033[32m\u2713\033[0m fzf  fd  rg  bat  zoxide  yazi"
    raw = _strip_ansi(_box_row_raw(ansi_content, width))
    assert len(raw) == width, f"ANSI row_raw: {len(raw)} != {width}"
    # Corners must be box-drawing verticals
    assert raw[0] == "\u2502"
    assert raw[-1] == "\u2502"


def test_sysinfo_uses_box_drawing_chars(capsys, monkeypatch):
    """sysinfo output should contain box-drawing characters."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "\u256d" in output  # ╭
    assert "\u256e" in output  # ╮
    assert "\u2570" in output  # ╰
    assert "\u256f" in output  # ╯
    assert "\u2502" in output  # │


def test_sysinfo_shows_unknown_version_when_not_set(capsys, monkeypatch):
    """When MY_SHELL_VERSION is not set, should show 'unknown'."""
    monkeypatch.delenv("MY_SHELL_VERSION", raising=False)
    monkeypatch.delenv("MY_SHELL_DIR", raising=False)
    with patch("sysinfo.shutil.which", return_value=None):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "unknown" in output


def test_sysinfo_detects_prompt_engine(capsys, monkeypatch):
    """sysinfo should detect oh-my-posh as the prompt engine when available."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    def fake_which(name):
        return "/usr/bin/oh-my-posh" if name == "oh-my-posh" else None

    with patch("sysinfo.shutil.which", side_effect=fake_which):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "oh-my-posh" in output


def test_y_happy_path_changes_directory(tmp_path):
    """y should cd to the directory yazi writes to the cwd file."""
    target = tmp_path / "selected"
    target.mkdir()

    def fake_run(cmd, **kw):
        idx = cmd.index("--cwd-file") + 1
        Path(cmd[idx]).write_text(str(target))
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("navigation.shutil.which", return_value="/usr/bin/yazi"),
        patch("navigation.subprocess.run", side_effect=fake_run),
        patch("navigation.os.getcwd", return_value=str(tmp_path)),
        patch("navigation.os.chdir") as mock_chdir,
    ):
        _y([], None)
    mock_chdir.assert_called_once_with(str(target))


def test_y_no_chdir_when_same_directory(tmp_path):
    """y should not cd when yazi writes the current directory."""

    def fake_run(cmd, **kw):
        idx = cmd.index("--cwd-file") + 1
        Path(cmd[idx]).write_text(str(tmp_path))
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("navigation.shutil.which", return_value="/usr/bin/yazi"),
        patch("navigation.subprocess.run", side_effect=fake_run),
        patch("navigation.os.getcwd", return_value=str(tmp_path)),
        patch("navigation.os.chdir") as mock_chdir,
    ):
        _y([], None)
    mock_chdir.assert_not_called()


def test_y_cleans_up_temp_file():
    """y should delete the temp cwd file after yazi exits, even on error."""
    created_files = []

    def fake_run(cmd, **kw):
        idx = cmd.index("--cwd-file") + 1
        created_files.append(cmd[idx])
        raise FileNotFoundError("yazi binary missing")

    with (
        patch("navigation.shutil.which", return_value="/usr/bin/yazi"),
        patch("navigation.subprocess.run", side_effect=fake_run),
    ):
        _y([], None)

    assert len(created_files) == 1
    assert not os.path.exists(created_files[0])


def test_fj_happy_path_changes_directory(tmp_path):
    """fj should cd to the directory selected via fd+fzf."""
    target = str(tmp_path / "selected_dir")

    fd_proc = MagicMock()
    fd_proc.stdout = MagicMock()

    fzf_proc = MagicMock()
    fzf_proc.communicate.return_value = (target + "\n", "")

    def fake_popen(cmd, **kw):
        if cmd[0] in ("fd", "fdfind"):
            return fd_proc
        return fzf_proc

    with (
        patch("navigation._find_fd", return_value="fd"),
        patch("navigation.shutil.which", return_value="/usr/bin/fzf"),
        patch("navigation.subprocess.Popen", side_effect=fake_popen),
        patch("navigation.os.chdir") as mock_chdir,
    ):
        _fj([], None)
    mock_chdir.assert_called_once_with(target)


def test_fx_happy_path_runs_command(capsys):
    """fx should run the command selected by fzf."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "fzf":
            return subprocess.CompletedProcess(cmd, 0, stdout="htop\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("fuzzy.shutil.which", return_value="/usr/bin/fzf"),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fx([], None)
    assert ["htop"] in calls
    assert "Running: htop" in capsys.readouterr().out


@pytest.mark.parametrize(
    "plat,fzf_selection,expected_pid,expected_kill_cmd",
    [
        (
            "Linux",
            "root      1234  0.1  0.5  12345  6789 ?   Ss   00:00   0:01 python",
            "1234",
            ["kill", "-9", "1234"],
        ),
        (
            "Windows",
            '"python.exe","5678","Console","1","12,345 K"',
            "5678",
            ["taskkill", "/PID", "5678", "/F"],
        ),
    ],
)
def test_fk_happy_path_kills_process(plat, fzf_selection, expected_pid, expected_kill_cmd, capsys):
    """fk should kill the process selected by fzf."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] in ("ps", "tasklist"):
            return subprocess.CompletedProcess(
                cmd, 0, stdout="HEADER LINE\n" + fzf_selection + "\n"
            )
        if cmd[0] == "fzf":
            return subprocess.CompletedProcess(cmd, 0, stdout=fzf_selection + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("fuzzy.shutil.which", side_effect=lambda n: "/usr/bin/fzf" if n == "fzf" else None),
        patch("fuzzy.platform.system", return_value=plat),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fk([], None)
    assert expected_kill_cmd in calls
    assert f"Killing process {expected_pid}" in capsys.readouterr().out


def test_fk_windows_tasklist_csv_parses_pid_not_session(capsys):
    """On Windows fk must read tasklist as CSV and kill the PID column, not Session#.

    Real `tasklist` columns are: Image Name, PID, Session Name, Session#, Mem Usage.
    A whitespace split picks the wrong numeric token; CSV column index 1 is the PID.
    """
    # PID 4321, but Session# is 1 and Mem Usage has an embedded comma -- a naive
    # "last numeric token" parse would grab the Session# (1) or choke on "123,456".
    csv_row = '"chrome.exe","4321","Console","1","123,456 K"'
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "tasklist":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='"Image Name","PID","Session Name","Session#","Mem Usage"\n'
                + csv_row
                + "\n",
            )
        if cmd[0] == "fzf":
            return subprocess.CompletedProcess(cmd, 0, stdout=csv_row + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("fuzzy.shutil.which", side_effect=lambda n: "/usr/bin/fzf" if n == "fzf" else None),
        patch("fuzzy.platform.system", return_value="Windows"),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fk([], None)

    assert ["tasklist", "/fo", "csv"] in calls
    assert ["taskkill", "/PID", "4321", "/F"] in calls
    assert "Killing process 4321" in capsys.readouterr().out


def test_port_happy_path_shows_process(capsys):
    """port should display process info for a port in use."""
    netstat_output = "  TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    1234\n"

    def fake_run(cmd, **kw):
        if cmd[0] in ("netstat", "findstr"):
            return subprocess.CompletedProcess(cmd, 0, stdout=netstat_output)
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["8080"], None)
    output = capsys.readouterr().out
    assert "8080" in output
    assert "1234" in output


def test_port_list_happy_path(capsys):
    """port --list should show all listening ports."""
    listening = (
        "  TCP    0.0.0.0:8080    LISTENING    1234\n  TCP    0.0.0.0:3000    LISTENING    5678\n"
    )

    def fake_run(cmd, **kw):
        if cmd[0] in ("netstat", "findstr"):
            return subprocess.CompletedProcess(cmd, 0, stdout=listening)
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["--list"], None)
    output = capsys.readouterr().out
    assert "8080" in output
    assert "3000" in output


def test_port_kill_happy_path(capsys):
    """port --kill should extract PID and kill the process."""
    netstat_line = "  TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    1234"

    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] in ("netstat", "findstr"):
            return subprocess.CompletedProcess(cmd, 0, stdout=netstat_line + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["8080", "--kill"], None)
    assert ["taskkill", "/PID", "1234", "/F"] in calls
    assert "Killing" in capsys.readouterr().out


def test_clip_copy_windows(capsys):
    """clip with stdin on Windows should copy via clip.exe."""
    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], io.StringIO("hello"))
    mock_run.assert_called_once_with(
        ["clip.exe"],
        input="hello",
        text=True,
        check=False,
    )
    assert "Copied" in capsys.readouterr().out


def test_clip_paste_windows(capsys):
    """clip without stdin on Windows should paste via Get-Clipboard."""
    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout="clipboard_content\n",
        )
        _clip([], None)
    mock_run.assert_called_once_with(
        ["powershell", "-command", "Get-Clipboard"],
        capture_output=True,
        text=True,
    )
    assert "clipboard_content" in capsys.readouterr().out


def test_clip_copy_linux_xclip(capsys):
    """clip with stdin on Linux should copy via xclip."""
    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch(
            "utilities.shutil.which",
            side_effect=lambda n: "/usr/bin/xclip" if n == "xclip" else None,
        ),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], io.StringIO("hello"))
    mock_run.assert_called_once_with(
        ["xclip", "-selection", "clipboard"],
        input="hello",
        text=True,
        check=False,
    )
    assert "Copied" in capsys.readouterr().out


def test_clip_paste_linux_xclip():
    """clip without stdin on Linux should paste via xclip."""
    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch(
            "utilities.shutil.which",
            side_effect=lambda n: "/usr/bin/xclip" if n == "xclip" else None,
        ),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], None)
    mock_run.assert_called_once_with(
        ["xclip", "-selection", "clipboard", "-o"],
        check=False,
    )


def test_port_happy_path_unix_lsof(capsys):
    """port on Unix with lsof should display process info."""
    lsof_output = (
        "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
        "python3  1234 user   4u  IPv4 123456      0t0  TCP *:8080 (LISTEN)"
    )

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value="/usr/bin/lsof"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout=lsof_output)
        _port(["8080"], None)
    output = capsys.readouterr().out
    assert "8080" in output
    assert "1234" in output
    mock_run.assert_called_once_with(
        ["lsof", "-i", ":8080"],
        capture_output=True,
        text=True,
    )


def test_trash_windows_happy_path(tmp_path, capsys):
    """trash on Windows should use PowerShell to send to Recycle Bin."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _trash([str(target)], None)
    output = capsys.readouterr().out
    assert "Recycle Bin" in output
    call_cmd = mock_run.call_args[0][0]
    assert call_cmd[0] == "powershell"
    assert "DeleteFile" in call_cmd[2]
    assert "file.txt" in call_cmd[2]


def test_trash_macos_happy_path(tmp_path, capsys):
    """trash on macOS should use the trash command when available."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    with (
        patch("utilities.platform.system", return_value="Darwin"),
        patch("utilities.shutil.which", return_value="/usr/local/bin/trash"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _trash([str(target)], None)
    output = capsys.readouterr().out
    assert "Moved to Trash" in output
    call_cmd = mock_run.call_args[0][0]
    assert call_cmd[0] == "trash"
    assert call_cmd[1] == os.path.abspath(str(target))


def test_port_list_unix(capsys):
    """port --list on Unix should use netstat -tuln + grep."""
    listening = "tcp  0  0.0.0.0:8080  0.0.0.0:*  LISTEN\n"

    def fake_run(cmd, **kw):
        if cmd[0] == "netstat":
            return subprocess.CompletedProcess(cmd, 0, stdout=listening)
        if cmd[0] == "grep":
            return subprocess.CompletedProcess(cmd, 0, stdout=listening)
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["--list"], None)
    assert "8080" in capsys.readouterr().out


def test_port_unix_netstat_grep_fallback(capsys):
    """port on Unix without lsof should fall back to netstat + grep."""
    netstat_line = "tcp  0  0.0.0.0:3000  0.0.0.0:*  LISTEN  1234/node"

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=netstat_line)

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["3000"], None)
    assert "3000" in capsys.readouterr().out


def test_port_kill_unix(capsys):
    """port --kill on Unix should extract PID from lsof output and kill."""
    lsof_output = (
        "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
        "python3  9876 user   4u  IPv4 123456      0t0  TCP *:8080 (LISTEN)"
    )
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=lsof_output)

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value="/usr/bin/lsof"),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["8080", "--kill"], None)
    assert ["kill", "-9", "9876"] in calls
    assert "Killing" in capsys.readouterr().out


def test_port_no_process_found(capsys):
    """port should print 'No process found' when output is empty."""
    with (
        patch("utilities.platform.system", return_value="Windows"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="")
        _port(["9999"], None)
    assert "No process found" in capsys.readouterr().out


def test_clip_copy_darwin(capsys):
    """clip with stdin on macOS should use pbcopy."""
    with (
        patch("utilities.platform.system", return_value="Darwin"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], io.StringIO("hello"))
    mock_run.assert_called_once_with(["pbcopy"], input="hello", text=True, check=False)
    assert "Copied" in capsys.readouterr().out


def test_clip_paste_darwin():
    """clip without stdin on macOS should use pbpaste."""
    with (
        patch("utilities.platform.system", return_value="Darwin"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], None)
    mock_run.assert_called_once_with(["pbpaste"], check=False)


def test_clip_copy_linux_xsel(capsys):
    """clip with stdin on Linux with xsel should copy via xsel."""
    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch(
            "utilities.shutil.which", side_effect=lambda n: "/usr/bin/xsel" if n == "xsel" else None
        ),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], io.StringIO("hello"))
    mock_run.assert_called_once_with(
        ["xsel", "--clipboard", "--input"],
        input="hello",
        text=True,
        check=False,
    )
    assert "Copied" in capsys.readouterr().out


def test_clip_paste_linux_xsel():
    """clip without stdin on Linux with xsel should paste via xsel."""
    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch(
            "utilities.shutil.which", side_effect=lambda n: "/usr/bin/xsel" if n == "xsel" else None
        ),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _clip([], None)
    mock_run.assert_called_once_with(["xsel", "--clipboard", "--output"], check=False)


def test_trash_linux_trash_put(tmp_path, capsys):
    """trash on Linux should use trash-put when available."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value="/usr/bin/trash-put"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _trash([str(target)], None)
    assert "Moved to Trash" in capsys.readouterr().out


def test_trash_linux_freedesktop_fallback(tmp_path, capsys):
    """trash on Linux without trash-put should use freedesktop spec."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.os.path.expanduser", return_value=str(fake_home)),
    ):
        _trash([str(target)], None)
    assert "Moved to Trash" in capsys.readouterr().out
    trash_files = fake_home / ".local" / "share" / "Trash" / "files"
    assert (trash_files / "file.txt").exists()


def test_trash_macos_osascript_fallback(tmp_path, capsys):
    """trash on macOS without trash command should use osascript."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    with (
        patch("utilities.platform.system", return_value="Darwin"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _trash([str(target)], None)
    assert "Moved to Trash" in capsys.readouterr().out
    call_cmd = mock_run.call_args[0][0]
    assert call_cmd[0] == "osascript"


def test_fh_delegates_to_atuin():
    """fh should use atuin search when atuin is available."""
    with (
        patch(
            "fuzzy.shutil.which", side_effect=lambda n: "/usr/bin/atuin" if n == "atuin" else None
        ),
        patch("fuzzy.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        _fh([], None)
    mock_run.assert_called_once_with(["atuin", "search", "--interactive"], check=False)


def test_fj_custom_search_path(tmp_path):
    """fj should pass the argument as the search path to fd."""
    fd_proc = MagicMock()
    fd_proc.stdout = MagicMock()

    fzf_proc = MagicMock()
    fzf_proc.communicate.return_value = ("", "")

    popen_calls = []

    def fake_popen(cmd, **kw):
        popen_calls.append(list(cmd))
        if cmd[0] in ("fd", "fdfind"):
            return fd_proc
        return fzf_proc

    with (
        patch("navigation._find_fd", return_value="fd"),
        patch("navigation.shutil.which", return_value="/usr/bin/fzf"),
        patch("navigation.subprocess.Popen", side_effect=fake_popen),
        patch("navigation.os.chdir"),
    ):
        _fj([str(tmp_path)], None)
    fd_call = popen_calls[0]
    assert str(tmp_path) in fd_call


def test_fj_default_windows_path():
    """fj with no args on Windows should use USERPROFILE."""
    fd_proc = MagicMock()
    fd_proc.stdout = MagicMock()
    fzf_proc = MagicMock()
    fzf_proc.communicate.return_value = ("", "")

    def fake_popen(cmd, **kw):
        return fd_proc if cmd[0] in ("fd", "fdfind") else fzf_proc

    with (
        patch("navigation._find_fd", return_value="fd"),
        patch("navigation.shutil.which", return_value="/usr/bin/fzf"),
        patch("navigation.platform.system", return_value="Windows"),
        patch("navigation.subprocess.Popen", side_effect=fake_popen),
        patch("navigation.os.chdir"),
    ):
        _fj([], None)


def test_sysinfo_darwin_os_display(capsys, monkeypatch):
    """sysinfo on macOS should show macOS version."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    with (
        patch("sysinfo.platform.system", return_value="Darwin"),
        patch("sysinfo.platform.machine", return_value="arm64"),
        patch("sysinfo.platform.node", return_value="mac"),
        patch("sysinfo.platform.mac_ver", return_value=("14.0", ("", "", ""), "")),
        patch("sysinfo.shutil.which", return_value=None),
        patch("sysinfo.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="xonsh 0.17.0")
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "macOS" in output
    assert "arm64" in output


def test_sysinfo_linux_pkg_mgr(capsys, monkeypatch):
    """sysinfo on Linux should detect apt as package manager."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    def fake_which(name):
        return "/usr/bin/apt" if name == "apt" else None

    with (
        patch("sysinfo.platform.system", return_value="Linux"),
        patch("sysinfo.platform.machine", return_value="x86_64"),
        patch("sysinfo.platform.node", return_value="dev"),
        patch("sysinfo.shutil.which", side_effect=fake_which),
        patch("sysinfo.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="xonsh 0.17.0")
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "apt" in output


def test_sysinfo_xonsh_version_not_found(capsys, monkeypatch):
    """sysinfo should show unknown when xonsh --version fails."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    with (
        patch("sysinfo.shutil.which", return_value=None),
        patch("sysinfo.subprocess.run", side_effect=FileNotFoundError("xonsh")),
    ):
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "unknown" in output


def test_sysinfo_terminal_windows_terminal(capsys, monkeypatch):
    """sysinfo should detect Windows Terminal via WT_SESSION."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setenv("WT_SESSION", "some-guid")

    with (
        patch("sysinfo.shutil.which", return_value=None),
        patch("sysinfo.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="xonsh 0.17.0")
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "Windows Terminal" in output


def test_fx_collects_executables_from_path(tmp_path, capsys):
    """fx should collect executables from PATH directories."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "mytool"
    exe.write_text("#!/bin/sh")
    exe.chmod(0o755)

    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "fzf":
            assert "mytool" in kw.get("input", "")
            return subprocess.CompletedProcess(cmd, 0, stdout="mytool\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("fuzzy.shutil.which", return_value="/usr/bin/fzf"),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
        patch.dict(os.environ, {"PATH": str(bin_dir)}, clear=False),
    ):
        _fx([], None)
    assert "Running: mytool" in capsys.readouterr().out


def test_fx_handles_error(capsys):
    """fx should handle subprocess errors gracefully."""
    with (
        patch("fuzzy.shutil.which", return_value="/usr/bin/fzf"),
        patch("fuzzy.subprocess.run", side_effect=FileNotFoundError("fzf")),
    ):
        _fx([], None)
    assert "Error" in capsys.readouterr().out


def test_fh_xonsh_history_fallback(capsys):
    """fh should fall back to fzf over xonsh history when atuin is not available."""
    import builtins

    # Mock xonsh history
    mock_item1 = {"inp": "ls -la"}
    mock_item2 = {"inp": "cd /tmp"}
    mock_history = MagicMock()
    mock_history.items.return_value = [mock_item1, mock_item2]

    mock_xonsh = MagicMock()
    mock_xonsh.history = mock_history

    original_xonsh = getattr(builtins, "__xonsh__", None)
    builtins.__xonsh__ = mock_xonsh

    try:

        def fake_run(cmd, **kw):
            if cmd[0] == "fzf":
                return subprocess.CompletedProcess(cmd, 0, stdout="ls -la\n")
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch(
                "fuzzy.shutil.which", side_effect=lambda n: "/usr/bin/fzf" if n == "fzf" else None
            ),
            patch("fuzzy.subprocess.run", side_effect=fake_run),
            patch("builtins.input", return_value="y"),  # confirm execution
        ):
            _fh([], None)
        output = capsys.readouterr().out
        assert "Command: ls -la" in output
        mock_xonsh.execer.exec.assert_called_once_with("ls -la")
    finally:
        if original_xonsh is None:
            delattr(builtins, "__xonsh__")
        else:
            builtins.__xonsh__ = original_xonsh


def test_fh_fallback_declined_does_not_execute(capsys):
    """fh must NOT run the selected history command when the user answers no."""
    import builtins

    mock_xonsh = MagicMock()
    mock_xonsh.history.items.return_value = [{"inp": "rm -rf /tmp/x"}]
    original_xonsh = getattr(builtins, "__xonsh__", None)
    builtins.__xonsh__ = mock_xonsh
    try:

        def fake_run(cmd, **kw):
            if cmd[0] == "fzf":
                return subprocess.CompletedProcess(cmd, 0, stdout="rm -rf /tmp/x\n")
            return subprocess.CompletedProcess(cmd, 0)

        with (
            patch(
                "fuzzy.shutil.which", side_effect=lambda n: "/usr/bin/fzf" if n == "fzf" else None
            ),
            patch("fuzzy.subprocess.run", side_effect=fake_run),
            patch("builtins.input", return_value=""),  # decline (default N)
        ):
            _fh([], None)
        mock_xonsh.execer.exec.assert_not_called()
    finally:
        if original_xonsh is None:
            delattr(builtins, "__xonsh__")
        else:
            builtins.__xonsh__ = original_xonsh


def test_fh_atuin_exception_falls_through():
    """fh should fall through to fzf if atuin raises an exception."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "atuin":
            raise FileNotFoundError("atuin binary missing")
        # fzf fallback will trigger, but no fzf available
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    with (
        patch(
            "fuzzy.shutil.which",
            side_effect=lambda n: "/usr/bin/" + n if n in ("atuin", "fzf") else None,
        ),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fh([], None)
    # atuin was tried first
    assert ["atuin", "search", "--interactive"] in calls


def test_fk_procs_tree(capsys):
    """fk should use procs --tree when available."""
    procs_output = "PID  COMMAND\n1234  python"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "procs":
            return subprocess.CompletedProcess(cmd, 0, stdout=procs_output)
        if cmd[0] == "fzf":
            return subprocess.CompletedProcess(cmd, 0, stdout="1234  python\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch(
            "fuzzy.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}" if n in ("fzf", "procs") else None,
        ),
        patch("fuzzy.platform.system", return_value="Linux"),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fk([], None)
    assert ["procs", "--tree"] in calls
    assert "Killing" in capsys.readouterr().out


def test_fk_procs_tree_extracts_pid_not_command(capsys):
    """procs --tree puts PID in column 0 -- fk must kill the PID, not the command column."""
    procs_output = "PID  COMMAND\n1234  python"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "procs":
            return subprocess.CompletedProcess(cmd, 0, stdout=procs_output)
        if cmd[0] == "fzf":
            return subprocess.CompletedProcess(cmd, 0, stdout="1234  python\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch(
            "fuzzy.shutil.which",
            side_effect=lambda n: f"/usr/bin/{n}" if n in ("fzf", "procs") else None,
        ),
        patch("fuzzy.platform.system", return_value="Linux"),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fk([], None)
    assert ["kill", "-9", "1234"] in calls


def test_fk_ps_aux_numeric_uid_picks_pid_column(capsys):
    """With `ps aux` and a numeric UID in column 0, fk must kill the PID (column 1)."""
    # Owner has no passwd entry -> ps prints the raw UID (99999) in the USER column.
    ps_line = "99999    4321  0.1  0.5  12345  6789 ?  Ss  00:00  0:01 python"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "ps":
            return subprocess.CompletedProcess(cmd, 0, stdout="HEADER\n" + ps_line + "\n")
        if cmd[0] == "fzf":
            return subprocess.CompletedProcess(cmd, 0, stdout=ps_line + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("fuzzy.shutil.which", side_effect=lambda n: "/usr/bin/fzf" if n == "fzf" else None),
        patch("fuzzy.platform.system", return_value="Linux"),
        patch("fuzzy.subprocess.run", side_effect=fake_run),
    ):
        _fk([], None)
    # PID is 4321 (column 1), NOT 99999 (the numeric UID in column 0).
    assert ["kill", "-9", "4321"] in calls


def test_sysinfo_all_tools_found(capsys, monkeypatch):
    """sysinfo should show 'none missing' when all tools are found."""
    monkeypatch.setenv("MY_SHELL_VERSION", "unknown")
    monkeypatch.setenv("MY_SHELL_DIR", "/tmp/my-shell")

    with (
        patch("sysinfo.shutil.which", return_value="/usr/bin/tool"),
        patch("sysinfo.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="xonsh 0.17.0")
        _sysinfo([], None)
    output = capsys.readouterr().out
    assert "none missing" in output


def test_trash_linux_trash_put_failure(tmp_path, capsys):
    """trash on Linux with trash-put failure should show error."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value="/usr/bin/trash-put"),
        patch("utilities.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 1)
        _trash([str(target)], None)
    assert "Error" in capsys.readouterr().out


def test_trash_linux_freedesktop_oserror(tmp_path, capsys):
    """trash on Linux with freedesktop failure should show error."""
    target = tmp_path / "file.txt"
    target.write_text("content")

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities._trash_freedesktop", side_effect=OSError("permission denied")),
    ):
        _trash([str(target)], None)
    assert "Error" in capsys.readouterr().out


def test_pq_happy_path_passes_args():
    """pq should start daemon if needed and pass args to pueue."""
    calls = []

    def fake_which(name):
        if name in ("pueue", "pueued"):
            return f"/usr/bin/{name}"
        return None

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    with (
        patch("utilities.shutil.which", side_effect=fake_which),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _pq(["status"], None)
    assert ["pueue", "status"] in calls


def test_pq_not_installed(capsys):
    """pq should print install instructions when pueue is missing."""
    with patch("utilities.shutil.which", return_value=None):
        _pq(["status"], None)
    assert "pueue not found" in capsys.readouterr().out


def test_port_unix_fallback_uses_tlnp():
    """Without lsof the netstat fallback needs -p (PID column) for --kill to work."""
    seen = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.subprocess.run", side_effect=fake_run),
    ):
        _port(["3000"], None)
    assert ["netstat", "-tlnp"] in seen


def test_port_kill_unix_netstat_fallback(capsys):
    """port --kill without lsof parses the PID from netstat -tlnp's last column."""
    netstat_line = "tcp  0  0  0.0.0.0:3000  0.0.0.0:*  LISTEN  1234/node"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] in ("netstat", "grep"):
            return subprocess.CompletedProcess(cmd, 0, stdout=netstat_line + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.subprocess.run", side_effect=fake_run),
        patch("_common.platform.system", return_value="Linux"),
    ):
        _port(["3000", "--kill"], None)
    assert ["kill", "-9", "1234"] in calls


def test_port_kill_unix_netstat_no_pid(capsys):
    """netstat -tlnp shows '-' without root; --kill must refuse, not kill PID 0."""
    netstat_line = "tcp  0  0  0.0.0.0:3000  0.0.0.0:*  LISTEN  -"
    kills = []

    def fake_run(cmd, **kw):
        if cmd[0] == "kill":
            kills.append(list(cmd))
        if cmd[0] in ("netstat", "grep"):
            return subprocess.CompletedProcess(cmd, 0, stdout=netstat_line + "\n")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch("utilities.platform.system", return_value="Linux"),
        patch("utilities.shutil.which", return_value=None),
        patch("utilities.subprocess.run", side_effect=fake_run),
        patch("_common.platform.system", return_value="Linux"),
    ):
        _port(["3000", "--kill"], None)
    assert kills == []
    assert "no killable PID" in capsys.readouterr().out
