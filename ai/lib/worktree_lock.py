"""Advisory whole-run lock for worktree-mutating ``pr`` subcommands.

Two concurrent runs against one worktree corrupt each other: they both
read-modify-write ``.workbench/state.json``, and with ``--fix`` they both
edit and commit the same files. This serializes them at the process level
— a second run refuses to start rather than interleaving.

Uses ``fcntl.flock`` on ``.workbench/run.lock``. The kernel drops the lock
when the holder exits for any reason, including SIGKILL, so there is no
stale-lock state to reap.

Delegate scripts (``claude-review``, ``ci-check``, ``review-threads``) are
entry points in their own right and take the lock themselves, so a direct
invocation is guarded too. When ``pr`` launched them they inherit
``WORKBENCH_WORKTREE_LOCK`` from it and pass through as a no-op instead of
deadlocking against the lock their own parent holds.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from pathlib import Path

import log

LOCK_FILE = "run.lock"
LOCK_ENV = "WORKBENCH_WORKTREE_LOCK"

# Handles held for the lifetime of the process by claim_for_process. Kept
# only so they stay open — the kernel drops their flocks when we exit.
_HELD: list = []


class LockBusy(RuntimeError):
    """Raised when another process already holds the worktree lock."""

    def __init__(self, holder: dict, path: Path):
        self.holder = holder
        self.path = path
        pid = holder.get("pid", "?")
        command = holder.get("command", "unknown command")
        started = holder.get("started", "unknown time")
        super().__init__(
            f"another pr run already owns this worktree: "
            f"{command} (pid {pid}, started {started})"
        )


def _read_holder(path: Path) -> dict:
    """Best-effort read of the holder record written by the lock owner.

    Purely diagnostic — the flock, not this file, is what enforces
    exclusion, so an unreadable or half-written record must not stop us
    from reporting contention.
    """
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _claim(handle, path: Path, command: str, started: str) -> None:
    """Take the flock and record who owns it, or raise LockBusy."""
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise LockBusy(_read_holder(path), path) from exc
    handle.seek(0)
    handle.truncate()
    json.dump({"pid": os.getpid(), "command": command, "started": started}, handle)
    handle.flush()


def _restore_env(previous: str | None) -> None:
    """Put LOCK_ENV back the way we found it."""
    if previous is None:
        os.environ.pop(LOCK_ENV, None)
        return
    os.environ[LOCK_ENV] = previous


def _prepare(worktree_root: Path | None):
    """Open the lock file for a worktree, or return None if no lock applies."""
    root = None if worktree_root is None else Path(worktree_root)
    # A worktree that isn't on disk has no concurrent run to collide with, and
    # creating the tree here would conjure a directory the caller never had.
    if root is None or not root.is_dir():
        return None
    target = str(root.resolve())
    # Already ours: pass through rather than deadlock on our own parent.
    if os.environ.get(LOCK_ENV) == target:
        return None
    path = root / ".workbench" / LOCK_FILE
    path.parent.mkdir(exist_ok=True)
    # "a+" rather than "w": opening must not destroy the current holder's
    # record before we know whether we can take the lock away from them.
    return open(path, "a+"), path, target


@contextlib.contextmanager
def acquire(worktree_root: Path | None, command: str, started: str):
    """Hold the worktree lock for the duration of the block.

    No-ops when ``worktree_root`` is None (bare repo, nothing to protect)
    or when this process tree already holds the lock for the same path.
    Raises LockBusy if a different process holds it.
    """
    prepared = _prepare(worktree_root)
    if prepared is None:
        yield
        return

    handle, path, target = prepared
    previous = os.environ.get(LOCK_ENV)
    try:
        _claim(handle, path, command, started)
        os.environ[LOCK_ENV] = target
        yield
    finally:
        _restore_env(previous)
        # The record stays on disk: flock releases on close, and the stale
        # text is what makes the next contender's error message readable.
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def claim_for_process(worktree_root: Path | None, command: str, started: str) -> None:
    """Take the worktree lock for the rest of this process's life, or exit 1.

    For entry points whose entire body is the critical section. The kernel
    releases the flock at exit, so there is nothing to unwind — which spares
    every ``main()`` from wrapping itself in a ``with`` block just to lock.
    """
    prepared = _prepare(worktree_root)
    if prepared is None:
        return

    handle, path, target = prepared
    try:
        _claim(handle, path, command, started)
    except LockBusy as exc:
        handle.close()
        report_busy(exc)
        sys.exit(1)
    os.environ[LOCK_ENV] = target
    _HELD.append(handle)


def report_busy(exc: LockBusy) -> None:
    """Print a contention error with the remediation hint."""
    log.error(str(exc))
    log.info(f"wait for it to finish, or stop it with: kill {exc.holder.get('pid', '<pid>')}")
