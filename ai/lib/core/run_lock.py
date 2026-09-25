"""Advisory whole-run lock, scoped to what a run targets and, when it writes
to a checkout, the checkout too.

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

Neither file is ever deleted, so a machine accumulates one ``run.lock`` per
target it has ever run against and one ``workbench-run-tree.lock`` per
checkout it has ever written to — the two counts need not match, since a
target can be worked from several checkouts and a checkout can serve several
targets. Nearly all of the accumulated files name processes that exited long
ago. That is not a leak and deleting them is not maintenance: the record is what
makes the next contender's error message name a command rather than a pid.
**The presence of a lock file says nothing about whether a lock is held** — a
dead pid in one is the normal case rather than evidence of a crash. A released
record carries a ``released`` timestamp, written under the flock just before
it is dropped; a held one has ``released: null``. To ask the kernel rather
than read the file, call ``is_held``.

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

Ownership is asked twice, in order, and the two questions differ in what they
can prove. First a process-local registry of the flocks this process itself
took: that one *is* proof — we opened the descriptor and took the flock here,
so a hit is a lock we hold and re-taking it is a no-op rather than a refusal.
Only on a miss is the env marker consulted, and it remains an exact string
match that does not prove the flock is ours. A value exported into a shell by
hand, or left behind by a run killed before its ``finally``, therefore still
reads as ownership. Proving it would mean re-probing a lock another process in
our tree holds, which fails precisely because it is held; the marker is the
only thing that can answer for that case, so it is trusted there — and only
there.

That registry is what lets one process hold locks on several targets at once.
``flock`` is not re-entrant across file descriptors, and the marker is a single
value, so a process taking a second lock would otherwise lose the name of the
first and refuse its own lock on re-entry. The marker is still written, because
it is the only channel a subprocess delegate can read, but it is derived from
the registry rather than saved and restored per call: a ``claim_for_process``
take outlives the block that was holding the value it displaced, so restoring
that value would clear a marker whose flock is still held.
"""

# doc-group: platform

from __future__ import annotations

import atexit
import contextlib
import dataclasses
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

@dataclasses.dataclass
class _Held:
    """One flock this process holds, and how many takers are counted on it.

    ``depth`` is what makes a take and its release symmetric without demanding
    that they nest. ``claim_for_process`` contributes a taker it never returns,
    so a surrounding ``acquire`` block ending cannot drop a lock the claim
    promised to hold for the life of the process.
    """

    handle: object
    path: Path
    value: str
    var: str
    depth: int = 1


# The flocks this process holds, keyed on the lock file's path and in the order
# they were taken. Keyed on the path rather than the marker value because the
# path is the thing flock is taken on, so "is this already ours" and "is this
# key present" are one question — and because it keeps a target dir and a git
# dir that happen to be the same directory as two distinct locks.
_HELD: dict[str, _Held] = {}

# What each marker said before this process took its first lock for that var,
# so releasing the last one hands back what a parent exported rather than
# clearing it.
_INHERITED: dict[str, str | None] = {}

_ATEXIT_REGISTERED = False


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


@dataclasses.dataclass(frozen=True)
class _LockSpec:
    """Which flock to take, and what to call it when it is refused."""

    value: str
    path: Path
    var: str
    subject: str


def _target_spec(target_dir: Path) -> _LockSpec:
    """Name the target's lock. Resolution only — nothing is opened here."""
    root = Path(target_dir)
    return _LockSpec(str(root), root / LOCK_FILE, LOCK_ENV, "target")


def _sync_marker(var: str) -> None:
    """Point *var* at the innermost lock this process still holds for it.

    Derived rather than saved-and-restored per call, because
    ``claim_for_process`` takes a lock it never gives back: a take can outlive
    the block that was holding the value it displaced, and restoring that value
    would clear a marker whose flock is still held.
    """
    # ceiling: the marker stays a single value, so a subprocess sees only the
    # innermost lock this process holds for that var. Upgrade to a separated
    # multi-value marker if a lock-taking delegate is ever spawned from a point
    # where this process holds more than one key for one var — today the five
    # that lock are spawned only by `pr`'s dispatch and by `cmd_fix`, which
    # resolve a single target and forward it.
    for held in reversed(list(_HELD.values())):
        if held.var == var:
            os.environ[var] = held.value
            return
    _restore_env(_INHERITED.pop(var, None), var)


def _register_atexit() -> None:
    """Arrange for the exit walker to run, once however many locks we take."""
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    atexit.register(_release_all)
    _ATEXIT_REGISTERED = True


