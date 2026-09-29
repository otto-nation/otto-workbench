"""Tests for core.session_lock — the record an interactive session leaves.

The subject is a *verdict*, not a diagnostic: a fix pass refuses on what these
records say, so a stale record blocks real work and a missing one lets a pass
commit into a tree somebody is editing. Both directions are covered.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from core import session_lock  # noqa: E402


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A real repo, since the lock path comes from git rather than a join."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


@pytest.fixture
def sleeper():
    """A live process to record, cleaned up however the test ends."""
    started: list[subprocess.Popen] = []

    def spawn() -> subprocess.Popen:
        proc = subprocess.Popen(["sleep", "300"])
        started.append(proc)
        return proc

    yield spawn
    for proc in started:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


class TestTheRecord:
    def test_a_lock_path_is_inside_the_worktrees_git_dir(self, worktree: Path):
        path = session_lock.lock_path(worktree)
        assert path is not None
        assert path.parent == worktree / ".git"
        assert path.name == session_lock.LOCK_FILE

    def test_a_directory_outside_a_repo_has_no_lock_path(self, tmp_path: Path):
        assert session_lock.lock_path(tmp_path / "not-a-repo") is None

    def test_an_acquired_session_is_a_holder(self, worktree: Path, sleeper):
        proc = sleeper()
        assert session_lock.acquire(
            worktree, pid=proc.pid, harness="pi", command="pi"
        )
        held = session_lock.holders(worktree)
        assert [h.pid for h in held] == [proc.pid]
        assert held[0].harness == "pi"

    def test_two_sessions_in_one_worktree_are_both_held(
        self, worktree: Path, sleeper
    ):
        # Legitimate: two agents editing one checkout is the case the record
        # is JSONL for.
        first, second = sleeper(), sleeper()
        session_lock.acquire(worktree, pid=first.pid, harness="pi")
        session_lock.acquire(worktree, pid=second.pid, harness="claude")
        assert {h.pid for h in session_lock.holders(worktree)} == {
            first.pid,
            second.pid,
        }

    def test_acquiring_twice_for_one_pid_replaces_its_record(
        self, worktree: Path, sleeper
    ):
        # Pi's /reload emits shutdown then start with the pid unchanged. A
        # second entry for one process would outlive the release that only
        # drops one.
        proc = sleeper()
        session_lock.acquire(worktree, pid=proc.pid, harness="pi", command="first")
        session_lock.acquire(worktree, pid=proc.pid, harness="pi", command="second")
        held = session_lock.holders(worktree)
        assert len(held) == 1
        assert held[0].command == "second"

    def test_releasing_drops_only_its_own_entry(self, worktree: Path, sleeper):
        first, second = sleeper(), sleeper()
        session_lock.acquire(worktree, pid=first.pid, harness="pi")
        session_lock.acquire(worktree, pid=second.pid, harness="claude")
        session_lock.release(worktree, first.pid)
        assert [h.pid for h in session_lock.holders(worktree)] == [second.pid]

    def test_a_torn_line_is_skipped_rather_than_fatal(
        self, worktree: Path, sleeper
    ):
        proc = sleeper()
        session_lock.acquire(worktree, pid=proc.pid, harness="pi")
        path = session_lock.lock_path(worktree)
        assert path is not None
        path.write_text('{"pid": not json\n' + path.read_text())
        assert [h.pid for h in session_lock.holders(worktree)] == [proc.pid]


class TestLiveness:
    def test_a_killed_session_stops_holding_the_tree(
        self, worktree: Path, sleeper
    ):
        # Kill-safety without a daemon, and the reason a record can stand in
        # for a flock. wait() is the synchronisation point, so this is
        # deterministic rather than timed.
        proc = sleeper()
        session_lock.acquire(worktree, pid=proc.pid, harness="pi")
        assert session_lock.holders(worktree)

        proc.kill()
        proc.wait()

        assert session_lock.holders(worktree) == []
        assert session_lock.held_by_others(worktree) == []

    def test_a_recycled_pid_does_not_inherit_the_record(
        self, worktree: Path, sleeper
    ):
        # The start-time half of liveness. A live pid whose start time is not
        # the recorded one is a different process wearing a dead session's
        # number, and must not keep refusing passes.
        proc = sleeper()
        session_lock.acquire(worktree, pid=proc.pid, harness="pi")
        path = session_lock.lock_path(worktree)
        assert path is not None
        record = json.loads(path.read_text().strip())
        record["started"] = "Thu Jan  1 00:00:00 1970"
        path.write_text(json.dumps(record) + "\n")

        assert session_lock.holders(worktree) == []

    def test_a_process_that_is_not_running_cannot_acquire(self, worktree: Path):
        proc = subprocess.Popen(["true"])
        proc.wait()
        assert not session_lock.acquire(worktree, pid=proc.pid, harness="pi")


class TestSelfExemption:
    def test_a_session_does_not_refuse_its_own_descendant(
        self, worktree: Path
    ):
        # The case that matters most: `pr comments --fix` typed into the very
        # session holding the tree must run. This process stands in for that
        # tool call, and its own parent for the session.
        import os

        session_lock.acquire(worktree, pid=os.getppid(), harness="pi")
        assert session_lock.holders(worktree)
        assert session_lock.held_by_others(worktree) == []

    def test_an_unrelated_session_is_held_against_the_caller(
        self, worktree: Path, sleeper
    ):
        proc = sleeper()
        session_lock.acquire(
            worktree, pid=proc.pid, harness="claude", command="claude"
        )
        foreign = session_lock.held_by_others(worktree)
        assert [h.pid for h in foreign] == [proc.pid]

    def test_a_pi_session_id_match_exempts_a_reparented_tool(
        self, worktree: Path, sleeper, monkeypatch
    ):
        # A tool subprocess that re-parents drops out of the ancestry walk;
        # the harness's own session id is what still ties it to the session.
        proc = sleeper()
        session_lock.acquire(
            worktree, pid=proc.pid, harness="pi", session_id="abc-123"
        )
        monkeypatch.setenv("PI_SESSION_ID", "abc-123")
        assert session_lock.held_by_others(worktree) == []

    def test_a_claude_pid_match_exempts_a_reparented_tool(
        self, worktree: Path, sleeper, monkeypatch
    ):
        proc = sleeper()
        session_lock.acquire(worktree, pid=proc.pid, harness="claude")
        monkeypatch.setenv("CLAUDE_PID", str(proc.pid))
        assert session_lock.held_by_others(worktree) == []


class TestTheRefusalMessage:
    def test_a_holder_names_its_pid_command_and_start(
        self, worktree: Path, sleeper
    ):
        # #1453 asks for pid, command and start time by name: the incident it
        # came from was an investigation precisely because none were reported.
        proc = sleeper()
        session_lock.acquire(
            worktree, pid=proc.pid, harness="pi", command="pi --resume"
        )
        described = session_lock.holders(worktree)[0].describe()
        assert str(proc.pid) in described
        assert "pi --resume" in described
        assert session_lock.holders(worktree)[0].started in described
