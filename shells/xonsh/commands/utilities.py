"""Utility commands for xonsh (port of shells/nushell/commands/utilities.nu)."""

import os
import platform
import shutil
import subprocess
from datetime import datetime
from urllib.parse import quote

from _common import _kill_pid


def _trash_freedesktop(abs_path: str) -> None:
    """Move a file to trash following the Freedesktop Trash spec.

    Creates both the file entry in Trash/files and the corresponding
    .trashinfo metadata file in Trash/info so desktop trash managers
    can restore the file.
    """
    trash_base = os.path.join(os.path.expanduser("~"), ".local", "share", "Trash")
    files_dir = os.path.join(trash_base, "files")
    info_dir = os.path.join(trash_base, "info")
    os.makedirs(files_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)

    basename = os.path.basename(abs_path)
    dest = os.path.join(files_dir, basename)

    # Handle name collisions by appending a counter
    counter = 1
    name_no_ext, ext = os.path.splitext(basename)
    while os.path.exists(dest) or os.path.exists(os.path.join(info_dir, basename + ".trashinfo")):
        basename = f"{name_no_ext}.{counter}{ext}"
        dest = os.path.join(files_dir, basename)
        counter += 1

    # Write .trashinfo file (Freedesktop Trash spec 1.0)
    trashinfo_path = os.path.join(info_dir, basename + ".trashinfo")
    deletion_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    trashinfo_content = (
        f"[Trash Info]\nPath={quote(abs_path, safe='/:@')}\nDeletionDate={deletion_date}\n"
    )
    with open(trashinfo_path, "w", encoding="utf-8") as f:
        f.write(trashinfo_content)

    shutil.move(abs_path, dest)


def _pq_ensure():
    """Start pueue daemon if not already running."""
    if shutil.which("pueued"):
        result = subprocess.run(
            ["pueue", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            subprocess.run(["pueued", "-d"], check=False)


def _pq(args, stdin=None):
    """Pq - Task queue manager (pueue wrapper).

    Usage:
        pq status               # Show queue status
        pq add -- sleep 10      # Add a task
        pq follow 0             # Follow task output
    """
    if not shutil.which("pueue"):
        print("pueue not found. Install with: brew install pueue  or  cargo install pueue")
        return
    _pq_ensure()
    subprocess.run(["pueue", *args], check=False)


def _port(args, stdin=None):
    """Port - Check and manage network ports.

    Usage:
        port 8080           # Check what's using port 8080
        port 8080 --kill    # Kill process using port 8080
        port --list         # List all listening ports
    """
    kill_flag = "--kill" in args or "-k" in args
    list_flag = "--list" in args or "-l" in args

    # Remove flags from args to get port number
    port_args = [a for a in args if a not in ("--kill", "-k", "--list", "-l")]

    if list_flag:
        if platform.system() == "Windows":
            netstat = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                check=False,
            )
            findstr = subprocess.run(
                ["findstr", "LISTENING"],
                input=netstat.stdout,
                capture_output=True,
                text=True,
                check=False,
            )
            print(findstr.stdout, end="")
        else:
            netstat = subprocess.run(
                ["netstat", "-tuln"],
                capture_output=True,
                text=True,
                check=False,
            )
            grep = subprocess.run(
                ["grep", "LISTEN"],
                input=netstat.stdout,
                capture_output=True,
                text=True,
                check=False,
            )
            print(grep.stdout, end="")
        return

    if not port_args:
        print("Usage: port <port_number> [--kill]")
        print("       port --list")
        return

    try:
        port_int = int(port_args[0])
        if not (1 <= port_int <= 65535):
            raise ValueError
    except ValueError:
        print(f"Invalid port number: {port_args[0]}")
        return
    port_number = str(port_int)

    use_lsof = shutil.which("lsof") is not None
    if platform.system() == "Windows":
        netstat = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
        )
        result = subprocess.run(
            ["findstr", f":{port_number}"],
            input=netstat.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
    elif use_lsof:
        result = subprocess.run(
            ["lsof", "-i", f":{port_number}"],
            capture_output=True,
            text=True,
        )
    else:
        # -p adds the PID/program column so --kill can find the PID
        netstat = subprocess.run(
            ["netstat", "-tlnp"],
            capture_output=True,
            text=True,
            check=False,
        )
        result = subprocess.run(
            ["grep", f":{port_number}"],
            input=netstat.stdout,
            capture_output=True,
            text=True,
            check=False,
        )

    output = result.stdout.strip()
    if not output:
        print(f"No process found using port {port_number}")
        return

    print(output)

    if kill_flag:
        lines = output.splitlines()
        if platform.system() == "Windows":
            # PID is the last numeric column in netstat output
            parts = [p for p in lines[0].split() if p]
            numeric = [p for p in parts if p.isdigit()]
            pid = numeric[-1] if numeric else (parts[-1] if parts else "")
        elif use_lsof:
            # PID is the second column (skip lsof header)
            data_line = lines[1] if len(lines) > 1 else lines[0]
            parts = [p for p in data_line.split() if p]
            pid = parts[1] if len(parts) > 1 else ""
        else:
            # netstat -tlnp: last column is "PID/program", or "-" without root
            parts = [p for p in lines[0].split() if p]
            pid = parts[-1].split("/")[0] if parts else ""

        _kill_pid(pid)


def _clip(args, stdin=None):
    """Clip - Clipboard operations.

    Usage:
        echo "text" | clip   # Copy text to clipboard
        clip                 # Paste from clipboard
    """
    # Check if we have piped input
    input_text = None
    if stdin is not None:
        input_text = stdin.read() if hasattr(stdin, "read") else str(stdin)

    if platform.system() == "Windows":
        if input_text:
            subprocess.run(["clip.exe"], input=input_text, text=True, check=False)
            print("Copied to clipboard")
        else:
            result = subprocess.run(
                ["powershell", "-command", "Get-Clipboard"],
                capture_output=True,
                text=True,
            )
            print(result.stdout, end="")

    elif platform.system() == "Darwin":
        if input_text:
            subprocess.run(["pbcopy"], input=input_text, text=True, check=False)
            print("Copied to clipboard")
        else:
            subprocess.run(["pbpaste"], check=False)

    else:
        # Linux: xclip or xsel
        tool = None
        if shutil.which("xclip"):
            tool = "xclip"
        elif shutil.which("xsel"):
            tool = "xsel"

        if not tool:
            print("No clipboard tool found. Install xclip or xsel.")
            return

        if tool == "xclip":
            if input_text:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=input_text,
                    text=True,
                    check=False,
                )
                print("Copied to clipboard")
            else:
                subprocess.run(["xclip", "-selection", "clipboard", "-o"], check=False)
        else:
            if input_text:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=input_text,
                    text=True,
                    check=False,
                )
                print("Copied to clipboard")
            else:
                subprocess.run(["xsel", "--clipboard", "--output"], check=False)


