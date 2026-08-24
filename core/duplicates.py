"""Detect and clean up duplicate shell and tool installations."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer

from .utils import get_home_dir, is_windows, log_error, log_step, log_success, log_warn


@dataclass
class ShellInstallation:
    """A single detected shell binary installation."""

    path: Path
    version: str | None = None
    is_active: bool = False


@dataclass
class DuplicateReport:
    """Report of all installations found for a single shell binary."""

    binary_name: str
    display_name: str
    installations: list[ShellInstallation] = field(default_factory=list)

    @property
    def has_duplicates(self) -> bool:
        return len(self.installations) > 1


# ── Protected directories ─────────────────────────────────────────


def _is_protected_directory(path: Path) -> bool:
    """Return True if path is a system directory that should never be removed."""
    # Use as_posix() for consistent comparison (avoids Windows resolve issues)
    path_str = str(path)
    if is_windows():
        path_lower = path_str.lower().replace("/", "\\")
        protected = [
            os.environ.get("SYSTEMROOT", r"C:\Windows").lower(),
            r"c:\windows",
            r"c:\program files",
            r"c:\program files (x86)",
        ]
        for p in protected:
            if path_lower == p:
                return True
    else:
        # Use as_posix() so tests work cross-platform (WindowsPath would use backslashes)
        normalized = path.as_posix().rstrip("/") or "/"
        protected_unix = {
            "/usr/bin",
            "/bin",
            "/sbin",
            "/usr/sbin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/home/linuxbrew/.linuxbrew/bin",
        }
        if normalized in protected_unix:
            return True
    return False


# ── Version detection ─────────────────────────────────────────────


def _get_version_at_path(binary_path: Path) -> str | None:
    """Run `<binary> --version` and parse the output."""
    try:
        result = subprocess.run(
            [str(binary_path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            # xonsh outputs "xonsh/0.22.3"
            if "/" in text:
                return text.split("/", 1)[1]
            return text
    except FileNotFoundError, OSError, subprocess.TimeoutExpired:
        pass
    return None


# ── Installation discovery ────────────────────────────────────────


def _known_locations(binary: str) -> list[Path]:
    """Return platform-specific known locations to probe for a binary."""
    home = get_home_dir()
    locations: list[Path] = []

    if is_windows():
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if binary == "nu":
            locations.append(Path(r"C:\Program Files\nu\bin"))
            if localappdata:
                locations.append(Path(localappdata) / "Programs" / "nu" / "bin")
        elif binary == "xonsh":
            if localappdata:
                locations.append(Path(localappdata) / "Programs" / "xonsh")
        locations.append(home / ".cargo" / "bin")
    else:
        locations.extend(
            [
                Path("/usr/local/bin"),
                home / ".local" / "bin",
                home / ".cargo" / "bin",
                Path("/opt/homebrew/bin"),
            ]
        )

    return locations


def find_all_installations(binary: str) -> list[Path]:
    """Find all installations of a binary on the system.

    Uses `where.exe` (Windows) or PATH walking (Unix) plus known location probing.
    Returns deduplicated list of paths to the actual binary files.
    """
    found: list[Path] = []

    if is_windows():
        try:
            result = subprocess.run(
                ["where.exe", binary],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    line = line.strip()
                    if line:
                        found.append(Path(line))
        except FileNotFoundError, OSError, subprocess.TimeoutExpired:
            pass
    else:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        for d in path_dirs:
            candidate = Path(d) / binary
            if candidate.is_file():
                found.append(candidate)

    ext = ".exe" if is_windows() else ""
    for loc in _known_locations(binary):
        candidate = loc / f"{binary}{ext}"
        if candidate.is_file():
            found.append(candidate)

    # case-insensitive dedupe on Windows
    seen: set[str] = set()
    unique: list[Path] = []
    for p in found:
        try:
            resolved = str(p.resolve())
            key = resolved.lower() if is_windows() else resolved
            if key not in seen:
                seen.add(key)
                unique.append(p)
        except OSError:
            continue

    return unique


# ── Report builder ────────────────────────────────────────────────


def detect_duplicate_shells() -> list[DuplicateReport]:
    """Detect duplicate installations for nu and xonsh.

    Returns a list of DuplicateReport, one per shell that has any installations.
    Only reports with duplicates are actionable.
    """
    shells = [("nu", "Nushell"), ("xonsh", "xonsh")]
    reports: list[DuplicateReport] = []

    for binary, display_name in shells:
        paths = find_all_installations(binary)
        if not paths:
            continue

        active_path = shutil.which(binary)
        active_resolved = None
        if active_path:
            try:
                active_resolved = str(Path(active_path).resolve())
                if is_windows():
                    active_resolved = active_resolved.lower()
            except OSError:
                pass

        installations: list[ShellInstallation] = []
        for p in paths:
            version = _get_version_at_path(p)
            try:
                resolved = str(p.resolve())
                if is_windows():
                    resolved = resolved.lower()
                is_active = resolved == active_resolved
            except OSError:
                is_active = False
            installations.append(ShellInstallation(path=p, version=version, is_active=is_active))

        reports.append(
            DuplicateReport(
                binary_name=binary,
                display_name=display_name,
                installations=installations,
            )
        )

    return reports


# ── Interactive cleanup ───────────────────────────────────────────


def prompt_cleanup_duplicates(reports: list[DuplicateReport]) -> None:
    """Interactively offer to remove stale (non-active) duplicate installations.

    Never raises -- errors are logged and setup continues.
    """
    actionable = [r for r in reports if r.has_duplicates]
    if not actionable:
        return

    interactive = sys.stdin is not None and sys.stdin.isatty()

    log_step("Duplicate shell installations detected:")

    for report in actionable:
        print(f"\n  {report.display_name} ({report.binary_name}):")
        for inst in report.installations:
            marker = " (active)" if inst.is_active else ""
            ver = f" v{inst.version}" if inst.version else ""
            print(f"    - {inst.path}{ver}{marker}")

        for inst in report.installations:
            if inst.is_active:
                continue

            bin_dir = inst.path.parent
            if _is_protected_directory(bin_dir):
                log_warn(f"Skipping protected directory: {bin_dir}")
                continue

            if not os.access(bin_dir, os.W_OK):
                log_warn(f"No write access to {bin_dir} (may need admin/sudo)")
                continue

            if not interactive:
                log_warn(f"Non-interactive mode, skipping cleanup of {bin_dir}")
                continue

            try:
                if typer.confirm(
                    f"  Remove stale installation at {bin_dir}?",
                    default=False,
                ):
                    _remove_installation(inst.path, bin_dir)
            except Exception as e:
                log_error(f"Cleanup failed for {bin_dir}: {e}")


def _remove_installation(binary_path: Path, bin_dir: Path) -> None:
    """Remove a binary and its parent directory if empty."""
    try:
        binary_path.unlink()
        log_success(f"Removed {binary_path}")
        try:
            remaining = list(bin_dir.iterdir())
            if not remaining:
                bin_dir.rmdir()
                log_success(f"Removed empty directory {bin_dir}")
                # e.g. Programs/nu/ is left empty after removing bin/
                parent = bin_dir.parent
                if parent != parent.parent:  # not filesystem root
                    remaining_parent = list(parent.iterdir())
                    if not remaining_parent:
                        parent.rmdir()
                        log_success(f"Removed empty directory {parent}")
        except OSError:
            pass  # directory not empty or permission issue -- that's fine
    except OSError as e:
        log_error(f"Failed to remove {binary_path}: {e}")


# ── Multi-source tool detection ──────────────────────────────────


@dataclass
class MultiSourceReport:
    """A tool installed by more than one package manager."""

    tool_name: str
    sources: dict[str, list[Path]] = field(default_factory=dict)


def classify_install_source(path: Path) -> str:
    """Name the package manager that owns *path* ('other' when unknown)."""
    p = str(path).replace("\\", "/").lower()
    if "/mise/shims/" in p or "/mise/installs/" in p:
        return "mise"
    if "/chocolatey/" in p:
        return "choco"
    if "/.cargo/" in p:
        return "cargo"
    if "/winget/" in p:
        return "winget"
    if "/scoop/" in p:
        return "scoop"
    if "/python/python" in p and "/scripts/" in p:
        return "pip"
    if "/nodejs/" in p or "/npm/" in p:
        return "npm"
    if "/homebrew/" in p or "/cellar/" in p:
        return "homebrew"
    if p.startswith(("/usr/", "/bin/", "/sbin/")):
        return "system"
    return "other"


def detect_multi_source_tools() -> list[MultiSourceReport]:
    """Find registry tools installed by more than one package manager."""
    from .registry import DETECT_TOOLS, INTEGRATION_TOOLS, OPTIONAL_TOOLS

    infos = {**INTEGRATION_TOOLS, **OPTIONAL_TOOLS}
    reports: list[MultiSourceReport] = []
    for tool in DETECT_TOOLS:
        binary = infos[tool].binary if tool in infos else tool
        sources: dict[str, list[Path]] = {}
        for p in find_all_installations(binary):
            sources.setdefault(classify_install_source(p), []).append(p)
        if len(sources) > 1:
            reports.append(MultiSourceReport(tool_name=tool, sources=sources))
    return reports


# ── Stale standalone tool detection ──────────────────────────────


@dataclass
class StaleToolReport:
    """Report of a tool installed both via mise and standalone."""

    tool_name: str
    mise_path: str
    standalone_paths: list[Path] = field(default_factory=list)
    winget_id: str | None = None


def _get_mise_tool_path(tool_name: str) -> str | None:
    """Check if mise manages a tool. Returns the mise-managed path or None."""
    if not shutil.which("mise"):
        return None
    try:
        result = subprocess.run(
            ["mise", "which", tool_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError, OSError, subprocess.TimeoutExpired:
        pass
    return None


def _find_standalone_paths(tool_name: str, standalone_dirs: tuple[str, ...]) -> list[Path]:
    """Find standalone installations of a tool outside mise."""
    found: list[Path] = []
    if not is_windows():
        return found

    localappdata = os.environ.get("LOCALAPPDATA", "")
    if not localappdata:
        return found

    for rel_dir in standalone_dirs:
        candidate_dir = Path(localappdata) / rel_dir
        if candidate_dir.is_dir():
            found.append(candidate_dir)

    return found


def detect_stale_tools() -> list[StaleToolReport]:
    """Find integration tools installed both via mise and standalone.

    Returns a list of StaleToolReport for tools with redundant standalone installs.
    """
    from .registry import INTEGRATION_TOOLS

    reports: list[StaleToolReport] = []

    for name, info in INTEGRATION_TOOLS.items():
        if not info.standalone_dirs:
            continue

        mise_path = _get_mise_tool_path(info.binary)
        if not mise_path:
            continue

        standalone = _find_standalone_paths(name, info.standalone_dirs)
        if not standalone:
            continue

        reports.append(
            StaleToolReport(
                tool_name=name,
                mise_path=mise_path,
                standalone_paths=standalone,
                winget_id=info.winget_id,
            )
        )

    return reports


def prompt_cleanup_stale_tools(reports: list[StaleToolReport]) -> None:
    """Offer to uninstall standalone tool copies that are redundant with mise.

    Never raises -- errors are logged and setup continues.
    """
    if not reports:
        return

    interactive = sys.stdin is not None and sys.stdin.isatty()

    log_step("Stale standalone tool installations detected:")

    for report in reports:
        print(f"\n  {report.tool_name}:")
        print(f"    - mise-managed: {report.mise_path}")
        for p in report.standalone_paths:
            print(f"    - standalone:   {p}")

        if not interactive:
            log_warn(f"Non-interactive mode, skipping cleanup of standalone {report.tool_name}")
            continue

        if not report.winget_id:
            log_warn(
                f"No winget ID for {report.tool_name}, remove manually: {report.standalone_paths}"
            )
            continue

        if not is_windows():
            continue

        try:
            if typer.confirm(
                f"  Uninstall standalone {report.tool_name} via winget?",
                default=False,
            ):
                success = _uninstall_via_winget(report.tool_name, report.winget_id)
                if not success:
                    _offer_directory_removal(report.tool_name, report.standalone_paths)
        except Exception as e:
            log_error(f"Cleanup failed for {report.tool_name}: {e}")


def _offer_directory_removal(tool_name: str, standalone_paths: list[Path]) -> None:
    """Offer to remove standalone directories directly after winget failure."""
    for path in standalone_paths:
        if _is_protected_directory(path):
            log_warn(f"Skipping protected directory: {path}")
            continue
        if not os.access(path, os.W_OK):
            log_warn(f"No write access to {path} (may need admin)")
            continue
        try:
            if typer.confirm(
                f"  winget couldn't find the package. Remove {path} directly?",
                default=False,
            ):
                shutil.rmtree(path)
                log_success(f"Removed {path}")
        except OSError as e:
            log_error(f"Failed to remove {path}: {e}")


def _uninstall_via_winget(tool_name: str, winget_id: str) -> bool:
    """Uninstall a tool using winget. Returns True on success, False on failure."""
    cmd = [
        "winget",
        "uninstall",
        "--id",
        winget_id,
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    log_step(f"  Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, timeout=120)
        log_success(f"Uninstalled standalone {tool_name}")
        return True
    except subprocess.TimeoutExpired:
        log_error(f"Uninstall timed out for {tool_name}")
        return False
    except subprocess.CalledProcessError as e:
        log_error(f"Failed to uninstall {tool_name} (exit {e.returncode})")
        return False
