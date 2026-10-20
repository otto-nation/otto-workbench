"""Tests for `git.client`'s fetch path: the per-repository mutex, the retry,
and the maintenance settings every fetch runs with.

Real repositories throughout, except where the subject is how the client
answers a particular git failure — those fake `core.proc.run`, because a
racing fetch cannot be made to print `cannot lock ref` on demand.
"""

import fcntl
import os
import re
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc  # noqa: E402
import git.client  # noqa: E402
from core.proc import CmdResult  # noqa: E402

from batch_git_support import remote_and_clone  # noqa: E402
from conftest import git_in, git_out, init_worktree  # noqa: E402

# A bound on the waits below that are expected to end on their own. Long
# enough for a loaded machine, short enough that a regression fails rather
# than hangs.
_WAIT = 30.0


def _spy(monkeypatch, answers=None):
    """Record every argv `git.client` hands `core.proc.run`.

    With *answers*, each fetch is answered from it in order instead of running;
    every other git call runs for real.
    """
    real = core.proc.run
    seen: list[list[str]] = []
    queue = list(answers or [])

    def spy(cmd, **kwargs):
        seen.append(cmd)
        if "fetch" in cmd and answers is not None:
            return queue.pop(0)
        return real(cmd, **kwargs)

    monkeypatch.setattr(git.client.core.proc, "run", spy)
    return seen


def _fetches(seen):
    return [cmd for cmd in seen if "fetch" in cmd]


