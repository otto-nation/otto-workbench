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

    def test_scratch_files_an_agent_left_behind_are_not_committed(self, git_wt):
        """An agent's throwaway file is not the pass's work.

        A fix agent with no permitted way to delete a file created a fresh one
        each time it needed a scratch — debug.test.ts, probe.test.ts, and a
        zzz.test.tsx through zzz7 trail in one observed run. Untracked files are
        in the scope by design, so all of them were committed and pushed.
        """
        before = fix_scope.changed_files(str(git_wt))
        (git_wt / "real_fix.py").write_text("the actual work\n")
        for name in ("debug.test.ts", "probe.test.ts", "zzz.test.tsx",
                     "zzz4.test.tsx", "scratch.py", "delete-me.txt"):
            (git_wt / name).write_text("throwaway\n")

        assert fix_scope.agent_changed(str(git_wt), before) == {"real_fix.py"}

    # passes-at-base: the guard against over-matching — before _drop_scratch nothing was dropped, so these survived by default; the case holds the pattern narrow from here and fails on a widened one
    def test_a_real_file_whose_name_merely_contains_a_scratch_word_is_kept(
        self, git_wt,
    ):
        """The cost of a false positive is a real fix left uncommitted.

        Matched on the whole basename, so a deliberate contribution under a
        debugger/ directory or named for what it tests is untouched.
        """
        before = fix_scope.changed_files(str(git_wt))
        (git_wt / "tests").mkdir()
        for name in ("tests/test_probe.py", "tests/debugger_test.py",
                     "tests/test_debug_output.py", "tests/tmpdir_isolation.py"):
            (git_wt / name).write_text("real work\n")

        assert fix_scope.agent_changed(str(git_wt), before) == {
            "tests/test_probe.py", "tests/debugger_test.py",
            "tests/test_debug_output.py", "tests/tmpdir_isolation.py",
        }


class TestDropOutside:
    """The second predicate on the same warn-and-leave mechanism as scratch.

    Attribution still reports the path; this is what stops it reaching the
    commit. The cost of a false positive is a real fix left uncommitted, so
    colocated tests are only same-directory stem matches.
    """

    def test_an_out_of_branch_path_is_dropped_and_reported(self, tmp_path, capsys):
        kept = fix_scope.drop_outside(
            {"src.py", "lib/nesting/bash.py"},
            fix_scope.commit_allowed({"src.py"}, set()),
            tmp_path,
        )
        assert kept == {"src.py"}
        err = capsys.readouterr().err
        assert "not committing 1 file(s) outside this branch" in err
        assert "lib/nesting/bash.py" in err

    def test_an_in_branch_path_is_kept(self, tmp_path, capsys):
        kept = fix_scope.drop_outside(
            {"src.py"},
            fix_scope.commit_allowed({"src.py"}, set()),
            tmp_path,
        )
        assert kept == {"src.py"}
        assert "not committing" not in capsys.readouterr().err

    def test_a_colocated_test_of_an_in_branch_file_is_kept(self, tmp_path):
        allowed = fix_scope.commit_allowed({"src/foo.py"}, set())
        kept = fix_scope.drop_outside(
            {"src/foo.py", "src/foo_test.py", "src/test_foo.py"},
            allowed,
            tmp_path,
        )
        assert kept == {"src/foo.py", "src/foo_test.py", "src/test_foo.py"}

    def test_a_test_under_a_test_root_named_for_the_source_is_kept(
        self, tmp_path,
    ):
        """The convention this repo actually uses for every Python test.

        189 test files live in `tests/` and none beside their source, so a
        rule that only admits a colocated test drops the regression test the
        fix template requires of every pass.
        """
        kept = fix_scope.drop_outside(
            {"tests/foo_test.py"},
            fix_scope.commit_allowed({"ai/lib/foo.py"}, set()),
            tmp_path,
            {"ai/lib/foo.py"},
        )
        assert kept == {"tests/foo_test.py"}

    def test_a_nested_test_root_is_kept(self, tmp_path):
        kept = fix_scope.drop_outside(
            {"tests/unit/foo_test.py"},
            fix_scope.commit_allowed({"ai/lib/foo.py"}, set()),
            tmp_path,
            {"ai/lib/foo.py"},
        )
        assert kept == {"tests/unit/foo_test.py"}

    def test_an_unrelated_suite_edit_is_still_dropped(self, tmp_path):
        """Only a test named for an in-branch source is admitted."""
        kept = fix_scope.drop_outside(
            {"tests/bar_test.py"},
            fix_scope.commit_allowed({"ai/lib/foo.py"}, set()),
            tmp_path,
            {"ai/lib/foo.py"},
        )
        assert kept == set()

    def test_a_finding_anchor_not_on_the_branch_is_kept(self, tmp_path):
        kept = fix_scope.drop_outside(
            {"helper.py"},
            fix_scope.commit_allowed(set(), {"helper.py"}),
            tmp_path,
        )
        assert kept == {"helper.py"}

    def test_an_empty_drop_is_silent(self, tmp_path, capsys):
        kept = fix_scope.drop_outside(
            set(), fix_scope.commit_allowed({"src.py"}, set()), tmp_path,
        )
        assert kept == set()
        assert capsys.readouterr().err == ""


class TestRenamePartners:
    """A rename must not be committed by halves.

    The branch file list is fixed when the PR is collected, so a name the
    agent invents mid-fix is never on it: the destination reads as out of
    branch while the source's deletion reads as in it. Committing only the
    deletion leaves a tree that does not build.
    """

    def test_a_renamed_destination_rejoins_its_kept_source(self, git_wt):
        git_out(git_wt, "mv", "src.py", "renamed.py")
        partners = fix_scope.rename_partners(
            {"renamed.py"}, {"src.py"}, git_wt,
        )
        assert partners == {"renamed.py"}

    def test_an_unrelated_drop_is_not_readmitted(self, git_wt):
        git_out(git_wt, "mv", "src.py", "renamed.py")
        (git_wt / "elsewhere.py").write_text("new\n")
        git_out(git_wt, "add", "-A")
        partners = fix_scope.rename_partners(
            {"renamed.py", "elsewhere.py"}, {"src.py"}, git_wt,
        )
        assert partners == {"renamed.py"}

    def test_nothing_dropped_asks_git_nothing(self, git_wt):
        assert fix_scope.rename_partners(set(), {"src.py"}, git_wt) == set()

    @patch("fix.scope.git_client.run")
    def test_a_failed_read_readmits_nothing(self, mock_run, git_wt):
        mock_run.side_effect = [CmdResult(returncode=1, stdout="", stderr="boom")]
        partners = fix_scope.rename_partners(
            {"renamed.py"}, {"src.py"}, git_wt,
        )
        assert partners == set()
