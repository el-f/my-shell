# .github/workflows/

One workflow, [`test.yml`](test.yml), on every push and pull request against `main`: lint, format,
typecheck and a dependency audit, then the pytest suite on Linux, Windows, macOS and in Docker, plus
the wheel and both install scripts.

Run a job locally with [`act`](https://github.com/nektos/act) before pushing:

```bash
act -j check
act -j test-linux
```
