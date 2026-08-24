"""Data models for the beta testing framework.

Persona and Story use Pydantic for input validation (parsed from TOML).
Internal types (CommandResult, StoryReport, etc.) use plain dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class Persona(BaseModel):
    """A beta testing persona loaded from personas.toml."""

    name: str
    title: str
    platform: str
    dockerfile: str = ""
    experience: str
    shells: list[str]
    profile: str
    description: str
    personality: str
    skip: bool = False
    setup_commands: list[str] = Field(default_factory=list)
    setup_env: dict[str, str] = Field(default_factory=dict)
    docker_platform: str | None = None


class Story(BaseModel):
    """A test story loaded from a story TOML file."""

    id: str
    title: str
    description: str
    commands: list[str]
    expected_outcomes: list[str] = Field(default_factory=list)
    judge_criteria: list[str] = Field(default_factory=list)
    category: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    docker_options: dict[str, str] = Field(default_factory=dict)


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int


@dataclass
class StoryExecution:
    story: Story
    command_results: list[CommandResult]


@dataclass
class StoryReport:
    story_id: str
    status: str
    output_summary: str
    expected_vs_actual: str
    ux_notes: str
    friction_points: list[str]
    suggestions: list[str]
    severity: str


@dataclass
class PersonaReport:
    persona: str
    story_reports: list[StoryReport]
    overall_impression: str
    top_issues: list[str]
    top_strengths: list[str]
    would_recommend: bool
    confidence_score: float
