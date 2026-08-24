"""Helpers for running mise consistently across the codebase."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TRUSTED_CONFIG_PATHS_ENV = "MISE_TRUSTED_CONFIG_PATHS"


@dataclass(frozen=True)
class CommandSpec:
    """A fully resolved command invocation."""

    args: list[str]
    cwd: Path | None = None
    env: dict[str, str] | None = None


def mise_binary() -> str | None:
    """Return the resolved mise binary, if available."""
    return shutil.which("mise")


def mise_shims_dir() -> Path:
    """The mise shims directory, mirroring mise's own data-dir resolution."""
    data_dir = os.environ.get("MISE_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "shims"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "mise" / "shims"
    return Path.home() / ".local" / "share" / "mise" / "shims"


def _looks_like_mise_shim(path: Path) -> bool:
    """Return True when *path* points at a mise shim wrapper."""
    parts = {part.lower() for part in path.parts}
    return "mise" in parts and "shims" in parts


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def build_trusted_env(
    *,
    trusted_paths: tuple[Path | str, ...] = (),
    base_env: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Return an env dict with trusted mise config paths merged in."""
    if not trusted_paths and base_env is None:
        return None

    env = dict(base_env or os.environ)
    merged_paths: list[str] = []
    existing = env.get(TRUSTED_CONFIG_PATHS_ENV, "")
    if existing:
        merged_paths.extend(path for path in existing.split(os.pathsep) if path)
    merged_paths.extend(str(Path(path)) for path in trusted_paths)
    env[TRUSTED_CONFIG_PATHS_ENV] = os.pathsep.join(_dedupe_paths(merged_paths))
    return env


def which(tool: str, *, project_dir: Path | None = None, timeout: int = 5) -> str | None:
    """Resolve a tool through mise, optionally trusting a project config."""
    mise = mise_binary()
    if not mise:
        return None

    trusted_paths: tuple[Path | str, ...] = (project_dir,) if project_dir else ()
    cwd = project_dir or Path.home()
    try:
        result = subprocess.run(
            [mise, "which", tool],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=build_trusted_env(trusted_paths=trusted_paths),
        )
    except OSError, subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None

    path = result.stdout.strip()
    return path or None


def resolve_command(cmd: list[str], *, project_dir: Path | None = None) -> CommandSpec:
    """Resolve a command to an executable path with any required trusted env.

    A tool mise manages for *project_dir* (per mise.toml/mise.lock) wins over a
    same-named binary elsewhere on PATH (Homebrew, apt, a manual install) --
    those diverge in version and this project pins and tests against the mise
    one specifically. Falls back to bare PATH only when mise doesn't manage it.
    """
    tool = cmd[0]
    if project_dir is not None and tool != "mise":
        resolved_via_mise = which(tool, project_dir=project_dir)
        if resolved_via_mise:
            trusted_paths: tuple[Path | str, ...] = (project_dir,)
            return CommandSpec(
                args=[resolved_via_mise, *cmd[1:]],
                cwd=project_dir,
                env=build_trusted_env(trusted_paths=trusted_paths),
            )

    direct_path = shutil.which(tool)
    if direct_path is not None:
        resolved = Path(direct_path)
        needs_trust = project_dir is not None and (
            tool == "mise" or _looks_like_mise_shim(resolved)
        )
        trusted_paths = (project_dir,) if needs_trust and project_dir else ()
        return CommandSpec(
            args=[str(resolved), *cmd[1:]],
            cwd=project_dir if needs_trust else None,
            env=build_trusted_env(trusted_paths=trusted_paths),
        )

    return CommandSpec(args=list(cmd))
