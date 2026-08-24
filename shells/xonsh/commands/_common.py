"""Small helpers shared by the xonsh command modules."""

import platform
import shutil
import subprocess


def _require_tool(name: str, install_hint: str = "") -> bool:
    """Check if a tool is available, printing an error if not. Returns True if available."""
    if shutil.which(name):
        return True
    hint = f" ({install_hint})" if install_hint else ""
    print(f"Error: {name} not found{hint}")
    return False


def _valid_pid(pid: str) -> bool:
    """A killable PID: a positive integer.

    Rejects "0" (kill -9 0 signals the whole process group), "-" (netstat, not our
    process), "" and any non-numeric token.
    """
    return pid.isdigit() and int(pid) > 0


def _kill_pid(pid: str) -> None:
    """Validate then kill a PID -- the one place fk/port run the destructive command."""
    if not _valid_pid(pid):
        print(f"Error: no killable PID found ({pid!r})")
        return
    print(f"Killing process {pid}...")
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", pid, "/F"], check=False)
    else:
        subprocess.run(["kill", "-9", pid], check=False)
