"""A fix pass refuses to land after a branch switch or rewrite, and tolerates a commit on top."""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke
import fix.engine
import fix.head_guard
import git.land
from fix.head_guard import Drift
from fix_containment_support import _repo
from fix_engine_support import StubAdapter, _answer
from git.land import CommitStatus

_PUSHED_RESULT = git.land.LandResult(CommitStatus.PUSHED, "abc1234")


def _two_commit_repo(tmp_path):
    wt = _repo(tmp_path, {"a.py": "one\n"})
    (wt / "a.py").write_text("two\n")
    git_out(wt, "commit", "-qam", "second")
    return wt


def _agent_doing(adapter, wt, *git_args, write=None):
    """A `run_fix` stub that runs a git command mid-pass, then answers."""
    def run_fix(phase, prompt, **kwargs):
        (wt / "a.py").write_text(write or "agent\n")
        if git_args:
            git_out(wt, *git_args)
        return _answer(adapter)(phase, prompt, **kwargs)
    return run_fix


def _run_pass(wt, *git_args, spec=None):
    adapter = StubAdapter(wt, spec=spec)
    with patch.object(git.land, "land", return_value=_PUSHED_RESULT) as land:
        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_agent_doing(adapter, wt, *git_args),
        ):
            run = fix.engine.run(adapter)
    return run, land


class TestDrift:
    def test_a_failed_rev_list_is_unreadable_not_zero_commits(self, tmp_path):
        wt = _two_commit_repo(tmp_path)
        start = fix.head_guard.read(wt)
        git_out(wt, "commit", "-q", "--allow-empty", "-m", "third")
        real = fix.head_guard.git.client.run

        def run(*args, **kw):
            if args and args[0] == "rev-list":
                return real("rev-list", "--bogus-flag", **kw)
            return real(*args, **kw)

        with patch.object(fix.head_guard.git.client, "run", run):
            assert fix.head_guard.drift(wt, start).kind is Drift.UNREADABLE

    def test_each_drift_kind(self, tmp_path):
        wt = _two_commit_repo(tmp_path)
        start = fix.head_guard.read(wt)
        assert fix.head_guard.drift(wt, start).kind is Drift.UNCHANGED

        git_out(wt, "commit", "-q", "--allow-empty", "-m", "third")
        advanced = fix.head_guard.drift(wt, start)
        assert advanced.kind is Drift.ADVANCED
        assert len(advanced.commits) == 1
        assert not advanced.refuses_landing

        later = fix.head_guard.read(wt)
        git_out(wt, "reset", "-q", "--hard", "HEAD~1")
        assert fix.head_guard.drift(wt, later).kind is Drift.REWRITTEN

        git_out(wt, "checkout", "-q", "-b", "other")
        branch = fix.head_guard.drift(wt, start)
        assert branch.kind is Drift.BRANCH_CHANGED
        assert branch.refuses_landing

        assert fix.head_guard.drift(tmp_path, start).kind is Drift.UNREADABLE


class TestLanding:
    def test_an_agent_branch_switch_refuses_to_land(self, tmp_path, capsys):
        wt = _two_commit_repo(tmp_path)

        run, land = _run_pass(wt, "checkout", "-q", "-b", "elsewhere")

        land.assert_not_called()
        assert run.landed.status is CommitStatus.COMMIT_FAILED
        assert "elsewhere" in capsys.readouterr().err

    def test_an_agent_reset_refuses_to_land(self, tmp_path, capsys):
        wt = _two_commit_repo(tmp_path)

        run, land = _run_pass(wt, "reset", "-q", "--soft", "HEAD~1")

        land.assert_not_called()
        assert run.landed.status is CommitStatus.COMMIT_FAILED
        assert "rewritten" in capsys.readouterr().err

    def test_a_commit_on_top_still_lands(self, tmp_path, capsys):
        wt = _two_commit_repo(tmp_path)
        (wt / "b.py").write_text("hand\n")
        git_out(wt, "add", "b.py")

        run, land = _run_pass(wt, "commit", "-qm", "by hand", "b.py")

        land.assert_called_once()
        assert run.landed.status is CommitStatus.PUSHED
        assert "during the fix pass" not in capsys.readouterr().err

    def test_a_recover_domain_is_not_refused(self, tmp_path):
        wt = _two_commit_repo(tmp_path)
        spec = fix.engine.LandSpec(message="m", recover=True)

        _run, land = _run_pass(wt, "checkout", "-q", "-b", "elsewhere", spec=spec)

        land.assert_called_once()
