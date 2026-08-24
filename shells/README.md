# shells/

Nushell and xonsh get the same aliases, commands and integrations, rendered from the TOML in
[`config/`](../config/) into each shell's own language. `shared/` holds what both read (oh-my-posh
themes).

Nushell's integration init files are generated at deploy time. The xonsh ones in
`xonsh/integrations/` are written by hand and resolve their paths at startup.
