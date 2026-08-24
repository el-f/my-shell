#!/usr/bin/env python3
"""Beta testing orchestrator.

Builds Docker containers for each persona, executes story commands
deterministically, sends captured output to Claude for qualitative UX
judgment, and produces structured reports.

Usage:
    python -m tests.beta                                       # All Docker personas
    python -m tests.beta --persona frodo                       # Single persona
    python -m tests.beta --persona frodo --story frodo-03
    python -m tests.beta --dry-run                             # Skip LLM judge
    python -m tests.beta --model opus                          # Override model
    python -m tests.beta --resume                              # Skip completed
    python -m tests.beta --no-build                            # Reuse images
    python -m tests.beta --verbose                             # Show output live
    python -m tests.beta --parallel 3                          # Run 3 concurrently
    python -m tests.beta --retry-from-execution frodo_execution.json
"""

from __future__ import annotations

# Re-launch as a module for `python tests/beta/orchestrator.py`; must precede relative imports.
if __name__ == "__main__":
    import subprocess
    import sys
    from pathlib import Path

    project_root = str(Path(__file__).resolve().parent.parent.parent)
    sys.exit(
        subprocess.call(
            [sys.executable, "-m", "tests.beta", *sys.argv[1:]],
            cwd=project_root,
        )
    )

import argparse
import io
import json
import os
import sys

# Force UTF-8 output on Windows (console defaults to cp1252)
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ._log import log_error, log_step, log_success, log_verbose, log_warn
from .config import discover_story_files, load_personas, load_stories
from .docker_mgr import LONG_TIMEOUT, DockerManager
from .executor import execute_story
from .judge import judge_persona, parse_report, render_judge_prompt
from .models import CommandResult, Persona, PersonaReport, Story, StoryExecution
from .reports import write_execution_data, write_json_report, write_md_report, write_summary

BETA_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BETA_DIR / "reports"


def _resolve_container_config(
    persona: Persona,
    stories: list[Story],
) -> tuple[str | None, str | None, dict[str, str]]:
    """Resolve Docker platform, network, and env for a persona's container."""
    platform = persona.docker_platform

    # Stories fake offline with an unroutable http_proxy, so the container keeps the default network.
    network = None

    container_env: dict[str, str] = {}
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token:
        container_env["GITHUB_TOKEN"] = gh_token
        log_step("GITHUB_TOKEN detected -- passing to container")
    else:
        log_warn("No GITHUB_TOKEN/GH_TOKEN found -- mise may hit GitHub API rate limits")

    return platform, network, container_env


def _run_setup(
    docker: DockerManager,
    container_name: str,
    persona: Persona,
    gh_token: str | None,
    verbose: bool,
) -> bool:
    """Run persona setup_commands. Returns True if setup was executed."""
    if not persona.setup_commands:
        return False

    log_step(f"Running {len(persona.setup_commands)} setup command(s)...")
    setup_env = dict(persona.setup_env or {})
    if gh_token:
        setup_env["GITHUB_TOKEN"] = gh_token
    install_seen = False
    for setup_cmd in persona.setup_commands:
        log_verbose(f"  setup$ {setup_cmd}", verbose)
        is_install = "install.sh" in setup_cmd
        actual_cmd = setup_cmd
        # After install.sh, inject PATH so mise/uv are findable
        if install_seen and not is_install:
            from .executor import PATH_PREAMBLE

            actual_cmd = PATH_PREAMBLE + setup_cmd
        cr = docker.exec_command(
            container_name, actual_cmd, env=setup_env or None, timeout=LONG_TIMEOUT
        )
        if is_install:
            install_seen = True
        if verbose and cr.stdout.strip():
            for line in cr.stdout.strip().splitlines()[:10]:
                log_verbose(f"    stdout: {line}", verbose)
        if cr.exit_code != 0:
            log_error(f"Setup command failed (exit {cr.exit_code}): {setup_cmd}")
            if cr.stderr.strip():
                for line in cr.stderr.strip().splitlines()[:10]:
                    log_error(f"    {line}")
        else:
            log_success(f"Setup command completed: {setup_cmd}")
    return True


def _execute_stories(
    docker: DockerManager,
    container_name: str,
    stories: list[Story],
    install_done: bool,
    verbose: bool,
) -> list[StoryExecution]:
    """Execute all stories, handling install dependencies and blocking."""
    executions: list[StoryExecution] = []
    install_failed = False

    for i, story in enumerate(stories, 1):
        print(f"  [{i}/{len(stories)}] {story.id}: {story.title}")

        # Block dependent stories when install failed
        if install_failed and story.category != "install":
            blocked = CommandResult(
                command="(skipped)",
                stdout="",
                stderr="Blocked: install failed",
                exit_code=-2,
                duration_ms=0,
            )
            executions.append(StoryExecution(story=story, command_results=[blocked]))
            log_step("  Results: BLOCKED (install failed)")
            continue

        ex = execute_story(docker, container_name, story, verbose=verbose, inject_path=install_done)
        if not install_done and any("install.sh" in c for c in story.commands):
            install_done = True
            # Check if install.sh itself failed
            if any(cr.exit_code != 0 for cr in ex.command_results if "install.sh" in cr.command):
                install_failed = True
        executions.append(ex)

        # Quick status line
        statuses = [f"exit={cr.exit_code}" for cr in ex.command_results]
        log_step(f"  Results: {', '.join(statuses)}")

    return executions


