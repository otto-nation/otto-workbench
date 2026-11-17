"""board.locks — the one answer to "is something working on this PR"."""

import fcntl
import json
import subprocess
from pathlib import Path

import pytest

import board.locks
import core.run_lock
import core.session_lock
import pr.target


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


@pytest.fixture
def held_run():
    """Hold a target's run.lock with COMMAND recorded; yields a starter."""
    handles = []

    def hold(repo_key: str, branch: str, command: str):
        target = pr.target.target_dir(repo_key, branch)
        target.mkdir(parents=True, exist_ok=True)
        lock = target / core.run_lock.LOCK_FILE
        lock.write_text(json.dumps({"pid": 1, "command": command, "started": "x", "released": None}))
        handle = open(lock, "a+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handles.append(handle)

    yield hold
    for handle in handles:
        handle.close()


@pytest.fixture
def sleeper():
    procs = []

    def spawn():
        proc = subprocess.Popen(["sleep", "300"])
        procs.append(proc)
        return proc

    yield spawn
    for proc in procs:
        proc.kill()
        proc.wait()


def test_nothing_held_is_none(worktree):
    assert board.locks.lock_of("k", "feat/a", str(worktree)) is None


def test_a_held_run_is_labelled_by_its_subcommand_only(held_run, worktree):
    held_run("k", "feat/a", "pr review --repo-dir /Users/me/secret 7")
    assert board.locks.lock_of("k", "feat/a", str(worktree)) == "Locked · pr review"
    assert board.locks.lock_of("k", "feat/a", None) == "Locked · pr review"


def test_an_unreadable_held_record_falls_back_to_pr(worktree):
    target = pr.target.target_dir("k", "feat/u")
    target.mkdir(parents=True, exist_ok=True)
    lock = target / core.run_lock.LOCK_FILE
    lock.write_text("not json")
    handle = open(lock, "a+")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert board.locks.lock_of("k", "feat/u", None) == "Locked · pr"
    finally:
        handle.close()


def test_a_live_session_is_labelled_by_harness(worktree, sleeper):
    proc = sleeper()
    assert core.session_lock.acquire(worktree, pid=proc.pid, harness="pi")
    assert board.locks.lock_of("k", "feat/a", str(worktree)) == "Locked · Pi session"


def test_a_claude_session_is_labelled_claude(worktree, sleeper):
    proc = sleeper()
    assert core.session_lock.acquire(worktree, pid=proc.pid, harness="claude")
    assert board.locks.lock_of("k", "feat/a", str(worktree)) == "Locked · Claude session"


def test_a_run_beats_a_session(held_run, worktree, sleeper):
    proc = sleeper()
    core.session_lock.acquire(worktree, pid=proc.pid, harness="pi")
    held_run("k", "feat/a", "pr comments --fix")
    assert board.locks.lock_of("k", "feat/a", str(worktree)) == "Locked · pr comments"


def test_no_worktree_skips_the_session_probe(monkeypatch):
    monkeypatch.setattr(
        core.session_lock, "holders", lambda *_a, **_k: pytest.fail("session probe ran")
    )
    assert board.locks.lock_of("k", "feat/a", None) is None
