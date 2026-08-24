"""Shared pytest fixtures."""

import shutil
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_verbose():
    yield
    import core.utils as _utils

    _utils.set_verbose(False)
    # Clear caches so tests don't leak state
    _utils.is_available.cache_clear()
    _utils._project_dir_cache = None


@pytest.fixture(autouse=True)
def _clean_mise_env(monkeypatch):
    """Strip MISE_TRUSTED_CONFIG_PATHS from the environment so build_trusted_env
    tests aren't polluted by CI or local env vars."""
    monkeypatch.delenv("MISE_TRUSTED_CONFIG_PATHS", raising=False)


@pytest.fixture
def tmp_aliases_config(tmp_path: Path) -> Path:
    """Create just the config/aliases.toml structure for testing."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    (config_dir / "aliases.toml").write_text(
        textwrap.dedent("""\
        [navigation]
        ".." = "cd .."
        "..." = "cd ../.."

        [modern_replacements]
        grep = { command = "rg", comment = "ripgrep" }
        cat = { command = "bat", comment = "bat with syntax highlighting" }

        [git]
        g = "git"
        gs = "git status"
        glog = "git log --oneline --graph --decorate"

        [listing]
        l = "ls"

        [clear]
        c = { command = "clear", xonsh_fn = "clear_screen" }
        cls = { command = "clear", xonsh_fn = "clear_screen" }

        [system_info]
        meminfo = { nushell = "sys mem", xonsh_fn = "platform_meminfo", comment = "Memory info" }
        cpuinfo = { nushell = "sys cpu", xonsh_fn = "platform_cpuinfo", comment = "CPU info" }

        [docker]
        d = "docker"
        dc = "docker compose"

        [kubectl]
        k = "kubectl"
        kg = "kubectl get"

        [help]
        halp = { command = "tldr", comment = "simplified man pages" }

        [pueue]
        pqa = { command = "pueue add --", comment = "Add task to queue" }
        pqs = { command = "pueue status", comment = "Show queue status" }

        [wrappers.fd]
        preferred = "fd"
        fallback = "fdfind"
        error = "fd not found (install fd or fd-find)"

        [wrappers.bat]
        preferred = "bat"
        fallback = "batcat"
        error = "bat not found (install bat or batcat)"
    """),
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def tmp_project(tmp_aliases_config: Path) -> Path:
    """Create a minimal my-shell project structure for testing.

    Builds on tmp_aliases_config (which provides config/aliases.toml)
    and adds shell directories and integrations.
    """
    tmp_path = tmp_aliases_config

    # Plugin config
    (tmp_path / "config" / "plugins.toml").write_text(
        "[plugins.nu_plugin_gstat]\n"
        'crate = "nu_plugin_gstat"\n'
        'description = "Git status as structured data"\n'
        "\n"
        "[plugins.nu_plugin_formats]\n"
        'crate = "nu_plugin_formats"\n'
        'description = "Extra format support (eml, ics, ini, vcf)"\n',
        encoding="utf-8",
    )

    # Shell directories
    nushell_dir = tmp_path / "shells" / "nushell"
    nushell_dir.mkdir(parents=True)
    (nushell_dir / "commands").mkdir()
    (nushell_dir / "config.nu.template").write_text("# test template\n", encoding="utf-8")
    (nushell_dir / "env.nu.template").write_text("# test env\n", encoding="utf-8")
    (nushell_dir / "commands" / "navigation.nu").write_text("# nav\n", encoding="utf-8")
    (nushell_dir / "commands" / "fuzzy.nu").write_text("# fuzzy\n", encoding="utf-8")
    (nushell_dir / "commands" / "utilities.nu").write_text("# utils\n", encoding="utf-8")
    (nushell_dir / "commands" / "sysinfo.nu").write_text("# sysinfo\n", encoding="utf-8")
    (nushell_dir / "commands" / "commands.nu").write_text("# commands\n", encoding="utf-8")

    xonsh_dir = tmp_path / "shells" / "xonsh"
    xonsh_dir.mkdir(parents=True)
    (xonsh_dir / "commands").mkdir()
    (xonsh_dir / "xonshrc_base.xsh").write_text("# test xonsh template\n", encoding="utf-8")
    (xonsh_dir / "commands" / "__init__.py").write_text("", encoding="utf-8")
    # Copied, not re-written: the renderer derives the xonsh_fn allowlist from it.
    real_alias_fns = Path(__file__).parent.parent / "shells" / "xonsh" / "commands" / "alias_fns.py"
    shutil.copy2(real_alias_fns, xonsh_dir / "commands" / "alias_fns.py")

    # Shared themes (used by both nushell and xonsh)
    shared_omp_dir = tmp_path / "shells" / "shared" / "oh-my-posh" / "themes"
    shared_omp_dir.mkdir(parents=True)
    (shared_omp_dir / "jblab_2021.omp.json").write_text("{}", encoding="utf-8")

    # Integrations (xonsh-specific, under shells/xonsh/)
    omp_dir = tmp_path / "shells" / "xonsh" / "integrations" / "oh-my-posh"
    omp_dir.mkdir(parents=True)
    (omp_dir / "init.xsh").write_text("# omp xonsh\n", encoding="utf-8")

    zoxide_dir = tmp_path / "shells" / "xonsh" / "integrations" / "zoxide"
    zoxide_dir.mkdir(parents=True)
    (zoxide_dir / "init.xsh").write_text("# zoxide xonsh\n", encoding="utf-8")

    atuin_dir = tmp_path / "shells" / "xonsh" / "integrations" / "atuin"
    atuin_dir.mkdir(parents=True)
    (atuin_dir / "init.xsh").write_text("# atuin xonsh\n", encoding="utf-8")

    carapace_dir = tmp_path / "shells" / "xonsh" / "integrations" / "carapace"
    carapace_dir.mkdir(parents=True)
    (carapace_dir / "init.xsh").write_text("# carapace xonsh\n", encoding="utf-8")

    mise_dir = tmp_path / "shells" / "xonsh" / "integrations" / "mise"
    mise_dir.mkdir(parents=True)
    (mise_dir / "init.xsh").write_text("# mise xonsh\n", encoding="utf-8")

    return tmp_path


@pytest.fixture
def clean_home(tmp_path: Path):
    """Provide an isolated HOME directory for deploy tests."""
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture(scope="session")
def real_project_dir() -> Path:
    """Return the real my-shell project root (session-scoped, read-only)."""
    from core.utils import get_project_dir

    return get_project_dir()


@pytest.fixture
def nu_deployed(tmp_path, real_project_dir):
    """Deploy real nushell config to a temp dir. Returns config dir path."""
    from core.config import _DEFAULT_SETTINGS
    from core.merge import deploy

    config_dir = tmp_path / "nushell"
    # Use default settings (all groups enabled) so settings.local.toml from
    # other tests (e.g. setup --profile) doesn't disable command modules.
    default_settings = {k: dict(v) for k, v in _DEFAULT_SETTINGS.items()}
    with (
        patch("core.merge.is_available", return_value=False),
        patch("core.merge.load_settings", return_value=default_settings),
    ):
        deploy("nushell", config_dir=config_dir, project_dir=real_project_dir)
    return config_dir


@pytest.fixture
def xonsh_deployed(tmp_path, real_project_dir):
    """Deploy real xonsh config to a temp dir. Returns xonshrc path."""
    from core.config import _DEFAULT_SETTINGS
    from core.merge import deploy

    config_dir = tmp_path / "xonsh"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    default_settings = {k: dict(v) for k, v in _DEFAULT_SETTINGS.items()}
    with (
        patch("core.merge.is_available", return_value=False),
        patch("core.merge.get_home_dir", return_value=fake_home),
        patch("core.merge._find_xonsh_python", return_value=None),
        patch("core.merge.load_settings", return_value=default_settings),
    ):
        deploy("xonsh", config_dir=config_dir, project_dir=real_project_dir)
    return fake_home / ".xonshrc"
