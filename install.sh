#!/bin/sh
# my-shell installer for Unix systems
# Usage: curl -sSf https://raw.githubusercontent.com/el-f/my-shell/main/install.sh | sh
#
# Environment variables:
#   MY_SHELL_INSTALL_DIR  - Install directory (default: ~/.local/share/my-shell)
#   MY_SHELL_REPO         - Git repository URL (default: https://github.com/el-f/my-shell.git)
#   MY_SHELL_BRANCH       - Git branch to clone (default: main)

set -eu

# ── Colors ──────────────────────────────────────────────────────

if command -v tput >/dev/null 2>&1 && [ -t 1 ]; then
    GREEN=$(tput setaf 2 2>/dev/null) || GREEN=""
    CYAN=$(tput setaf 6 2>/dev/null) || CYAN=""
    YELLOW=$(tput setaf 3 2>/dev/null) || YELLOW=""
    RED=$(tput setaf 1 2>/dev/null) || RED=""
    BOLD=$(tput bold 2>/dev/null) || BOLD=""
    RESET=$(tput sgr0 2>/dev/null) || RESET=""
else
    GREEN=""
    CYAN=""
    YELLOW=""
    RED=""
    BOLD=""
    RESET=""
fi

cleanup() { printf '%s' "$RESET"; }
trap cleanup EXIT

