import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review_findings
import review_pipeline
from review_findings import Finding


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
        assert mock_run.call_count == 2  # unstaged + staged checks

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


class TestParseCheckboxState:
    def test_unchecked_finding(self):
        text = "## Must fix\n- [ ] **[M1]** **`file.go:10`** — Bug found\n"
        findings = review_findings.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].checked is False

    def test_checked_finding(self):
        text = "## Must fix\n- [x] **[M1]** **`file.go:10`** — Bug fixed\n"
        findings = review_findings.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].checked is True

    def test_no_checkbox_finding(self):
        text = "## Must fix\n- **[M1]** **`file.go:10`** — Bug found\n"
        findings = review_findings.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].checked is False

    def test_mixed_checkbox_states(self):
        text = (
            "## Must fix\n"
            "- [x] **[M1]** **`a.go:1`** — Fixed\n"
            "- [ ] **[M2]** **`b.go:2`** — Not fixed\n"
            "## Nit\n"
            "- [x] **[N1]** **`c.go:3`** — Also fixed\n"
        )
        findings = review_findings.parse_findings(text)
        assert len(findings) == 3
        by_id = {f.id: f for f in findings}
        assert by_id["M1"].checked is True
        assert by_id["M2"].checked is False
        assert by_id["N1"].checked is True


class TestExtractSkipReasons:
    def test_extracts_skip_reason_em_dash(self):
        findings = [Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped — requires design decision)* — Some finding body",
        )]
        review_findings.extract_skip_reasons(findings)
        assert findings[0].skip_reason == "requires design decision"

    def test_extracts_skip_reason_double_hyphen(self):
        findings = [Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped -- needs confirmation)* — body",
        )]
        review_findings.extract_skip_reasons(findings)
        assert findings[0].skip_reason == "needs confirmation"

    def test_no_skip_reason(self):
        findings = [Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="Plain finding body",
        )]
        review_findings.extract_skip_reasons(findings)
        assert findings[0].skip_reason == ""

    def test_skips_checked_findings(self):
        findings = [Finding(
            id="M1", severity="M", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped — stale)* — body", checked=True,
        )]
        review_findings.extract_skip_reasons(findings)
        assert findings[0].skip_reason == ""


class TestDiffFindings:
    def _finding(self, fid, checked=False, skip_reason=""):
        sev = fid[0]
        seq = int(fid[1:])
        return Finding(
            id=fid, severity=sev, seq=seq, path="file.go",
            line=1, end_line=None, body="body",
            checked=checked, skip_reason=skip_reason,
        )

    def test_finding_fixed(self):
        before = [self._finding("M1", checked=False)]
        after = [self._finding("M1", checked=True)]
        result = review_pipeline._diff_findings(before, after)
        assert result.fixed_count == 1
        assert result.skipped_count == 0

    def test_finding_skipped_with_reason(self):
        before = [self._finding("S1", checked=False)]
        after = [self._finding("S1", checked=False, skip_reason="needs design")]
        result = review_pipeline._diff_findings(before, after)
        assert result.fixed_count == 0
        assert result.skipped_count == 1
        assert result.skipped[0].skip_reason == "needs design"

    def test_finding_skipped_without_reason(self):
        before = [self._finding("N1", checked=False)]
        after = [self._finding("N1", checked=False)]
        result = review_pipeline._diff_findings(before, after)
        assert result.fixed_count == 0
        assert result.skipped_count == 1

    def test_already_checked_is_unchanged(self):
        before = [self._finding("M1", checked=True)]
        after = [self._finding("M1", checked=True)]
        result = review_pipeline._diff_findings(before, after)
        assert result.fixed_count == 0
        assert result.skipped_count == 0
        assert len(result.unchanged) == 1

    def test_mixed_outcomes(self):
        before = [
            self._finding("M1", checked=False),
            self._finding("S1", checked=False),
            self._finding("N1", checked=True),
        ]
        after = [
            self._finding("M1", checked=True),
            self._finding("S1", checked=False, skip_reason="design choice"),
            self._finding("N1", checked=True),
        ]
        result = review_pipeline._diff_findings(before, after)
        assert result.fixed_count == 1
        assert result.skipped_count == 1
        assert len(result.unchanged) == 1


