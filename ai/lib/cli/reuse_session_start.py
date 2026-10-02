"""SessionStart hook entry point: the context lines `config.session_start`
derives, printed for Claude Code to read. Failure is left to the hook
command's `|| true` in settings.json (Decision F).
"""

# doc-group: cli

from __future__ import annotations

import config.session_start


def main(argv: list[str] | None = None) -> int:
    config.session_start.run()
    return 0
