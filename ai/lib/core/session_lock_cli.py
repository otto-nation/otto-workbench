"""Command-line face of the interactive session lock.

Separate from session_lock.py so the library stays importable without argparse
ceremony, and so each harness's hook has one file to invoke.

Unlike ``tree_lock_cli``, nothing here wraps a child process. That module's
lock is a flock, which lives only while a process holds the descriptor, so the
work has to run underneath it. This one records a pid and returns — which is
the whole reason a record was chosen: the harness process is the holder, and a
hook that exits immediately can still name it. See ``session_lock``'s header.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import session_lock


def _check(worktree: Path) -> int:
    """Report holders. Exit 0 when a session is editing, 1 when free.

    Reports every live holder, not only foreign ones: this is the operator's
    window onto the record, and a caller asking whether a tree is being edited
    wants the answer including its own session. The fix pass's own exemption
    is `session_lock.held_by_others`, not this.
    """
    held = session_lock.holders(worktree)
    if not held:
        print(f"{worktree}: no interactive session")
        return 1
    print(f"{worktree}: being edited")
    for holder in held:
        print(f"  {holder.describe()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="with-session-lock",
        description="Record that an interactive session is editing a worktree.",
    )
    parser.add_argument("worktree", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--acquire", action="store_true")
    group.add_argument("--release", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument(
        "--pid",
        type=int,
        help="the harness process to record; defaults to this process's parent",
    )
    parser.add_argument("--harness", default="unknown")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--command", default="")
    args = parser.parse_args(argv)

    worktree = args.worktree
    if args.check:
        return _check(worktree)

    # The parent is the right default for a hook: the hook process itself dies
    # immediately, and recording it would leave a record that is dead before
    # anything reads it.
    pid = args.pid if args.pid is not None else os.getppid()

    if args.release:
        session_lock.release(worktree, pid)
        return 0

    ok = session_lock.acquire(
        worktree,
        pid=pid,
        harness=args.harness,
        session_id=args.session_id,
        command=args.command,
    )
    # Failing open on purpose. A session that cannot record itself is a
    # session that goes unprotected, which is today's behaviour; a hook that
    # exits non-zero is a harness that reports an error at every start.
    if not ok:
        print(
            f"could not record a session lock for {worktree}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
