"""Backup and rollback infrastructure for deployed configs.

Before overwriting, copies current configs to timestamped backup directories.
Keeps last N backups (configurable, default 5). The very first snapshot -- the
one holding the config the user had before my-shell -- is kept in a reserved
directory that the rotating ring never prunes.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from .config import load_settings
from .uninstall import (
    _NUSHELL_MANAGED_FILES,
    _NUSHELL_USER_FILES,
    _XONSH_USER_FILES,
    _is_my_shell_file,
)
from .utils import (
    get_home_dir,
    get_project_dir,
    guard_test_write,
    log_debug,
    log_header,
    log_info,
    log_success,
)

_BACKUP_DIR_NAME = ".my-shell-backup"
_PRE_ADOPTION_DIR_NAME = "pre-my-shell"

_USER_FILE_NAMES = frozenset(_NUSHELL_USER_FILES) | frozenset(_XONSH_USER_FILES)


def _get_backup_root(config_dir: Path) -> Path:
    """Get the backup root directory for a shell's config dir."""
    return config_dir / _BACKUP_DIR_NAME


def _copy_into(files: list[Path], dest_dir: Path) -> Path:
    """Copy *files* into *dest_dir*, creating it if needed."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for fpath in files:
        shutil.copy2(fpath, dest_dir / fpath.name)
        log_debug(f"Backed up: {fpath.name}")
    return dest_dir


def backup_before_deploy(
    shell: str,
    config_dir: Path,
    project_dir: Path | None = None,
) -> Path | None:
    """Create a timestamped backup of current config files before deploying.

    Returns the backup directory path, or None if there was nothing to back up.
    """
    try:
        settings = load_settings(project_dir or get_project_dir())
    except RuntimeError:
        # uninstall can run with the project dir gone; back up anyway, with the default ring.
        settings = {}
    max_count = settings.get("backup", {}).get("max_count", 5)

    if shell == "nushell":
        files_to_backup = list(_NUSHELL_MANAGED_FILES) + list(_NUSHELL_USER_FILES)
    elif shell == "xonsh":
        # xonsh uses ~/.xonshrc
        xonshrc = get_home_dir() / ".xonshrc"
        files_to_backup = []
        if xonshrc.exists():
            files_to_backup.append(str(xonshrc))
    else:
        return None

    existing_files = []
    if shell == "nushell":
        for fname in files_to_backup:
            fpath = config_dir / fname
            if fpath.exists():
                existing_files.append(fpath)
    elif shell == "xonsh":
        for fpath_str in files_to_backup:
            fpath = Path(fpath_str)
            if fpath.exists():
                existing_files.append(fpath)
        user_custom = config_dir / "user-custom.xsh"
        if user_custom.exists():
            existing_files.append(user_custom)

    if not existing_files:
        log_debug("No existing config files to back up")
        return None

    backup_root = _get_backup_root(config_dir)

    # Only worth reserving when something here was not written by my-shell.
    pre_adoption = backup_root / _PRE_ADOPTION_DIR_NAME
    if not pre_adoption.exists() and any(
        not _is_my_shell_file(f) for f in existing_files if f.name not in _USER_FILE_NAMES
    ):
        _copy_into(existing_files, pre_adoption)
        log_success(f"Saved pre-my-shell config: {pre_adoption}")

    # Microseconds: two deploys in the same second must not share a directory.
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")
    backup_dir = _copy_into(existing_files, backup_root / timestamp)

    log_success(f"Backup created: {backup_dir}")

    _prune_backups(backup_root, max_count)

    return backup_dir


def _rotating_backups(backup_root: Path) -> list[Path]:
    """Timestamped backup directories, newest first (pre-adoption excluded)."""
    return sorted(
        (d for d in backup_root.iterdir() if d.is_dir() and d.name != _PRE_ADOPTION_DIR_NAME),
        key=lambda d: d.name,
        reverse=True,
    )


def _prune_backups(backup_root: Path, max_count: int) -> None:
    """Keep only the most recent N timestamped backups."""
    if not backup_root.exists():
        return

    # A max_count of 0 or less would delete the backup taken seconds ago.
    keep = max(max_count, 1)
    for old_backup in _rotating_backups(backup_root)[keep:]:
        shutil.rmtree(old_backup, ignore_errors=True)
        log_debug(f"Pruned old backup: {old_backup.name}")


def list_backups(config_dir: Path) -> list[Path]:
    """List available backups, newest first, with the pre-my-shell snapshot last."""
    backup_root = _get_backup_root(config_dir)
    if not backup_root.exists():
        return []

    pre_adoption = backup_root / _PRE_ADOPTION_DIR_NAME
    backups = _rotating_backups(backup_root)
    if pre_adoption.is_dir():
        backups.append(pre_adoption)
    return backups


def restore_backup(backup_dir: Path, config_dir: Path, shell: str) -> None:
    """Restore files from a backup directory."""
    guard_test_write(config_dir, f"restore {shell} backup")
    if not backup_dir.exists():
        raise FileNotFoundError(f"Backup directory not found: {backup_dir}")

    for fpath in backup_dir.iterdir():
        if not fpath.is_file():
            continue

        # Layer 3 is never overwritten; the backup copy stays for manual recovery.
        if fpath.name in _USER_FILE_NAMES:
            log_info(f"Kept current: {fpath.name}")
            continue

        if shell == "xonsh" and fpath.name == ".xonshrc":
            dest = get_home_dir() / ".xonshrc"
        else:
            dest = config_dir / fpath.name

        shutil.copy2(fpath, dest)
        log_success(f"Restored: {fpath.name}")

    log_info(f"Rollback complete from {backup_dir.name}")


def preview_restore(backup_dir: Path, config_dir: Path, shell: str) -> bool:
    """Print a diff of what restoring *backup_dir* would change.

    Returns True if at least one file would change; False when the backup already
    matches the deployed config, so the caller can skip a no-op restore.
    """
    from .dry_run import _print_diff, _read_file, _unified_diff

    changed = False
    for fpath in sorted(backup_dir.iterdir()):
        if not fpath.is_file() or fpath.name in _USER_FILE_NAMES:
            continue
        if shell == "xonsh" and fpath.name == ".xonshrc":
            dest = get_home_dir() / ".xonshrc"
        else:
            dest = config_dir / fpath.name
        diff = _unified_diff(_read_file(dest), _read_file(fpath), fpath.name)
        if diff.strip():
            changed = True
            log_header(f"  {fpath.name}")
            _print_diff(diff)
    return changed
