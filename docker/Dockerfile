# syntax=docker/dockerfile:1
FROM ubuntu:24.04 AS base

# System deps
RUN apt-get update && apt-get install -y \
    curl git python3 python3-pip python3-venv eza \
    xclip trash-cli pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# ubuntu:24.04 already ships an unprivileged `ubuntu` user (uid 1000)
USER ubuntu
ENV PATH="/home/ubuntu/.local/bin:${PATH}"

# Pinned: a stale unpinned mise resolved the freethreaded cpython asset from
# python-build-standalone 20260728 and failed ("missing a lib directory")
RUN curl -fsSL https://mise.run | MISE_VERSION="v2026.7.15" sh

# Copy project
COPY --chown=ubuntu:ubuntu . /home/ubuntu/my-shell
WORKDIR /home/ubuntu/my-shell
ENV MY_SHELL_DIR="/home/ubuntu/my-shell"

# Fix Windows line endings on shell scripts (autocrlf=true on Windows host)
RUN find . -name "*.sh" -exec sed -i 's/\r$//' {} +

# No .git here: lefthook install and _get_version both tolerate its absence.

# GITHUB_TOKEN raises the API limit used by release-attestation verification.
ENV MISE_TRUSTED_CONFIG_PATHS="/home/ubuntu/my-shell"
RUN --mount=type=secret,id=github_token,env=GITHUB_TOKEN \
    mise install --locked \
    && mise exec -- uv sync --frozen --group dev \
    && mise exec -- uv run my-shell install --shell xonsh

# The Docker e2e service is the one CI path that exercises real cargo plugin builds.
RUN mise use -g rust@latest

# ── deployed stage: unit tests + full deploy ──────────────────────
FROM base AS deployed

# Run unit tests (build-time validation, exclude e2e -- those run in the e2e service)
RUN mise exec -- uv run pytest -v -m "not e2e"

# Deploy both shells
RUN --mount=type=secret,id=github_token,env=GITHUB_TOKEN \
    mise exec -- uv run my-shell setup --shell nushell \
    && mise exec -- uv run my-shell setup --shell xonsh

# Default: interactive xonsh
CMD ["mise", "exec", "--", "xonsh"]
