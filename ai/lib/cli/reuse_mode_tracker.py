"""Reads hook JSON from stdin, checks if the prompt matches /reuse <level>,
writes the level into the workbench config, and emits context. Which key and
which file are ai/lib/config/workbench_config.py's to say; this only hands it a
value.

Usage (called by settings.json UserPromptSubmit hook):
  echo '{"prompt":"/reuse ultra"}' | reuse-mode-tracker
"""

# doc-group: cli

from __future__ import annotations

import sys

import config.reuse_levels


def main(argv: list[str] | None = None) -> int:
    config.reuse_levels.track_reuse_command(sys.stdin)
    return 0
