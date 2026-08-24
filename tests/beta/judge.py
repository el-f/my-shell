"""LLM judge for beta test evaluation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ._log import log_error, log_step, log_warn
from .models import Persona, PersonaReport, StoryExecution, StoryReport

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    sys.exit("ERROR: jinja2 is required. Install with: pip install jinja2")

BETA_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BETA_DIR / "prompts"

STDOUT_TRUNCATE = 4000  # chars
STDERR_TRUNCATE = 4000  # chars
JUDGE_TIMEOUT = 300  # LLM judge timeout

_DATA_TAG_RE = re.compile(r"</?untrusted_command_output", re.IGNORECASE)


def truncate(text: str, limit: int) -> str:
    """Truncate text to limit chars, adding a marker if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"


def defuse(text: str) -> str:
    """Break the markers that could end the data block in the judge prompt.

    The container runs third-party installers, so its output is untrusted. A
    closing tag or a code fence inside it would end the block that holds it, and
    the rest would reach the judge as instructions.
    """
    text = text.replace("```", "` ` `").replace("~~~", "~ ~ ~")
    return _DATA_TAG_RE.sub("&lt;untrusted_command_output", text)


def render_judge_prompt(persona: Persona, executions: list[StoryExecution]) -> str:
    """Render the Jinja2 system prompt with persona and execution results."""
    env = Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        keep_trailing_newline=True,
    )
    template = env.get_template("system_prompt.md.j2")

    # Prepare execution data for the template
    stories_with_results = []
    for ex in executions:
        cmd_results = []
        for cr in ex.command_results:
            cmd_results.append(
                {
                    "command": cr.command,
                    "stdout": defuse(truncate(cr.stdout or "", STDOUT_TRUNCATE)),
                    "stderr": defuse(truncate(cr.stderr or "", STDERR_TRUNCATE)),
                    "exit_code": cr.exit_code,
                    "duration_ms": cr.duration_ms,
                }
            )
        stories_with_results.append(
            {
                "id": ex.story.id,
                "title": ex.story.title,
                "description": ex.story.description,
                "commands": ex.story.commands,
                "expected_outcomes": ex.story.expected_outcomes,
                "judge_criteria": ex.story.judge_criteria,
                "category": ex.story.category,
                "command_results": cmd_results,
            }
        )

    return template.render(
        persona=persona,
        stories=stories_with_results,
    )


def _invoke_claude(prompt: str, model: str) -> str | None:
    """Single invocation of `claude -p`. Returns response text or None."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        # Remove CLAUDECODE env var to allow nested invocation from within
        # a Claude Code session (claude -p is non-interactive, safe to nest)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        with open(prompt_file, encoding="utf-8") as prompt_fh:
            # The prompt carries untrusted container output: judging needs no tools.
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    "--model",
                    model,
                    "--permission-mode",
                    "plan",
                    "--allowedTools",
                    "",
                ],
                stdin=prompt_fh,
                capture_output=True,
                text=True,
                timeout=JUDGE_TIMEOUT,
                env=env,
                encoding="utf-8",
                errors="replace",
            )
        if result.returncode != 0:
            log_error(f"Claude CLI failed (exit {result.returncode})")
            if result.stderr:
                log_error(f"  {result.stderr.strip()[:500]}")
            return None
        return result.stdout
    except FileNotFoundError:
        log_error(
            "'claude' CLI not found. Install Claude Code: https://docs.anthropic.com/en/docs/claude-code"
        )
        return None
    except subprocess.TimeoutExpired:
        log_error(f"Claude CLI timed out after {JUDGE_TIMEOUT}s")
        return None
    finally:
        os.unlink(prompt_file)


def judge_persona(prompt: str, model: str, max_retries: int = 2) -> str | None:
    """Invoke Claude with retries and exponential backoff.

    Retries up to max_retries times with 10s, 20s backoff on failure.
    """
    log_step(f"Invoking Claude ({model}) for judgment...")

    for attempt in range(1 + max_retries):
        if attempt > 0:
            wait = 10 * (2 ** (attempt - 1))  # 10s, 20s
            log_step(f"Retrying in {wait}s (attempt {attempt + 1}/{1 + max_retries})...")
            time.sleep(wait)
        result = _invoke_claude(prompt, model)
        if result is not None:
            return result

    log_error(f"LLM judge failed after {1 + max_retries} attempts")
    return None


def parse_report(
    response_text: str,
    persona_name: str,
    expected_story_count: int | None = None,
) -> PersonaReport | None:
    """Parse JSON blocks from the LLM response into a PersonaReport.

    Validates story count against expected if provided, and warns on
    missing summary fields.
    """
    # Extract all ```json ... ``` blocks
    json_blocks = re.findall(r"```json\s*\n(.*?)```", response_text, re.DOTALL)
    if not json_blocks:
        log_error("No JSON blocks found in LLM response")
        return None

    story_reports: list[StoryReport] = []
    summary: dict | None = None

    for block in json_blocks:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError as e:
            log_warn(f"Failed to parse JSON block: {e}")
            continue

        # Distinguish story reports from summary by checking for "story_id" key
        if "story_id" in data:
            story_reports.append(
                StoryReport(
                    story_id=data.get("story_id", ""),
                    status=data.get("status", "unknown"),
                    output_summary=data.get("output_summary", ""),
                    expected_vs_actual=data.get("expected_vs_actual", ""),
                    ux_notes=data.get("ux_notes", ""),
                    friction_points=data.get("friction_points", []),
                    suggestions=data.get("suggestions", []),
                    severity=data.get("severity", "unknown"),
                )
            )
        elif "overall_impression" in data or "persona" in data:
            summary = data

    # Validate story count if expected
    if expected_story_count is not None and len(story_reports) != expected_story_count:
        log_warn(f"Expected {expected_story_count} story reports, got {len(story_reports)}")

    if not summary:
        log_warn("No summary block found in LLM response; using defaults")
        summary = {}

    # Validate required summary fields
    missing_fields = [
        f for f in ("overall_impression", "top_issues", "top_strengths") if f not in summary
    ]
    if missing_fields:
        log_warn(f"Summary missing fields: {', '.join(missing_fields)}")

    return PersonaReport(
        persona=persona_name,
        story_reports=story_reports,
        overall_impression=summary.get("overall_impression", ""),
        top_issues=summary.get("top_issues", []),
        top_strengths=summary.get("top_strengths", []),
        would_recommend=summary.get("would_recommend", True),
        confidence_score=summary.get("confidence_score", 0.0),
    )
