"""Helpers for the suites that hold, probe or contend for a run lock.

Shared rather than restated: holding a lock "from another run" and asking
whether one is held both need a descriptor the code under test's registry
knows nothing about, and the one subtle fact — flock conflicts across open
file descriptions inside a single process too — is said once, here.
"""

import fcntl
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import run_checked, seed_repo  # noqa: E402


@contextmanager
def held_elsewhere(path, record):
    """Hold *path*'s flock, with *record* as its holder, on a descriptor of our own.

    To the code under test this is another run holding the lock: its registry
    has no entry for it and no marker names it, so only the kernel can answer.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        json.dump(record, handle)
        handle.flush()
        yield


@contextmanager
def live_pid():
    """A pid that is alive and not this process, then reaped.

    Holder records that should classify as a live foreign holder need a pid
    ``os.kill(pid, 0)`` accepts and that is not ``os.getpid()`` — this
    process's own pid is treated as vacated (see ``is_live(allow_self=False)``).
    """
    proc = subprocess.Popen(["sleep", "30"])
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


def stamp_pid(path, pid: int) -> None:
    """Set the holder record's pid, leaving every other field and the flock."""
    path = Path(path)
    record = json.loads(path.read_text())
    record["pid"] = pid
    path.write_text(json.dumps(record))


def flocked(path) -> bool:
    """Whether anyone holds *path*'s flock, asked from a fresh descriptor."""
    path = Path(path)
    if not path.exists():
        return False
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def git_dir(worktree) -> Path:
    """The checkout's own git dir, which is where its checkout lock lives."""
    out = run_checked(["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"])
    return Path(out.stdout.strip())


def repo_with_origin(path) -> Path:
    """A one-commit repo whose `origin` names a GitHub repo, so `pr` can key a target on it."""
    seed_repo(path)
    run_checked(["git", "-C", str(path), "remote", "add", "origin",
                 "https://github.com/acme/widget.git"])
    return Path(path)
