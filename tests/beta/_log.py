"""Logging helpers for the beta testing framework."""

from __future__ import annotations

import sys


def log_step(msg: str) -> None:
    print(f"  -> {msg}")


def log_success(msg: str) -> None:
    print(f"  [OK] {msg}")


def log_error(msg: str) -> None:
    print(f"  [ERR] {msg}", file=sys.stderr)


def log_warn(msg: str) -> None:
    print(f"  [WARN] {msg}", file=sys.stderr)


def log_verbose(msg: str, verbose: bool) -> None:
    if verbose:
        print(f"  [DBG] {msg}")
