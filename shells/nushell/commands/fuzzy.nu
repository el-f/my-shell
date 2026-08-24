# Fuzzy Commands

use ../utils.nu *

# Fuzzy Exec - Fuzzy find and execute commands from PATH
# Lists all executables in your PATH and lets you select one to run
# Usage:
#   fx          # Select a command to run
export def fx [] {
    require-tool "fzf" "fx command"

    let commands = (
        $env.PATH
        | split row (char esep)
        | each {|path|
            if ($path | path exists) {
                try {
                    ls $path
                    | where type == file
                    | get name
                    | path basename
                } catch {
                    []
                }
            } else {
                []
            }
        }
        | flatten
        | uniq
        | sort
    )

    # `which` is not a command in the Windows cmd shell that fzf spawns for
    # the preview; use `where` there so the preview pane doesn't just error.
    let preview = if (is-windows) { 'where {}' } else { 'which {}' }
    let selected = ($commands | str join (char newline) | fzf --preview $preview)

    if not ($selected | is-empty) {
        print $"Running: ($selected)"
        ^$selected
    }
}

# Fuzzy History - Search shell history interactively
# If atuin is installed, delegates to atuin's interactive search.
# Otherwise falls back to fzf over local history.
# Usage:
#   fh          # Search and execute a command from history
export def fh [] {
    if (is-available "atuin") {
        ^atuin search --interactive
        return
    }

    require-tool "fzf" "fh command"

    let selected = (
        history
        | get command
        | reverse
        | uniq
        | str join (char newline)
        | fzf --preview 'echo {}'
    )

    if not ($selected | is-empty) {
        print $"Command: ($selected)"
        let confirm = (input "Execute? [y/N] ")
        if $confirm == "y" or $confirm == "Y" {
            nu -c $selected
        }
    }
}

# Fuzzy Kill - Fuzzy find and kill processes
# Usage:
#   fk          # Select a process to kill
export def fk [] {
    require-tool "fzf" "fk command"

    let used_procs = (is-available "procs")
    let processes = (
        if $used_procs {
            # Use procs if available for better formatting
            ^procs --tree
        } else if (is-windows) {
            # Windows: CSV keeps the PID in a fixed column. Plain tasklist is
            # space-aligned, so a split can't tell PID from Session#/Mem Usage.
            ^tasklist /fo csv
        } else {
            ^ps aux
        }
    )

    # procs --tree prints a 2-line header; tasklist/ps print 1. Skip the right count
    # so a header row is never selectable (it would parse to a non-PID).
    let header_lines = if $used_procs { 2 } else { 1 }
    let selected = ($processes | fzf --header-lines $header_lines --preview 'echo {}')

    if not ($selected | is-empty) {
        # Pass the flag matching the producer that ran, so procs on Windows uses
        # the procs parser (not the tasklist CSV one).
        let pid = if $used_procs {
            extract-pid $selected --procs
        } else if (is-windows) {
            extract-pid $selected --csv
        } else {
            extract-pid $selected
        }
        if (is-valid-pid $pid) {
            print $"Killing process ($pid)..."
            kill-process $pid
        } else {
            log-error $"Could not parse a killable PID from: ($selected)"
        }
    }
}
