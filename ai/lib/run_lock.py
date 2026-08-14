"""Advisory whole-run lock, scoped to what a run targets.

Two concurrent runs against one PR corrupt each other: they both
read-modify-write that target's ``state.json``, and with ``--fix`` they both
edit and commit the same checkout. This serializes them at the process level —
a second run refuses to start rather than interleaving.

The lock is keyed on the target, not the caller: ``pr review 2973`` from a repo
root and ``pr review --self`` from inside the PR's own worktree take the same
lock, while reviews of two different PRs launched from one directory take two.

Uses ``fcntl.flock`` on ``<target_dir>/run.lock``. The kernel drops the lock
when the holder exits for any reason, including SIGKILL, so there is no
stale-lock state to reap.

``claude-review`` (both its PR and its ``--self`` paths), ``ci-check`` and
``review-threads`` take the lock themselves, so invoking those three directly is
guarded too. When ``pr`` launched them they resolve the same target, compute the
same key, find it in ``WORKBENCH_RUN_LOCK`` and pass through as a no-op instead
of deadlocking against the lock their own parent holds.

That list is exhaustive, not an example: ``pr-rebase`` and ``pr-describe`` are
delegates that take no lock of their own, so running either directly is
unguarded and only ``pr rebase`` / ``pr describe`` serialize them.
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
LOCK_ENV = "WORKBENCH_RUN_LOCK"

# Handles held for the lifetime of the process by claim_for_process. Kept
# only so they stay open — the kernel drops their flocks when we exit.
_HELD: list = []


class LockBusy(RuntimeError):
    """Raised when another process already holds the target's lock."""

    def __init__(self, holder: dict, path: Path):
        self.holder = holder
        self.path = path
        pid = holder.get("pid", "?")
        command = holder.get("command", "unknown command")
        started = holder.get("started", "unknown time")
        super().__init__(
            f"another pr run already owns this target: "
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


def _prepare(target_dir: Path):
    """Open the target's lock file, or return None when it is already ours."""
    root = Path(target_dir)
    target = str(root)
    # Already ours: pass through rather than deadlock on our own parent.
    if os.environ.get(LOCK_ENV) == target:
        return None
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_FILE
    # "a+" rather than "w": opening must not destroy the current holder's
    # record before we know whether we can take the lock away from them.
    return open(path, "a+"), path, target


@contextlib.contextmanager
def acquire(target_dir: Path, command: str, started: str):
    """Hold the target's lock for the duration of the block.

    No-ops only when this process tree already holds the lock for the same
    target. Raises LockBusy if a different process holds it.
    """
    prepared = _prepare(target_dir)
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


def claim_for_process(target_dir: Path, command: str, started: str) -> None:
    """Take the target's lock for the rest of this process's life, or exit 1.

    For entry points whose entire body is the critical section. The kernel
    releases the flock at exit, so there is nothing to unwind — which spares
    every ``main()`` from wrapping itself in a ``with`` block just to lock.
    """
    prepared = _prepare(target_dir)
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
    log.info(
        f"wait for it to finish, or stop it with: kill {exc.holder.get('pid', '<pid>')}"
    )
