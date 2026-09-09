"""Tests for `pr.thread_context` — what a review thread is read against.

The two context readers had drifted apart before this module existed: one
framed its snippet for a single prompt and the other labelled it for a round,
and nothing held them to the same window. They share `_window` now, so the
cases below assert the window itself once and the two framings separately.
"""

import sys

from conftest import REPO_ROOT, git_in, git_out, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from git import topology as git_topology  # noqa: E402
from pr import thread_context  # noqa: E402
from pr.thread_models import ReportThread  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    """A file of 60 numbered lines, so a window's bounds are readable."""
    (tmp_path / "a.py").write_text(
        "".join(f"line{n}\n" for n in range(1, 61)))
    return tmp_path


class TestTheContextWindow:
    """One window, asserted once — both framings below are built from it."""

    def test_it_reaches_ten_lines_either_side(self, tree):
        out = thread_context.code_context_for_thread("a.py", 30, tree)
        assert "line20\n" in out and "line40\n" in out
        assert "line19" not in out and "line41" not in out

    def test_it_clamps_at_the_start_of_the_file(self, tree):
        out = thread_context.code_context_for_thread("a.py", 2, tree)
        assert out.startswith("```\nline1\n")

    def test_it_clamps_at_the_end_of_the_file(self, tree):
        out = thread_context.code_context_for_thread("a.py", 59, tree)
        assert out.rstrip().endswith("line60\n```")

    @pytest.mark.parametrize("path,line", [
        ("", 5),
        ("a.py", 0),
        ("missing.py", 5),
        ("a.py", -1),
    ])
    def test_nothing_to_read_is_no_context(self, tree, path, line):
        """A file the comment names but the tree lacks is ordinary after a
        rename, not an error worth stopping a triage round for."""
        assert thread_context.code_context_for_thread(path, line, tree) == ""

    def test_a_directory_is_not_a_file_to_read(self, tree):
        (tree / "pkg").mkdir()
        assert thread_context.code_context_for_thread("pkg", 1, tree) == ""

    def test_undecodable_bytes_do_not_raise(self, tree):
        (tree / "bin.py").write_bytes(b"ok\n\xff\xfe\nmore\n")
        assert thread_context.code_context_for_thread("bin.py", 2, tree) != ""


class TestTheTwoFramings:
    """Same window, different label, because the readers differ."""

    def test_one_thread_is_fenced_for_a_prompt(self, tree):
        out = thread_context.code_context_for_thread("a.py", 30, tree)
        assert out.startswith("```\n") and out.endswith("\n```")

    def test_a_round_labels_each_block_with_its_range(self, tree):
        threads = [ReportThread(id="t1", file="a.py", line=30)]
        out = thread_context.gather_code_context(threads, tree)
        assert out.startswith("--- a.py:20-40 ---\n")
        assert out.endswith("\n---")

    def test_the_two_agree_on_the_source_they_return(self, tree):
        """The drift this module exists to prevent."""
        one = thread_context.code_context_for_thread("a.py", 30, tree)
        many = thread_context.gather_code_context(
            [ReportThread(id="t1", file="a.py", line=30)], tree)
        assert one.strip("`\n") == many.split("---\n")[1].rstrip("\n-")

    def test_a_round_skips_threads_with_nowhere_to_read(self, tree):
        threads = [
            ReportThread(id="t1", file="a.py", line=30),
            ReportThread(id="t2", file="gone.py", line=5),
            ReportThread(id="t3", file="", line=0),
        ]
        out = thread_context.gather_code_context(threads, tree)
        assert out.count("--- ") == 1

    def test_a_round_with_nothing_readable_is_empty(self, tree):
        threads = [ReportThread(id="t1", file="gone.py", line=5)]
        assert thread_context.gather_code_context(threads, tree) == ""


