"""Tests for rebase.stash — carrying uncommitted work across a rebase."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import client as git_client
from rebase import inspect as rebase_inspect
from rebase import stash as rebase_stash
from rebase import types as rebase_types


def _ok(**kwargs):
    return mock.Mock(ok=True, returncode=0, stdout="", stderr="", **kwargs)


def _failed(stderr="boom"):
    return mock.Mock(ok=False, returncode=1, stdout="", stderr=stderr,
                     combined_output=stderr)


class TestAutoStash:
    @pytest.mark.parametrize("dirty,expected", [
        ([" M ai/lib/review.py"], True),
        (["?? scratch.txt"], True),
        ([], False),
    ])
    def test_it_stashes_only_a_dirty_tree(self, dirty, expected):
        with mock.patch.object(rebase_inspect, "status_lines", return_value=dirty), \
             mock.patch.object(git_client, "run", return_value=_ok()):
            assert rebase_stash.auto_stash("/fake") is expected

    def test_untracked_files_are_stashed_too(self):
        """The pre-push hooks validate the worktree, so strays must not be in it."""
        with mock.patch.object(rebase_inspect, "status_lines",
                               return_value=["?? scratch.txt"]), \
             mock.patch.object(git_client, "run", return_value=_ok()) as run:
            rebase_stash.auto_stash("/fake")

        assert run.call_args[0] == (
            "stash", "push", "-u", "-m", rebase_stash.STASH_MSG)

    def test_a_tree_it_cannot_read_is_not_stashed(self):
        """None is "cannot tell", which must not be mistaken for clean."""
        with mock.patch.object(rebase_inspect, "status_lines", return_value=None), \
             mock.patch.object(git_client, "run") as run:
            assert rebase_stash.auto_stash("/fake") is None
        run.assert_not_called()

    def test_a_failed_stash_is_not_reported_as_stashed(self):
        with mock.patch.object(rebase_inspect, "status_lines",
                               return_value=[" M a.py"]), \
             mock.patch.object(git_client, "run", return_value=_failed()):
            assert rebase_stash.auto_stash("/fake") is None


class TestAutoUnstash:
    def test_a_clean_pop_says_so(self, capsys):
        with mock.patch.object(git_client, "run", return_value=_ok()):
            rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)
        assert "Restored stashed changes" in capsys.readouterr().err

    def test_a_pop_that_failed_without_conflicts_names_the_stash(self, capsys):
        """Nothing is lost — the entry survives, so the message points at it."""
        with mock.patch.object(git_client, "run", return_value=_failed()), \
             mock.patch.object(rebase_inspect, "detect_conflicts", return_value=[]):
            rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)

        err = capsys.readouterr().err
        assert rebase_stash.STASH_MSG in err
        assert "git stash pop" in err

    def test_conflicts_are_left_alone_when_the_run_may_not_resolve(self, capsys):
        """Without --fix the user resolves them, so say so rather than prompting."""
        with mock.patch.object(git_client, "run", return_value=_failed()), \
             mock.patch.object(rebase_inspect, "detect_conflicts",
                               return_value=["a.py"]):
            rebase_stash.auto_unstash("/fake", rebase_types.RunMode.PUSH)

        assert "resolve manually" in capsys.readouterr().err
