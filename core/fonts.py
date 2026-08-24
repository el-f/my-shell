"""Nerd Font detection and installation for oh-my-posh prompt theming.

Provides a font registry, filesystem-based detection, and multi-strategy
installation (oh-my-posh CLI, brew, GitHub releases).
"""

import contextlib
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from .config import load_settings
from .utils import (
    download_bytes,
    get_home_dir,
    get_os,
    guard_test_write,
    is_available,
    log_debug,
    log_error,
    log_step,
    log_success,
    log_warn,
)

# Latest Nerd Fonts release tag used for GitHub downloads
_NERD_FONTS_VERSION = "v3.3.0"
_GITHUB_RELEASE_URL = (
    f"https://github.com/ryanoasis/nerd-fonts/releases/download/{_NERD_FONTS_VERSION}"
)

NERD_FONTS: dict[str, dict[str, str]] = {
    "meslo": {
        "name": "Meslo",
        "display": "Meslo LG Nerd Font",
        "glob": "MesloLG*Nerd*",
        "brew_cask": "font-meslo-lg-nerd-font",
        "github_asset": "Meslo.zip",
    },
    "firacode": {
        "name": "FiraCode",
        "display": "FiraCode Nerd Font",
        "glob": "FiraCode*Nerd*",
        "brew_cask": "font-fira-code-nerd-font",
        "github_asset": "FiraCode.zip",
    },
}


def is_headless_linux() -> bool:
    """Linux with no graphical session; font install is pointless there."""
    return (
        get_os() == "linux"
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    )


def _font_dirs() -> list[Path]:
    """Return platform-specific font directories to search."""
    os_name = get_os()
    home = get_home_dir()

    if os_name == "windows":
        local_fonts = home / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
        system_fonts = Path("C:/Windows/Fonts")
        return [local_fonts, system_fonts]
    elif os_name == "macos":
        return [home / "Library" / "Fonts", Path("/Library/Fonts")]
    else:
        return [home / ".local" / "share" / "fonts", Path("/usr/share/fonts")]


