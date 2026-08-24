#!/usr/bin/env bash
# Post-create setup for my-shell development and beta testing in Codespaces.
# Runs once after the Codespace is created.
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[ok]${NC}   $1"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }

echo "=== my-shell Codespace setup ==="
echo ""

# --- 1. Install mise ---
if command -v mise &>/dev/null; then
    ok "mise already installed ($(mise --version))"
else
    echo "Installing mise..."
    if curl -fsSL https://mise.run | sh; then
        # shellcheck disable=SC2016
        echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc
        export PATH="$HOME/.local/bin:$PATH"
        eval "$(~/.local/bin/mise activate bash)"
        ok "mise installed ($(mise --version))"
    else
        fail "mise installation failed"
        echo "  Try manually: curl https://mise.run | sh"
        exit 1
    fi
fi

# --- 2. mise install (tools from mise.toml) ---
echo "Installing tools via mise (Python 3.14, uv, fzf, ripgrep, bat, etc.)..."
if mise install; then
    ok "mise tools installed"
else
    warn "Some mise tools failed to install -- this is usually fine"
    echo "  Run 'mise install' again later to retry"
fi

# --- 3. uv sync (FATAL -- nothing works without Python deps) ---
echo "Installing Python dependencies..."
if mise exec -- uv sync --frozen --group dev; then
    ok "Python dependencies installed"
else
    fail "uv sync failed -- cannot continue"
    echo "  Try manually: uv sync --frozen --group dev"
    exit 1
fi

# --- 4. Install Claude Code CLI (the beta harness judge shells out to it) ---
echo "Installing Claude Code CLI..."
if npm install -g @anthropic-ai/claude-code; then
    ok "Claude Code CLI installed ($(claude --version 2>/dev/null || echo 'unknown version'))"
else
    warn "Claude Code CLI installation failed"
    echo "  Install manually: npm install -g @anthropic-ai/claude-code"
fi

# --- 5. lefthook install (git hooks) ---
echo "Setting up git hooks..."
if command -v lefthook &>/dev/null && lefthook install; then
    ok "lefthook git hooks installed"
else
    warn "lefthook setup skipped (install mise tools first)"
fi

# --- 6. QEMU binfmt for ARM64 cross-builds (Legolas persona) ---
echo "Registering QEMU binfmt for ARM64 cross-builds..."
if docker run --privileged --rm tonistiigi/binfmt --install arm64 2>/dev/null; then
    ok "ARM64 emulation registered"
else
    warn "QEMU binfmt registration failed (ARM64 cross-builds won't work)"
    echo "  Retry: docker run --privileged --rm tonistiigi/binfmt --install arm64"
fi

# --- Status summary ---
echo ""
echo "=== Setup complete ==="
echo ""

# Check what's ready
if command -v mise &>/dev/null; then ok "mise"; else warn "mise"; fi
if mise exec -- python --version &>/dev/null; then ok "Python $(mise exec -- python --version 2>&1 | head -1)"; else warn "Python"; fi
if mise exec -- uv --version &>/dev/null; then ok "uv $(mise exec -- uv --version 2>&1)"; else warn "uv"; fi
if command -v docker &>/dev/null && docker info &>/dev/null; then ok "Docker"; else warn "Docker"; fi

if [ -n "${GITHUB_TOKEN:-}" ] || [ -n "${GH_TOKEN:-}" ]; then
    ok "GITHUB_TOKEN available"
else
    warn "GITHUB_TOKEN not set -- mise may hit GitHub API rate limits"
fi

echo ""
echo "Quick start:"
echo "  mise test     # run the test suite"
echo "  mise lint     # ruff check"
echo "  mise check    # lint + format + typecheck"
echo "  mise setup    # render config and deploy it in this container"
echo ""
