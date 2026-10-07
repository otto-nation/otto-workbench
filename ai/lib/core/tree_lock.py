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
target directory, and serializes ``pr`` runs. Distinct from ``job_slots.py``:
that one is counted, keyed on the machine, and hands out test parallelism.
This one is shared, keyed on a git worktree, and serializes nothing — it only
publishes a fact.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import enum
import fcntl
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import core.timeouts

LOCK_FILE = "workbench-validate.lock"
LOCK_ENV = "WORKBENCH_TREE_LOCK"

# What git rev-parse -C says (under LC_ALL=C) when there is no repository to
# ask about: outside one, or a -C directory that does not exist. The Pi twin
# in ai/pi/extensions/tree-lock-guard/detect.ts matches the same two.
_NOT_A_REPO_MARKERS = ("not a git repository", "cannot change to")


class LockState(enum.Enum):
    """What a probe learned about a tree's validation lock."""

    HELD = "held"
    FREE = "free"
    # The probe could not answer — git hung, would not start or failed, or the
    # lock file would not open or lock. Not the same as FREE: a reader that
    # fails open must still be able to say it did, or a load flake reads as
    # "nobody validating".
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LockProbe:
    """A probe's verdict, with the reason when it is UNKNOWN."""

    state: LockState
    reason: str = ""

    @property
    def held(self) -> bool:
        return self.state is LockState.HELD


@dataclass(frozen=True)
class _GitDirLookup:
    """The git dir, or why git could not be asked. Both empty: not a repo."""

    path: Path | None
    error: str = ""


def _git_dir(tree_root: Path) -> Path | None:
    """The worktree's private git dir, or None outside a repo.

    Asked of git rather than assembled as ``<root>/.git``: in a linked worktree
    that path is a file pointing at ``<bare>/worktrees/<name>``, and writing a
    lock there would put every worktree's lock in one place.

    None also when git could not be asked at all; ``probe`` is the reader that
    needs those two told apart, and goes through ``_lookup_git_dir`` for it.
    """
    return _lookup_git_dir(tree_root).path


def _lookup_git_dir(tree_root: Path) -> _GitDirLookup:
    """``_git_dir``, keeping "not a repo" apart from "git did not answer"."""
    # GIT_DIR / GIT_WORK_TREE skip discovery: git -C then answers the caller's
    # repo, not tree_root. Hooks export both; strip them so a reader inside a
    # hook still resolves the tree it was asked about.
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    # git localises its messages, and the not-a-repo check below reads one.
    env["LC_ALL"] = "C"
    try:
        out = subprocess.run(
            ["git", "-C", str(tree_root), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=True,
            timeout=core.timeouts.LOCAL,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        # git exits 128 for every fatal error, so the status alone cannot say
        # "not a repo". Only that message, or a directory that is gone, is an
        # answer; dubious ownership, a corrupt repo or an unreadable config is
        # git failing to answer, and must not read as a tree nobody validates.
        stderr = (exc.stderr or "").strip()
        if any(marker in stderr for marker in _NOT_A_REPO_MARKERS):
            return _GitDirLookup(path=None)
        return _GitDirLookup(path=None, error=f"git rev-parse exited {exc.returncode}: {stderr}")
    except subprocess.TimeoutExpired:
        return _GitDirLookup(path=None, error=f"git rev-parse timed out after {core.timeouts.LOCAL}s")
    except (OSError, subprocess.SubprocessError) as exc:
        return _GitDirLookup(path=None, error=f"git rev-parse could not run: {exc}")
    path = out.stdout.strip()
    return _GitDirLookup(path=Path(path) if path else None)


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

    A probe that could not answer is False here — every reader fails open. Use
    ``probe`` to tell that apart from a tree nobody is validating.
    """
    return probe(tree_root).held


def probe(tree_root: Path) -> LockProbe:
    """Whether a validator holds this tree: HELD, FREE, or UNKNOWN with a reason.

    FREE outside a repo and when no validator has ever taken the lock; UNKNOWN
    when git or the lock file could not be asked. Only git saying "not a
    repository" counts as outside one — any other git failure is UNKNOWN.
    """
    lookup = _lookup_git_dir(Path(tree_root))
    if lookup.error:
        return LockProbe(LockState.UNKNOWN, lookup.error)
    if lookup.path is None:
        return LockProbe(LockState.FREE)
    path = lookup.path / LOCK_FILE
    # stat before open: "a+" would create the file, and a probe must not.
    try:
        path.stat()
    except FileNotFoundError:
        return LockProbe(LockState.FREE)
    except OSError as exc:
        return LockProbe(LockState.UNKNOWN, f"could not stat {path}: {exc}")
    try:
        handle = open(path, "a+")
    except OSError as exc:
        return LockProbe(LockState.UNKNOWN, f"could not open {path}: {exc}")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return LockProbe(LockState.HELD)
    except OSError as exc:
        # ENOLCK, EBADF and the like: flock failed, which is not "held".
        return LockProbe(LockState.UNKNOWN, f"could not flock {path}: {exc}")
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return LockProbe(LockState.FREE)
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


def _restore_lock_env(previous: str | None) -> None:
    if previous is None:
        os.environ.pop(LOCK_ENV, None)
    else:
        os.environ[LOCK_ENV] = previous


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
        # Still set LOCK_ENV so writers that re-exec under this wrapper stop
        # rather than looping: lock_path is None whenever git cannot answer
        # (no .git, dubious ownership, a moved bare repo), not only "not a
        # repo", and those writers key their re-exec on the marker being set.
        previous = os.environ.get(LOCK_ENV)
        os.environ[LOCK_ENV] = target
        try:
            yield
        finally:
            _restore_lock_env(previous)
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
        _restore_lock_env(previous)
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
