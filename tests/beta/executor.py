"""Story command execution in Docker containers."""

from __future__ import annotations

from ._log import log_verbose
from .docker_mgr import DEFAULT_TIMEOUT, LONG_TIMEOUT, DockerManager
from .models import CommandResult, Story, StoryExecution

# A fresh `docker exec` shell doesn't source ~/.bashrc, so mise/uv are off PATH.
PATH_PREAMBLE = (
    'export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims'
    ':$HOME/.local/share/mise/bin:$HOME/.cargo/bin:$PATH"; '
)


def execute_story(
    docker: DockerManager,
    container_name: str,
    story: Story,
    verbose: bool = False,
    inject_path: bool = False,
) -> StoryExecution:
    """Execute all commands for a story and capture results."""
    timeout = LONG_TIMEOUT if story.category in ("install", "plugins", "slow") else DEFAULT_TIMEOUT
    results: list[CommandResult] = []

    path_active = inject_path
    for cmd in story.commands:
        is_install = "install.sh" in cmd
        actual_cmd = cmd
        # Prepend PATH preamble so mise/uv are findable after install.sh,
        # but skip for install.sh itself (it manages its own PATH).
        if path_active and not is_install:
            actual_cmd = PATH_PREAMBLE + cmd
        log_verbose(f"  $ {actual_cmd}", verbose)
        cr = docker.exec_command(container_name, actual_cmd, env=story.env or None, timeout=timeout)
        # Store original command in result for readability
        cr.command = cmd
        results.append(cr)
        # After install.sh runs, enable PATH injection for remaining commands
        if is_install:
            path_active = True
        if verbose:
            if (cr.stdout or "").strip():
                for line in cr.stdout.strip().splitlines()[:20]:
                    log_verbose(f"    stdout: {line}", verbose)
            if (cr.stderr or "").strip():
                for line in cr.stderr.strip().splitlines()[:10]:
                    log_verbose(f"    stderr: {line}", verbose)
            log_verbose(f"    exit={cr.exit_code} ({cr.duration_ms}ms)", verbose)

    return StoryExecution(story=story, command_results=results)
