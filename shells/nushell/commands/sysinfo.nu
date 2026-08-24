# System Information Command

use ../utils.nu *

# Show system information and my-shell setup overview
export def main [] {
    let width = (box-width)

    # ── System section ──
    let host = sys host
    let os_name = $host.name
    let os_version = ($host | get os_version? | default "")
    let hostname = ($host | get hostname? | default "unknown")
    # `sys host` has no arch column; $nu.os-info.arch is a guaranteed parse-time constant.
    let arch = $nu.os-info.arch

    let os_display = if ($os_version | is-empty) {
        $"($os_name) \(($arch)\)"
    } else {
        $"($os_name) ($os_version) \(($arch)\)"
    }

    let nu_version = (version).version
    let terminal = ($env | get TERM_PROGRAM? | default ($env | get WT_SESSION? | if ($in | is-not-empty) { "Windows Terminal" } else { "unknown" }))

    let pkg_candidates = if (is-windows) {
        ["winget" "scoop" "choco"]
    } else if (is-macos) {
        ["brew"]
    } else {
        ["apt" "dnf" "pacman"]
    }
    let pkg_mgr = ($pkg_candidates | where {|p| is-available $p } | str join ", " | if ($in | is-empty) { "none" } else { $in })

    print (box-header "System" $width)
    print (box-row "OS" $os_display $width)
    print (box-row "Host" $hostname $width)
    print (box-row "Shell" $"nushell ($nu_version)" $width)
    print (box-row "Terminal" $terminal $width)
    print (box-row "Package Mgr" $pkg_mgr $width)
    print (box-footer $width)
    print ""

    # ── my-shell section ──
    let version = ($env | get MY_SHELL_VERSION? | default "unknown")
    let project_dir = ($env | get MY_SHELL_DIR? | default "unknown")
    let prompt = if (is-available "oh-my-posh") { "oh-my-posh" } else if (is-available "starship") { "starship" } else { "default" }

    print (box-header "my-shell" $width)
    print (box-row "Version" $version $width)
    print (box-row "Project" $project_dir $width)
    print (box-row "Prompt" $prompt $width)
    print (box-footer $width)
    print ""

    # ── Tools section ──
    let tools = [
        "eza" "fzf" "fd" "rg" "bat" "zoxide" "yazi"
        "delta" "procs" "sd" "jq" "gron"
        "lefthook" "just" "atuin" "pueue"
        "dust" "duf" "lazygit" "tldr"
    ]

    let found = $tools | where {|t| is-available $t }
    let missing = $tools | where {|t| not (is-available $t) }

    print (box-header "Tools" $width)

    # Print found tools
    if ($found | is-not-empty) {
        let chunks = $found | chunks 6
        let first = $chunks | first
        let rest = $chunks | skip 1
        print (box-row-raw $"  (ansi green)✓(ansi reset) ($first | str join '  ')" $width)
        for chunk in $rest {
            print (box-row-raw $"    ($chunk | str join '  ')" $width)
        }
    }

    # Print missing tools
    if ($missing | is-not-empty) {
        let chunks = $missing | chunks 6
        let first = $chunks | first
        let rest = $chunks | skip 1
        print (box-row-raw $"  (ansi red)✗(ansi reset) ($first | str join '  ')" $width)
        for chunk in $rest {
            print (box-row-raw $"    ($chunk | str join '  ')" $width)
        }
    }

    print (box-footer $width)
    print ""

    # ── Plugins section ──
    let installed_plugins = try {
        plugin list | get name | each {|n| $n | str replace "nu_plugin_" "" }
    } catch {
        []
    }

    print (box-header "Plugins" $width)
    if ($installed_plugins | is-not-empty) {
        let chunks = $installed_plugins | chunks 6
        let first = $chunks | first
        let rest = $chunks | skip 1
        print (box-row-raw $"  (ansi green)✓(ansi reset) ($first | str join '  ')" $width)
        for chunk in $rest {
            print (box-row-raw $"    ($chunk | str join '  ')" $width)
        }
    } else {
        print (box-row-raw "  (none installed)" $width)
    }
    print (box-footer $width)
    print ""

    # ── Completions section ──
    let has_carapace = (which carapace | is-not-empty)
    print (box-header "Completions" $width)
    if $has_carapace {
        print (box-row-raw $"  (ansi green)✓(ansi reset) carapace \(1000+ tools\)" $width)
    } else {
        print (box-row-raw $"  (ansi red)✗(ansi reset) carapace \(install carapace-bin\)" $width)
    }
    print (box-footer $width)
}
