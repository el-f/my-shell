# shells/xonsh/

`uv run my-shell setup --shell xonsh` writes `~/.xonshrc` in your home directory, built from
[`xonshrc_base.xsh`](xonshrc_base.xsh) -- change a default there. `user-custom.xsh` goes in the
xonsh config directory. `aliases.xsh` is rendered from `config/aliases.toml` and git-ignored;
[`commands/alias_fns.py`](commands/alias_fns.py) holds the Python functions those aliases use for
`clear`, `meminfo`, `cpuinfo` and the fd/bat fallbacks.

A custom command is any module-level `def _name(args, stdin=None)` in
[`commands/`](commands/). The generated `~/.xonshrc` imports each module and puts those functions in
xonsh's `aliases` dict; `commands` finds them by inspection, so a new one needs no list edit.

## Xontribs

Deploy installs these into xonsh's own Python environment and `~/.xonshrc` loads them:

| Xontrib | What it adds |
|---------|-------------|
| `whole_word_jumping` | Ctrl+Left/Right jumps by word in the prompt |
| `abbrevs` | Fish-like command abbreviations that expand on space |
| `bashisms` | Support for common bash syntax (`!!`, `$()`, etc.) |
| `argcomplete` | Tab completions for Python tools using argcomplete |
| `back2dir` | Return to the last directory on shell startup |
| `output_search` | Search and select from previous command output |
| `vox` | Python virtualenv management (create, activate, deactivate) |
| `hist_navigator` | Walk command history with up/down arrows, matching the prefix you typed |
| `free_cwd` | *Windows only* -- release the CWD lock so the directory can be deleted or renamed |

> [Xonsh Tutorial](https://xon.sh/tutorial.html) · [Xonsh Environment Variables](https://xon.sh/envvars.html)
