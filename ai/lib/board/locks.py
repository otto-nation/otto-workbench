"""Whether something is working on a PR right now, in the words its badge shows.

``lock_of(repo_key, branch, worktree)`` is the board's one answer. A live ``pr``
run on the branch's target (``core.run_lock.live_holder`` over
``pr.target.target_dir``) wins and reads ``Locked · pr <subcommand>``; else a
live interactive session in the branch's worktree (``core.session_lock.holders``)
reads ``Locked · <Pi|Claude> session``; else None. Only the first two words of
the run's recorded command are shown: the full argv carries paths and body text.
"""

# doc-group: board

from __future__ import annotations

from pathlib import Path

import core.run_lock
import core.session_lock
import pr.target

PREFIX = "Locked · "

_HARNESS_NAMES = {"pi": "Pi", "claude": "Claude"}


def lock_of(repo_key: str, branch: str, worktree: str | None) -> str | None:
    """The lock label for the PR on *branch*, or None when nothing holds it."""
    # ceiling: one flock probe per open row, plus git rev-parse and ps per row
    # with a worktree; upgrade to a batched probe if listing latency regresses
    # on large boards.
    if branch:
        holder = core.run_lock.live_holder(pr.target.target_dir(repo_key, branch))
        if holder is not None:
            return PREFIX + _subcommand(holder)
    if worktree:
        held = core.session_lock.holders(Path(worktree))
        if held:
            harness = held[0].harness
            return f"{PREFIX}{_HARNESS_NAMES.get(harness, harness)} session"
    return None


def _subcommand(holder: dict) -> str:
    words = str(holder.get("command") or "").split()[:2]
    return " ".join(words) or "pr"
