"""``agent-worktree`` — publish which worktree an agent works in on its herdr terminal.

Usage:
  agent-worktree report   (stdin: one post-tool hook payload, Claude's shape)
  agent-worktree clear

``report`` reads the payload from stdin and labels ``$HC_TERMINAL_ID`` with
``otto.worktree=<path>`` when the call targeted a worktree other than the
agent's own and that differs from the last one published; ``clear`` empties
the label. Both are ``pr.agent_worktree``'s; this module only parses and maps.

Always exits 0, prints nothing, and never raises: it runs from both harnesses'
post-tool hooks (``ai/bin/report-agent-worktree``), and a hook that fails would
report a failure for a tool call that already succeeded. Malformed JSON, an
unknown action, a missing ``hc`` and any exception are all a silent 0.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import os
import sys

import pr.agent_worktree

SCRIPT = "agent-worktree"


def build_parser() -> argparse.ArgumentParser:
    """The agent-worktree parser: one positional action."""
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Publish the worktree an agent works in on its herdr terminal.",
    )
    parser.add_argument("action", choices=("report", "clear"),
                        help="report: read a hook payload on stdin; clear: empty the label")
    return parser


def _run(argv: list[str] | None) -> None:
    args = build_parser().parse_args(argv)
    env = dict(os.environ)
    if args.action == "clear":
        pr.agent_worktree.clear(env)
        return
    payload = json.loads(sys.stdin.read())
    call = pr.agent_worktree.ToolCall.from_payload(payload, env)
    pr.agent_worktree.report(call, env)


def main(argv: list[str] | None = None) -> int:
    # Fail open on everything, argparse's own exits included: a usage error
    # here is a mis-wired hook, and the tool call it follows already succeeded.
    try:
        _run(argv)
    except SystemExit:
        return 0
    except Exception:
        return 0
    return 0