class TestThreadCommentText:
    def test_comments_are_quoted_under_their_author(self):
        out = thread_context.thread_comment_text([
            {"author": {"login": "kgn"}, "body": "first"},
            {"author": {"login": "amp"}, "body": "second"},
        ])
        assert out == "**@kgn:**\n> first\n\n**@amp:**\n> second"

    def test_every_line_of_a_body_is_quoted(self):
        out = thread_context.thread_comment_text(
            [{"author": {"login": "kgn"}, "body": "one\ntwo"}])
        assert out == "**@kgn:**\n> one\n> two"

    def test_an_empty_body_contributes_nothing(self):
        out = thread_context.thread_comment_text([
            {"author": {"login": "kgn"}, "body": "   "},
            {"author": {"login": "amp"}, "body": "real"},
        ])
        assert out == "**@amp:**\n> real"

    def test_a_missing_author_reads_as_unknown(self):
        out = thread_context.thread_comment_text([{"body": "orphan"}])
        assert out.startswith("**@unknown:**")

    def test_no_comments_is_no_text(self):
        assert thread_context.thread_comment_text([]) == ""


class TestBranchReadings:
    """The two that need a real branch to answer."""

    @pytest.fixture
    def branch(self, tmp_path):
        origin = tmp_path / "origin"
        run_checked(["git", "init", "--bare", "-q", "-b", "trunk", str(origin)])
        work = tmp_path / "work"
        run_checked(["git", "clone", "-q", str(origin), str(work)])
        git_in(work, "config", "user.email", "t@example.com")
        git_in(work, "config", "user.name", "Test")
        (work / "a.py").write_text("base\n")
        git_in(work, "add", "-A")
        git_in(work, "commit", "-q", "--no-verify", "-m", "base")
        git_in(work, "push", "-q", "-u", "origin", "trunk")
        # A bare `git clone` of an empty origin leaves no origin/HEAD, and
        # `default_branch` then falls back to "main" by design. Setting it is
        # what makes this a `trunk` repo rather than a repo git cannot answer
        # for — which is the case worth testing.
        git_in(work, "remote", "set-head", "origin", "trunk")
        git_in(work, "checkout", "-q", "-b", "feature")
        (work / "a.py").write_text("base\nchanged\n")
        git_in(work, "commit", "-q", "--no-verify", "-am", "first change")
        (work / "b.py").write_text("new\n")
        git_in(work, "add", "-A")
        git_in(work, "commit", "-q", "--no-verify", "-m", "second change")
        git_topology.default_branch_cached.cache_clear()
        return work

    def test_the_log_is_newest_first_and_branch_scoped(self, branch):
        out = thread_context.branch_commit_log(branch)
        lines = out.splitlines()
        assert len(lines) == 2
        assert "second change" in lines[0]
        assert "first change" in lines[1]
        assert "base" not in out

    def test_the_log_resolves_a_trunk_that_is_not_main(self, branch):
        """`origin/main` in a `trunk` repo would answer nothing at all."""
        assert thread_context.branch_commit_log(branch) != ""

    def test_no_worktree_is_no_log(self):
        assert thread_context.branch_commit_log(None) == ""

    def test_the_diff_is_scoped_to_one_file(self, branch):
        out = thread_context.diff_context_for_file("a.py", branch)
        assert out.startswith("```diff\n")
        assert "changed" in out
        assert "b.py" not in out

    def test_an_unchanged_file_has_no_diff(self, branch):
        (branch / "c.py").write_text("untracked\n")
        assert thread_context.diff_context_for_file("c.py", branch) == ""

    def test_no_file_is_no_diff(self, branch):
        assert thread_context.diff_context_for_file("", branch) == ""

    def test_a_long_diff_is_truncated_and_says_so(self, branch):
        (branch / "big.py").write_text(
            "".join(f"l{n}\n" for n in range(400)))
        git_in(branch, "add", "-A")
        git_in(branch, "commit", "-q", "--no-verify", "-m", "big")

        out = thread_context.diff_context_for_file("big.py", branch)

        body = out.removeprefix("```diff\n").removesuffix("\n```")
        lines = body.splitlines()
        assert len(lines) == thread_context.DIFF_CONTEXT_MAX_LINES + 1
        assert lines[-1].startswith("... (")
        assert "more lines)" in lines[-1]
