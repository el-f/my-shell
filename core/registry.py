"""Single source of truth for tool metadata.

Every tool that my-shell integrates with is registered here.
install.py, merge.py, and detect.py all reference this registry
instead of maintaining their own lists.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolInfo:
    """Metadata for an integration tool."""

    name: str
    display_name: str = ""
    # Binary name to check in PATH (defaults to name)
    binary: str = ""
    nushell_init_file: str = ""
    nushell_missing_status: str = "Not installed"
    setup_hint: str = ""
    shell_comment_label: str = "integration"
    # Xonsh integration directory (defaults to the tool name)
    xonsh_init_dir: str = ""
    install_commands: dict[str, list[str]] = field(default_factory=dict)
    # Known standalone install directories (relative to LOCALAPPDATA on Windows)
    standalone_dirs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.display_name:
            object.__setattr__(self, "display_name", self.name)
        if not self.binary:
            object.__setattr__(self, "binary", self.name)
        if not self.xonsh_init_dir:
            object.__setattr__(self, "xonsh_init_dir", self.name)

    @property
    def winget_id(self) -> str | None:
        """Extract the winget package ID from install_commands, if present."""
        cmds = self.install_commands.get("winget", [])
        if not cmds:
            return None
        # Prefer explicit --id flag
        try:
            idx = cmds.index("--id")
            return cmds[idx + 1]
        except ValueError, IndexError:
            pass
        # Fall back to last arg (positional package ID)
        last = cmds[-1]
        if "." in last and not last.startswith("-"):
            return last
        return None


# Packages that are part of the managed xonsh environment. Keeping them in
# the uv tool receipt prevents `uv tool upgrade xonsh` from pruning them.
XONTRIB_PACKAGES = [
    "xontrib-whole-word-jumping",
    "xontrib-abbrevs",
    "xontrib-bashisms",
    "xontrib-argcomplete",
    "xontrib-back2dir",
    "xontrib-output-search",
    "xontrib-vox",
    "xontrib-hist-navigator",
]
XONTRIB_PACKAGES_WINDOWS = ["xontrib-free-cwd"]


# ── Core integrations (pre-flight checked, init generated) ────────
INTEGRATION_TOOLS: dict[str, ToolInfo] = {
    "oh-my-posh": ToolInfo(
        name="oh-my-posh",
        display_name="Oh-My-Posh",
        nushell_init_file="oh-my-posh.nu",
        setup_hint="install oh-my-posh and re-run setup",
        install_commands={
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "JanDeDobbeleer.OhMyPosh",
            ],
            "homebrew": ["brew", "install", "oh-my-posh"],
            "mise": ["mise", "use", "-g", "aqua:JanDeDobbeleer/oh-my-posh"],
        },
        standalone_dirs=("Programs/oh-my-posh",),
    ),
    "zoxide": ToolInfo(
        name="zoxide",
        display_name="Zoxide",
        nushell_init_file="zoxide.nu",
        setup_hint="install zoxide and re-run setup",
        install_commands={
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "ajeetdsouza.zoxide",
            ],
            "homebrew": ["brew", "install", "zoxide"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "zoxide"],
            "cargo": ["cargo", "install", "zoxide", "--locked"],
            "mise": ["mise", "use", "-g", "zoxide"],
        },
    ),
    "atuin": ToolInfo(
        name="atuin",
        display_name="Atuin",
        nushell_init_file="atuin.nu",
        setup_hint="install atuin and re-run setup",
        install_commands={
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "Atuinsh.Atuin",
            ],
            "homebrew": ["brew", "install", "atuin"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "atuin"],
            "apt": [
                "bash",
                "-c",
                "set -o pipefail; curl --proto '=https' --tlsv1.2 -LsSf https://setup.atuin.sh | sh",
            ],
            "cargo": ["cargo", "install", "atuin"],
            "mise": ["mise", "use", "-g", "github:atuinsh/atuin"],
        },
    ),
    "carapace": ToolInfo(
        name="carapace",
        display_name="Carapace",
        nushell_init_file="carapace.nu",
        setup_hint="install carapace-bin for completions",
        shell_comment_label="completions",
        install_commands={
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "carapace-sh.carapace-bin",
            ],
            "homebrew": ["brew", "install", "carapace"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "carapace-bin"],
            "apt": [
                "bash",
                "-c",
                "set -o pipefail; curl --proto '=https' --tlsv1.2 -LsSf "
                "https://github.com/carapace-sh/carapace-bin/releases/latest/download/setup.sh | sh",
            ],
            "mise": ["mise", "use", "-g", "aqua:carapace-sh/carapace-bin"],
        },
    ),
    "mise": ToolInfo(
        name="mise",
        display_name="Mise",
        nushell_init_file="mise.nu",
        nushell_missing_status="Not activated",
        setup_hint="install mise and re-run setup",
        install_commands={
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "jdx.mise",
            ],
            "homebrew": ["brew", "install", "mise"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "mise"],
            "apt": [
                "bash",
                "-c",
                "set -o pipefail; curl --proto '=https' --tlsv1.2 -LsSf https://mise.run | sh",
            ],
        },
    ),
}

# ── Optional tools (checked in pre-flight and detect, managed via mise) ──
OPTIONAL_TOOLS: dict[str, ToolInfo] = {
    # eza publishes no macOS binary, so mise cannot serve it everywhere -- install per platform.
    "eza": ToolInfo(
        name="eza",
        install_commands={
            "homebrew": ["brew", "install", "eza"],
            "apt": ["sudo", "apt-get", "install", "-y", "eza"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "eza-community.eza",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "eza"],
            "cargo": ["cargo", "install", "eza"],
        },
    ),
    "fzf": ToolInfo(
        name="fzf",
        install_commands={
            "homebrew": ["brew", "install", "fzf"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "junegunn.fzf",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "fzf"],
            "mise": ["mise", "use", "-g", "fzf"],
        },
    ),
    "fd": ToolInfo(
        name="fd",
        install_commands={
            "homebrew": ["brew", "install", "fd"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "sharkdp.fd",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "fd"],
            "cargo": ["cargo", "install", "fd-find"],
            "mise": ["mise", "use", "-g", "fd"],
        },
    ),
    "bat": ToolInfo(
        name="bat",
        install_commands={
            "homebrew": ["brew", "install", "bat"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "sharkdp.bat",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "bat"],
            "cargo": ["cargo", "install", "bat"],
            "mise": ["mise", "use", "-g", "bat"],
        },
    ),
    "rg": ToolInfo(
        name="rg",
        binary="rg",
        install_commands={
            "homebrew": ["brew", "install", "ripgrep"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "BurntSushi.ripgrep.MSVC",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "ripgrep"],
            "cargo": ["cargo", "install", "ripgrep"],
            "mise": ["mise", "use", "-g", "ripgrep"],
        },
    ),
    "yazi": ToolInfo(
        name="yazi",
        install_commands={
            "homebrew": ["brew", "install", "yazi"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "sxyazi.yazi",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "yazi"],
            "cargo": ["cargo", "install", "yazi-fm", "yazi-cli"],
            "mise": ["mise", "use", "-g", "yazi"],
        },
    ),
    "delta": ToolInfo(
        name="delta",
        install_commands={
            "homebrew": ["brew", "install", "git-delta"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "dandavison.delta",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "git-delta"],
            "cargo": ["cargo", "install", "git-delta"],
            "mise": ["mise", "use", "-g", "delta"],
        },
    ),
    "procs": ToolInfo(
        name="procs",
        install_commands={
            "homebrew": ["brew", "install", "procs"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "dalance.procs",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "procs"],
            "cargo": ["cargo", "install", "procs"],
            "mise": ["mise", "use", "-g", "github:dalance/procs"],
        },
    ),
    "pueue": ToolInfo(
        name="pueue",
        install_commands={
            "homebrew": ["brew", "install", "pueue"],
            "cargo": ["cargo", "install", "pueue"],
            "mise": ["mise", "use", "-g", "github:Nukesor/pueue"],
        },
    ),
    "sd": ToolInfo(
        name="sd",
        install_commands={
            "homebrew": ["brew", "install", "sd"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "sd"],
            "cargo": ["cargo", "install", "sd"],
            "mise": ["mise", "use", "-g", "sd"],
        },
    ),
    "jq": ToolInfo(
        name="jq",
        install_commands={
            "homebrew": ["brew", "install", "jq"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "jq"],
            "mise": ["mise", "use", "-g", "jq"],
        },
    ),
    "gron": ToolInfo(
        name="gron",
        install_commands={
            "homebrew": ["brew", "install", "gron"],
            "mise": ["mise", "use", "-g", "gron"],
        },
    ),
    "lefthook": ToolInfo(
        name="lefthook",
        install_commands={
            "homebrew": ["brew", "install", "lefthook"],
            "mise": ["mise", "use", "-g", "lefthook"],
        },
    ),
    "just": ToolInfo(
        name="just",
        install_commands={
            "homebrew": ["brew", "install", "just"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "just"],
            "cargo": ["cargo", "install", "just"],
            "mise": ["mise", "use", "-g", "just"],
        },
    ),
    "dust": ToolInfo(
        name="dust",
        install_commands={
            "homebrew": ["brew", "install", "dust"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "dust"],
            "cargo": ["cargo", "install", "du-dust"],
            "mise": ["mise", "use", "-g", "dust"],
        },
    ),
    "duf": ToolInfo(
        name="duf",
        install_commands={
            "homebrew": ["brew", "install", "duf"],
            "pacman": ["pacman", "-Sy", "--noconfirm", "duf"],
            "mise": ["mise", "use", "-g", "duf"],
        },
    ),
    "lazygit": ToolInfo(
        name="lazygit",
        install_commands={
            "homebrew": ["brew", "install", "lazygit"],
            "winget": [
                "winget",
                "install",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "JesseDuffield.lazygit",
            ],
            "pacman": ["pacman", "-Sy", "--noconfirm", "lazygit"],
            "mise": ["mise", "use", "-g", "lazygit"],
        },
    ),
    "tldr": ToolInfo(
        name="tldr",
        install_commands={
            "homebrew": ["brew", "install", "tlrc"],
            "mise": ["mise", "use", "-g", "github:tldr-pages/tlrc"],
        },
    ),
}

TOOL_REGISTRY: dict[str, ToolInfo] = {**INTEGRATION_TOOLS, **OPTIONAL_TOOLS}

# ── Detection tools (shown in `my-shell detect`) ──
DETECT_TOOLS: list[str] = list(TOOL_REGISTRY)

# ── Installable tools (for `my-shell install-tools`) ──
INSTALLABLE_TOOLS: dict[str, dict[str, list[str]]] = {
    name: info.install_commands for name, info in TOOL_REGISTRY.items() if info.install_commands
}
