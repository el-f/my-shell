# core/

The Python package behind the CLI. It reads the TOML config, renders shell-native files, and deploys
them to each shell's config directory.

| File | What it does |
|------|-------------|
| [`cli.py`](cli.py) | Typer entry point, one function per command |
| [`render.py`](render.py) | Reads `aliases.toml` and writes the shell-native alias files (`.nu` / `.xsh`) |
| [`merge.py`](merge.py) | Generates the full config files and deploys the three layers (base template, my-shell, user) |
| [`detect.py`](detect.py) | Reports OS, distro, package manager, current shell and installed tools |
| [`install.py`](install.py) | Installs nushell (package manager or GitHub release) and xonsh (uv or pip) |
| [`duplicates.py`](duplicates.py) | Finds and removes duplicate shell and tool installs left by other package managers |
| [`plugins.py`](plugins.py) | Installs and registers nushell plugins with `cargo` |
| [`config.py`](config.py) | Loads the TOML files and merges any `.local.toml` override |
| [`utils.py`](utils.py) | Shared helpers -- paths, logging (`log_step` / `log_success` / `log_error` / `log_debug`), shell string escaping |
| [`mise.py`](mise.py) | One place to build the environment and argv for calling `mise` |
| [`backup.py`](backup.py) | Timestamped config backups taken before a deploy; list and restore for `rollback` |
| [`benchmark.py`](benchmark.py) | Measures shell startup time (own timing loop, or hyperfine when installed) |
| [`doctor.py`](doctor.py) | Health checks -- shell binaries, tools, config hashes, integrations |
| [`dry_run.py`](dry_run.py) | Diffs what a deploy would write against what is deployed, without writing |
| [`fonts.py`](fonts.py) | Nerd Font detection and install (oh-my-posh, brew, GitHub release) |
| [`init_wizard.py`](init_wizard.py) | The guided `init` setup wizard |
| [`profiles.py`](profiles.py) | Config profiles that pick which integrations and command groups are on |
| [`registry.py`](registry.py) | One table of tool and integration metadata that the other modules read |
| [`uninstall.py`](uninstall.py) | Removes the deployed config files from the shell directories |
| [`validate.py`](validate.py) | Schema checks for `aliases.toml`, `settings.toml`, `plugins.toml` |
