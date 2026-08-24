# Custom Commands & Aliases Overview

use ../utils.nu *

# Format a section name for display (e.g., "modern_replacements" -> "Modern Replacements")
def format-section-name [name: string] {
    $name | str replace -a "_" " " | split words | each {|w| ($w | str capitalize)} | str join " "
}

# Show all available my-shell custom commands and aliases
export def main [] {
    let width = (box-width)

    let aliases_path = ([$env.MY_SHELL_DIR "config" "aliases.toml"] | path join)
    let aliases_toml = if ($aliases_path | path exists) { open $aliases_path } else { {} }

    # Match modules by their .../nushell/commands/ path tail -- separator, symlink and drive agnostic.
    let my_command_names = (
        scope modules
        | where {|m|
            let tail = ($m.file | path split | last 3)
            ($tail | length) == 3 and ($tail | get 0) == "nushell" and ($tail | get 1) == "commands" and $m.name != "wrappers"
        }
        | each {|m| $m.commands.name | each {|cn| if $cn == "main" { $m.name } else { $cn } } }
        | flatten
        | uniq
    )
    let custom_cmds = (
        scope commands
        | where {|c| $c.name in $my_command_names }
        | select name description
        | sort-by name
    )

    print (box-header "Custom Commands" $width)
    for cmd in $custom_cmds {
        let desc = ($cmd.description | lines | where {|l| ($l | str trim) != "" } | first | default "")
        print (box-row $"(ansi green)($cmd.name)(ansi reset)" $desc $width)
    }
    print (box-footer $width)
    print ""

    # ── Aliases section (from config/aliases.toml) ──
    if ($aliases_toml | is-empty) {
        print (box-header "Aliases" $width)
        print (box-row-raw $"  (ansi yellow)aliases.toml not found(ansi reset)" $width)
        print (box-footer $width)
        return
    }

    # Get all top-level sections (skip 'wrappers' as it has a different structure)
    let sections = $aliases_toml | columns | where {|s| $s != "wrappers" }

    for section in $sections {
        let section_data = $aliases_toml | get $section
        let section_title = (format-section-name $section)

        print (box-header $"Aliases: ($section_title)" $width)

        let keys = $section_data | columns

        for key in $keys {
            let val = $section_data | get $key
            let display = if ($val | describe | str starts-with "record") {
                # Record/dict value -- prefer 'command', fall back to 'nushell', then first value
                let cmd = if ("command" in ($val | columns)) {
                    $val.command
                } else if ("nushell" in ($val | columns)) {
                    $val.nushell
                } else {
                    $val | values | first
                }
                let comment = if ("comment" in ($val | columns)) {
                    $" (ansi dark_gray)\(($val.comment)\)(ansi reset)"
                } else {
                    ""
                }
                $"($cmd)($comment)"
            } else {
                # Simple string value
                ($val | into string)
            }

            print (box-row $"(ansi yellow)($key)(ansi reset)" $display $width)
        }

        print (box-footer $width)
        print ""
    }

    # ── Wrappers section ──
    if "wrappers" in ($aliases_toml | columns) {
        let wrappers = $aliases_toml | get wrappers
        print (box-header "Tool Wrappers" $width)
        for wrapper_name in ($wrappers | columns) {
            let w = $wrappers | get $wrapper_name
            let preferred = ($w | get preferred? | default "?")
            let fallback = ($w | get fallback? | default "?")
            print (box-row $"(ansi green)($wrapper_name)(ansi reset)" $"($preferred) → ($fallback)" $width)
        }
        print (box-footer $width)
    }
}