def _take(spec: _LockSpec, command: str, started: str) -> bool:
    """Take one lock, and say whether this call owes a matching ``_drop``.

    Ownership is asked registry-first and marker-second, and the order is the
    correctness argument: the registry is proof that the flock is ours, while
    the marker is hearsay that a stale or hand-exported value can forge. Asking
    the registry first means such a value can no longer shadow a real entry.

    Returns False only for the marker pass-through — a lock some other process
    in our tree holds, which is not ours to release.
    """
    key = str(spec.path)
    held = _HELD.get(key)
    if held is not None:
        held.depth += 1
        return True
    if os.environ.get(spec.var) == spec.value:
        return False
    spec.path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" rather than "w": opening must not destroy the current holder's
    # record before we know whether we can take the lock away from them.
    handle = open(spec.path, "a+")
    try:
        _claim(handle, spec.path, command, started, spec.subject)
    except BaseException:
        # Closed without a release stamp: we never held this flock, and
        # annotating it would write "released" onto the live holder's record.
        handle.close()
        raise
    _INHERITED.setdefault(spec.var, os.environ.get(spec.var))
    _HELD[key] = _Held(handle, spec.path, spec.value, spec.var)
    _register_atexit()
    _sync_marker(spec.var)
    return True


def _drop(key: str) -> None:
    """Return one taker's hold, releasing the flock when it was the last."""
    held = _HELD.get(key)
    if held is None:
        return
    held.depth -= 1
    if held.depth > 0:
        return
    del _HELD[key]
    _sync_marker(held.var)
    # The record stays on disk: flock releases on close, and the text is what
    # makes the next contender's error message readable. Stamped as released
    # first, so what stays is not mistaken for a live holder by anyone reading
    # the file later.
    _note_release(held.handle, held.path)
    fcntl.flock(held.handle, fcntl.LOCK_UN)
    held.handle.close()


def _release_all() -> None:
    """Stamp and drop every lock this process still holds, innermost first.

    Reverse of the order they were taken, mirroring the target-then-checkout
    order ``acquire`` nests them in. Best-effort per entry: one bad descriptor
    must not strand the records of the locks beside it.
    """
    global _ATEXIT_REGISTERED
    for key in reversed(list(_HELD)):
        held = _HELD.get(key)
        if held is not None:
            held.depth = 1
        with contextlib.suppress(OSError, ValueError):
            _drop(key)
    _HELD.clear()
    atexit.unregister(_release_all)
    _ATEXIT_REGISTERED = False


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


def _tree_spec(worktree: Path | None) -> _LockSpec | None:
    """Name the checkout's lock, or None when there is nothing to lock.

    None covers the two cases that mean there is no checkout lock to take — no
    worktree was named, or git cannot answer for the path. A run whose worktree
    cannot be resolved keeps the target lock alone, which is what it had before
    this lock existed. Whether we already hold it is a different question and
    ``_take`` answers it.
    """
    if worktree is None:
        return None
    git_dir = _git_dir(Path(worktree))
    if git_dir is None:
        return None
    return _LockSpec(
        str(git_dir), git_dir / TREE_LOCK_FILE, TREE_LOCK_ENV, "checkout",
    )


@contextlib.contextmanager
def _holding(spec: _LockSpec | None, command: str, started: str):
    """Hold one lock for the block, returning our taker however it ends."""
    if spec is None:
        yield
        return
    mine = _take(spec, command, started)
    try:
        yield
    finally:
        if mine:
            _drop(str(spec.path))


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
    with _holding(_target_spec(target_dir), command, started):
        with _holding(_tree_spec(worktree), command, started):
            yield


def claim_for_process(target_dir: Path, command: str, started: str, *,
                      worktree: Path | None = None) -> None:
    """Take the locks for the rest of this process's life, or exit 1.

    For entry points whose entire body is the critical section. The kernel
    releases the flocks at exit, so there is nothing to unwind for the lock
    itself — which spares every ``main()`` from wrapping itself in a ``with``
    block just to lock. The record on disk is a separate concern: nothing
    stamps it as released just because the flock is gone, so an ``atexit``
    hook does that part explicitly, the same way ``_holding`` does for
    ``acquire``'s context-manager path.

    Re-claiming a lock this process already holds is a no-op, and claiming a
    second target does not disturb the first. A caller that wants its lock
    released at the end of a phase rather than at exit wants ``acquire``.

    ``worktree`` is the checkout this run writes to; see ``acquire``.
    """
    for spec in (_target_spec(target_dir), _tree_spec(worktree)):
        if spec is None:
            continue
        try:
            # The taker is deliberately never returned: holding for the life of
            # the process is this function's whole contract, and the uncounted
            # depth is what stops an enclosing ``acquire`` block from dropping
            # the flock when it ends.
            _take(spec, command, started)
        except LockBusy as exc:
            report_busy(exc)
            sys.exit(1)


def is_held(target_dir: Path) -> bool:
    """Whether a run currently holds *target_dir*'s lock.

    The only honest way to ask. A ``run.lock`` on disk says nothing — the file
    is never removed, so a machine accumulates one per target ever used (and a
    ``workbench-run-tree.lock`` per checkout ever written to) and almost all of
    them name processes that exited long ago. Probing for the
    exclusive lock is what distinguishes them: it fails precisely when someone
    holds it.

    Deliberately ignores the env markers *and* the process-local registry: a
    caller inside a holder's own process tree is asking about the lock, not
    about its own ancestry, and the registry is only a more reliable form of
    the same ancestry. Consulting it would add no correct answer either —
    ``flock`` conflicts across open file descriptions, so probing from a second
    descriptor already reports our own lock as held.
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
