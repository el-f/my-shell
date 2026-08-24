"""Shared test helpers (not a conftest, so it can be imported explicitly)."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def apply_isolation_patches(stack: ExitStack, tmp_project: Path, fake_home: Path) -> None:
    """Apply the standard deploy-isolation patches onto an ExitStack.

    Single source of truth so test_cli.py and test_new_features.py can't drift
    (they previously kept two copies that diverged once).
    """
    for target, kwargs in [
        ("core.merge.get_project_dir", {"return_value": tmp_project}),
        ("core.merge.get_home_dir", {"return_value": fake_home}),
        ("core.merge.is_available", {"return_value": False}),
        ("core.merge._find_xonsh_python", {"return_value": None}),
        ("core.merge._verify_global_nushell_runtime", {}),
        ("core.render.get_project_dir", {"return_value": tmp_project}),
        ("core.config.get_project_dir", {"return_value": tmp_project}),
        ("core.install.install_shells_for_setup", {}),
        ("core.install.install_all_tools", {}),
    ]:
        stack.enter_context(patch(target, **kwargs))