def _mutex(repo: Path) -> Path:
    common = git_out(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return Path(common) / git.client.FETCH_MUTEX


class _Held:
    """`LOCK_EX` on a repository's fetch mutex, taken the way another process would."""

    def __init__(self, path: Path):
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def release(self):
        os.close(self.fd)


def _is_held(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


# ── Settings ────────────────────────────────────────────────────────────────


def test_every_fetch_runs_with_maintenance_off(tmp_path, monkeypatch):
    """A fetch's detached maintenance can take packed-refs.lock after the mutex is gone."""
    pair = remote_and_clone(tmp_path)
    seen = _spy(monkeypatch)
    assert git.client.run("fetch", "-q", "origin", cwd=pair.work).ok
    [fetch] = _fetches(seen)
    head = fetch[:fetch.index("fetch")]
    assert "maintenance.auto=false" in head
    assert "gc.auto=0" in head


def test_a_caller_setting_wins_over_the_fetch_default(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    seen = _spy(monkeypatch)
    git.client.run("fetch", "-q", "origin", cwd=pair.work, config={"gc.auto": "7"})
    [fetch] = _fetches(seen)
    assert "gc.auto=7" in fetch
    assert "gc.auto=0" not in fetch


# ── Mutex ───────────────────────────────────────────────────────────────────


def test_the_fetch_runs_while_holding_the_repositorys_mutex(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    mutex = _mutex(pair.work)
    real = core.proc.run
    held_during: list[bool] = []

    def spy(cmd, **kwargs):
        if "fetch" in cmd:
            held_during.append(_is_held(mutex))
        return real(cmd, **kwargs)

    monkeypatch.setattr(git.client.core.proc, "run", spy)
    assert git.client.run("fetch", "-q", "origin", cwd=pair.work).ok
    assert held_during == [True]
    assert not _is_held(mutex)


def test_a_linked_worktree_waits_on_the_same_mutex(tmp_path, monkeypatch):
    """Worktrees share one .git, so the mutex is the common dir's, not the worktree's."""
    pair = remote_and_clone(tmp_path)
    linked = tmp_path / "linked"
    git_in(pair.work, "worktree", "add", "-q", "-b", "other", str(linked))
    monkeypatch.setattr(git.client, "_FETCH_WAIT", 0.3)
    seen = _spy(monkeypatch)
    holder = _Held(_mutex(pair.work))
    try:
        r = git.client.run("fetch", "-q", "origin", cwd=linked)
    finally:
        holder.release()
    assert r.returncode == core.proc.TIMEOUT_RETURNCODE
    assert "waiting for another fetch" in r.stderr
    assert _fetches(seen) == []


def test_a_fetch_proceeds_once_the_holder_lets_go(tmp_path, monkeypatch):
    pair = remote_and_clone(tmp_path)
    holder = _Held(_mutex(pair.work))
    result: list[CmdResult] = []
    waiter = threading.Thread(
        target=lambda: result.append(git.client.run("fetch", "-q", "origin", cwd=pair.work)))
    waiter.start()
    waiter.join(0.3)
    assert waiter.is_alive()
    holder.release()
    waiter.join(_WAIT)
    assert result and result[0].ok


def test_outside_a_repository_git_answers_for_itself(tmp_path):
    r = git.client.run("fetch", "origin", cwd=tmp_path)
    assert not r.ok
    assert r.returncode != core.proc.TIMEOUT_RETURNCODE
    assert "not a git repository" in r.stderr
    assert not (tmp_path / git.client.FETCH_MUTEX).exists()


# ── Retry ───────────────────────────────────────────────────────────────────

_RACED = CmdResult(1, "", "error: cannot lock ref 'refs/remotes/origin/feat': "
                          "is at 1111 but expected 2222")


def test_a_fetch_that_lost_a_ref_race_is_retried_once(tmp_path, monkeypatch):
    repo = init_worktree(tmp_path / "repo")
    seen = _spy(monkeypatch, [_RACED, CmdResult(0, "", "")])
    assert git.client.run("fetch", "origin", cwd=repo).ok
    assert len(_fetches(seen)) == 2


def test_a_second_race_is_returned_rather_than_retried_again(tmp_path, monkeypatch):
    repo = init_worktree(tmp_path / "repo")
    seen = _spy(monkeypatch, [_RACED, _RACED, CmdResult(0, "", "")])
    r = git.client.run("fetch", "origin", cwd=repo)
    assert r == _RACED
    assert len(_fetches(seen)) == 2


def test_an_ordinary_fetch_failure_is_not_retried(tmp_path, monkeypatch):
    repo = init_worktree(tmp_path / "repo")
    refused = CmdResult(128, "", "fatal: couldn't find remote ref nope")
    seen = _spy(monkeypatch, [refused, CmdResult(0, "", "")])
    assert git.client.run("fetch", "origin", "nope", cwd=repo) == refused
    assert len(_fetches(seen)) == 1


@pytest.mark.parametrize("stderr", [
    "error: cannot lock ref 'refs/remotes/origin/x': is at a but expected b",
    "error: remote did not send all necessary objects",
    "fatal: remote error: upload-pack: not our ref 1234",
    "fatal: bad object refs/remotes/origin/x",
])
def test_each_race_leftover_is_recognised(stderr):
    assert git.client.FETCH_RETRYABLE.search(stderr)


def test_wt_fetch_default_retries_on_the_same_pattern():
    """The hook's bash copy of the pattern drifts silently unless held to the client's."""
    script = (REPO_ROOT / "git" / "bin" / "wt-fetch-default").read_text()
    match = re.search(r"^FETCH_RETRYABLE='([^']*)'$", script, re.MULTILINE)
    assert match, "wt-fetch-default no longer declares FETCH_RETRYABLE"
    assert match.group(1) == git.client.FETCH_RETRYABLE.pattern


# ── The race itself ─────────────────────────────────────────────────────────

# An upload-pack that forwards everything, but holds the server's first answer
# to a client request — the ref advertisement — until RELEASE exists, having
# created HOLDING. The refs are computed before the hold, so the fetch that
# goes through it acts on an advertisement that goes stale while it waits.
_STALLING_UPLOAD_PACK = """#!/usr/bin/env python3
import os, subprocess, sys, threading, time
p = subprocess.Popen(["git-upload-pack"] + sys.argv[1:], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, bufsize=0)
asked = threading.Event()
def pump():
    while True:
        b = os.read(0, 65536)
        if not b:
            p.stdin.close(); return
        asked.set(); p.stdin.write(b)
threading.Thread(target=pump, daemon=True).start()
held = False
while True:
    b = os.read(p.stdout.fileno(), 65536)
    if not b:
        break
    if asked.is_set() and not held:
        held = True
        open(os.environ["HOLDING"], "w").close()
        while not os.path.exists(os.environ["RELEASE"]):
            time.sleep(0.02)
    os.write(1, b)
sys.exit(p.wait())
"""


def test_a_stale_fetch_cannot_move_a_tracking_ref_backwards(tmp_path, monkeypatch):
    """Two fetches into one .git while origin is force-pushed between them.

    A reads origin at S1 and stalls; origin moves to S2; B fetches S2. Without
    the mutex B lands first and A then writes its stale S1 over it, exit 0 — a
    remote-tracking ref moved backwards with nothing reporting it. With the
    mutex B waits for A, so the last write is the current one.
    """
    pair = remote_and_clone(tmp_path)
    linked = tmp_path / "linked"
    git_in(pair.work, "worktree", "add", "-q", "-b", "other", str(linked))
    git_in(pair.seed, "checkout", "-q", "feat")
    tree = git_out(pair.seed, "rev-parse", "HEAD^{tree}").strip()
    s1 = git_out(pair.seed, "commit-tree", "-p", "HEAD", "-m", "S1", tree).strip()
    s2 = git_out(pair.seed, "commit-tree", "-p", "HEAD", "-m", "S2", tree).strip()
    git_in(pair.seed, "push", "-q", "-f", "origin", f"{s1}:refs/heads/feat")

    stall = tmp_path / "stalling-upload-pack"
    stall.write_text(_STALLING_UPLOAD_PACK)
    stall.chmod(0o755)
    holding, release = tmp_path / "HOLDING", tmp_path / "RELEASE"
    spec = "+refs/heads/feat:refs/remotes/origin/feat"
    env = {**os.environ, "HOLDING": str(holding), "RELEASE": str(release)}
    results: dict[str, CmdResult] = {}

    def fetch_into(key, cwd, **kwargs):
        results[key] = git.client.run("fetch", "-q", "origin", spec, cwd=cwd, **kwargs)

    a = threading.Thread(target=fetch_into, args=("a", pair.work), kwargs={
        "env": env, "config": {"remote.origin.uploadpack": str(stall)}})
    a.start()
    try:
        for _ in range(int(_WAIT / 0.02)):
            if holding.exists():
                break
            threading.Event().wait(0.02)
        assert holding.exists(), "the stalled fetch never reached its advertisement"
        git_in(pair.seed, "push", "-q", "-f", "origin", f"{s2}:refs/heads/feat")
        b = threading.Thread(target=fetch_into, args=("b", linked))
        b.start()
        # Without the mutex B finishes here, before A's stale write; with it
        # B is still queued behind A when the wait runs out.
        b.join(2.0)
    finally:
        release.touch()
    a.join(_WAIT)
    b.join(_WAIT)
    assert results["a"].ok and results["b"].ok
    assert git_out(pair.work, "rev-parse", "refs/remotes/origin/feat").strip() == s2
