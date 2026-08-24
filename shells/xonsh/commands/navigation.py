"""Navigation commands for xonsh (port of shells/nushell/commands/navigation.nu)."""

import os
import platform
import shutil
import subprocess
import tempfile

from _common import _require_tool


def _fj(args, stdin=None):
    """Folder Jump - Fuzzy find and jump to directories.

    Usage:
        fj              # Search all directories from root
        fj ~/projects   # Search directories under ~/projects
        fj .            # Search directories from current location
    """
    fd_cmd = _find_fd()
    if not fd_cmd:
        print("Error: fd not found (install fd or fd-find)")
        return
    if not _require_tool("fzf", "install fzf"):
        return

    if args:
        search_path = args[0]
    elif platform.system() == "Windows":
        search_path = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    else:
        search_path = "/"

    try:
        fd_proc = subprocess.Popen(
            [fd_cmd, "--type", "d", "--hidden", "--exclude", ".git", ".", search_path],
            stdout=subprocess.PIPE,
            text=True,
        )
        fzf_proc = subprocess.Popen(
            ["fzf"],
            stdin=fd_proc.stdout,
            stdout=subprocess.PIPE,
            text=True,
        )
        fd_proc.stdout.close()
        selected, _ = fzf_proc.communicate()
        selected = selected.strip()

        if selected:
            os.chdir(selected)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        print(f"Error: {e}")


def _y(args, stdin=None):
    """Yazi - Terminal file manager with directory change support.

    When you quit yazi (q), the shell will cd to the last directory you were in.

    Usage:
        y               # Open yazi in current directory
        y ~/projects    # Open yazi in ~/projects
    """
    if not shutil.which("yazi"):
        print("Error: yazi not found (install yazi)")
        return

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="yazi-cwd-", suffix=".txt", delete=False) as f:
            tmp_path = f.name

        cmd = ["yazi", *list(args), "--cwd-file", tmp_path]
        subprocess.run(cmd, check=False)

        if os.path.exists(tmp_path):
            with open(tmp_path) as fh:
                cwd = fh.read().strip()
            if cwd and cwd != os.getcwd():
                os.chdir(cwd)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        print(f"Error running yazi: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _find_fd():
    """Find the fd binary (may be 'fd' or 'fdfind' on Debian)."""
    for name in ("fd", "fdfind"):
        if shutil.which(name):
            return name
    return None
