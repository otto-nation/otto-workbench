"""pr.thread_context: diff context for a file."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import _git_ran, _no_published_summary  # noqa: E402
import git.topology
import pr.thread_context


# ── thread_context.diff_context_for_file ─────────────────────────────────────────────────

class TestDiffContextForFile:
    def test_empty_file_path(self):
        assert pr.thread_context.diff_context_for_file("", Path("/wt")) == ""

    @patch("git.client.run")
    def test_returns_diff(self, mock_run):
        mock_run.return_value = _git_ran(0, stdout="+ added line\n- removed line\n")
        result = pr.thread_context.diff_context_for_file("src/foo.go", Path("/wt"))
        assert "```diff" in result
        assert "+ added line" in result

    @patch("git.client.run")
    def test_truncates_long_diff(self, mock_run):
        long_diff = "\n".join(f"+ line {i}" for i in range(200))
        mock_run.return_value = _git_ran(0, stdout=long_diff)
        result = pr.thread_context.diff_context_for_file("src/foo.go", Path("/wt"))
        assert "more lines" in result

    @patch("git.client.run")
    def test_git_failure_returns_empty(self, mock_run):
        mock_run.return_value = _git_ran(1)
        assert pr.thread_context.diff_context_for_file("src/foo.go", Path("/wt")) == ""

    @patch("git.client.run")
    def test_an_omitted_branch_is_resolved_not_assumed_to_be_main(self, mock_run):
        """The signature used to default to the literal "main".

        Every production caller passes the resolved trunk, so the literal only
        ever fired for one that forgot — and then silently, as an empty diff
        from a ref the repository does not have.
        """
        mock_run.return_value = _git_ran(0, stdout="+ added line\n")
        with patch.object(git.topology, "default_branch_cached", return_value="trunk"):
            pr.thread_context.diff_context_for_file("src/foo.go", Path("/wt"))

        assert "origin/trunk" in mock_run.call_args[0]
