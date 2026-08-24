"""Report generation for beta testing results."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ._log import log_step, log_success
from .models import Persona, PersonaReport, StoryExecution, StoryReport


def count_statuses(reports: list[StoryReport]) -> dict[str, int]:
    """Count story statuses (pass/fail/partial/blocked)."""
    counts: dict[str, int] = {"pass": 0, "fail": 0, "partial": 0, "blocked": 0}
    for sr in reports:
        counts[sr.status] = counts.get(sr.status, 0) + 1
    return counts


def report_to_dict(report: PersonaReport) -> dict:
    """Convert a PersonaReport to a JSON-serializable dict."""
    return {
        "persona": report.persona,
        "generated_at": datetime.now(UTC).isoformat(),
        "story_reports": [
            {
                "story_id": sr.story_id,
                "status": sr.status,
                "output_summary": sr.output_summary,
                "expected_vs_actual": sr.expected_vs_actual,
                "ux_notes": sr.ux_notes,
                "friction_points": sr.friction_points,
                "suggestions": sr.suggestions,
                "severity": sr.severity,
            }
            for sr in report.story_reports
        ],
        "overall_impression": report.overall_impression,
        "top_issues": report.top_issues,
        "top_strengths": report.top_strengths,
        "would_recommend": report.would_recommend,
        "confidence_score": report.confidence_score,
    }


def write_json_report(report: PersonaReport, path: Path) -> None:
    """Write the persona report as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report_to_dict(report), f, indent=2)
    log_success(f"JSON report: {path}")


def write_md_report(report: PersonaReport, persona: Persona, path: Path) -> None:
    """Write a human-readable Markdown report."""
    path.parent.mkdir(parents=True, exist_ok=True)

    counts = count_statuses(report.story_reports)

    lines = [
        f"# Beta Test Report: {persona.name}",
        "",
        "## Summary",
        f"- **Persona**: {persona.name} ({persona.title})",
        f"- **Platform**: {persona.platform}",
        f"- **Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **Stories Attempted**: {len(report.story_reports)}",
        f"- **Passed**: {counts['pass']} | **Failed**: {counts['fail']}"
        f" | **Partial**: {counts['partial']} | **Blocked**: {counts['blocked']}",
        "",
        "## Story Results",
        "",
    ]

    for sr in report.story_reports:
        lines.extend(
            [
                f"### {sr.story_id}: {sr.status.upper()}",
                f"- **Output Summary**: {sr.output_summary}",
                f"- **Expected vs Actual**: {sr.expected_vs_actual}",
                f"- **UX Notes**: {sr.ux_notes}",
                f"- **Friction Points**: {', '.join(sr.friction_points) if sr.friction_points else 'None'}",
                f"- **Suggestions**: {', '.join(sr.suggestions) if sr.suggestions else 'None'}",
                f"- **Severity**: {sr.severity}",
                "",
            ]
        )

    lines.extend(
        [
            "## Overall Assessment",
            "",
            "### Overall Impression",
            report.overall_impression or "(not provided)",
            "",
            "### Top Issues",
        ]
    )
    for i, issue in enumerate(report.top_issues, 1):
        lines.append(f"{i}. {issue}")
    if not report.top_issues:
        lines.append("(none)")

    lines.extend(["", "### Top Strengths"])
    for i, strength in enumerate(report.top_strengths, 1):
        lines.append(f"{i}. {strength}")
    if not report.top_strengths:
        lines.append("(none)")

    lines.extend(
        [
            "",
            f"### Would Recommend: {'Yes' if report.would_recommend else 'No'}",
            f"### Confidence Score: {report.confidence_score}",
            "",
        ]
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log_success(f"Markdown report: {path}")


def write_execution_data(persona_name: str, executions: list[StoryExecution], path: Path) -> None:
    """Save raw execution data for manual LLM retry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "persona": persona_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "executions": [
            {
                "story_id": ex.story.id,
                "story_title": ex.story.title,
                "commands": [
                    {
                        "command": cr.command,
                        "stdout": cr.stdout,
                        "stderr": cr.stderr,
                        "exit_code": cr.exit_code,
                        "duration_ms": cr.duration_ms,
                    }
                    for cr in ex.command_results
                ],
            }
            for ex in executions
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log_step(f"Execution data saved: {path}")


def write_summary(all_reports: dict[str, PersonaReport], path: Path) -> None:
    """Write an aggregated summary across all personas."""
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Beta Testing Summary",
        "",
        f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Personas tested**: {len(all_reports)}",
        "",
        "## Results by Persona",
        "",
        "| Persona | Pass | Fail | Partial | Blocked | Recommend | Confidence |",
        "|---------|------|------|---------|---------|-----------|------------|",
    ]

    total_pass = total_fail = total_partial = total_blocked = 0
    all_issues: list[str] = []
    all_strengths: list[str] = []

    for name, report in sorted(all_reports.items()):
        counts = count_statuses(report.story_reports)
        total_pass += counts["pass"]
        total_fail += counts["fail"]
        total_partial += counts["partial"]
        total_blocked += counts["blocked"]
        recommend = "Yes" if report.would_recommend else "No"
        lines.append(
            f"| {name.capitalize()} | {counts['pass']} | {counts['fail']}"
            f" | {counts['partial']} | {counts['blocked']}"
            f" | {recommend} | {report.confidence_score:.2f} |"
        )
        all_issues.extend(report.top_issues)
        all_strengths.extend(report.top_strengths)

    total = total_pass + total_fail + total_partial + total_blocked
    lines.extend(
        [
            "",
            f"**Total stories**: {total}  ",
            f"**Pass rate**: {total_pass}/{total} ({100 * total_pass / total:.0f}%)"
            if total
            else "**Pass rate**: N/A",
            "",
            "## Aggregated Issues",
            "",
        ]
    )
    for i, issue in enumerate(all_issues[:20], 1):
        lines.append(f"{i}. {issue}")
    if not all_issues:
        lines.append("(none)")

    lines.extend(["", "## Aggregated Strengths", ""])
    for i, strength in enumerate(all_strengths[:20], 1):
        lines.append(f"{i}. {strength}")
    if not all_strengths:
        lines.append("(none)")

    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log_success(f"Summary: {path}")
