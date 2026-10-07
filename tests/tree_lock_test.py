"""Tests for the tree validation lock."""

import json
import os
import signal
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
import core.signal_relay  # noqa: E402
import core.tree_lock_cli  # noqa: E402
from core.tree_lock import (  # noqa: E402
    LOCK_ENV,
    LOCK_FILE,
    LockState,
    acquire,
    holders,
    is_locked,
    lock_path,
    probe,
)


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


def test_git_timeout_is_an_unknown_probe_not_a_free_tree(tmp_path, monkeypatch):
    """is_locked fails open on a hung git; probe must still say it could not tell."""

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    verdict = probe(tmp_path)
    assert verdict.state is LockState.UNKNOWN
    assert "timed out" in verdict.reason


def test_git_that_will_not_start_is_an_unknown_probe(tmp_path, monkeypatch):
    """A spawn failure is the probe breaking, not git saying "not a repo"."""

    def boom(*_args, **_kwargs):
        raise OSError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(subprocess, "run", boom)
    verdict = probe(tmp_path)
    assert verdict.state is LockState.UNKNOWN
    assert "could not run" in verdict.reason


def test_non_repo_probe_is_free_not_unknown(tmp_path):
    """git answering "not a repository" is an answer: nothing there to validate."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert probe(plain).state is LockState.FREE


def test_git_failing_for_another_reason_is_unknown_not_free(tmp_path, monkeypatch):
    """git exits 128 for every fatal error; only "not a repository" is an answer."""

    def dubious(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            128, "git", stderr="fatal: detected dubious ownership in repository at '/x'"
        )

    monkeypatch.setattr(subprocess, "run", dubious)
    verdict = probe(tmp_path)
    assert verdict.state is LockState.UNKNOWN
    assert "dubious ownership" in verdict.reason


def test_a_missing_tree_is_free_not_unknown(tmp_path):
    """git -C on a directory that is gone says "cannot change to": nothing to validate."""
    assert probe(tmp_path / "gone").state is LockState.FREE


def test_flock_failing_for_another_reason_is_unknown_not_held(worktree, monkeypatch):
    """Only EWOULDBLOCK means someone holds it; ENOLCK is the probe failing."""
    import errno
    import fcntl

    lock_path(worktree).write_text("")

    def no_locks(*_args, **_kwargs):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)
    verdict = probe(worktree)
    assert verdict.state is LockState.UNKNOWN
    assert "could not flock" in verdict.reason


def test_unstatable_lock_file_is_unknown(worktree, monkeypatch):
    """An unsearchable git dir makes the existence check raise; the probe still answers."""
    target = lock_path(worktree)
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    verdict = probe(worktree)
    assert verdict.state is LockState.UNKNOWN
    assert "could not stat" in verdict.reason


def test_probe_reports_held_and_free(worktree):
    """Both senses through probe itself, not only through is_locked."""
    assert probe(worktree).state is LockState.FREE
    with acquire(worktree, command="run-tests", started="t"):
        assert probe(worktree).state is LockState.HELD
    assert probe(worktree).state is LockState.FREE


def test_check_exits_3_with_a_reason_when_it_cannot_tell(tmp_path, monkeypatch, capsys):
    """Exit 1 means free; a probe that broke must not exit 1."""

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert core.tree_lock_cli._check(tmp_path) == core.tree_lock_cli.EXIT_UNKNOWN == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not tell" in captured.err and "timed out" in captured.err


def test_check_prints_the_free_line_on_a_free_tree(worktree, capsys):
    """Readers require this line alongside exit 1 to call a tree free."""
    assert core.tree_lock_cli._check(worktree) == 1
    assert capsys.readouterr().out == f"{worktree}{core.tree_lock_cli.FREE_SUFFIX}\n"


def test_check_prints_the_held_line_on_a_held_tree(worktree, capsys):
    """Readers require this line alongside exit 0 to call a tree held."""
    with acquire(worktree, command="run-tests", started="t"):
        assert core.tree_lock_cli._check(worktree) == 0
    assert capsys.readouterr().out.startswith(f"{worktree}{core.tree_lock_cli.HELD_SUFFIX}\n")


def test_claude_guard_passes_on_the_reason_when_the_probe_cannot_answer(worktree, tmp_path):
    """Exit 3 lets the edit through but is not silent, as it is not for the Pi guard."""
    seed_repo(worktree)
    subprocess.run(["git", "-C", str(worktree), "checkout", "-q", "-b", "feat"], check=True)
    shims = tmp_path / "shims"
    shims.mkdir()
    shim = shims / "python3"
    shim.write_text("#!/bin/sh\necho 'tree_lock_cli: could not tell: shim' >&2\nexit 3\n")
    shim.chmod(0o755)
    payload = json.dumps({"tool_input": {"file_path": str(worktree / "file.txt")}})
    result = subprocess.run(
        [str(REPO_ROOT / "ai" / "claude" / "bin" / "claude-edit-guard")],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0
    assert "could not tell: shim" in result.stderr


def test_the_cli_runs_the_child_through_the_shared_runner(monkeypatch):
    """The wrapper must not grow its own spawn; the relay owns that."""
    seen: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        seen.append((list(argv), kwargs))
        return 0

    monkeypatch.setattr(core.signal_relay, "run_child", fake_run)
    assert core.tree_lock_cli._run_child(["echo", "ok"]) == 0
    argv, kwargs = seen[0]
    assert argv == ["echo", "ok"]
    assert kwargs.get("origin") == "tree_lock_cli"


def test_the_cli_reports_a_signalled_child_as_128_plus_the_signal(worktree):
    """End to end, so the wrapper's own exit-code mapping is covered.

    The mock-level test above only sees the call into the relay; this is the
    process a caller of `with-tree-lock` actually gets an exit status from.
    """
    result = subprocess.run(
        [sys.executable, str(LIB_DIR / "core" / "tree_lock_cli.py"),
         "--tree", str(worktree), "--", "sh", "-c", "kill -TERM $$"],
        capture_output=True, timeout=60,
    )
    assert result.returncode == 128 + signal.SIGTERM


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
