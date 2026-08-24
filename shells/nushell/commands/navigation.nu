# Navigation Commands

use ../utils.nu *

# Folder Jump - Fuzzy find and jump to directories
# Usage:
#   fj              # Search all directories from root
#   fj ~/projects   # Search directories under ~/projects
#   fj .            # Search directories from current location
export def --env fj [dir?: string] {
    let fd_cmd = (require-one-of ["fd" "fdfind"] "fj command")
    require-tool "fzf" "fj command"

    let search_path = if ($dir | is-empty) {
        if (is-windows) {
            $env.USERPROFILE
        } else {
            "/"
        }
    } else {
        $dir
    }

    let selected = (^$fd_cmd --type d --hidden --exclude .git . $search_path | fzf)

    if not ($selected | is-empty) {
        cd ($selected | str trim)
    }
}

# Yazi - Terminal file manager with directory change support
# When you quit yazi (q), the shell will cd to the last directory you were in
# Usage:
#   y           # Open yazi in current directory
#   y ~/projects # Open yazi in ~/projects
export def --env y [...args] {
    require-tool "yazi" "y command"

    let tmp = if (is-windows) {
        # mktemp doesn't exist on Windows; use $env.TEMP
        let tmp_dir = ($env.TEMP? | default ($env.USERPROFILE | path join "AppData" "Local" "Temp"))
        let name = $"yazi-cwd-(random uuid)"
        $tmp_dir | path join $name
    } else {
        mktemp -t "yazi-cwd.XXXXX"
    }

    try {
        ^yazi ...$args --cwd-file $tmp
        let cwd = (open $tmp | str trim)
        if ($cwd != "" and $cwd != $env.PWD) {
            cd $cwd
        }
    } catch {
        |err|
        log-error $"Error running yazi: ($err.msg)"
    }

    if ($tmp | path exists) {
        rm $tmp
    }
}
