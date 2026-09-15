"""Tests for the tree validation lock."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from conftest import init_worktree, seed_repo  # noqa: E402
from core.tree_lock import LOCK_ENV, LOCK_FILE, acquire, holders, is_locked, lock_path


@pytest.fixture(autouse=True)
def _clear_lock_env():
    """Never inherit a real validation's marker into a test."""
    saved = os.environ.pop(LOCK_ENV, None)
    yield
    os.environ.pop(LOCK_ENV, None)
    if saved is not None:
        os.environ[LOCK_ENV] = saved


def test_lock_path_is_inside_the_git_dir(worktree):
    """The lock belongs to the worktree's private git dir, not the tree."""
    path = lock_path(worktree)
    assert path.name == LOCK_FILE
    assert path.parent == (worktree / ".git").resolve()


def test_lock_path_follows_a_linked_worktree(worktree, tmp_path):
    """A linked worktree has a private git dir; .git there is a file."""
    seed_repo(worktree)
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(worktree), "worktree", "add", "-q", "-b", "feat", str(linked)],
        check=True,
    )
    assert (linked / ".git").is_file()
    assert lock_path(linked) != lock_path(worktree)
    assert "worktrees" in str(lock_path(linked))


def test_unheld_tree_is_not_locked(worktree):
    """No holder, no lock."""
    assert is_locked(worktree) is False


def test_is_locked_answers_true_while_held(worktree):
    """The busy branch must report busy.

    Explicit alongside the round-trip test below because inverting this one
    sense makes both branches answer "free": the guard then never blocks and
    never errors, which no other test in this file would catch.
    """
    with acquire(worktree, command="run-tests", started="t"):
        assert is_locked(worktree) is True


def test_acquire_makes_the_tree_locked(worktree):
    """A reader probing during a validation sees it as busy."""
    with acquire(worktree, command="bin/local/run-tests", started="t"):
        assert is_locked(worktree) is True
    assert is_locked(worktree) is False


def test_two_validators_share_the_lock(worktree):
    """LOCK_SH: pre-push runs validate-all and run-tests over one tree."""
    os.environ.pop(LOCK_ENV, None)
    with acquire(worktree, command="validate-all", started="t"):
        os.environ.pop(LOCK_ENV, None)
        with acquire(worktree, command="run-tests", started="t"):
            assert is_locked(worktree) is True
        assert is_locked(worktree) is True


def test_holders_names_every_validator(worktree):
    """Records are append-only, so a second holder does not erase the first."""
    os.environ.pop(LOCK_ENV, None)
    with acquire(worktree, command="validate-all", started="t1"):
        os.environ.pop(LOCK_ENV, None)
        with acquire(worktree, command="run-tests", started="t2"):
            found = holders(worktree)
    commands = [h.get("command") for h in found]
    assert "validate-all" in commands
    assert "run-tests" in commands


def test_holder_record_carries_the_diagnostic_fields(worktree):
    """Enough for a blocked edit to name what is holding the tree."""
    with acquire(worktree, command="bin/local/run-tests", started="2026-09-15T10:00:00"):
        record = holders(worktree)[0]
    assert record["pid"] == os.getpid()
    assert record["command"] == "bin/local/run-tests"
    assert record["started"] == "2026-09-15T10:00:00"
    assert record["tree_root"] == str(worktree.resolve())


def test_two_trees_lock_independently(worktree, tmp_path):
    """A suite in worktree A must not freeze edits in worktree B."""
    other = init_worktree(tmp_path / "other")
    with acquire(worktree, command="run-tests", started="t"):
        assert is_locked(worktree) is True
        assert is_locked(other) is False


def test_reentrant_acquire_in_same_process_tree(worktree):
    """A nested validator passes through rather than re-recording."""
    with acquire(worktree, command="pre-push", started="t"):
        with acquire(worktree, command="run-tests", started="t"):
            assert is_locked(worktree) is True
        assert len(holders(worktree)) == 1


def test_reentrancy_is_keyed_on_the_tree(worktree, tmp_path):
    """Holding A's lock does not wave through a claim on B."""
    other = init_worktree(tmp_path / "other")
    with acquire(worktree, command="outer", started="t"):
        with acquire(other, command="inner", started="t"):
            assert is_locked(other) is True


def test_env_marker_cleared_after_release(worktree):
    """The marker must not leak into whatever runs next."""
    with acquire(worktree, command="run-tests", started="t"):
        assert os.environ[LOCK_ENV] == str(worktree.resolve())
    assert LOCK_ENV not in os.environ


def test_lock_released_when_body_raises(worktree):
    """A failing suite still releases the tree."""
    with pytest.raises(RuntimeError):
        with acquire(worktree, command="run-tests", started="t"):
            raise RuntimeError("suite blew up")
    assert is_locked(worktree) is False


def test_holders_tolerates_an_unreadable_record(worktree):
    """The flock is the verdict; a corrupt record must not change it."""
    path = lock_path(worktree)
    path.write_text("not json\n{also not json\n")
    assert holders(worktree) == []
    assert is_locked(worktree) is False


def test_probe_does_not_destroy_the_holder_record(worktree):
    """is_locked opens the file; it must not truncate a live holder's record."""
    with acquire(worktree, command="run-tests", started="t"):
        is_locked(worktree)
        assert holders(worktree)[0]["command"] == "run-tests"


def test_records_do_not_accumulate_across_runs(worktree):
    """Dead holders are pruned, so a blocked edit never names a finished run."""
    for _ in range(3):
        with acquire(worktree, command="run-tests", started="t"):
            pass
    with acquire(worktree, command="validate-all", started="t"):
        found = holders(worktree)
    assert [h["command"] for h in found] == ["validate-all"]


def test_lock_survives_only_while_the_holder_lives(worktree):
    """The kernel drops the flock on SIGKILL — no stale lock to reap."""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(f"""
                import sys, time
                sys.path.insert(0, {str(LIB_DIR)!r})
                from core.tree_lock import acquire
                with acquire({str(worktree)!r}, command="run-tests", started="t"):
                    print("ready", flush=True)
                    time.sleep(30)
            """),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        child.stdout.readline()
        assert is_locked(worktree) is True
        child.kill()
        child.wait(timeout=10)
        assert is_locked(worktree) is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_non_repo_path_has_no_lock(tmp_path):
    """Outside a git repo there is nothing to lock and nothing to block."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert is_locked(plain) is False


def test_non_repo_acquire_sets_the_env_marker(tmp_path):
    """Writers re-exec on the marker; a missing one here is a fork bomb."""
    plain = tmp_path / "plain"
    plain.mkdir()
    with acquire(plain, command="run-tests", started="t"):
        assert os.environ[LOCK_ENV] == str(plain.resolve())
        assert is_locked(plain) is False
    assert LOCK_ENV not in os.environ


def test_git_timeout_is_treated_as_no_lock(tmp_path, monkeypatch):
    """A hung git must fail-soft; TimeoutExpired is not CalledProcessError."""

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert lock_path(tmp_path) is None
    assert is_locked(tmp_path) is False


def test_inherited_git_dir_does_not_hijack_resolution(tmp_path, monkeypatch):
    """Hooks export GIT_DIR; git -C must still resolve the asked-about tree."""
    tree = init_worktree(tmp_path / "tree")
    other = init_worktree(tmp_path / "other")
    other_git = subprocess.run(
        ["git", "-C", str(other), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", other_git)
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    path = lock_path(tree)
    assert path == (tree / ".git" / LOCK_FILE).resolve()
