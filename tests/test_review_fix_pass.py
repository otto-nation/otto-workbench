import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review_pipeline


class TestCountUnchecked:
    def test_counts_unchecked_findings(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** some must-fix finding\n"
            "- [x] **[M2]** already fixed\n"
            "## Should fix\n"
            "- [ ] **[S1]** some should-fix\n"
        )
        assert review_pipeline._count_unchecked(str(review)) == 2

    def test_all_checked(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "- [x] **[M1]** fixed\n"
            "- [x] **[S1]** fixed\n"
        )
        assert review_pipeline._count_unchecked(str(review)) == 0

    def test_none_checked(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "- [ ] **[M1]** finding one\n"
            "- [ ] **[S1]** finding two\n"
            "- [ ] **[N1]** finding three\n"
        )
        assert review_pipeline._count_unchecked(str(review)) == 3

    def test_missing_file(self):
        assert review_pipeline._count_unchecked("/nonexistent/review.md") == 0

    def test_ignores_non_finding_checkboxes(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "- [ ] **[M1]** real finding\n"
            "Some text with - [ ] inline is not a finding\n"
            "  - [ ] indented checkbox is not a finding\n"
        )
        assert review_pipeline._count_unchecked(str(review)) == 1


class TestCountChecked:
    def test_counts_checked_findings(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "- [x] **[M1]** fixed finding\n"
            "- [ ] **[S1]** not fixed\n"
            "- [x] **[S2]** also fixed\n"
        )
        assert review_pipeline._count_checked(str(review)) == 2

    def test_none_checked(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text("- [ ] **[M1]** not fixed\n")
        assert review_pipeline._count_checked(str(review)) == 0

    def test_missing_file(self):
        assert review_pipeline._count_checked("/nonexistent/review.md") == 0


class TestCountChangedSourceFiles:
    @patch("review_pipeline.subprocess.run")
    def test_counts_changed_files_excluding_review(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="handler.go\nstore.go\nreview.md\n",
        )
        assert review_pipeline._count_changed_source_files("/wt") == 2

    @patch("review_pipeline.subprocess.run")
    def test_empty_diff(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert review_pipeline._count_changed_source_files("/wt") == 0

    @patch("review_pipeline.subprocess.run")
    def test_git_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert review_pipeline._count_changed_source_files("/wt") == 0


class TestCommitFixes:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        job.review_file = str(tmp_path / "review.md")
        return job

    @patch("review_pipeline.subprocess.run")
    def test_no_diff_returns_early(self, mock_run, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.return_value = MagicMock(returncode=0)
        review_pipeline._commit_fixes(job, fixed=3, skipped=1)
        assert mock_run.call_count == 1

    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commits_with_counts(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=3, skipped=1)
        commit_call = mock_run.call_args_list[2]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "3 fixed, 1 skipped" in msg

    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_zero_fixed_omits_count_from_message(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=0, skipped=2)
        commit_call = mock_run.call_args_list[2]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert msg == "fix: self-review findings"

    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commit_includes_fix_summary(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        review = Path(job.review_file)
        review.write_text(
            "- [x] **[M1]** fixed\n"
            "- [ ] **[S1]** skipped\n"
            "<!-- fix-summary\n"
            "## Fixed\n"
            "- [M1] corrected wrong condition\n"
            "\n"
            "## Skipped\n"
            "- [S1] reason: requires design decision\n"
            "-->\n"
        )
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=1, skipped=1)
        commit_call = mock_run.call_args_list[2]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "1 fixed, 1 skipped" in msg
        assert "corrected wrong condition" in msg
        assert "requires design decision" in msg


class TestExtractFixSummary:
    def test_extracts_summary(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "- [x] **[M1]** fixed\n"
            "<!-- fix-summary\n"
            "## Fixed\n"
            "- [M1] corrected wrong condition\n"
            "\n"
            "## Skipped\n"
            "- [S1] reason: requires design decision\n"
            "-->\n"
        )
        summary = review_pipeline._extract_fix_summary(str(review))
        assert "corrected wrong condition" in summary
        assert "requires design decision" in summary

    def test_returns_empty_when_no_summary(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text("- [x] **[M1]** fixed\n")
        assert review_pipeline._extract_fix_summary(str(review)) == ""

    def test_returns_empty_for_missing_file(self):
        assert review_pipeline._extract_fix_summary("/nonexistent/review.md") == ""


class TestTurnBudgetScaling:
    def test_small_review_uses_default(self):
        turns = min(max(review_pipeline.DEFAULT_MAX_TURNS_FIX, 5 * 2), review_pipeline.MAX_TURNS_FIX_CAP)
        assert turns == review_pipeline.DEFAULT_MAX_TURNS_FIX

    def test_large_review_scales_up(self):
        turns = min(max(review_pipeline.DEFAULT_MAX_TURNS_FIX, 25 * 2), review_pipeline.MAX_TURNS_FIX_CAP)
        assert turns == 50

    def test_very_large_review_caps(self):
        turns = min(max(review_pipeline.DEFAULT_MAX_TURNS_FIX, 100 * 2), review_pipeline.MAX_TURNS_FIX_CAP)
        assert turns == review_pipeline.MAX_TURNS_FIX_CAP
