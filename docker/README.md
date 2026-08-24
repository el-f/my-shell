# docker/

Three Dockerfiles, all on `ubuntu:24.04`.

- [`Dockerfile`](Dockerfile) builds the project, installs both shells, runs the unit tests, and
  deploys the configs. It runs as the unprivileged `ubuntu` user from the base image; the project
  lives at `/home/ubuntu/my-shell`.
- [`Dockerfile.install-test`](Dockerfile.install-test) runs `install.sh` end to end. It first builds
  a git repo out of the build context and points `MY_SHELL_REPO` at it, so the installer clones from
  there instead of GitHub. Every check is a `RUN` step, so a failed check fails the build.
- [`Dockerfile.screenshots`](Dockerfile.screenshots) renders the screenshots used by the root
  README.

All three take `GITHUB_TOKEN` as a build secret. Pass one for reliable builds: mise verifies some
GitHub release artifacts through the API, whose unauthenticated rate limit is easy to exhaust.

## Usage

```bash
# smoke tests (this is what CI runs)
docker compose -f docker/docker-compose.yml run --rm smoke

# pytest in the container
docker compose -f docker/docker-compose.yml run --rm test

# full e2e suite, including the real cargo plugin build
docker compose -f docker/docker-compose.yml run --rm e2e

# installer paths
docker compose -f docker/docker-compose.yml build install-test
docker compose -f docker/docker-compose.yml build install-test-wget

# a shell in the container, with the config already deployed
docker compose -f docker/docker-compose.yml run --rm nushell
docker compose -f docker/docker-compose.yml run --rm xonsh
```