def _trash(args, stdin=None):
    """Trash - Safe delete (move to trash instead of permanent delete).

    Usage:
        trash file.txt          # Move file to trash
        trash *.log             # Move all .log files to trash
    """
    if not args:
        print("Usage: trash <file1> [file2] [...]")
        return

    for filepath in args:
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            print(f"Error: {filepath} does not exist")
            continue

        if platform.system() == "Windows":
            # Use PowerShell to move to Recycle Bin
            escaped = abs_path.replace("'", "''")
            result = subprocess.run(
                [
                    "powershell",
                    "-command",
                    f"Add-Type -AssemblyName Microsoft.VisualBasic; "
                    f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                    f"'{escaped}', 'OnlyErrorDialogs', 'SendToRecycleBin')",
                ],
                check=False,
            )
            if result.returncode == 0:
                print(f"Moved to Recycle Bin: {filepath}")
            else:
                print(f"Error: failed to trash {filepath}")

        elif platform.system() == "Darwin":
            if shutil.which("trash"):
                result = subprocess.run(["trash", abs_path], check=False)
            else:
                script = 'on run argv\ntell application "Finder" to delete POSIX file (item 1 of argv)\nend run'
                result = subprocess.run(
                    ["osascript", "-e", script, abs_path],
                    check=False,
                )
            if result.returncode == 0:
                print(f"Moved to Trash: {filepath}")
            else:
                print(f"Error: failed to trash {filepath}")

        else:
            # Linux
            if shutil.which("trash-put"):
                result = subprocess.run(["trash-put", abs_path], check=False)
                if result.returncode == 0:
                    print(f"Moved to Trash: {filepath}")
                else:
                    print(f"Error: failed to trash {filepath}")
            else:
                try:
                    _trash_freedesktop(abs_path)
                    print(f"Moved to Trash: {filepath}")
                except OSError as e:
                    print(f"Error: failed to trash {filepath}: {e}")
