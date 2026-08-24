"""Shared utilities used across the codebase."""

import functools
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

from .mise import mise_binary
from .mise import which as mise_which

# ── Platform detection ──────────────────────────────────────────────


def get_os() -> str:
    """Return 'windows', 'macos', or 'linux'."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def is_windows() -> bool:
    return get_os() == "windows"


# ── Tool availability ──────────────────────────────────────────────


def resolve_tool_path(tool: str) -> str | None:
    """Return the resolved path of *tool* (PATH first, then mise), or None.

    Same lookup order as ``is_available``; returns the path so callers like
    ``detect`` can show which binary a bare name resolves to (a same-named
    program earlier on PATH shadows the intended tool).
    """
    direct = shutil.which(tool)
    if direct is not None:
        log_debug(f"resolve_tool_path({tool}): PATH -> {direct}")
        return direct

    if mise_binary() is None:
        log_debug(f"resolve_tool_path({tool}): not in PATH, mise not available")
        return None

    project_dir: Path | None = None
    try:
        project_dir = get_project_dir()
    except RuntimeError:
        project_dir = None

    # mise_which returns None on any failure (OSError, timeout, non-zero exit)
    path = mise_which(tool, project_dir=project_dir)
    log_debug(f"resolve_tool_path({tool}): mise which -> {path!r}")
    return path


@functools.lru_cache(maxsize=128)
def is_available(tool: str) -> bool:
    """Check if a command/tool is available in PATH or via mise.

    Results are cached for the lifetime of the process.
    """
    return resolve_tool_path(tool) is not None


def clear_availability_cache() -> None:
    """Clear cached tool lookups after install/upgrade operations."""
    is_available.cache_clear()


# ── Paths ──────────────────────────────────────────────────────────


class RealMachineWriteError(RuntimeError):
    """A test tried to write to the developer's own shell config."""


def guard_test_write(target: Path, action: str) -> None:
    """Refuse *action* on *target* when a test run would hit the developer's own files.

    Only fires under pytest, and only for a path inside the home directory but
    outside the temp tree -- every fixture-based test writes under tmp_path, so
    a hit means the test escaped its sandbox.
    """
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return
    if os.environ.get("MY_SHELL_ALLOW_REAL_WRITES", "").strip():
        return

    resolved = _canonical(target)
    if _is_path_under(resolved, _canonical(tempfile.gettempdir())):
        return
    home = _canonical(get_home_dir())
    if not _is_path_under(resolved, home):
        return

    raise RealMachineWriteError(
        f"Refusing to {action} to {Path(os.path.abspath(target))}: it is inside the "
        f"home directory ({get_home_dir()}) and outside the temp tree, so this test would overwrite real "
        f"shell config. Point the test at tmp_path, or set MY_SHELL_ALLOW_REAL_WRITES=1 "
        f"if you really mean it."
    )


def _canonical(path: Path | str) -> str:
    """Normalised absolute path: resolves 8.3 short names, symlinks and case.

    A CI runner's temp dir reaches us as C:/Users/RUNNER~1/... while pytest hands
    out the long form, and macOS /var is a symlink to /private/var.
    """
    return os.path.normcase(os.path.realpath(str(path)))


def _is_path_under(entry: str, root: str) -> bool:
    return entry == root or entry.startswith(root + os.sep)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a text file atomically so readers never observe a partial write.

    A symlinked target is written THROUGH, so a dotfiles manager keeps its link
    (this is the contract foreign_owner_warnings promises the user).
    """
    if path.is_symlink():
        resolved = path.resolve()
        if resolved.parent.is_dir():
            path = resolved
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def get_home_dir() -> Path:
    """Cross-platform home directory."""
    for var_name in ("HOME", "USERPROFILE"):
        value = os.environ.get(var_name, "").strip()
        if value:
            return Path(value)

    homedrive = os.environ.get("HOMEDRIVE", "").strip()
    homepath = os.environ.get("HOMEPATH", "").strip()
    if homedrive and homepath:
        return Path(f"{homedrive}{homepath}")

    return Path.home()


_project_dir_cache: Path | None = None


def get_project_dir() -> Path:
    """Find the my-shell repo root.

    Checks ``MY_SHELL_DIR`` env var first, then walks up from this file.
    Result is cached after first successful call.
    """
    global _project_dir_cache
    if _project_dir_cache is not None:
        return _project_dir_cache

    env_dir = os.environ.get("MY_SHELL_DIR", "")
    if env_dir and Path(env_dir).is_dir():
        _project_dir_cache = Path(env_dir)
        return _project_dir_cache

    # Walk up from this source file to find the repo root
    candidate = Path(__file__).resolve().parent.parent  # core/ -> repo root
    marker = candidate / "shells" / "nushell" / "env.nu.template"
    if marker.exists():
        _project_dir_cache = candidate
        return _project_dir_cache

    raise RuntimeError(
        "MY_SHELL_DIR is not set and cannot locate my-shell repo root. "
        "Set MY_SHELL_DIR or run from the repo."
    )


def get_cache_dir() -> Path:
    """Per-user cache dir for my-shell state (benchmark history, nudge timestamps)."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA", "").strip() or str(
            get_home_dir() / "AppData" / "Local"
        )
        return Path(base) / "my-shell"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "my-shell"
    return get_home_dir() / ".cache" / "my-shell"