info()  { printf '%s[info]%s %s\n' "$CYAN" "$RESET" "$1"; }
ok()    { printf '%s[ok]%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '%s[warn]%s %s\n' "$YELLOW" "$RESET" "$1"; }
fail()  { printf '%s[fail]%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# ── Validate environment ───────────────────────────────────────

[ -z "${HOME:-}" ] && fail "HOME is not set. Cannot determine install directory."

# ── Configuration ───────────────────────────────────────────────

INSTALL_DIR="${MY_SHELL_INSTALL_DIR:-$HOME/.local/share/my-shell}"
REPO="${MY_SHELL_REPO:-https://github.com/el-f/my-shell.git}"
BRANCH="${MY_SHELL_BRANCH:-main}"

# git runs a command for an `ext::` URL, and treats a leading `-` as an option.
case "$REPO" in
    https://*|ssh://*|git@*|file://*|/*) ;;
    *) fail "MY_SHELL_REPO must be an https, ssh or file:// URL, or an absolute path. Got: $REPO" ;;
esac

# ── Banner ──────────────────────────────────────────────────────

printf '\n%s%s' "$BOLD" "$CYAN"
cat << 'BANNER'
                          _          _ _
  _ __ ___  _   _    ___| |__   ___| | |
 | '_ ` _ \| | | |  / __| '_ \ / _ \ | |
 | | | | | | |_| |  \__ \ | | |  __/ | |
 |_| |_| |_|\__, |  |___/_| |_|\___|_|_|
             |___/
BANNER
printf '%s\n' "$RESET"

info "Installing my-shell..."
info "Install dir: $INSTALL_DIR"
info "Repository:  $REPO"
info "Branch:      $BRANCH"
echo

# ── Prerequisites ───────────────────────────────────────────────

# Check git
if ! command -v git >/dev/null 2>&1; then
    fail "git is required but not installed. Install git first."
fi
ok "git is available"

# The uv installer computes its checksum with awk; without it the check fails as a mismatch.
if ! command -v awk >/dev/null 2>&1; then
    fail "awk is required but not installed. Install gawk (or mawk) first."
fi

# Install uv if missing
if [ -f "$HOME/.local/bin/uv" ] || [ -f "$HOME/.cargo/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    info "uv not found -- installing..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh || fail "uv download/install failed. Install manually: https://docs.astral.sh/uv/"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh || fail "uv download/install failed. Install manually: https://docs.astral.sh/uv/"
    else
        fail "Neither curl nor wget available. Install uv manually: https://docs.astral.sh/uv/"
    fi

    # Source the uv env (it adds itself to PATH via shell profile)
    # shellcheck disable=SC1091
    if [ -f "$HOME/.cargo/env" ]; then . "$HOME/.cargo/env"; fi
    # shellcheck disable=SC1091
    if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if ! command -v uv >/dev/null 2>&1; then
        fail "uv installation failed. Install manually: https://docs.astral.sh/uv/"
    fi
    ok "uv installed successfully"
else
    ok "uv is available"
fi

# Install mise if missing
if ! command -v mise >/dev/null 2>&1; then
    info "mise not found -- installing..."
    if command -v curl >/dev/null 2>&1; then
        curl -sSf https://mise.run | sh || fail "mise download/install failed. Install manually: https://mise.jdx.dev/"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://mise.run | sh || fail "mise download/install failed. Install manually: https://mise.jdx.dev/"
    else
        fail "Neither curl nor wget available. Install mise manually: https://mise.jdx.dev/"
    fi

    export PATH="$HOME/.local/share/mise/bin:$HOME/.local/bin:$PATH"

    if ! command -v mise >/dev/null 2>&1; then
        fail "mise installation failed. Install manually: https://mise.jdx.dev/"
    fi
    ok "mise installed successfully"
else
    ok "mise is available"
fi

# ── Clone / Update ──────────────────────────────────────────────

update_ok=true
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Existing installation found -- updating..."
    if git -C "$INSTALL_DIR" pull --ff-only; then
        ok "Updated existing installation"
    else
        warn "git pull failed, continuing with existing version"
        update_ok=false
    fi
elif [ -d "$INSTALL_DIR" ]; then
    fail "$INSTALL_DIR exists but is not a git repository. Remove it or set MY_SHELL_INSTALL_DIR."
else
    info "Cloning my-shell..."
    mkdir -p "$(dirname "$INSTALL_DIR")" || fail "Cannot create install directory"
    git clone --depth 1 --branch "$BRANCH" -- "$REPO" "$INSTALL_DIR" || fail "git clone failed. Check your network connection and try again."
    ok "Cloned to $INSTALL_DIR"
fi

# ── Setup ───────────────────────────────────────────────────────

info "Running my-shell setup (this may take a few minutes on first install)..."
cd "$INSTALL_DIR"

if [ -n "${GITHUB_TOKEN:-}" ]; then
    export GITHUB_TOKEN
    info "GITHUB_TOKEN detected -- using authenticated GitHub API access"
fi

export MISE_YES=1
tools_ok=true
if ! mise install --locked; then
    warn "mise install had errors -- some optional tools are missing."
    warn "Re-run later: cd $INSTALL_DIR && mise install --locked"
    tools_ok=false
fi
mise setup || fail "mise setup failed. Read the error above, then re-run: cd $INSTALL_DIR && mise setup"

info "Verifying the deployed shell configuration..."
if ! uv run my-shell doctor; then
    fail "The deployed shell failed its health check. Fix the diagnostics above, then re-run: cd $INSTALL_DIR && mise setup"
fi

# ── Result ──────────────────────────────────────────────────────

echo
if [ "$tools_ok" = true ] && [ "$update_ok" = true ]; then
    printf '%s%s' "$BOLD" "$GREEN"
    cat << 'SUCCESS'
  ✔ my-shell installed successfully!
SUCCESS
    printf '%s\n' "$RESET"
else
    printf '%s%s' "$BOLD" "$YELLOW"
    cat << 'PARTIAL'
  ⚠ my-shell is deployed, but see the warnings above.
PARTIAL
    printf '%s\n' "$RESET"
    warn "Re-run: cd $INSTALL_DIR && mise install --locked && mise setup"
fi

info "Installed at: $INSTALL_DIR"
info "Next steps:"
info "  1. Start nushell:              nu"
info "  2. Start xonsh:                xonsh"
info "  3. Re-run the health check:    cd $INSTALL_DIR && uv run my-shell doctor"
echo
