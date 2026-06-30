"""Shared reuse-level constants for mode tracker and session start hooks."""

from __future__ import annotations

import os
from pathlib import Path

VALID_LEVELS = {"lite", "full", "ultra"}
DEFAULT_LEVEL = "full"
LEVEL_FILE = Path.home() / ".config" / "workbench" / "reuse-level"
DEFAULT_FILE = Path.home() / ".config" / "workbench" / "reuse-default"

LEVEL_DESCRIPTIONS = {
    "lite": "Build what's asked, name the lazier alternative in one line. User picks.",
    "full": "Enforce the reuse ladder. Stdlib and native first. Shortest diff.",
    "ultra": "Challenge the requirement. Deletion before addition. Ship the one-liner.",
}


def read_default() -> str:
    """Resolve the default level: env var > default file > hardcoded."""
    env = os.environ.get("REUSE_DEFAULT_MODE", "").strip().lower()
    if env in VALID_LEVELS:
        return env
    try:
        persisted = DEFAULT_FILE.read_text().strip().lower()
        if persisted in VALID_LEVELS:
            return persisted
    except FileNotFoundError:
        pass
    return DEFAULT_LEVEL


def read_level() -> str:
    """Read the active session level, falling back to the configured default."""
    try:
        level = LEVEL_FILE.read_text().strip().lower()
        if level in VALID_LEVELS:
            return level
    except FileNotFoundError:
        pass
    return read_default()
