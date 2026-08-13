"""Shared reuse-level constants for mode tracker and session start hooks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

import workbench_paths  # noqa: E402

VALID_LEVELS = {"lite", "full", "ultra"}
DEFAULT_LEVEL = "full"
# A level the user picks, so it belongs to the config root — not to the state
# root that happens to share its default path.
LEVEL_FILE = workbench_paths.config_dir() / "reuse-level"
DEFAULT_FILE = workbench_paths.config_dir() / "reuse-default"

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
