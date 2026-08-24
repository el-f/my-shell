"""Tests for the tool registry -- single source of truth for tool metadata."""

from core.registry import (
    DETECT_TOOLS,
    INSTALLABLE_TOOLS,
    INTEGRATION_TOOLS,
    OPTIONAL_TOOLS,
    ToolInfo,
)


def test_integration_tools_have_names():
    """All integration tools should have valid names."""
    for name, info in INTEGRATION_TOOLS.items():
        assert info.name == name


def test_installable_tools_are_registered():
    """Every installable tool must be in INTEGRATION_TOOLS or OPTIONAL_TOOLS."""
    all_tools = {**INTEGRATION_TOOLS, **OPTIONAL_TOOLS}
    for name in INSTALLABLE_TOOLS:
        assert name in all_tools, f"{name} is installable but not registered"


def test_detect_tools_are_derived_from_registry():
    assert [*INTEGRATION_TOOLS, *OPTIONAL_TOOLS] == DETECT_TOOLS


def test_installable_tools_have_install_commands():
    """All installable tools should have at least one install method."""
    for name, commands in INSTALLABLE_TOOLS.items():
        assert len(commands) >= 1, f"{name} has no install commands"


def test_detect_extras_are_installable_optional_tools():
    """Aliased/user CLI tools are optional, installable, and doctor-covered."""
    for tool in ("sd", "jq", "gron", "lefthook", "just", "dust", "duf", "lazygit", "tldr"):
        assert tool in OPTIONAL_TOOLS, f"{tool} missing from OPTIONAL_TOOLS"
        assert tool in INSTALLABLE_TOOLS, f"{tool} missing from INSTALLABLE_TOOLS"


def test_all_tools_have_mise_command_when_upstream_has_a_cross_platform_asset():
    """eza has no macOS release asset; mise itself is the installer bootstrap."""
    for name, info in {**INTEGRATION_TOOLS, **OPTIONAL_TOOLS}.items():
        if name in {"mise", "eza"}:
            continue
        assert info.install_commands.get("mise"), f"{name} has no mise install command"


def test_eza_has_native_installers_for_supported_binaryless_platforms():
    commands = OPTIONAL_TOOLS["eza"].install_commands
    assert commands["homebrew"] == ["brew", "install", "eza"]
    assert commands["apt"] == ["sudo", "apt-get", "install", "-y", "eza"]


def test_arm64_capable_github_backends_are_used_for_procs_and_tldr():
    assert OPTIONAL_TOOLS["procs"].install_commands["mise"][-1] == "github:dalance/procs"
    assert OPTIONAL_TOOLS["tldr"].install_commands["mise"][-1] == "github:tldr-pages/tlrc"


class TestWingetId:
    def test_winget_id_with_id_flag(self):
        """Extracts winget ID from --id flag."""
        tool = ToolInfo(
            name="test",
            install_commands={"winget": ["winget", "install", "--id", "Foo.Bar"]},
        )
        assert tool.winget_id == "Foo.Bar"

    def test_winget_id_positional(self):
        """Falls back to last positional arg with a dot."""
        tool = ToolInfo(
            name="test",
            install_commands={"winget": ["winget", "install", "-e", "Foo.Bar"]},
        )
        assert tool.winget_id == "Foo.Bar"

    def test_winget_id_no_winget_commands(self):
        """Returns None when no winget install commands."""
        tool = ToolInfo(
            name="test",
            install_commands={"homebrew": ["brew", "install", "test"]},
        )
        assert tool.winget_id is None


def test_every_pacman_command_syncs_the_database():
    """Without -Sy pacman requests a version the mirrors already dropped."""
    from core.registry import INTEGRATION_TOOLS, OPTIONAL_TOOLS

    checked, stale = 0, []
    for group in (INTEGRATION_TOOLS, OPTIONAL_TOOLS):
        for name, info in group.items():
            cmd = (info.install_commands or {}).get("pacman")
            if not cmd:
                continue
            checked += 1
            if "-S" in cmd:
                stale.append(name)
    assert checked, "no pacman commands found -- the invariant would be vacuous"
    assert not stale, f"pacman install without -Sy: {stale}"
