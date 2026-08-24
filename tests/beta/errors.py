"""Structured error types for the beta testing framework."""

from __future__ import annotations


class BetaTestError(Exception):
    """Base exception for beta testing errors."""


class ConfigError(BetaTestError):
    """Invalid persona or story configuration."""


class DockerBuildError(BetaTestError):
    """Docker image build failed."""


class DockerContainerError(BetaTestError):
    """Docker container start/exec failed."""


class LLMJudgeError(BetaTestError):
    """LLM judge invocation failed."""


class ReportParseError(BetaTestError):
    """Failed to parse LLM response into structured report."""
