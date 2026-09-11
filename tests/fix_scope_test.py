"""Tests for the snapshot a fix pass's commit is scoped from — `fix.scope`.

The two snapshots and their difference, on their own. Which adapter asks for
them and what each does with the answer is that adapter's test file; what is
here is the reading itself, and the distinction the whole thing turns on:
an empty set is a worktree the agent did not touch, and None is a worktree the
pass could not read. Spelling the second like the first is how a pass commits
nothing and reports success, or commits everything and pushes somebody else's
work.

The real-repo cases run against a real repo, because a snapshot is a set of
path strings git produced and a stubbed `status` line would agree with whatever
the test expected.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.proc import TIMEOUT_RETURNCODE, CmdResult
from fix import scope as fix_scope


@pytest.fixture
def git_wt(tmp_path):
    """A real repo with one commit, and one gitignored pattern to exclude."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    # Empty hooks dir: the developer's own `core.hooksPath` is global, so
    # without this the fixture runs their pre-commit hook and the suite passes
    # or fails on whatever that machine has installed.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(wt, "init", "-q", "-b", "main")
    git_out(wt, "config", "user.email", "test@example.com")
    git_out(wt, "config", "user.name", "Test")
    git_out(wt, "config", "commit.gpgsign", "false")
    git_out(wt, "config", "core.hooksPath", str(hooks))
    (wt / "src.py").write_text("original\n")
    (wt / ".gitignore").write_text("*.cache\n")
    git_out(wt, "add", "-A")
    git_out(wt, "commit", "-qm", "initial")
    return wt


class TestChangedFiles:
    @patch("fix.scope.git_client.run")
    def test_includes_untracked_files(self, mock_run):
        """A fix that only adds a new test file still fixed the finding."""
        mock_run.side_effect = [
            CmdResult(0, "src/auth.go\n"),
            CmdResult(0, "tests/run_ai.bats\n"),
        ]
        assert fix_scope.changed_files("/wt") == {
            "src/auth.go", "tests/run_ai.bats",
        }

    @patch("fix.scope.git_client.run")
    def test_untracked_query_excludes_ignored_files(self, mock_run):
        mock_run.side_effect = [CmdResult(), CmdResult()]
        fix_scope.changed_files("/wt")
        assert "--exclude-standard" in mock_run.call_args_list[1].args

    @patch("fix.scope.git_client.run")
    def test_a_failed_diff_is_not_a_partial_snapshot(self, mock_run):
        """Half a snapshot omits the tracked edits, silently and permanently.

        The untracked half answering is not a reason to keep going: every path
        the failed half would have named is a path the pass never commits.
        """
        mock_run.side_effect = [
            CmdResult(128),
            CmdResult(0, "tests/new.bats\n"),
        ]
        assert fix_scope.changed_files("/wt") is None

    @patch("fix.scope.git_client.run")
    def test_a_failed_untracked_listing_is_not_a_partial_snapshot(self, mock_run):
        mock_run.side_effect = [
            CmdResult(0, "src/auth.go\n"),
            CmdResult(128),
        ]
        assert fix_scope.changed_files("/wt") is None

    @patch("fix.scope.git_client.run")
    def test_a_killed_snapshot_is_not_an_empty_one(self, mock_run):
        mock_run.side_effect = [CmdResult(TIMEOUT_RETURNCODE, "", "")]
        assert fix_scope.changed_files("/wt") is None

    def test_a_path_that_is_not_a_repo_has_no_snapshot(self, tmp_path):
        assert fix_scope.changed_files(str(tmp_path)) is None

    def test_gitignored_paths_are_in_neither_snapshot(self, git_wt):
        (git_wt / "build.cache").write_text("artifact\n")
        (git_wt / "real.py").write_text("x = 1\n")
        assert fix_scope.changed_files(str(git_wt)) == {"real.py"}


class TestAgentChanged:
    def test_an_unchanged_worktree_is_an_empty_delta_not_a_failed_one(self, git_wt):
        """Empty says the agent changed nothing; None says the pass cannot tell."""
        before = fix_scope.changed_files(str(git_wt))
        assert fix_scope.agent_changed(str(git_wt), before) == set()

    def test_the_delta_is_only_what_appeared_after_the_baseline(self, git_wt):
        """A file already dirty when the pass started is not the agent's work."""
        (git_wt / "theirs.py").write_text("someone else\n")
        before = fix_scope.changed_files(str(git_wt))
        (git_wt / "ours.py").write_text("the agent\n")

        assert fix_scope.agent_changed(str(git_wt), before) == {"ours.py"}

    def test_no_baseline_yields_no_delta(self, git_wt):
        """None in, None out: with no baseline nothing can be attributed.

        Reading the second snapshot alone would claim every dirty file in the
        worktree as the agent's, which is the whole-tree commit by another
        route.
        """
        (git_wt / "theirs.py").write_text("someone else\n")
        assert fix_scope.agent_changed(str(git_wt), None) is None

    def test_a_second_snapshot_that_failed_yields_no_delta(self, git_wt):
        before = fix_scope.changed_files(str(git_wt))
        (git_wt / ".git" / "index").write_bytes(b"garbage")
        assert fix_scope.agent_changed(str(git_wt), before) is None
