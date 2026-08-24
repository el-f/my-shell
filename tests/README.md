# tests/

```bash
uv run pytest -v                         # everything
uv run pytest -m "not e2e" --tb=short    # skip the tests that launch real shells
uv run pytest -m e2e                     # only those (needs nushell / xonsh installed)
```

Markers: `e2e` launches a real shell interpreter, `integration` simulates a filesystem without one,
`slow` compiles a nushell plugin with cargo. Tests that need a shell binary skip when it is missing.

CI runs everything except `slow` on Linux, Windows and macOS; `slow` runs only in the Docker e2e job.
Coverage is gated at 95% in the `test-linux` job alone. `mise mutation` runs mutmut over the
`core/*.py` files that changed against `origin/main` -- Linux, macOS or WSL, mutmut has no Windows build.

The Docker persona harness lives in [`beta/`](beta/README.md).
