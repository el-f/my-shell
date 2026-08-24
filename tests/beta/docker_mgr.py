"""Docker container management for beta testing (docker-py SDK)."""

from __future__ import annotations

import atexit
import contextlib
import shlex
import time
from pathlib import Path

from docker.errors import APIError, BuildError, DockerException, NotFound

import docker

from ._log import log_error, log_step, log_success, log_verbose
from .models import CommandResult

BETA_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BETA_DIR.parent.parent
DOCKERFILES_DIR = BETA_DIR / "dockerfiles"

DEFAULT_TIMEOUT = 120  # seconds per command
LONG_TIMEOUT = 600  # seconds -- plugins compile from source


class DockerManager:
    """Manages Docker image builds and container lifecycle."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self._containers: dict[str, docker.models.containers.Container] = {}
        try:
            self._client = docker.from_env()
        except DockerException as e:
            log_error(f"Docker not available: {e}")
            raise
        atexit.register(self.cleanup)

    def build_image(self, dockerfile: str, tag: str, context_dir: Path | None = None) -> bool:
        """Build a Docker image. Returns True on success."""
        context_path = context_dir or PROJECT_ROOT
        df_path = DOCKERFILES_DIR / dockerfile
        if not df_path.exists():
            log_error(f"Dockerfile not found: {df_path}")
            return False

        # docker-py needs dockerfile relative to context, using forward slashes
        df_relative = df_path.relative_to(context_path).as_posix()

        log_step(f"Building image {tag} from {dockerfile}...")
        try:
            _image, build_log = self._client.images.build(
                path=str(context_path),
                dockerfile=df_relative,
                tag=tag,
                rm=True,
            )
            if self.verbose:
                for chunk in build_log:
                    if "stream" in chunk:
                        line = chunk["stream"].rstrip()
                        if line:
                            log_verbose(line, self.verbose)
        except BuildError as e:
            log_error(f"Docker build failed for {tag}")
            for chunk in e.build_log:
                if "error" in chunk:
                    log_error(f"  {chunk['error'].rstrip()}")
                elif "stream" in chunk:
                    line = chunk["stream"].rstrip()
                    if line:
                        log_error(f"  {line}")
            return False
        except APIError as e:
            log_error(f"Docker API error during build of {tag}: {e}")
            return False

        log_success(f"Image {tag} built")
        return True

    def start_container(
        self,
        tag: str,
        name: str,
        platform: str | None = None,
        network: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str | None:
        """Start a container with sleep infinity. Returns container ID or None."""
        log_step(f"Starting container {name}...")
        try:
            container = self._client.containers.run(
                tag,
                name=name,
                detach=True,
                platform=platform,
                network=network,
                environment=env or {},
            )
        except APIError as e:
            log_error(f"Failed to start container {name}: {e}")
            return None

        self._containers[name] = container
        container_id = container.id
        log_success(f"Container {name} started ({container_id[:12]})")
        return container_id

    def exec_command(
        self,
        container_name: str,
        command: str,
        env: dict[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        """Execute a command in a running container."""
        container = self._containers[container_name]
        # Wrap with Unix timeout command (coreutils) since docker-py exec_run
        # has no timeout parameter (docker/docker-py#2651)
        wrapped_cmd = f"timeout {timeout} bash -c {shlex.quote(command)}"

        start = time.monotonic()
        result = container.exec_run(
            ["bash", "-c", wrapped_cmd],
            environment=env or {},
            demux=True,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        stdout_bytes, stderr_bytes = result.output
        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

        # Exit code 124 = timeout from coreutils
        if result.exit_code == 124:
            return CommandResult(
                command=command,
                stdout=stdout,
                stderr=f"TIMEOUT after {timeout}s",
                exit_code=-1,
                duration_ms=duration_ms,
            )

        return CommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            duration_ms=duration_ms,
        )

    def stop_container(self, name: str) -> None:
        """Stop and remove a container."""
        container = self._containers.pop(name, None)
        if container:
            with contextlib.suppress(NotFound, APIError):
                container.remove(force=True)
        else:
            # Try to remove by name even if not tracked (cleanup leftover containers)
            with contextlib.suppress(NotFound, APIError):
                c = self._client.containers.get(name)
                c.remove(force=True)

    def cleanup(self) -> None:
        """Stop all managed containers (atexit handler)."""
        for _name, container in list(self._containers.items()):
            with contextlib.suppress(Exception):
                container.remove(force=True)
        self._containers.clear()
