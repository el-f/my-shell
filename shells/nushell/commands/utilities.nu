# Utility Commands

use ../utils.nu *

# Port - Check and manage network ports
# Usage:
#   port 8080           # Check what's using port 8080
#   port 8080 -k        # Kill process using port 8080
#   port --list         # List all listening ports
export def port [
    port_number?: int   # Port number to check
    --kill(-k)          # Kill the process using this port
    --list(-l)          # List all listening ports
] {
    if $list {
        # List all listening ports
        if (is-windows) {
            ^netstat -ano | findstr LISTENING
        } else {
            ^netstat -tuln | grep LISTEN
        }
        return
    }

    if ($port_number | is-empty) {
        print "Usage: port <port_number> [--kill]"
        print "       port --list"
        return
    }

    if ($port_number < 1 or $port_number > 65535) {
        print "Invalid port number"
        return
    }

    # Find process using the port
    let process_info = if (is-windows) {
        ^netstat -ano | findstr $":($port_number)"
    } else {
        if (is-available "lsof") {
            ^lsof -i $":($port_number)"
        } else {
            # -p adds the PID/program column so --kill can find the PID
            ^netstat -tlnp | grep $":($port_number)"
        }
    }

    if ($process_info | is-empty) {
        print $"No process found using port ($port_number)"
        return
    }

    print $process_info

    if $kill {
        let pid = if (is-windows) {
            extract-pid ($process_info | lines | first) --windows
        } else if (is-available "lsof") {
            extract-pid ($process_info | lines | skip 1 | first)
        } else {
            extract-pid ($process_info | lines | first) --procnet
        }
        # netstat -tlnp shows "-" (not our process, no root); PID 0 would signal the
        # whole process group. is-valid-pid rejects both (same guard kill-process uses).
        if not (is-valid-pid $pid) {
            log-error $"Could not parse a killable PID for port ($port_number)"
            return
        }

        print $"Killing process ($pid) on port ($port_number)..."
        kill-process $pid
    }
}

# Clip - Clipboard operations
# Usage:
#   cat file.txt | clip          # Copy file contents to clipboard
#   clip                         # Paste from clipboard
#   echo "text" | clip           # Copy text to clipboard
export def clip [] {
    let input = $in
    if (is-windows) {
        # Windows: use clip.exe for copying, Get-Clipboard for pasting
        if ($input | is-empty) {
            # Paste mode
            ^powershell -command "Get-Clipboard"
        } else {
            # Copy mode
            $input | ^clip.exe
            print "Copied to clipboard"
        }
    } else if (is-macos) {
        # macOS: use pbcopy/pbpaste
        if ($input | is-empty) {
            # Paste mode
            ^pbpaste
        } else {
            # Copy mode
            $input | ^pbcopy
            print "Copied to clipboard"
        }
    } else {
        # Linux: use xclip or xsel
        let clip_tool = (find-available ["xclip" "xsel"])
        if ($clip_tool == null) {
            log-error "No clipboard tool found. Install xclip or xsel."
            return
        }

        if $clip_tool == "xclip" {
            if ($input | is-empty) {
                ^xclip -selection clipboard -o
            } else {
                $input | ^xclip -selection clipboard
                print "Copied to clipboard"
            }
        } else {
            if ($input | is-empty) {
                ^xsel --clipboard --output
            } else {
                $input | ^xsel --clipboard --input
                print "Copied to clipboard"
            }
        }
    }
}

# Pq-ensure - Start pueue daemon if not already running (internal helper)
def pq-ensure [] {
    if (is-available "pueued") {
        # `complete` captures the exit code without printing, and needs no /dev/null (absent on Windows).
        let status = (^pueue status | complete)
        if $status.exit_code != 0 {
            ^pueued -d
        }
    }
}

# Pq - Task queue manager (pueue wrapper)
# Usage:
#   pq status               # Show queue status
#   pq add -- sleep 10      # Add a task
#   pq follow 0             # Follow task output
export def pq [...args: string] {
    if not (is-available "pueue") {
        print "pueue not found. Install with: brew install pueue  or  cargo install pueue"
        return
    }
    pq-ensure
    ^pueue ...$args
}

# Trash - Safe delete (move to trash instead of permanent delete)
# Usage:
#   trash file.txt              # Move file to trash
#   trash *.log                 # Move all .log files to trash
export def trash [...files: string] {
    if ($files | is-empty) {
        print "Usage: trash <file1> [file2] [...]"
        return
    }

    for file in $files {
        if not ($file | path exists) {
            log-error $"($file) does not exist"
            continue
        }

        # Get absolute path and escape it for shell commands
        let abs_path = ($file | path expand)
        let escaped_path = (escape-shell-arg $abs_path)

        if (is-windows) {
            # Windows: use PowerShell to move to Recycle Bin
            # Escape single quotes for PowerShell by doubling them
            let ps_path = ($abs_path | str replace -a "'" "''")
            try {
                ^powershell -NoProfile -command $"Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile\('($ps_path)', 'OnlyErrorDialogs', 'SendToRecycleBin'\)"
                print $"Moved to Recycle Bin: ($file)"
            } catch {
                log-error $"Failed to move to Recycle Bin: ($file)"
            }
        } else if (is-macos) {
            # macOS: use trash command or osascript
            if (is-available "trash") {
                try {
                    ^trash $abs_path
                    print $"Moved to Trash: ($file)"
                } catch {
                    log-error $"Failed to move to Trash: ($file)"
                }
            } else {
                # Use AppleScript with properly escaped path
                try {
                    ^osascript -e $"tell application \"Finder\" to delete POSIX file \"($escaped_path)\""
                    print $"Moved to Trash: ($file)"
                } catch {
                    log-error $"Failed to move to Trash: ($file)"
                }
            }
        } else {
            # Linux: use trash-cli if available, otherwise move to ~/.local/share/Trash
            if (is-available "trash-put") {
                try {
                    ^trash-put $abs_path
                    print $"Moved to Trash: ($file)"
                } catch {
                    log-error $"Failed to move to Trash: ($file)"
                }
            } else {
                let trash_dir = (get-home-dir | path join ".local/share/Trash/files")
                mkdir $trash_dir
                mv $abs_path $trash_dir
                print $"Moved to Trash: ($file)"
            }
        }
    }
}
