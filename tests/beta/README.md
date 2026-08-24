# Beta testing harness

Runs scripted user sessions in Docker, then has Claude review the output as each persona.

## Prerequisites

- Docker running, and the dev dependencies installed (`docker`, `jinja2`, `pydantic`)
- Claude Code CLI on PATH -- the judge runs through `claude -p`, so it uses the subscription, not an API key
- `GITHUB_TOKEN` or `GH_TOKEN` exported. It is passed into the containers so `mise install` does not
  hit GitHub's unauthenticated rate limit.
- For Legolas (ARM64): `docker run --privileged --rm tonistiigi/binfmt --install arm64`

## Running

```bash
python -m tests.beta                     # all personas
python -m tests.beta --persona frodo     # one persona
python -m tests.beta --dry-run           # run the commands, skip the judge
python -m tests.beta --help              # the rest of the flags
```

`--model` is passed straight to `claude -p --model`, so it takes `sonnet` (the default), `opus`,
`haiku` or a full model ID.

## Personas

Named after the Fellowship, plus Bilbo. Each persona's stories live in `stories/<key>.toml`.

| Name     | Platform             | Experience   | Shells  | Key trait                                 |
|----------|----------------------|--------------|---------|-------------------------------------------|
| Frodo    | Ubuntu 24.04         | Beginner     | Nushell | Greenfield first install, follows README  |
| Merry    | Fedora 41            | Intermediate | Both    | RPM-based distro, startup time            |
| Pippin   | openSUSE Tumbleweed  | Advanced     | Nushell | Rolling release, zypper, lifecycle        |
| Sam      | Arch Linux           | Expert       | Both    | DevOps, Docker/k8s aliases, cargo plugins |
| Bilbo    | Ubuntu 22.04         | Intermediate | xonsh   | Data scientist, Python focus              |
| Gimli    | Debian 12 minimal    | Advanced     | Nushell | Headless server, wget-only, minimal       |
| Aragorn  | Ubuntu 22.04         | Intermediate | Nushell | Corporate proxy, cautious, dry-run first  |
| Legolas  | Debian ARM64         | Intermediate | Nushell | aarch64 detection, QEMU emulation         |
| Boromir  | Ubuntu 24.04         | Advanced     | Both    | Config tinkerer, local overrides          |
| Gandalf  | All platforms        | Expert       | Both    | CI automation, exit codes, parseable output |

## Reports

Written to `reports/` (git-ignored): `<name>_report.json`, `<name>_report.md`, and one
`beta_summary.md` across all personas. `--dry-run` writes `<name>_execution.json` (raw command
output) and `<name>_prompt.md`, which can be piped to `claude -p` by hand.