def _judge_and_report(
    name: str,
    persona: Persona,
    executions: list[StoryExecution],
    model: str,
) -> PersonaReport | None:
    """Run LLM judge, parse response, and write reports."""
    prompt = render_judge_prompt(persona, executions)
    response = judge_persona(prompt, model)
    if not response:
        write_execution_data(name, executions, REPORTS_DIR / f"{name}_execution.json")
        log_error(f"LLM judge failed for {name}; execution data saved for manual retry")
        return None

    report = parse_report(response, name, expected_story_count=len(executions))
    if not report:
        # Save raw response for debugging
        raw_path = REPORTS_DIR / f"{name}_report_raw.md"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(response)
        write_execution_data(name, executions, REPORTS_DIR / f"{name}_execution.json")
        log_warn(f"Could not parse LLM response; raw saved to {raw_path}")
        return None

    # Write reports
    write_json_report(report, REPORTS_DIR / f"{name}_report.json")
    write_md_report(report, persona, REPORTS_DIR / f"{name}_report.md")
    return report


def run_persona(
    name: str,
    persona: Persona,
    stories: list[Story],
    docker: DockerManager,
    *,
    model: str = "sonnet",
    dry_run: bool = False,
    no_build: bool = False,
    verbose: bool = False,
    story_filter: str | None = None,
) -> PersonaReport | None:
    """Run the full pipeline for one persona. Returns report or None on failure."""
    image_tag = f"my-shell-beta-{name}"
    container_name = f"my-shell-beta-{name}"

    # Filter to a single story if requested
    if story_filter:
        stories = [s for s in stories if s.id == story_filter]
        if not stories:
            log_error(f"Story '{story_filter}' not found for persona '{name}'")
            return None

    # Build
    if not no_build:
        if persona.docker_platform:
            log_step(f"Note: {name} uses --platform {persona.docker_platform} (QEMU)")
        if not docker.build_image(persona.dockerfile, image_tag):
            return None
    else:
        log_step(f"Skipping build for {image_tag} (--no-build)")

    # Clean up any leftover container with the same name
    docker.stop_container(container_name)

    # Resolve container config from persona and stories
    platform, network, container_env = _resolve_container_config(persona, stories)
    gh_token = container_env.get("GITHUB_TOKEN")

    container_id = docker.start_container(
        image_tag, container_name, platform=platform, network=network, env=container_env
    )
    if not container_id:
        return None

    try:
        # Run persona setup_commands before stories
        install_done = _run_setup(docker, container_name, persona, gh_token, verbose)

        # Execute stories
        executions = _execute_stories(docker, container_name, stories, install_done, verbose)

        # Dry-run: save execution data and rendered prompt, skip LLM
        if dry_run:
            write_execution_data(name, executions, REPORTS_DIR / f"{name}_execution.json")
            prompt = render_judge_prompt(persona, executions)
            prompt_path = REPORTS_DIR / f"{name}_prompt.md"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt)
            log_success(f"Dry-run prompt: {prompt_path}")
            print(f"  Dry-run complete for {name}. Rendered prompt: {len(prompt)} chars")
            return None

        return _judge_and_report(name, persona, executions, model)

    finally:
        docker.stop_container(container_name)


def retry_from_execution(
    execution_path: Path,
    model: str,
) -> PersonaReport | None:
    """Re-run LLM judgment from saved execution JSON without Docker."""
    with open(execution_path, encoding="utf-8") as f:
        data = json.load(f)

    persona_name = data["persona"]
    personas = load_personas()
    if persona_name not in personas:
        log_error(f"Persona '{persona_name}' not found in personas.toml")
        return None
    persona = personas[persona_name]

    # Reconstruct StoryExecution objects from saved data
    story_files = discover_story_files()
    all_stories = load_stories(persona_name, story_files)
    story_map = {s.id: s for s in all_stories}

    executions: list[StoryExecution] = []
    for ex_data in data["executions"]:
        story = story_map.get(ex_data["story_id"])
        if not story:
            log_warn(f"Story '{ex_data['story_id']}' not found in current config, skipping")
            continue
        cmd_results = [
            CommandResult(
                command=cmd["command"],
                stdout=cmd["stdout"],
                stderr=cmd["stderr"],
                exit_code=cmd["exit_code"],
                duration_ms=cmd["duration_ms"],
            )
            for cmd in ex_data["commands"]
        ]
        executions.append(StoryExecution(story=story, command_results=cmd_results))

    print(f"Retrying judgment for {persona_name} ({len(executions)} stories)")
    return _judge_and_report(persona_name, persona, executions, model)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Beta testing orchestrator for my-shell",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                            Run all Docker personas
  %(prog)s --persona frodo             Single persona
  %(prog)s --persona frodo --story frodo-03  Single story
  %(prog)s --dry-run                  Execute commands, skip LLM judge
  %(prog)s --model opus               Use Opus for judgment
  %(prog)s --resume                   Skip completed personas
  %(prog)s --no-build                 Reuse existing Docker images
  %(prog)s --verbose                  Show command output live
  %(prog)s --parallel 3               Run 3 personas concurrently
  %(prog)s --retry-from-execution frodo_execution.json

