"""Fuzzy commands for xonsh (port of shells/nushell/commands/fuzzy.nu)."""

import csv
import os
import platform
import shutil
import subprocess

from _common import _kill_pid, _require_tool


def _fx(args, stdin=None):
    """Fuzzy Exec - Fuzzy find and execute commands from PATH.

    Lists all executables in your PATH and lets you select one to run.

    Usage:
        fx
    """
    if not _require_tool("fzf", "install fzf"):
        return

    # Collect all executables from PATH
    executables = set()
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isdir(path_dir):
            continue
        try:
            for entry in os.listdir(path_dir):
                full = os.path.join(path_dir, entry)
                if os.path.isfile(full) and os.access(full, os.X_OK):
                    executables.add(entry)
        except PermissionError:
            continue

    sorted_execs = "\n".join(sorted(executables))

    try:
        result = subprocess.run(
            ["fzf"],
            input=sorted_execs,
            capture_output=True,
            text=True,
        )
        selected = result.stdout.strip()

        if selected:
            print(f"Running: {selected}")
            subprocess.run([selected], check=False)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        print(f"Error: {e}")


def _fh(args, stdin=None):
    """Fuzzy History - Search shell history interactively.

    If atuin is installed, delegates to atuin's interactive search.
    Otherwise falls back to fzf over local xonsh history.

    Usage:
        fh
    """
    # Prefer atuin when available
    if shutil.which("atuin"):
        try:
            subprocess.run(["atuin", "search", "--interactive"], check=False)
            return
        except FileNotFoundError, OSError:
            pass

    # Fallback to fzf
    if not _require_tool("fzf", "install fzf"):
        return

    try:
        # Access xonsh history via builtins
        import builtins

        hist = builtins.__xonsh__.history

        # Get unique commands in reverse order (most recent first)
        seen = set()
        commands = []
        for item in reversed(list(hist.items())):
            cmd = item.get("inp", "").strip() if hasattr(item, "get") else str(item).strip()
            if cmd and cmd not in seen:
                seen.add(cmd)
                commands.append(cmd)

        if not commands:
            print("No history entries found.")
            return

        history_text = "\n".join(commands)

        result = subprocess.run(
            ["fzf"],
            input=history_text,
            capture_output=True,
            text=True,
        )
        selected = result.stdout.strip()

        if selected:
            # Confirm before running -- match nushell fh, which prompts [y/N].
            print(f"Command: {selected}")
            if input("Execute? [y/N] ").strip().lower() == "y":
                # Use execx to run in xonsh context
                builtins.__xonsh__.execer.exec(selected)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, AttributeError) as e:
        print(f"Error: {e}")


def _fk(args, stdin=None):
    """Fuzzy Kill - Fuzzy find and kill processes.

    Usage:
        fk
    """
    if not _require_tool("fzf", "install fzf"):
        return

    try:
        used_procs = bool(shutil.which("procs"))
        if used_procs:
            proc_output = subprocess.run(
                ["procs", "--tree"],
                capture_output=True,
                text=True,
            ).stdout
        elif platform.system() == "Windows":
            # /fo csv keeps the PID in a fixed column; plain tasklist is space-aligned and ambiguous.
            proc_output = subprocess.run(
                ["tasklist", "/fo", "csv"],
                capture_output=True,
                text=True,
            ).stdout
        else:
            proc_output = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
            ).stdout

        # procs --tree prints a 2-line header; tasklist/ps print 1. Skip the right
        # count so a header row is never selectable (it parses to a non-PID).
        header_lines = "--header-lines=2" if used_procs else "--header-lines=1"
        result = subprocess.run(
            ["fzf", header_lines],
            input=proc_output,
            capture_output=True,
            text=True,
        )
        selected = result.stdout.strip()

        if selected:
            parts = [p for p in selected.split() if p]
            if used_procs:
                # procs --tree: PID is the first numeric token (tree chars aren't numeric).
                pid = next((p for p in parts if p.isdigit()), "")
            elif platform.system() == "Windows":
                # tasklist /fo csv: quoted fields, PID is column index 1.
                # csv.reader handles the comma inside "12,345 K" (Mem Usage).
                row = next(csv.reader([selected]), [])
                pid = row[1] if len(row) > 1 else ""
            else:
                # ps aux: `USER PID %CPU ...` -- PID is column 1, immune to a
                # numeric UID appearing in column 0 (owner with no passwd entry).
                pid = parts[1] if len(parts) > 1 else ""

            _kill_pid(pid)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        print(f"Error: {e}")
