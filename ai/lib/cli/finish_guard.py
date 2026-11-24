"""Stop hook entry point: refuse to end a turn that offers to open a PR instead of opening it.

Reads the hook payload from stdin. When the last assistant message offers a PR
and the working directory is on a feature branch with none open
(``pr.finish_offer.should_block``), prints the ``{"decision": "block"}`` reply
that sends Claude back to open it. A payload with ``stop_hook_active`` is never
blocked a second time, so a turn that still ends on an offer ends. Failure is
left to the hook command's ``|| true`` in settings.json.

Exit codes: 0 always.
"""

# doc-group: cli

from __future__ import annotations

import json
import sys

import pr.finish_offer

REASON = ("You ended the turn offering to open a PR. general.md § Workflow: the turn that finishes "
          "the implementation ends with the PR. Load finishing-a-development-branch, run "
          "`pr create --draft`, and report the URL; do not ask.")


def _payload() -> dict:
    try:
        value = json.loads(sys.stdin.read())
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    payload = _payload()
    cwd = payload.get("cwd")
    if payload.get("stop_hook_active") or not isinstance(cwd, str) or not cwd:
        return 0
    message = payload.get("last_assistant_message")
    if not isinstance(message, str) or not message:
        transcript = payload.get("transcript_path")
        message = pr.finish_offer.last_assistant_text(transcript) if isinstance(transcript, str) else ""
    if pr.finish_offer.should_block(message, cwd):
        print(json.dumps({"decision": "block", "reason": REASON}))
    return 0
