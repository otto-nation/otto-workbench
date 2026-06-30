"""Shared reuse-level constants for mode tracker and session start hooks."""

from __future__ import annotations

from pathlib import Path

VALID_LEVELS = {"lite", "full", "ultra"}
DEFAULT_LEVEL = "full"
LEVEL_FILE = Path.home() / ".config" / "workbench" / "reuse-level"

LEVEL_DESCRIPTIONS = {
    "lite": "Build what's asked, name the lazier alternative in one line. User picks.",
    "full": "Enforce the reuse ladder. Stdlib and native first. Shortest diff.",
    "ultra": "Challenge the requirement. Deletion before addition. Ship the one-liner.",
}


def read_level() -> str:
    try:
        return LEVEL_FILE.read_text().strip()
    except OSError:
        return DEFAULT_LEVEL
