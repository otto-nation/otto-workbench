"""Advisory lock declaring that a tree is being validated.

Editing a working tree while a gate validates it silently invalidates the run:
the job reports against a tree that no longer exists, and nothing in its output
says so. A green gate is exactly as green when its inputs changed underneath it.

Rather than have every consumer infer which background jobs are validating —
matching command strings against a job registry no harness reliably exposes —
the validator declares itself here and anything that wants to know reads one
file.

Shared, not exclusive: several validators legitimately run over one tree
(``pre-push`` calls ``validate-all`` then ``run-tests``), so they hold
``LOCK_SH`` and coexist. A reader asks "is anyone validating?" by probing for
``LOCK_EX``, which fails exactly when at least one holder exists.

Uses ``fcntl.flock`` on ``<git-dir>/workbench-validate.lock``. The kernel drops
the lock when the holder exits for any reason, including SIGKILL, so there is
no stale-lock state to reap. That is the whole reason for flock over a pid
file: a crash under a pid file leaves every edit in the worktree blocked until
someone finds and deletes it, which is worse than the bug this prevents.

Distinct from ``run_lock.py``: that one is exclusive, keyed on an arbitrary
target directory, and serializes ``pr`` runs. This one is shared, keyed on a
git worktree, and serializes nothing — it only publishes a fact.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
from pathlib import Path

LOCK_FILE = "workbench-validate.lock"
LOCK_ENV = "WORKBENCH_TREE_LOCK"

# Handles held for the lifetime of the process. Kept only so they stay open —
# the kernel drops their flocks when we exit.
_HELD: list = []


def _git_dir(tree_root: Path) -> Path | None:
    """The worktree's private git dir, or None outside a repo.

    Asked of git rather than assembled as ``<root>/.git``: in a linked worktree
    that path is a file pointing at ``<bare>/worktrees/<name>``, and writing a
    lock there would put every worktree's lock in one place.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(tree_root), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    path = out.stdout.strip()
    return Path(path) if path else None


def lock_path(tree_root: Path) -> Path | None:
    """Where this worktree's lock lives, or None outside a repo."""
    git_dir = _git_dir(Path(tree_root))
    return None if git_dir is None else git_dir / LOCK_FILE


def holders(tree_root: Path) -> list[dict]:
    """Best-effort read of the records written by current holders.

    Purely diagnostic — the flock, not this file, is what says whether the tree
    is being validated, so an unreadable or half-written record must not stop
    us from reporting. Malformed lines are skipped rather than raising.
    """
    path = lock_path(tree_root)
    if path is None:
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            found.append(record)
    return found


def is_locked(tree_root: Path) -> bool:
    """Whether any validator currently holds this tree.

    Probes for the exclusive lock, which fails precisely when at least one
    shared holder exists — so a failed probe means locked. Getting that sense
    backwards makes the guard silently useless rather than noisy, since both
    branches then answer "free"; there is a test for each direction below.

    Deliberately ignores LOCK_ENV: a reader running inside a validator's own
    process tree is exactly the case we must still refuse, so trusting an
    inherited marker would pass through when it matters most.
    """
    path = lock_path(tree_root)
    if path is None or not path.exists():
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


def _alive(pid) -> bool:
    """Whether a recorded pid still exists.

    Only ever used to prune diagnostics. The flock remains the verdict, so a
    pid recycled onto an unrelated process costs at worst a misleading name in
    an error message, never a wrong allow or refuse.
    """
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _record(handle, command: str, started: str, tree_root: Path) -> None:
    """Add this holder's line, dropping any left by dead ones.

    Pruning belongs here rather than in a reader: this runs while holding the
    lock, and a reader's only chance to prove the file stale is the moment it
    finds no holders at all — which, on the path that matters, is never. Left
    unpruned the file grows without bound and a blocked edit names processes
    that exited days ago.
    """
    handle.seek(0)
    try:
        existing = handle.read()
    except OSError:
        existing = ""
    kept = []
    for line in existing.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and _alive(record.get("pid")):
            kept.append(record)
    kept.append(
        {
            "pid": os.getpid(),
            "command": command,
            "started": started,
            "tree_root": str(Path(tree_root).resolve()),
        }
    )
    handle.seek(0)
    handle.truncate()
    for record in kept:
        handle.write(json.dumps(record) + "\n")
    handle.flush()


@contextlib.contextmanager
def acquire(tree_root: Path, command: str, started: str):
    """Declare this tree under validation for the duration of the block.

    No-ops when this process tree already declared the same tree, so a nested
    validator does not add a second record for one run. Never raises on
    contention: shared holders coexist by design.
    """
    root = Path(tree_root).resolve()
    target = str(root)
    if os.environ.get(LOCK_ENV) == target:
        yield
        return

    path = lock_path(root)
    if path is None:
        # Not a git repo: nothing to declare, and refusing to run the suite
        # over a plain directory would be a regression for no safety gain.
        yield
        return

    # "a+" rather than "w": opening must not destroy a co-holder's record.
    handle = open(path, "a+")
    previous = os.environ.get(LOCK_ENV)
    try:
        # A successful LOCK_EX probe proves there are no holders, so any
        # leftover lines are stale — including this process's own prior runs,
        # whose pid is still alive and would survive an _alive prune.
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
        except BlockingIOError:
            pass
        fcntl.flock(handle, fcntl.LOCK_SH)
        _record(handle, command, started, root)
        os.environ[LOCK_ENV] = target
        yield
    finally:
        if previous is None:
            os.environ.pop(LOCK_ENV, None)
        else:
            os.environ[LOCK_ENV] = previous
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