class TestFormatFixSummary:
    def _finding(self, fid, body="body", skip_reason=""):
        sev = fid[0]
        seq = int(fid[1:])
        return Finding(
            id=fid, severity=sev, seq=seq, path="file.go",
            line=1, end_line=None, body=body,
            checked=True, skip_reason=skip_reason,
        )

    def test_fixed_and_skipped(self):
        result = review_pipeline.FixPassResult(
            fixed=[self._finding("M1", body="corrected condition")],
            skipped=[self._finding("S1", body="body", skip_reason="needs design")],
            unchanged=[],
        )
        summary = review_pipeline._format_fix_summary(result)
        assert "Fixed:" in summary
        assert "[M1]" in summary
        assert "corrected condition" in summary
        assert "Skipped:" in summary
        assert "[S1]" in summary
        assert "needs design" in summary

    def test_empty_result(self):
        result = review_pipeline.FixPassResult(fixed=[], skipped=[], unchanged=[])
        assert review_pipeline._format_fix_summary(result) == ""

    def test_skipped_without_reason_uses_default(self):
        result = review_pipeline.FixPassResult(
            fixed=[],
            skipped=[self._finding("N1", body="body", skip_reason="")],
            unchanged=[],
        )
        summary = review_pipeline._format_fix_summary(result)
        assert "no auto-fix" in summary


class TestCommitFixesWithSummary:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        job.review_file = str(tmp_path / "review.md")
        return job

    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commit_includes_summary(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        summary = "Fixed:\n  - [M1] corrected condition\nSkipped:\n  - [S1] needs design"
        review_pipeline._commit_fixes(job, fixed=1, skipped=1, summary=summary)
        commit_call = mock_run.call_args_list[2]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "1 fixed, 1 skipped" in msg
        assert "corrected condition" in msg
        assert "needs design" in msg

    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commit_without_summary(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=2, skipped=0, summary="")
        commit_call = mock_run.call_args_list[2]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "2 fixed, 0 skipped" in msg
        assert msg.count("\n\n") == 1


class TestHasUncommittedChanges:
    @patch("review_pipeline.subprocess.run")
    def test_unstaged_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert review_pipeline._has_uncommitted_changes("/tmp/wt") is True
        assert mock_run.call_count == 1

    @patch("review_pipeline.subprocess.run")
    def test_staged_only_changes(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # unstaged clean
            MagicMock(returncode=1),  # staged dirty
        ]
        assert review_pipeline._has_uncommitted_changes("/tmp/wt") is True
        assert mock_run.call_count == 2

    @patch("review_pipeline.subprocess.run")
    def test_no_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert review_pipeline._has_uncommitted_changes("/tmp/wt") is False
        assert mock_run.call_count == 2


class TestCommitFixesStagedChanges:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        job.review_file = str(tmp_path / "review.md")
        return job

    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_staged_only_changes_still_commit(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=0),  # unstaged clean
            MagicMock(returncode=1),  # staged dirty
            MagicMock(returncode=0),  # git add -u
            MagicMock(returncode=0, stdout="", stderr=""),  # git commit
        ]
        review_pipeline._commit_fixes(job, fixed=3, skipped=0)
        assert mock_run.call_count == 4
        commit_call = mock_run.call_args_list[3]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "3 fixed, 0 skipped" in msg


class TestReconcileCheckboxes:
    @patch("review_pipeline._changed_source_files")
    def test_checks_matching_findings(self, mock_changed, tmp_path):
        mock_changed.return_value = {"src/auth.go", "src/config.go"}
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** **`src/auth.go:10`** — Missing nil check\n"
            "## Nit\n"
            "- [ ] **[N1]** **`src/unrelated.go:5`** — Style issue\n"
        )
        review_pipeline._reconcile_checkboxes(str(review), str(tmp_path))
        text = review.read_text()
        assert "- [x] **[M1]**" in text
        assert "- [ ] **[N1]**" in text

    @patch("review_pipeline._changed_source_files")
    def test_no_changes_is_noop(self, mock_changed, tmp_path):
        mock_changed.return_value = set()
        review = tmp_path / "review.md"
        original = "- [ ] **[M1]** **`src/auth.go:10`** — Bug\n"
        review.write_text(original)
        review_pipeline._reconcile_checkboxes(str(review), str(tmp_path))
        assert review.read_text() == original

    @patch("review_pipeline._changed_source_files")
    def test_already_checked_not_modified(self, mock_changed, tmp_path):
        mock_changed.return_value = {"src/auth.go"}
        review = tmp_path / "review.md"
        original = "- [x] **[M1]** **`src/auth.go:10`** — Already fixed\n"
        review.write_text(original)
        review_pipeline._reconcile_checkboxes(str(review), str(tmp_path))
        assert review.read_text() == original


class TestTurnBudgetScaling:
    def test_small_review_uses_default(self):
        turns = review_pipeline._fix_turn_budget(5)
        assert turns == review_pipeline.DEFAULT_MAX_TURNS_FIX

    def test_large_review_scales_up(self):
        turns = review_pipeline._fix_turn_budget(25)
        assert turns == 50

    def test_very_large_review_caps(self):
        turns = review_pipeline._fix_turn_budget(100)
        assert turns == review_pipeline.MAX_TURNS_FIX_CAP
