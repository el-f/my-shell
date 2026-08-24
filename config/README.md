# config/

Four TOML files, read by the CLI and rendered into shell-native syntax. The root
[README](../README.md) covers what each one holds.

The font install is skipped when `MY_SHELL_SKIP_FONTS` is set, or on Linux with no `DISPLAY` or
`WAYLAND_DISPLAY`.

A profile can set `inherits = "<other profile>"` and override single keys. Profiles you add merge on
top of the built-in `minimal` and `full`. Applying one rewrites the `[integrations]` and
`[commands]` sections of `settings.local.toml` and leaves the rest of that file alone.
