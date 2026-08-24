# Common Utilities Module -- Nushell-native, not a port of core/utils.py.

# Check if a command/tool is available in PATH
export def is-available [tool: string] {
    which $tool | is-not-empty
}

# Find the first available tool from a list of candidates
export def find-available [tools: list<string>] {
    for tool in $tools {
        if (is-available $tool) {
            return $tool
        }
    }
    null
}

# Require a tool to be available, error if not
export def require-tool [tool: string, context?: string] {
    if not (is-available $tool) {
        let ctx = if ($context | is-empty) { "" } else { $" for ($context)" }
        error make { msg: $"Required tool '($tool)' is not installed($ctx). Please install it first." }
    }
}

# Require at least one tool from a list, return the first available
export def require-one-of [tools: list<string>, context?: string] {
    let available = (find-available $tools)
    if ($available == null) {
        let ctx = if ($context | is-empty) { "" } else { $" for ($context)" }
        let tool_list = ($tools | str join ", ")
        error make { msg: $"None of the required tools [($tool_list)] are installed($ctx). Please install one of them." }
    }
    $available
}

# Get the current operating system
export def get-os [] {
    # $nu.os-info is a parse-time constant -- no `sys host` syscall per call
    match $nu.os-info.name {
        "windows" => "windows"
        "macos" => "macos"
        _ => "linux"
    }
}

export def is-windows [] { (get-os) == "windows" }
export def is-macos [] { (get-os) == "macos" }
export def is-linux [] { (get-os) == "linux" }

export def get-home-dir [] {
    if (is-windows) {
        $env.USERPROFILE? | default ($env.HOME? | default "")
    } else {
        $env.HOME? | default ($env.USERPROFILE? | default "")
    }
}

export def log-info [msg: string] { print $"(ansi cyan)  [info] ($msg)(ansi reset)" }
export def log-success [msg: string] { print $"(ansi green)  [ok] ($msg)(ansi reset)" }
export def log-warn [msg: string] { print $"(ansi yellow)  [warn] ($msg)(ansi reset)" }
export def log-error [msg: string] { print $"(ansi red)  [fail] ($msg)(ansi reset)" }
export def log-step [msg: string] { print $"(ansi cyan_bold)($msg)(ansi reset)" }

export def escape-shell-arg [arg: string] {
    if (is-windows) {
        $arg | str replace -a "'" "''" | str replace -a "`" "``" | str replace -a "$" "`$"
    } else {
        $arg | str replace -a "'" "'\\'''"
    }
}

# Extract a PID from one process line. The FORMAT is the discriminator, not the
# OS: --csv for `tasklist /fo csv` (fk on Windows), --procs for `procs --tree`,
# --windows for `netstat -ano` whitespace (port --kill on Windows), --procnet for
# `netstat -tlnp` (port --kill on Linux, PID/program column), else `ps aux`.
export def extract-pid [line: string, --windows (-w), --procs (-p), --csv (-c), --procnet (-n)] {
    if $csv {
        # tasklist /fo csv: quoted fields, PID is column index 1.
        # from csv handles the comma inside "12,345 K" (Mem Usage).
        return ($line | from csv --noheaders | get 0.column1)
    }
    let parts = ($line | str trim | split row -r '\s+' | where {|x| $x != ""})
    if ($parts | is-empty) {
        error make { msg: "Could not extract PID from process line" }
    }
    if $procs {
        # procs --tree: PID is the first numeric token (tree chars aren't numeric)
        let numeric = ($parts | where {|x| ($x =~ '^\d+$') })
        if ($numeric | is-empty) { $parts | first } else { $numeric | first }
    } else if $procnet {
        # netstat -tlnp: last column is PID/program (e.g. "4321/python"), or "-"
        # for a process this user cannot see (no root). Take the part before "/".
        $parts | last | split row '/' | first
    } else if $windows {
        # netstat -ano: PID is the last numeric column
        let numeric = ($parts | where {|x| ($x =~ '^\d+$') })
        if ($numeric | is-empty) { $parts | last } else { $numeric | last }
    } else {
        # ps aux: `USER PID ...` -- PID is column 1, immune to a numeric UID in column 0
        if ($parts | length) > 1 { $parts | get 1 } else { $parts | first }
    }
}

# A killable PID: a positive integer. Rejects "0" (kill -9 0 signals the whole
# process group), "-" (netstat, not our process), and any non-numeric token.
export def is-valid-pid [pid: string] {
    $pid =~ '^[1-9]\d*$'
}

export def kill-process [pid: string] {
    # Self-guard the destructive command: every caller (fk, port) routes through
    # here, so validating once protects them all.
    if not (is-valid-pid $pid) {
        log-error $"Refusing to kill invalid PID: ($pid)"
        return
    }
    if (is-windows) {
        ^taskkill /PID $pid /F
    } else {
        ^kill -9 $pid
    }
}

# Terminal-aware box width: grows with the terminal, floored so short content
# still aligns and capped so boxes stay readable on very wide terminals.
export def box-width [minimum: int = 46, maximum: int = 80] {
    let cols = (try { term size | get columns } catch { 80 })
    let cols = if $cols > 0 { $cols } else { 80 }
    [$minimum ([($cols - 2) $maximum] | math min)] | math max
}

export def box-header [title: string, width: int = 44] {
    let label = $" ($title) "
    let pad_count = $width - ($label | ansi strip | split chars | length) - 3  # ╭─ prefix + ╮ suffix
    let pad = (0..<$pad_count | each {|| "─"} | str join)
    $"(ansi cyan)╭─($label)($pad)╮(ansi reset)"
}

export def box-row [label: string, value: string, width: int = 44] {
    let max_len = $width - 2
    let raw = $"  ($label | fill -w 14)($value)"
    let visible = ($raw | ansi strip)
    let content = if ($visible | split chars | length) > $max_len {
        # Truncate on visible chars so an ANSI escape is never sliced mid-sequence.
        $visible | split chars | first $max_len | str join
    } else {
        $raw
    }
    let pad_len = [($max_len - ($content | ansi strip | split chars | length)) 0] | math max
    let pad = ("" | fill -w $pad_len)
    $"(ansi cyan)│(ansi reset)($content)($pad)(ansi cyan)│(ansi reset)"
}

export def box-row-raw [content: string, width: int = 44] {
    let visual_len = $content | ansi strip | split chars | length
    let pad_len = [($width - $visual_len - 2) 0] | math max
    let pad = ("" | fill -w $pad_len)
    $"(ansi cyan)│(ansi reset)($content)($pad)(ansi cyan)│(ansi reset)"
}

export def box-footer [width: int = 44] {
    let inner = (0..<($width - 2) | each {|| "─"} | str join)
    $"(ansi cyan)╰($inner)╯(ansi reset)"
}
