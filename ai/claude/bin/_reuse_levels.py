"""Shared reuse-level constants for the mode tracker and session-start hooks.

The level and its default live in the workbench config
(``ai/lib/workbench_config.py``), which is what makes them editable in the same
file as everything else the user configures. These readers exist so the hooks
do not each spell out the fallback chain.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from config import workbench_config  # noqa: E402
from config import workbench_config_write  # noqa: E402
from config.workbench_config import ConfigError, ReuseLevel  # noqa: E402

# Re-exported: the writers below raise it, so a hook catching it should not
# have to reach past this module for the type.
__all__ = [
    "ConfigError", "DEFAULT_LEVEL", "LEVEL_DESCRIPTIONS", "VALID_LEVELS",
    "read_default", "read_level", "write_default", "write_level",
]

VALID_LEVELS = {str(level) for level in ReuseLevel}
DEFAULT_LEVEL = str(ReuseLevel.FULL)

LEVEL_DESCRIPTIONS = {
    "lite": "Build what's asked, name the lazier alternative in one line. User picks.",
    "full": "Enforce the reuse ladder. Stdlib and native first. Shortest diff.",
    "ultra": "Challenge the requirement. Deletion before addition. Ship the one-liner.",
}


def read_default() -> str:
    """Resolve the default level: env var > config > built-in."""
    env = os.environ.get("REUSE_DEFAULT_MODE", "").strip().lower()
    if env in VALID_LEVELS:
        return env
    return str(workbench_config.load_config_or_default().reuse.default)


def read_level() -> str:
    """The active level, falling back to the configured default."""
    level = workbench_config.load_config_or_default().reuse.level
    return str(level) if level is not None else read_default()


def write_level(level: str) -> None:
    """Persist the active level. Raises ``ConfigError`` when the write fails."""
    workbench_config_write.set_value(workbench_config.REUSE_LEVEL_KEY, level)


def write_default(level: str) -> None:
    """Persist the default level. Raises ``ConfigError`` when the write fails."""
    workbench_config_write.set_value(workbench_config.REUSE_DEFAULT_KEY, level)
