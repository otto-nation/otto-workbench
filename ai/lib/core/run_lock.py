"""Advisory whole-run lock, scoped to what a run targets.

Two concurrent runs against one PR corrupt each other: they both
read-modify-write that target's ``state.json``, and with ``--fix`` they both
edit and commit the same checkout. This serializes them at the process level —
a second run refuses to start rather than interleaving.

The lock is keyed on the target, not the caller: ``pr review 2973`` from a repo
root and ``pr review --self`` from inside the PR's own worktree take the same
lock, while reviews of two different PRs launched from one directory take two.

That key is ``(origin repo key, branch)`` and says nothing about *where* a run
writes. Two runs can name different branches and mutate one checkout: a ``--pr``
run keys on the branch GitHub reports for the PR, while the worktree it was
launched in stands on whatever it stands on, and ``pr_context`` only relocates a
run to a branch's own worktree when it was given ``--branch``. Both runs then
take different target locks, both succeed, and both edit and commit in the same
tree.

So a run that writes to a checkout locks that too, keyed on the checkout's own
``--absolute-git-dir`` — per linked worktree, not per repo. The two locks answer
different questions and neither implies the other: the target lock asks whether
this PR is being worked on, the checkout lock whether this working tree is being
written to. Both are non-blocking and always taken target-first, so a pair
cannot deadlock against another pair.

A caller that writes to no checkout, or whose worktree git cannot resolve, takes
the target lock alone.

Uses ``fcntl.flock`` on ``<target_dir>/run.lock`` and on
``<git-dir>/workbench-run-tree.lock``. The kernel drops both when the holder
exits for any reason, including SIGKILL, so there is no stale-lock state to
reap.

Neither file is ever deleted, so a machine accumulates one per target it has
ever run against and nearly all of them name processes that exited long ago.
That is not a leak and deleting them is not maintenance: the record is what
makes the next contender's error message name a command rather than a pid. But
it does mean **the presence of a lock file says nothing about whether a lock is
held**, and a dead pid in one is the normal case rather than evidence of a
crash. A released record carries a ``released`` timestamp, written under the
flock just before it is dropped; a held one has ``released: null``. To ask the
kernel rather than read the file, call ``is_held``.

``claude-review`` (both its PR and its ``--self`` paths), ``ci-check``,
``review-threads``, ``pr-rebase`` and ``pr-describe`` take the lock themselves,
so invoking any of them directly is guarded too. When ``pr`` launched them they
resolve the same target, compute the same key, find it in
``WORKBENCH_RUN_LOCK`` and pass through as a no-op instead of deadlocking
against the lock their own parent holds.

That list is exhaustive, not an example. ``review-post`` and ``review-rebuild``
are the remaining delegates and take no lock of their own, for a reason that is
not laziness: neither resolves a repo context. Both work from a review
directory and the ``meta.json`` beside it, whose ``repo`` is a ``owner/repo``
slug rather than a remote URL — and the two do not key alike, because
``pr.target`` reduces a local remote to its trailing path segment. A lock keyed
from the sidecar would name a directory no other run uses, which is worse than
no lock: it would report success while excluding nobody. They run under a
``pr review`` that holds the real lock across the subprocess, and a direct
invocation of either is undocumented.

The pass-through is an exact string match on the target and does not prove the
flock is ours. A value exported into a shell by hand, or left behind by a run
killed before its ``finally``, therefore reads as ownership. Proving it would
mean re-probing a lock we already hold, which fails precisely because we hold
it; the marker is the only thing that can answer, so it is trusted.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import log
from core import timeouts

LOCK_FILE = "run.lock"
LOCK_ENV = "WORKBENCH_RUN_LOCK"

# The checkout's own lock, and its marker. Separate from the target's because
# they answer different questions: the target lock asks "is this PR being
# worked on", this one asks "is this working tree being written to". A run
# keyed on one branch can mutate a checkout standing on another, so neither
# implies the other.
TREE_LOCK_FILE = "workbench-run-tree.lock"
TREE_LOCK_ENV = "WORKBENCH_RUN_LOCK_TREE"

# Handles held for the lifetime of the process by claim_for_process. Kept
# only so they stay open — the kernel drops their flocks when we exit.
_HELD: list = []


class LockBusy(RuntimeError):
    """Raised when another process already holds one of the two locks.

    ``subject`` names which, because the remedies differ: a contended target is
    the same work twice and the answer is to wait, while a contended checkout is
    two different targets in one working tree and the answer may be to run the
    second somewhere else.
    """

    def __init__(self, holder: dict, path: Path, subject: str = "target"):
        self.holder = holder
        self.path = path
        self.subject = subject
        pid = holder.get("pid", "?")
        command = holder.get("command", "unknown command")
        started = holder.get("started", "unknown time")
        super().__init__(
            f"another pr run already owns this {subject}: "
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


def _claim(handle, path: Path, command: str, started: str,
           subject: str = "target") -> None:
    """Take the flock and record who owns it, or raise LockBusy."""
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise LockBusy(_read_holder(path), path, subject) from exc
    _write_record(handle, {
        "pid": os.getpid(), "command": command, "started": started,
        # Overwritten with a timestamp on release. Present and null while held,
        # so a reader can tell "still running" from "finished" — rather than
        # having to guess from a pid that is dead either way.
        "released": None,
    })


def _write_record(handle, record: dict) -> None:
    """Replace the holder record under the flock we hold."""
    handle.seek(0)
    handle.truncate()
    json.dump(record, handle)
    handle.flush()


def _note_release(handle, path: Path) -> None:
    """Stamp the record as released, before the flock is dropped.

    Before, not after, and that ordering is the whole correctness argument: we
    still hold the lock, so no other process can be writing this file, and the
    stamp cannot land on top of the next holder's record.

    Best-effort. Failing to annotate a lock we are done with must not turn a
    finished run into a failed one.
    """
    record = _read_holder(path)
    if not record:
        return
    record["released"] = _now()
    try:
        _write_record(handle, record)
    except (OSError, ValueError):
        pass


def _now() -> str:
    """An ISO timestamp, resolved here so core.run_lock owes pr.state nothing."""
    return datetime.now(timezone.utc).isoformat()


def _restore_env(previous: str | None, var: str = LOCK_ENV) -> None:
    """Put *var* back the way we found it."""
    if previous is None:
        os.environ.pop(var, None)
        return
    os.environ[var] = previous


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


def _git_dir(worktree: Path) -> Path | None:
    """The checkout's private git dir, or None when git cannot name one.

    Asked of git rather than assembled as ``<root>/.git``: in a linked worktree
    that path is a file pointing at ``<bare>/worktrees/<name>``, and locking it
    would put every worktree of a repo behind one lock.

    GIT_DIR and GIT_WORK_TREE are stripped for the child. They skip discovery,
    so with either set ``git -C`` answers the caller's repo rather than the one
    named on the command line — a hook exports both, and a run launched from
    one would otherwise lock the hook's repo instead of its own.
    """
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    try:
        out = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, check=True,
            timeout=timeouts.LOCAL, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = out.stdout.strip()
    return Path(path) if path else None


def _prepare_tree(worktree: Path | None):
    """Open the checkout's lock file, or None when there is nothing to lock.

    None covers three cases that all mean the same thing here — no worktree was
    named, git cannot answer for the path, or this process tree already holds
    that checkout. A run whose worktree cannot be resolved keeps the target
    lock alone, which is what it had before this lock existed.
    """
    if worktree is None:
        return None
    git_dir = _git_dir(Path(worktree))
    if git_dir is None:
        return None
    tree = str(git_dir)
    if os.environ.get(TREE_LOCK_ENV) == tree:
        return None
    return open(git_dir / TREE_LOCK_FILE, "a+"), git_dir / TREE_LOCK_FILE, tree


@contextlib.contextmanager
def _holding(prepared, command: str, started: str, var: str,
             subject: str = "target"):
    """Hold one prepared lock, restoring *var* however the block ends."""
    if prepared is None:
        yield
        return
    handle, path, value = prepared
    previous = os.environ.get(var)
    try:
        _claim(handle, path, command, started, subject)
        os.environ[var] = value
        yield
    finally:
        _restore_env(previous, var)
        # The record stays on disk: flock releases on close, and the text is
        # what makes the next contender's error message readable. Stamped as
        # released first, so what stays is not mistaken for a live holder by
        # anyone reading the file later.
        _note_release(handle, path)
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


@contextlib.contextmanager
def acquire(target_dir: Path, command: str, started: str, *,
            worktree: Path | None = None):
    """Hold the target's lock, and the checkout's, for the block's duration.

    No-ops only when this process tree already holds the lock for the same
    target. Raises LockBusy if a different process holds it.

    ``worktree`` names the checkout the run will write to, and is locked
    alongside the target. Two runs can target different branches and mutate one
    checkout — a PR run keys on the GitHub head ref while the worktree stands on
    whatever it stands on — and the target lock alone lets both proceed. Omit it
    for a run that writes to no checkout.

    Target first, then checkout, always in that order and both non-blocking, so
    two runs taking the pair cannot deadlock against each other.
    """
    with _holding(_prepare(target_dir), command, started, LOCK_ENV):
        with _holding(_prepare_tree(worktree), command, started,
                      TREE_LOCK_ENV, "checkout"):
            yield


def claim_for_process(target_dir: Path, command: str, started: str, *,
                      worktree: Path | None = None) -> None:
    """Take the locks for the rest of this process's life, or exit 1.

    For entry points whose entire body is the critical section. The kernel
    releases the flocks at exit, so there is nothing to unwind — which spares
    every ``main()`` from wrapping itself in a ``with`` block just to lock.

    ``worktree`` is the checkout this run writes to; see ``acquire``.
    """
    for prepared, var, subject in (
        (_prepare(target_dir), LOCK_ENV, "target"),
        (_prepare_tree(worktree), TREE_LOCK_ENV, "checkout"),
    ):
        if prepared is None:
            continue
        handle, path, value = prepared
        try:
            _claim(handle, path, command, started, subject)
        except LockBusy as exc:
            handle.close()
            report_busy(exc)
            sys.exit(1)
        os.environ[var] = value
        _HELD.append(handle)


def is_held(target_dir: Path) -> bool:
    """Whether a run currently holds *target_dir*'s lock.

    The only honest way to ask. A ``run.lock`` on disk says nothing — the file
    is never removed, so a machine accumulates one per target ever used and
    almost all of them name processes that exited long ago. Probing for the
    exclusive lock is what distinguishes them: it fails precisely when someone
    holds it.

    Deliberately ignores the env markers: a caller inside a holder's own process
    tree is asking about the lock, not about its own ancestry.
    """
    path = Path(target_dir) / LOCK_FILE
    if not path.exists():
        return False
    try:
        handle = open(path, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def report_busy(exc: LockBusy) -> None:
    """Print a contention error with the remediation hint."""
    log.error(str(exc))
    if exc.subject == "checkout":
        # Not the same work twice: another target is being written to in this
        # tree, so waiting is one answer and running elsewhere is the other.
        log.info("that run is writing to this working tree — wait for it, run "
                 "this from the branch's own worktree, or stop it with: "
                 f"kill {exc.holder.get('pid', '<pid>')}")
        return
    log.info(
        f"wait for it to finish, or stop it with: kill {exc.holder.get('pid', '<pid>')}"
    )
