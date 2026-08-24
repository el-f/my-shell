"""Install shell binaries and integration tools."""

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .detect import detect_package_manager
from .registry import (
    INSTALLABLE_TOOLS,
    TOOL_REGISTRY,
    XONTRIB_PACKAGES,
    XONTRIB_PACKAGES_WINDOWS,
)
from .utils import (
    clear_availability_cache,
    download_bytes,
    get_os,
    is_available,
    log_error,
    log_step,
    log_success,
)


def _path_hint() -> str:
    """Directory a freshly installed tool likely landed in, for a PATH reminder."""
    pkg = detect_package_manager()
    if pkg == "winget":
        return str(Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links")
    if pkg == "homebrew":
        return "/opt/homebrew/bin (or /usr/local/bin on Intel Macs)"
    if is_available("cargo"):
        return str(Path.home() / ".cargo" / "bin")
    return str(Path.home() / ".local" / "bin")


def _not_in_path_msg(name: str) -> str:
    """Message when a tool installs but isn't on PATH yet, naming the dir to add."""
    return (
        f"{name} not found in PATH after installation. It is likely in {_path_hint()} -- "
        "open a new terminal to pick up PATH changes."
    )


def install_shell(shell: str) -> None:
    """Install a shell binary by name."""
    if shell == "nushell":
        _install_nushell()
    elif shell == "xonsh":
        _install_xonsh()
    else:
        raise ValueError(f"Unknown shell: {shell!r}")


def _get_nu_version() -> str | None:
    """Get the installed nushell version, or None if not available."""
    if not is_available("nu"):
        return None
    try:
        result = subprocess.run(
            ["nu", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError, subprocess.TimeoutExpired:
        # OSError covers FileNotFoundError plus a non-executable/corrupt binary
        pass
    return None


# winget exits 43 (UPDATE_NOT_APPLICABLE) when the package is already current.
_UPGRADE_NOT_APPLICABLE: dict[str, tuple[int, ...]] = {"winget": (43,)}


def _brew_has_outdated(formula: str) -> bool:
    """True when brew reports a newer version, or when the check itself fails."""
    try:
        result = subprocess.run(
            ["brew", "outdated", formula],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except OSError, subprocess.TimeoutExpired:
        return True
    if result.returncode != 0 and not result.stdout.strip():
        return True
    return bool(result.stdout.strip())


def _install_nushell() -> None:
    """Install or upgrade nushell using the system package manager."""
    old_version = _get_nu_version()
    already_installed = old_version is not None

    pkg = detect_package_manager()

    install_commands: dict[str, list[str]] = {
        "winget": [
            "winget",
            "install",
            "--id",
            "Nushell.Nushell",
            "-e",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ],
        "homebrew": ["brew", "install", "nushell"],
        # -Sy: without a database sync pacman requests a version the mirrors already dropped.
        "pacman": ["pacman", "-Sy", "--noconfirm", "nushell"],
    }

    upgrade_commands: dict[str, list[str]] = {
        "winget": [
            "winget",
            "upgrade",
            "--id",
            "Nushell.Nushell",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ],
        "homebrew": ["brew", "upgrade", "nushell"],
        # -Sy: without a database sync pacman requests a version the mirrors already dropped.
        "pacman": ["pacman", "-Sy", "--noconfirm", "nushell"],
    }

    if already_installed:
        log_step(f"nushell {old_version} found, checking for upgrade...")
        commands = upgrade_commands
    else:
        log_step("Installing nushell...")
        commands = install_commands

    if pkg in commands:
        if already_installed:
            if pkg == "homebrew" and not _brew_has_outdated("nushell"):
                log_success(f"nushell {old_version} is up to date")
                return
            try:
                _run(commands[pkg], ok_exit_codes=_UPGRADE_NOT_APPLICABLE.get(pkg, ()))
            except RuntimeError:
                log_step(
                    f"  Upgrade failed (may need admin rights), continuing with nushell {old_version}"
                )
        else:
            _run(commands[pkg])
    elif get_os() == "linux":
        if already_installed:
            # A binary supplied by mise, cargo, or a manual install has no
            # system-package upgrade path. Keep that working installation;
            # repeatedly downloading "latest" is both surprising and prone to
            # GitHub rate limits during setup/test loops.
            log_success(f"nushell {old_version} is installed (no upgrade method for {pkg})")
            return
        # The release tarball is plain glibc Linux, so it covers
        # dnf/zypper/unknown on a genuinely clean machine.
        _install_nushell_from_github()
    else:
        if already_installed:
            log_success(f"nushell {old_version} is installed (no upgrade method for {pkg})")
            return
        raise RuntimeError(
            "No supported package manager found. "
            "Install nushell manually: https://www.nushell.sh/book/installation.html"
        )

    new_version = _get_nu_version()
    if new_version and old_version and new_version != old_version:
        log_success(f"nushell upgraded: {old_version} -> {new_version}")
    elif new_version:
        log_success(f"nushell {new_version} is up to date")
    elif shutil.which("nu"):
        log_success("nushell installed successfully")
    else:
        log_error(_not_in_path_msg("nushell"))


def _install_nushell_from_github() -> None:
    """Download nushell from GitHub releases for Debian/Ubuntu systems."""
    arch = platform.machine()
    if arch == "x86_64":
        target = "x86_64-unknown-linux-gnu"
    elif arch in ("aarch64", "arm64"):
        target = "aarch64-unknown-linux-gnu"
    else:
        raise RuntimeError(f"Unsupported architecture for nushell GitHub install: {arch}")

    pinned = os.environ.get("MY_SHELL_NU_VERSION")
    release_url = (
        f"https://api.github.com/repos/nushell/nushell/releases/tags/{pinned}"
        if pinned
        else "https://api.github.com/repos/nushell/nushell/releases/latest"
    )
    log_step(f"Fetching nushell {pinned or 'latest'} release from GitHub...")
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(release_url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read().decode())

    tag = release["tag_name"]
    # Try both naming conventions (nushell dropped -full suffix in 0.110.0)
    asset_url = None
    asset_name = None
    for suffix in ["", "-full"]:
        asset_name = f"nu-{tag}-{target}{suffix}.tar.gz"
        for asset in release["assets"]:
            if asset["name"] == asset_name:
                asset_url = asset["browser_download_url"]
                break
        if asset_url:
            break

    if asset_url is None or asset_name is None:
        raise RuntimeError(f"Could not find release asset for {target!r} in {tag}")

    is_root = getattr(os, "getuid", lambda: 1000)() == 0
    install_dir = Path("/usr/local/bin") if is_root else Path.home() / ".local" / "bin"
    install_dir.mkdir(parents=True, exist_ok=True)

    log_step(f"Downloading {asset_name}...")
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = Path(tmpdir) / asset_name
        archive_path.write_bytes(download_bytes(asset_url, timeout=30, description=asset_name))

        log_step("Extracting nu binary...")
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("/nu") or member.name == "nu":
                    member.name = "nu"
                    tar.extract(member, install_dir)
                    (install_dir / "nu").chmod(0o755)
                    break
            else:
                raise RuntimeError(f"nu binary not found in archive {asset_name}")

    log_step(f"Installed nu to {install_dir / 'nu'}")


def _get_xonsh_version() -> str | None:
    """Get the installed xonsh version, or None if not available."""
    if not is_available("xonsh"):
        return None
    try:
        result = subprocess.run(
            ["xonsh", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output format: "xonsh/<ver>" or just "<ver>"
            text = result.stdout.strip()
            if "/" in text:
                return text.split("/", 1)[1]
            return text
    except OSError, subprocess.TimeoutExpired:
        # OSError covers FileNotFoundError plus a non-executable/corrupt binary
        pass
    return None


def _install_xonsh() -> None:
    """Install or upgrade xonsh via uv tool install, with pip fallback."""
    old_version = _get_xonsh_version()
    already_installed = old_version is not None

    has_uv = is_available("uv")
    has_pip = is_available("pip")
    xontribs = [*XONTRIB_PACKAGES]
    if sys.platform == "win32":
        xontribs.extend(XONTRIB_PACKAGES_WINDOWS)

    def uv_install_command(*, force: bool = False) -> list[str]:
        command = ["uv", "tool", "install"]
        if force:
            command.extend(["--force", "--upgrade"])
        command.append("xonsh[full]")
        for package in xontribs:
            command.extend(["--with", package])
        return command

    if already_installed:
        log_step(f"xonsh {old_version} found, checking for upgrade...")
        if has_uv:
            try:
                _run(["uv", "tool", "upgrade", "xonsh"])
            except RuntimeError:
                # The executable can come from Homebrew, pip, or a different
                # uv tool directory. Do not install a second copy just because
                # the active uv directory does not own this one.
                log_step("xonsh is not managed by the active uv tool directory; keeping it")
            else:
                # `uv tool upgrade` recreates the environment from its receipt.
                # Older installs did not record xontribs there, so immediately
                # persist the complete managed environment as one transaction.
                _run(uv_install_command(force=True))
        elif has_pip:
            _run(["pip", "install", "--user", "--upgrade", "xonsh[full]"])
        else:
            log_success(f"xonsh {old_version} is installed (no upgrade method available)")
            return
    else:
        log_step("Installing xonsh...")
        if has_uv:
            _run(uv_install_command())
        elif has_pip:
            _run(["pip", "install", "--user", "xonsh[full]"])
        else:
            raise RuntimeError(
                "Neither uv nor pip found. Install uv (https://docs.astral.sh/uv/) or pip, then retry."
            )

    new_version = _get_xonsh_version()
    if new_version and old_version and new_version != old_version:
        log_success(f"xonsh upgraded: {old_version} -> {new_version}")
    elif new_version:
        log_success(f"xonsh {new_version} is up to date")
    elif shutil.which("xonsh"):
        log_success("xonsh installed successfully")
    else:
        log_error(_not_in_path_msg("xonsh"))


def install_shells_for_setup(
    settings: dict | None = None,
    install_xonsh_override: bool = False,
    shells: list[str] | None = None,
) -> None:
    """Install shell binaries as part of setup.

    If *shells* is provided, only installs/upgrades those shells.
    Otherwise, always installs nushell and conditionally installs xonsh.
    """
    if settings is None:
        from .config import load_settings

        settings = load_settings()

    target_shells = shells or ["nushell", "xonsh"]

    if "nushell" in target_shells:
        _install_nushell()

    xonsh_enabled = settings.get("shells", {}).get("xonsh", False)
    if "xonsh" in target_shells and (xonsh_enabled or install_xonsh_override):
        _install_xonsh()

    # Scanning only pays off when we can prompt to fix what it finds.
    if not (sys.stdin is not None and sys.stdin.isatty()):
        return

    from .duplicates import (
        detect_duplicate_shells,
        detect_stale_tools,
        prompt_cleanup_duplicates,
        prompt_cleanup_stale_tools,
    )

    reports = detect_duplicate_shells()
    prompt_cleanup_duplicates(reports)

    stale = detect_stale_tools()
    prompt_cleanup_stale_tools(stale)


# ── Tool installation ─────────────────────────────────────────────

TOOLS = INSTALLABLE_TOOLS


def resolve_install_command(name: str) -> list[str] | None:
    """The command install_tool would run for `name` on this platform, or None.

    Mirrors install_tool's selection: mise when available (one manager owns all
    tools), else the detected package manager, else cargo. Used by --dry-run.
    """
    if name not in TOOLS:
        return None
    commands = TOOLS[name]
    if "mise" in commands and is_available("mise"):
        return commands["mise"]
    pkg = detect_package_manager()
    if pkg in commands:
        cmd = commands[pkg]
        is_root = getattr(os, "getuid", lambda: 1000)() == 0
        if is_root and cmd[:1] == ["sudo"]:
            cmd = cmd[1:]
        return cmd
    if "cargo" in commands and is_available("cargo"):
        return commands["cargo"]
    return None


def install_tool(name: str) -> None:
    """Install an integration tool by name."""
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name!r} (available: {', '.join(TOOLS)})")

    binary = TOOL_REGISTRY[name].binary
    if is_available(binary):
        log_success(f"{name} is already installed")
        return

    log_step(f"Installing {name}...")
    cmd = resolve_install_command(name)
    if cmd is None:
        raise RuntimeError(
            f"No supported install method for {name}. Supported: {', '.join(TOOLS[name])}"
        )
    # cargo builds from source; 120s is not enough for a cold compile.
    _run(cmd, timeout=1800 if cmd[:1] == ["cargo"] else 120)

    if shutil.which(binary):
        log_success(f"{name} installed successfully")
    else:
        log_error(_not_in_path_msg(binary))


def preview_install_all() -> None:
    """Print the command each enabled + missing tool would run, without installing."""
    from .config import is_integration_enabled, load_settings
    from .registry import INTEGRATION_TOOLS
    from .utils import get_project_dir, log_info

    settings = load_settings(get_project_dir())
    log_step("install-tools --dry-run: commands that would run")
    for name in TOOLS:
        if name in INTEGRATION_TOOLS and not is_integration_enabled(settings, name):
            continue
        if is_available(TOOL_REGISTRY[name].binary):
            log_info(f"{name}: already installed -- skip")
            continue
        cmd = resolve_install_command(name)
        log_info(f"{name}: {' '.join(cmd) if cmd else 'no install method for this platform'}")


def install_all_tools() -> None:
    """Install integration tools that are enabled in settings."""
    from .config import is_integration_enabled, load_settings
    from .registry import INTEGRATION_TOOLS
    from .utils import get_project_dir

    settings = load_settings(get_project_dir())

    # mise first: once it lands, every later tool can resolve through it
    ordered = sorted(TOOLS, key=lambda n: n != "mise")
    for name in ordered:
        # Skip integration tools that are disabled by profile
        if name in INTEGRATION_TOOLS and not is_integration_enabled(settings, name):
            continue
        try:
            install_tool(name)
        except RuntimeError as e:
            log_error(str(e))


def _run(
    cmd: list[str],
    *,
    capture: bool = False,
    timeout: int = 120,
    ok_exit_codes: tuple[int, ...] = (),
) -> None:
    """Run a subprocess command, raising on failure with a friendly message.

    With capture=True the command's output is captured (not streamed) and its
    stderr is appended to the RuntimeError, so a caller that swallows the error
    can still report why it failed. Default streams to the console.
    """
    log_step(f"  Running: {' '.join(cmd)}")
    run_kwargs: dict = {"check": True, "stdin": subprocess.DEVNULL, "timeout": timeout}
    if capture:
        run_kwargs["capture_output"] = True
        run_kwargs["text"] = True
    try:
        subprocess.run(cmd, **run_kwargs)
        clear_availability_cache()
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run: {' '.join(cmd)} ({exc})") from exc
    except subprocess.CalledProcessError as exc:
        if exc.returncode in ok_exit_codes:
            clear_availability_cache()
            return
        detail = f"Command failed (exit {exc.returncode}): {' '.join(cmd)}"
        stderr = (exc.stderr or "").strip() if capture else ""
        if stderr:
            detail += f"\n{stderr}"
        raise RuntimeError(detail) from exc
