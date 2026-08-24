"""Configuration loading for personas and stories."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from pydantic import ValidationError

from ._log import log_error
from .errors import ConfigError
from .models import Persona, Story

BETA_DIR = Path(__file__).resolve().parent
STORIES_DIR = BETA_DIR / "stories"


def load_personas(path: Path | None = None) -> dict[str, Persona]:
    """Load all personas from personas.toml."""
    path = path or (BETA_DIR / "personas.toml")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    personas: dict[str, Persona] = {}
    for key, val in data.items():
        try:
            personas[key] = Persona(**val)
        except ValidationError as e:
            raise ConfigError(f"Invalid persona '{key}' in {path}:\n{e}") from e
    return personas


def discover_story_files() -> dict[str, Path]:
    """Auto-discover story files by matching *.toml stems to persona keys."""
    mapping: dict[str, Path] = {}
    for path in sorted(STORIES_DIR.glob("*.toml")):
        # Strip leading digits+underscore for backward compat: "01_frodo" -> "frodo"
        stem = re.sub(r"^\d+_", "", path.stem)
        mapping[stem] = path
    return mapping


def load_stories(persona_name: str, story_files: dict[str, Path]) -> list[Story]:
    """Load stories for a persona from its TOML file."""
    path = story_files.get(persona_name)
    if not path:
        log_error(f"No story file found for persona '{persona_name}'")
        return []
    if not path.exists():
        log_error(f"Story file not found: {path}")
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    stories: list[Story] = []
    for s in data.get("stories", []):
        try:
            stories.append(Story(**s))
        except ValidationError as e:
            raise ConfigError(f"Invalid story in {path}:\n{e}") from e
    return stories