def is_nerd_font_installed(font_key: str) -> bool:
    """Check whether a Nerd Font is installed by scanning font directories.

    Falls back to ``fc-list`` on Linux when directory glob finds nothing.
    """
    meta = NERD_FONTS.get(font_key)
    if not meta:
        return False

    pattern = meta["glob"]

    for font_dir in _font_dirs():
        if not font_dir.is_dir():
            continue
        # Recursive glob to handle sub-directories (Linux /usr/share/fonts/*)
        if list(font_dir.rglob(f"{pattern}*.ttf")) or list(font_dir.rglob(f"{pattern}*.otf")):
            return True

    # Linux fallback: fc-list
    if get_os() == "linux" and is_available("fc-list"):
        try:
            result = subprocess.run(
                ["fc-list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if meta["display"].lower() in result.stdout.lower():
                return True
        except subprocess.TimeoutExpired, FileNotFoundError, OSError:
            pass

    return False


def _install_via_omp(font_name: str) -> bool:
    """Install a Nerd Font using ``oh-my-posh font install``."""
    if not is_available("oh-my-posh"):
        return False

    log_step(f"Installing {font_name} via oh-my-posh...")
    try:
        subprocess.run(
            ["oh-my-posh", "font", "install", font_name],
            check=True,
            timeout=120,
        )
        log_success(f"Installed {font_name} via oh-my-posh")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log_debug(f"oh-my-posh font install failed: {exc}")
        return False


def _install_via_brew(brew_cask: str) -> bool:
    """Install a Nerd Font via Homebrew cask (macOS)."""
    if get_os() != "macos" or not is_available("brew"):
        return False

    log_step(f"Installing {brew_cask} via Homebrew...")
    try:
        subprocess.run(
            ["brew", "install", "--cask", brew_cask],
            check=True,
            timeout=180,
        )
        log_success(f"Installed {brew_cask} via Homebrew")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log_debug(f"brew cask install failed: {exc}")
        return False


def _install_via_github(font_key: str) -> bool:
    """Download a Nerd Font zip from GitHub releases and install .ttf files."""
    meta = NERD_FONTS.get(font_key)
    if not meta:
        return False

    url = f"{_GITHUB_RELEASE_URL}/{meta['github_asset']}"

    os_name = get_os()
    if os_name == "windows":
        dest = get_home_dir() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"
    elif os_name == "macos":
        dest = get_home_dir() / "Library" / "Fonts"
    else:
        dest = get_home_dir() / ".local" / "share" / "fonts"

    guard_test_write(dest, "install a font")
    dest.mkdir(parents=True, exist_ok=True)

    log_step(f"Downloading {meta['display']} from GitHub...")
    try:
        import http.client
        import urllib.error

        data = download_bytes(url, timeout=60, description=meta["display"])
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        # HTTPException covers a truncated read (IncompleteRead); OSError covers
        # URLError/timeouts. A real bug (NameError etc.) still propagates.
        log_debug(f"GitHub download failed: {exc}")
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            count = 0
            for name in zf.namelist():
                if name.lower().endswith((".ttf", ".otf")) and not name.startswith("__MACOSX"):
                    zf.extract(name, dest)
                    count += 1
        if count == 0:
            log_debug("Zip contained no font files")
            return False
        log_success(f"Extracted {count} font files to {dest}")
    except (zipfile.BadZipFile, OSError) as exc:
        log_debug(f"Zip extraction failed: {exc}")
        return False

    # Refresh font cache on Linux
    if os_name == "linux" and is_available("fc-cache"):
        with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError, OSError):
            subprocess.run(["fc-cache", "-fv"], capture_output=True, timeout=30)

    return True


def install_nerd_font(font_key: str, *, force: bool = False) -> bool:
    """Install a Nerd Font using the best available method.

    Tries oh-my-posh first, then platform-specific fallbacks.
    Skips on headless systems (no DISPLAY/WAYLAND_DISPLAY) unless *force* is True.
    Returns True if installation succeeded.
    """
    meta = NERD_FONTS.get(font_key)
    if not meta:
        log_error(f"Unknown font: {font_key}. Available: {', '.join(NERD_FONTS)}")
        return False

    if (
        not force
        and get_os() == "linux"
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    ):
        log_success(
            f"Skipping {meta['display']} install -- no graphical environment detected. Use --force to override."
        )
        return True

    if is_nerd_font_installed(font_key):
        log_success(f"{meta['display']} is already installed")
        return True

    if _install_via_omp(meta["name"]):
        return True

    os_name = get_os()
    if os_name == "macos" and _install_via_brew(meta["brew_cask"]):
        return True

    if _install_via_github(font_key):
        return True

    log_warn(f"Could not install {meta['display']} automatically")
    log_warn("Install manually from: https://www.nerdfonts.com/font-downloads")
    return False


def font_preview(font_key: str) -> str:
    """A glyph sample plus a reminder to set the font in the terminal.

    Falls back to ASCII text on a terminal that can't encode the glyphs
    (guards against UnicodeEncodeError on legacy code pages).
    """
    meta = NERD_FONTS.get(font_key)
    display = meta["display"] if meta else font_key
    # Powerline separators + a few common Nerd Font icons.
    glyphs = "      "

    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in encoding:
        return (
            f"{display} installed.\n"
            f"  Glyph sample: {glyphs}\n"
            f'  If those show as boxes, set your terminal font to "{display}".'
        )
    return (
        f"{display} installed.\n"
        f'  Set your terminal font to "{display}" to render icons and powerline glyphs.'
    )


def ensure_nerd_font(project_dir: Path) -> None:
    """Non-fatal wrapper: install the configured Nerd Font during deploy.

    Reads ``[fonts]`` from settings.toml. Never raises -- catches and logs
    warnings so that deploy can continue even if font installation fails.
    """
    if os.environ.get("MY_SHELL_SKIP_FONTS"):
        log_debug("Font install skipped (MY_SHELL_SKIP_FONTS set)")
        return

    # Only Linux can be headless here; macOS and Windows always render fonts.
    if is_headless_linux():
        log_debug("Skipping font install (no graphical environment detected)")
        return

    try:
        settings = load_settings(project_dir)
        fonts_cfg = settings.get("fonts", {})

        if not fonts_cfg.get("auto_install", True):
            log_debug("Font auto-install disabled in settings")
            return

        font_key = fonts_cfg.get("nerd_font", "meslo")
        if font_key not in NERD_FONTS:
            log_warn(f"Unknown font '{font_key}' in settings. Available: {', '.join(NERD_FONTS)}")
            return

        if is_nerd_font_installed(font_key):
            log_debug(f"{NERD_FONTS[font_key]['display']} already installed")
            return

        install_nerd_font(font_key)
    except Exception as exc:
        log_warn(f"Font installation skipped: {exc}")
