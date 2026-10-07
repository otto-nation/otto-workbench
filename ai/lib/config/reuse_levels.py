"""The reuse-level vocabulary, its readers and writers, and the two hook bodies
that are nothing but those (``/reuse`` tracking, the subagent line).

Not: the config schema or keys (``config.workbench_config``), the write
mechanics (``config.workbench_config_write``), the session-start context
(``config.session_start``), the status line (``cli.workbench_statusline``).
"""

# doc-group: platform

from __future__ import annotations

import json
import os
from typing import TextIO

import config.workbench_config
import config.workbench_config_write
from config.workbench_config import ConfigError, ReuseLevel

# The slash command the UserPromptSubmit hook answers. Prose that names it is
# checked against this by bin/local/validate-prose-refs.
SLASH_COMMAND = "/reuse"

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
    return str(config.workbench_config.load_config_or_default().reuse.default)


def read_level() -> str:
    """The active level, falling back to the configured default."""
    level = config.workbench_config.load_config_or_default().reuse.level
    return str(level) if level is not None else read_default()


def write_level(level: str) -> None:
    """Persist the active level. Raises ``ConfigError`` when the write fails."""
    config.workbench_config_write.set_value(config.workbench_config.REUSE_LEVEL_KEY, level)


def write_default(level: str) -> None:
    """Persist the default level. Raises ``ConfigError`` when the write fails."""
    config.workbench_config_write.set_value(config.workbench_config.REUSE_DEFAULT_KEY, level)


def _handle_default(args: str) -> None:
    """Handle /reuse default [<level>]."""
    parts = args.split(None, 1)
    if len(parts) < 2:
        print(f"Current default reuse level: {read_default()}")
        return

    level = parts[1].lower().strip()
    if level not in VALID_LEVELS:
        print(f"Unknown reuse level: {level}. Valid: lite, full, ultra")
        return

    try:
        write_default(level)
    except ConfigError as exc:
        # This runs on UserPromptSubmit: an unwritable config must report itself
        # and let the prompt through, not abort the user's turn with a traceback.
        print(f"Could not save the default reuse level: {exc}")
        return
    print(f"Default reuse level set to: {level} — {LEVEL_DESCRIPTIONS[level]}")


def track_reuse_command(stdin: TextIO) -> None:
    try:
        data = json.load(stdin)
    except (json.JSONDecodeError, ValueError):
        return

    prompt = data.get("prompt", "").strip()
    if prompt != SLASH_COMMAND and not prompt.startswith(f"{SLASH_COMMAND} "):
        return

    parts = prompt.split(None, 1)
    if len(parts) < 2:
        print(f"Current reuse level: {read_level()}")
        return

    arg = parts[1].strip()
    if arg.lower() == "default" or arg.lower().startswith("default "):
        _handle_default(arg)
        return

    level = arg.lower()
    if level not in VALID_LEVELS:
        print(f"Unknown reuse level: {level}. Valid: lite, full, ultra, default <level>")
        return

    try:
        write_level(level)
    except ConfigError as exc:
        print(f"Could not save the reuse level: {exc}")
        return
    print(f"Reuse level set to: {level} — {LEVEL_DESCRIPTIONS[level]}")


def announce_to_subagent() -> None:
    level = read_level()
    # Intentionally compare against DEFAULT_LEVEL ("full"), not read_default().
    # Subagents don't receive SessionStart, so they won't discover a configured
    # default unless we inject it here. We inject whenever the level differs from
    # the universal baseline so a user-configured default (e.g. "lite") is not
    # silently lost. `config.session_start` uses read_default() because a top-level
    # session already ran at full before the default was applied.
    if level != DEFAULT_LEVEL:
        print(f"Reuse level: {level} — {LEVEL_DESCRIPTIONS.get(level, '')}")