Model names: sonnet (default), opus, haiku (or full model IDs)
""",
    )
    parser.add_argument(
        "--persona",
        help="Run a single persona (e.g., frodo, gandalf, sam)",
    )
    parser.add_argument(
        "--story",
        help="Run a single story by ID (requires --persona)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute commands but skip LLM judge; save prompt and execution data",
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Model for the LLM judge, passed to `claude -p --model` (default: sonnet)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip personas that already have reports in the reports/ directory",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip Docker build step (reuse existing images)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show command output and debug info",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete all existing reports before running",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of personas to run concurrently (default: 1)",
    )
    parser.add_argument(
        "--retry-from-execution",
        metavar="JSON_PATH",
        help="Re-run LLM judge from saved execution JSON (no Docker needed)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Retry-from-execution mode: skip Docker entirely
    if args.retry_from_execution:
        path = Path(args.retry_from_execution)
        if not path.exists():
            # Try relative to REPORTS_DIR
            path = REPORTS_DIR / args.retry_from_execution
        if not path.exists():
            sys.exit(f"ERROR: Execution file not found: {args.retry_from_execution}")
        report = retry_from_execution(path, args.model)
        if report:
            print(f"\nDone. Reports in: {REPORTS_DIR}")
        else:
            sys.exit("ERROR: Retry failed")
        return

    if args.story and not args.persona:
        sys.exit("ERROR: --story requires --persona")

    # Clean reports directory if requested
    if args.clean and REPORTS_DIR.exists():
        for f in REPORTS_DIR.iterdir():
            if f.is_file():
                f.unlink()
        log_step("Cleaned reports directory")

    # Load personas and discover story files
    personas = load_personas()
    story_files = discover_story_files()
    all_reports: dict[str, PersonaReport] = {}

    # Determine which personas to run
    if args.persona:
        name = args.persona.lower()
        if name not in personas:
            sys.exit(f"ERROR: Unknown persona '{name}'. Available: {', '.join(personas.keys())}")
        targets = {name: personas[name]}
    else:
        targets = personas

    print("Beta Testing Orchestrator")
    print(f"{'=' * 50}")
    print(f"Model: {args.model}")
    print(f"Dry run: {args.dry_run}")
    print(f"Personas: {len(targets)}")
    if args.parallel > 1:
        print(f"Parallel: {args.parallel}")
    print()

    # Build list of (name, persona, stories) to run
    run_list: list[tuple[str, Persona, list[Story]]] = []
    for name, persona in targets.items():
        # Skip non-Docker personas
        if persona.skip:
            log_step(f"Skipping {persona.name} (skip=true, non-Docker persona)")
            continue

        if not persona.dockerfile:
            log_step(f"Skipping {persona.name} (no Dockerfile, runs on native CI)")
            continue

        # Resume: skip if report already exists
        if args.resume and (REPORTS_DIR / f"{name}_report.json").exists():
            log_step(f"Skipping {persona.name} (report already exists, --resume)")
            continue

        # Load stories
        stories = load_stories(name, story_files)
        if not stories:
            log_error(f"No stories found for {name}, skipping")
            continue

        run_list.append((name, persona, stories))

    if args.parallel > 1 and len(run_list) > 1:
        # Parallel execution -- each persona gets its own DockerManager
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {}
            for name, persona, stories in run_list:
                docker = DockerManager(verbose=args.verbose)
                future = pool.submit(
                    run_persona,
                    name,
                    persona,
                    stories,
                    docker,
                    model=args.model,
                    dry_run=args.dry_run,
                    no_build=args.no_build,
                    verbose=args.verbose,
                    story_filter=args.story,
                )
                futures[future] = name

            for future in as_completed(futures):
                pname = futures[future]
                try:
                    report = future.result()
                    if report:
                        all_reports[pname] = report
                except Exception as e:
                    log_error(f"Persona {pname} failed: {e}")
    else:
        # Sequential execution
        docker = DockerManager(verbose=args.verbose)
        for name, persona, stories in run_list:
            print(f"\n{'=' * 50}")
            print(f"Persona: {persona.name} -- {persona.title}")
            print(f"{'=' * 50}")
            print(f"  Stories: {len(stories)}")
            print(f"  Dockerfile: {persona.dockerfile}")
            print(f"  Platform: {persona.platform}")
            print()

            report = run_persona(
                name,
                persona,
                stories,
                docker,
                model=args.model,
                dry_run=args.dry_run,
                no_build=args.no_build,
                verbose=args.verbose,
                story_filter=args.story,
            )
            if report:
                all_reports[name] = report

    # Write summary if we have reports
    if all_reports:
        print(f"\n{'=' * 50}")
        print("Writing summary...")
        write_summary(all_reports, REPORTS_DIR / "beta_summary.md")

    print(f"\nDone. Reports in: {REPORTS_DIR}")
