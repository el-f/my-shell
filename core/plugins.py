"""Nushell plugin management.

Handles discovery, installation, registration, and config generation
for nushell plugins installed via cargo.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import load_toml
from .utils import (
    escape_nushell_path,
    get_home_dir,
    is_available,
    is_windows,
    log_error,
    log_step,
    log_success,
)

DEFAULT_PLUGINS: dict[str, dict[str, str]] = {
    "nu_plugin_gstat": {
        "crate": "nu_plugin_gstat",
        "description": "Git status as structured data",
    },
    "nu_plugin_formats": {
        "crate": "nu_plugin_formats",
        "description": "Extra format support (eml, ics, ini, vcf)",
    },
    "nu_plugin_query": {
        "crate": "nu_plugin_query",
        "description": "Query JSON, XML, and web data",
    },
    # nu_plugin_clipboard removed -- clipboard is built into nushell 0.111+ (clip copy / clip paste)
}

MIN_RUST_VERSION = (1, 85, 0)

# A plugin name becomes a binary path, a crate name becomes a cargo argument.
_PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _check_plugin_name(label: str, value: str) -> None:
    """Raise when *value* holds anything outside the crates.io character set."""
    if not _PLUGIN_NAME_RE.match(value):
        raise ValueError(
            f"Invalid {label}: {value!r}. Must start with a letter or digit, then letters, digits, '.', '_' or '-'."
        )


def _get_rustc_version() -> tuple[int, int, int] | None:
    """Parse rustc version. Returns (major, minor, patch) or None."""
    try:
        result = subprocess.run(
            ["rustc", "--version"], capture_output=True, text=True, check=False, timeout=10
        )
        match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2)), int(match.group(3))
    except OSError, subprocess.TimeoutExpired:
        pass
    return None


def _get_nu_version() -> str | None:
    """Get installed nushell version string (e.g. '0.109.1')."""
    try:
        result = subprocess.run(
            ["nu", "--version"], capture_output=True, text=True, check=False, timeout=10
        )
        version = result.stdout.strip()
        if re.match(r"\d+\.\d+\.\d+", version):
            return version
    except OSError, subprocess.TimeoutExpired:
        pass
    return None


def _plugin_nu_version(version: str) -> str:
    """`plugin list` reports `<crate>+<nu>` for third-party plugins, plain `<nu>` for official ones."""
    return version.rsplit("+", 1)[-1]


def _minor(version: str) -> tuple[int, int] | None:
    """(major, minor) from a version string, or None when it is not one."""
    match = re.match(r"(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else None


def registered_plugin_versions() -> dict[str, str] | None:
    """Nushell version each registered plugin was built against, or None when nu cannot answer."""
    try:
        result = subprocess.run(
            ["nu", "--commands", "plugin list | select name version | to json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    try:
        entries = json.loads(result.stdout)
    except ValueError:
        return None
    return {e["name"]: e["version"] for e in entries if "name" in e and "version" in e}


def stale_plugin_names(registered: dict[str, str], nu_version: str | None) -> list[str]:
    """Registered plugins built against a different nushell minor -- nu refuses to load these."""
    current = _minor(nu_version) if nu_version else None
    if current is None:
        return []
    return [
        name
        for name, version in registered.items()
        if _minor(_plugin_nu_version(version)) not in (None, current)
    ]


def load_plugin_list(project_dir: Path) -> dict[str, dict[str, str]]:
    """Load plugin definitions from config/plugins.toml.

    Falls back to DEFAULT_PLUGINS if the file doesn't exist.
    Supports config/plugins.local.toml overrides.
    """
    base_path = project_dir / "config" / "plugins.toml"
    if not base_path.exists():
        return dict(DEFAULT_PLUGINS)

    data = load_toml(base_path)
    plugins: dict[str, dict[str, str]] = data.get("plugins", {})

    # Merge local overrides
    local_path = project_dir / "config" / "plugins.local.toml"
    if local_path.exists():
        local_data = load_toml(local_path)
        local_plugins: dict[str, Any] = local_data.get("plugins", {})
        for key, value in local_plugins.items():
            if isinstance(value, dict):
                plugins[key] = value

    for name, info in plugins.items():
        _check_plugin_name("plugin name", name)
        crate = info.get("crate")
        if isinstance(crate, str):
            _check_plugin_name(f"crate of plugin '{name}'", crate)

    return plugins


def get_cargo_bin_dir() -> Path:
    """Return the cargo bin directory (~/.cargo/bin), cross-platform."""
    # get_home_dir() prefers $HOME (like the rest of the codebase), so it stays
    # consistent with where deploy/uninstall resolve home under a custom $HOME.
    return get_home_dir() / ".cargo" / "bin"


def _plugin_binary_name(name: str) -> str:
    """Return the expected binary name for a plugin."""
    if is_windows():
        return f"{name}.exe"
    return name


def is_plugin_installed(name: str) -> bool:
    """Check if a plugin binary exists in the cargo bin directory."""
    binary = get_cargo_bin_dir() / _plugin_binary_name(name)
    return binary.exists()


def install_plugin(crate_name: str, version: str | None = None) -> bool:
    """Install a single plugin via cargo install. Returns True on success."""
    cmd = ["cargo", "install"]
    if version:
        cmd.extend(["--version", version])
    cmd.extend(["--", crate_name])

    try:
        log_step(
            f"  Installing {crate_name} (compiling from source -- this may take a few minutes)..."
        )
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            _, stderr = process.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            process.kill()
            # Reap the child after killing it; otherwise it remains a zombie on
            # POSIX systems. The partial output is useful when troubleshooting,
            # but does not alter the timeout outcome.
            _, stderr = process.communicate()
            if stderr:
                sys.stderr.write(stderr)
                sys.stderr.flush()
            log_error(f"  {crate_name}: cargo build timed out after 10 minutes")
            return False
        if stderr:
            sys.stderr.write(stderr)
            sys.stderr.flush()
    except FileNotFoundError:
        log_error(f"  Failed to install {crate_name}: cargo not found")
        return False

    if process.returncode == 0:
        log_success(f"  Installed {crate_name}")
        return True

    # Build failure -- don't retry, it'll fail the same way
    if "requires rustc" in stderr:
        log_error(f"  Failed to install {crate_name}: rustc is too old -- run `rustup update`")
        return False

    if "could not compile" in stderr or "failed to compile" in stderr:
        log_error(f"  Failed to install {crate_name}: compilation error")
        return False

    # A Nushell plugin must match the active Nushell minor version. Installing
    # an arbitrary latest release here can compile successfully but then fail
    # registration because it speaks an older plugin protocol.
    if version:
        log_error(f"  Failed to install {crate_name}: compatible version {version} not found")
        return False

    log_error(f"  Failed to install {crate_name}")
    return False


def install_plugins(project_dir: Path) -> bool:
    """Install all missing plugins and return whether every install succeeded."""
    if not is_available("cargo"):
        log_error("cargo not found in PATH -- cannot install nushell plugins")
        return False

    # Pre-flight: check Rust version
    rust_ver = _get_rustc_version()
    if rust_ver and rust_ver < MIN_RUST_VERSION:
        ver_str = ".".join(str(v) for v in rust_ver)
        min_str = ".".join(str(v) for v in MIN_RUST_VERSION)
        log_error(
            f"Rust {ver_str} is too old -- nushell plugins require {min_str}+. Run: rustup update"
        )
        return False

    plugins = load_plugin_list(project_dir)

    # Detect Nu version for auto-matching official plugins
    nu_version = _get_nu_version()
    nu_ver_spec = None
    if nu_version:
        parts = nu_version.split(".")
        if len(parts) >= 2:
            nu_ver_spec = f"~{parts[0]}.{parts[1]}"

    stale = stale_plugin_names(registered_plugin_versions() or {}, nu_version)

    installed = 0
    skipped = 0
    failed = 0

    for name, info in plugins.items():
        if is_plugin_installed(name) and name.removeprefix("nu_plugin_") not in stale:
            log_step(f"  {name}: already installed")
            skipped += 1
        else:
            explicit_ver = info.get("version")
            if explicit_ver:
                ver = explicit_ver
            elif info["crate"].startswith("nu_plugin_") and nu_ver_spec:
                ver = nu_ver_spec
            else:
                ver = None

            if install_plugin(info["crate"], version=ver):
                installed += 1
            else:
                failed += 1

    log_success(f"Plugins: {installed} installed, {skipped} already present, {failed} failed")
    return failed == 0


def register_plugin(name: str) -> bool:
    """Register a plugin with nushell via 'nu --commands "plugin add <path>"'."""
    binary = get_cargo_bin_dir() / _plugin_binary_name(name)
    if not binary.exists():
        log_error(f"  Plugin binary not found: {binary}")
        return False

    try:
        cmd = f'plugin add "{escape_nushell_path(binary)}"'
        subprocess.run(
            ["nu", "--commands", cmd],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        log_success(f"  Registered {name}")
        return True
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as e:
        log_error(f"  Failed to register {name}: {e}")
        return False


def register_plugins(project_dir: Path) -> bool:
    """Register all installed plugins and return whether every attempt succeeded."""
    if not is_available("nu"):
        log_error("nu not found in PATH -- cannot register plugins")
        return False

    plugins = load_plugin_list(project_dir)
    registered = 0
    failed = 0

    for name in plugins:
        if is_plugin_installed(name):
            if register_plugin(name):
                registered += 1
            else:
                failed += 1

    log_success(f"Plugins: {registered} registered, {failed} failed")
    return failed == 0


def generate_plugin_use_statements(project_dir: Path) -> str:
    """Generate 'plugin use <name>' lines for all installed plugins.

    Returns the block of text to insert into the generated nushell config.
    """
    plugins = load_plugin_list(project_dir)
    lines: list[str] = []

    for name in plugins:
        if is_plugin_installed(name):
            short_name = name.removeprefix("nu_plugin_")
            lines.append(f"plugin use {short_name}")

    if not lines:
        return "# No nushell plugins installed\n"

    return "\n".join(lines) + "\n"
