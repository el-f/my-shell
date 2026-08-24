# shells/nushell/

`uv run my-shell setup --shell nushell` writes `config.nu` and `env.nu` into your Nushell config
directory, built from [`config.nu.template`](config.nu.template) and
[`env.nu.template`](env.nu.template) -- change a default there. `aliases.nu` is rendered from
`config/aliases.toml` and git-ignored; [`commands/wrappers.nu`](commands/wrappers.nu) holds
`_run_wrapper`, which those generated aliases use for the fd/fdfind and bat/batcat fallbacks.

Tab completions come from [carapace](https://carapace.sh/). The generated `env.nu` sets
`CARAPACE_BRIDGES` to `zsh,fish,bash,inshellisense`, so carapace also reuses completions written for
those shells.

Deploy adds a `plugin use <name>` line to `config.nu` for each plugin in
[`config/plugins.toml`](../../config/plugins.toml) that is already built, so a plugin you have not
built yet does not break shell startup.

> [Nushell Configuration Docs](https://www.nushell.sh/book/configuration.html)
