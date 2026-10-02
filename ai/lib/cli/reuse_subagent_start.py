"""Emits the active reuse level so subagents inherit the parent session's mode.
Skips ceiling scan (too expensive for subagent startup).

Usage (called by settings.json SubagentStart hook):
  reuse-subagent-start
"""

# doc-group: cli

from __future__ import annotations

import config.reuse_levels


def main(argv: list[str] | None = None) -> int:
    config.reuse_levels.announce_to_subagent()
    return 0