def get_nushell_config_dir() -> Path:
    """Platform-specific Nushell config directory."""
    home = get_home_dir()
    # Nushell reads XDG_CONFIG_HOME on Windows too, so it wins over %APPDATA%.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "nushell"
    if is_windows():
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "nushell"
        return home / "AppData" / "Roaming" / "nushell"
    if get_os() == "macos":
        return home / "Library" / "Application Support" / "nushell"
    return home / ".config" / "nushell"


def get_xonsh_config_dir() -> Path:
    """Platform-specific xonsh config directory.

    xonsh follows XDG on every POSIX platform; unlike Nushell, macOS does not
    use Library/Application Support for xonsh configuration.
    """
    if is_windows():
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "xonsh"
        return get_home_dir() / "AppData" / "Roaming" / "xonsh"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "xonsh"
    return get_home_dir() / ".config" / "xonsh"


# ── Logging ────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN_BOLD = "\033[1;36m"
_GREEN_BOLD = "\033[1;32m"
_WHITE_BOLD = "\033[1;37m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_verbose = False


def set_verbose(enabled: bool) -> None:
    global _verbose
    _verbose = enabled


def log_success(msg: str) -> None:
    print(f"{_GREEN}  [ok] {msg}{_RESET}")


def log_error(msg: str) -> None:
    print(f"{_RED}  [fail] {msg}{_RESET}", file=sys.stderr)


def log_step(msg: str) -> None:
    print(f"{_CYAN_BOLD}{msg}{_RESET}")


def log_debug(msg: str) -> None:
    if _verbose:
        print(f"{_YELLOW}  [debug] {msg}{_RESET}")


def log_info(msg: str) -> None:
    """Print an informational message (not success/error)."""
    print(f"{_BLUE}  [info] {msg}{_RESET}")


def log_warn(msg: str) -> None:
    """Print a warning message."""
    print(f"{_YELLOW}  [warn] {msg}{_RESET}", file=sys.stderr)


def log_header(msg: str) -> None:
    """Print a section header."""
    print(f"\n{_WHITE_BOLD}{msg}{_RESET}")


# ── Shell helpers ──────────────────────────────────────────────────


def escape_nushell_path(path: Path | str) -> str:
    """Escape a path for a DOUBLE-quoted Nushell string.

    Nushell single-quoted strings have no escape character at all, so a path
    holding an apostrophe can only be written with double quotes. There only
    backslash and double quote need escaping.
    On Windows, backslashes become forward slashes first.
    """
    result = str(path)
    if is_windows():
        result = result.replace("\\", "/")
    return result.replace("\\", "\\\\").replace('"', '\\"')


def escape_python_path(path: Path | str) -> str:
    """Escape a path for a SINGLE-quoted Python/xonsh string literal."""
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


# ── Network ────────────────────────────────────────────────────────


def download_bytes(url: str, *, timeout: int = 60, description: str = "Downloading") -> bytes:
    """Download *url* into memory, with a progress bar on an interactive terminal.

    Falls back to a single read when output is not a TTY (CI, pipes) or the server
    reports no Content-Length. Network/OS errors propagate to the caller unchanged.
    """
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        # Non-interactive (tests, CI, pipes): read in one shot, no progress UI.
        if not sys.stdout.isatty():
            return bytes(resp.read())
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except ValueError:
            total = 0
        if total <= 0:
            return bytes(resp.read())

        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TransferSpeedColumn,
        )

        chunks: list[bytes] = []
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        ) as progress:
            task = progress.add_task(description, total=total)
            while chunk := resp.read(65536):
                chunks.append(chunk)
                progress.update(task, advance=len(chunk))
        return b"".join(chunks)
