# my-shell installer for Windows
# Usage: irm https://raw.githubusercontent.com/el-f/my-shell/main/install.ps1 | iex
#
# Environment variables:
#   MY_SHELL_INSTALL_DIR  - Install directory (default: $env:LOCALAPPDATA\my-shell)
#   MY_SHELL_REPO         - Git repository URL (default: https://github.com/el-f/my-shell.git)
#   MY_SHELL_BRANCH       - Git branch to clone (default: main)

#Requires -Version 5

# Runtime version check (piped iex ignores #Requires)
if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "PowerShell 5+ required. Current: $($PSVersionTable.PSVersion)"
}

$ErrorActionPreference = "Continue"

# ── Colors ──────────────────────────────────────────────────────

function Write-Info  { param([string]$msg) Write-Host "  [info] $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "  [ok] $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "  [warn] $msg" -ForegroundColor Yellow }
# throw, not exit: `irm ... | iex` runs in the caller's runspace, where exit closes the session.
function Write-Fail  { param([string]$msg) Write-Host "  [fail] $msg" -ForegroundColor Red; throw $msg }

# ── Configuration ───────────────────────────────────────────────

if (-not $env:LOCALAPPDATA) {
    Write-Fail "LOCALAPPDATA is not set. Cannot determine install directory."
}

$InstallDir = if ($env:MY_SHELL_INSTALL_DIR) { $env:MY_SHELL_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "my-shell" }
$Repo = if ($env:MY_SHELL_REPO) { $env:MY_SHELL_REPO } else { "https://github.com/el-f/my-shell.git" }
$Branch = if ($env:MY_SHELL_BRANCH) { $env:MY_SHELL_BRANCH } else { "main" }

# git runs a command for an `ext::` URL, and treats a leading `-` as an option.
if ($Repo -notmatch '^(https://|ssh://|git@|file://|/|[A-Za-z]:[\\/])') {
    Write-Fail "MY_SHELL_REPO must be an https, ssh or file:// URL, or an absolute path. Got: $Repo"
}

# ── TLS ─────────────────────────────────────────────────────────

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ── Banner ──────────────────────────────────────────────────────

Write-Host ""
Write-Host @'
                          _          _ _
  _ __ ___  _   _    ___| |__   ___| | |
 | '_ ` _ \| | | |  / __| '_ \ / _ \ | |
 | | | | | | |_| |  \__ \ | | |  __/ | |
 |_| |_| |_|\__, |  |___/_| |_|\___|_|_|
             |___/
'@ -ForegroundColor Cyan
Write-Host ""

Write-Info "Installing my-shell..."
Write-Info "Install dir: $InstallDir"
Write-Info "Repository:  $Repo"
Write-Info "Branch:      $Branch"
Write-Host ""

# ── Prerequisites ───────────────────────────────────────────────

# Check git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Fail "git is required but not installed. Install git from https://git-scm.com/"
}
Write-Ok "git is available"

# Install uv if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Info "uv not found -- installing..."
    $ErrorActionPreference = "Stop"
    try {
        & ([scriptblock]::Create((Invoke-RestMethod https://astral.sh/uv/install.ps1)))
    } catch {
        Write-Fail "uv installation failed: $_`nInstall manually: https://docs.astral.sh/uv/"
    }
    $ErrorActionPreference = "Continue"

    # Refresh PATH
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath) { $env:PATH = "$userPath;$env:PATH" }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Fail "uv installation succeeded but uv not in PATH. Restart your terminal and try again."
    }
    Write-Ok "uv installed successfully"
} else {
    Write-Ok "uv is available"
}

# Install mise if missing
if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    Write-Info "mise not found -- installing..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install jdx.mise --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "mise installation via winget failed (exit code $LASTEXITCODE). Install manually: https://mise.jdx.dev/"
        }
    } else {
        Write-Info "winget not available, using mise installer..."
        $ErrorActionPreference = "Stop"
        try {
            & ([scriptblock]::Create((Invoke-RestMethod https://mise.run/install.ps1)))
        } catch {
            Write-Fail "mise installation failed: $_`nInstall manually: https://mise.jdx.dev/"
        }
        $ErrorActionPreference = "Continue"
    }

    # Refresh PATH from registry
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath) { $env:PATH = "$userPath;$env:PATH" }

    if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
        Write-Fail "mise installation succeeded but mise not in PATH. Restart your terminal and try again."
    }
    Write-Ok "mise installed successfully"
} else {
    Write-Ok "mise is available"
}

# ── Clone / Update ──────────────────────────────────────────────

$toolsOk = $true
$updateOk = $true

if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "Existing installation found -- updating..."
    git -C $InstallDir pull --ff-only
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Updated existing installation"
    } else {
        Write-Warn "git pull failed, continuing with existing version"
        $updateOk = $false
    }
} elseif (Test-Path $InstallDir) {
    Write-Fail "$InstallDir exists but is not a git repository. Remove it or set MY_SHELL_INSTALL_DIR."
} else {
    Write-Info "Cloning my-shell..."
    $ParentDir = Split-Path $InstallDir -Parent
    if (-not (Test-Path $ParentDir)) {
        New-Item -ItemType Directory -Path $ParentDir -Force | Out-Null
    }
    git clone --depth 1 --branch $Branch -- $Repo $InstallDir
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git clone failed. Check your network connection and try again."
    }
    Write-Ok "Cloned to $InstallDir"
}

# ── Setup ───────────────────────────────────────────────────────

Write-Info "Running my-shell setup (this may take a few minutes on first install)..."
Push-Location $InstallDir
try {
    $env:MISE_YES = "1"

    mise install --locked
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "mise install had errors (exit code $LASTEXITCODE) -- some optional tools are missing."
        Write-Warn "Re-run later: cd $InstallDir; mise install --locked"
        $toolsOk = $false
    }

    mise setup
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "mise setup failed (exit code $LASTEXITCODE). Read the error above, then re-run: cd $InstallDir; mise setup"
    }

    # winget (run by mise setup) updates the registry, not this process's $env:PATH
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath) { $env:PATH = "$userPath;$env:PATH" }

    Write-Info "Verifying the deployed shell configuration..."
    uv run my-shell doctor
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "The deployed shell failed its health check. Fix the diagnostics above, then re-run: cd $InstallDir; mise setup"
    }
} finally {
    Pop-Location
}

# ── Result ──────────────────────────────────────────────────────

Write-Host ""
if ($toolsOk -and $updateOk) {
    Write-Host "  [ok] my-shell installed successfully!" -ForegroundColor Green
} else {
    Write-Warn "my-shell is deployed, but see the warnings above."
}
Write-Host ""
Write-Info "Installed at: $InstallDir"
Write-Info "Next steps:"
Write-Info "  1. Start nushell:            nu"
Write-Info "  2. Start xonsh:              xonsh"
Write-Info "  3. Re-run the health check:  cd $InstallDir; uv run my-shell doctor"
Write-Host ""
