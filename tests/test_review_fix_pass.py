import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review_common
import review_findings
import review_pipeline
from review_common import Diagnosis, DiagnosisKind, Effort, Phase
from review_findings import Finding

_MAX_TURNS = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)
_AGENT_ERROR = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="overloaded")


class TestCommitFixes:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        job.review_file = str(tmp_path / "review.md")
        return job

    @patch("review_pipeline.has_uncommitted_changes", return_value=False)
    @patch("review_pipeline.subprocess.run")
    def test_no_diff_returns_early(self, mock_run, mock_dirty, tmp_path):
        review_pipeline._commit_fixes(self._make_job(tmp_path), fixed=3, skipped=1)
        mock_run.assert_not_called()

    @patch("review_pipeline.has_uncommitted_changes", return_value=True)
    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commits_with_counts(self, mock_run, mock_push, mock_dirty, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=3, skipped=1)
        commit_call = mock_run.call_args_list[1]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "3 fixed, 1 skipped" in msg

    @patch("review_pipeline.has_uncommitted_changes", return_value=True)
    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_zero_fixed_omits_count_from_message(
        self, mock_run, mock_push, mock_dirty, tmp_path,
    ):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=0, skipped=2)
        commit_call = mock_run.call_args_list[1]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert msg == "fix: self-review findings"

    @patch("review_pipeline.has_uncommitted_changes", return_value=True)
    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_untracked_only_changes_are_staged(
        self, mock_run, mock_push, mock_dirty, tmp_path,
    ):
        """A fix agent that only adds new files must still get them committed."""
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=1, skipped=0)
        add_call = mock_run.call_args_list[0][0][0]
        assert add_call[-2:] == ["add", "-A"]
        assert mock_run.call_count == 2


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

    @patch("review_pipeline.has_uncommitted_changes", return_value=True)
    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commit_includes_summary(self, mock_run, mock_push, mock_dirty, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        summary = "Fixed:\n  - [M1] corrected condition\nSkipped:\n  - [S1] needs design"
        review_pipeline._commit_fixes(job, fixed=1, skipped=1, summary=summary)
        commit_call = mock_run.call_args_list[1]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "1 fixed, 1 skipped" in msg
        assert "corrected condition" in msg
        assert "needs design" in msg

    @patch("review_pipeline.has_uncommitted_changes", return_value=True)
    @patch("review_pipeline._push_fixes")
    @patch("review_pipeline.subprocess.run")
    def test_commit_without_summary(self, mock_run, mock_push, mock_dirty, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        review_pipeline._commit_fixes(job, fixed=2, skipped=0, summary="")
        commit_call = mock_run.call_args_list[1]
        msg = commit_call[0][0][commit_call[0][0].index("-m") + 1]
        assert "2 fixed, 0 skipped" in msg
        assert msg.count("\n\n") == 1


class TestHasUncommittedChanges:
    """The commit gate for every fix pass — pipeline, ci-check, review-threads."""

    @patch("review_common.subprocess.run")
    def test_unstaged_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=" M handler.go\n")
        assert review_common.has_uncommitted_changes("/tmp/wt") is True
        assert mock_run.call_count == 1

    @patch("review_common.subprocess.run")
    def test_staged_only_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="M  handler.go\n")
        assert review_common.has_uncommitted_changes("/tmp/wt") is True

    @patch("review_common.subprocess.run")
    def test_untracked_only_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="?? tests/run_ai.bats\n")
        assert review_common.has_uncommitted_changes("/tmp/wt") is True

    @patch("review_common.subprocess.run")
    def test_no_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert review_common.has_uncommitted_changes("/tmp/wt") is False

    @patch("review_common.subprocess.run")
    def test_accepts_a_path_object(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        review_common.has_uncommitted_changes(Path("/tmp/wt"))
        assert mock_run.call_args[0][0][2] == "/tmp/wt"


class TestPushFixes:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        return job

    def _push_result(self, stderr, stdout=""):
        return MagicMock(returncode=1, stdout=stdout, stderr=stderr)

    def _rev_parse_result(self, sha):
        return MagicMock(returncode=0, stdout=f"{sha}\n", stderr="")

    @patch("review_pipeline.log")
    @patch("review_pipeline.subprocess.run")
    def test_diverged_push_suggests_force_with_lease(self, mock_run, mock_log, tmp_path):
        mock_run.return_value = self._push_result(
            "! [rejected] main -> main (non-fast-forward)"
        )
        review_pipeline._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "diverged" in msg
        assert "--force-with-lease" in msg

    @patch("review_pipeline.log")
    @patch("review_pipeline.subprocess.run")
    def test_hook_failure_does_not_suggest_force_push(self, mock_run, mock_log, tmp_path):
        """A pre-push hook rejection is not divergence — force-pushing is wrong advice."""
        mock_run.side_effect = [
            self._push_result(
                "SC2164 (warning): Use 'cd ... || exit'\nerror: failed to push some refs"
            ),
            self._rev_parse_result("9bc3f64"),
        ]
        review_pipeline._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "--force-with-lease" not in msg

    @patch("review_pipeline.log")
    @patch("review_pipeline.subprocess.run")
    def test_hook_rejection_names_the_gate_the_commit_and_the_repair(
        self, mock_run, mock_log, tmp_path,
    ):
        """The fixes are committed but failed the repo's own checks — say so."""
        mock_run.side_effect = [
            self._push_result(
                "✗ Pytest failed\nerror: failed to push some refs to 'github.com:o/r.git'",
                stdout="FAILED tests/test_review_threads.py::TestRunReply::test_errors",
            ),
            self._rev_parse_result("9bc3f64"),
        ]
        review_pipeline._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "pre-push checks" in msg
        assert "9bc3f64" in msg
        assert "Pytest failed" in msg
        assert "FAILED tests/test_review_threads.py" in msg
        assert "Repair, then: git -C" in msg

    @patch("review_pipeline.log")
    @patch("review_pipeline.subprocess.run")
    def test_transport_failure_is_not_reported_as_a_failed_gate(
        self, mock_run, mock_log, tmp_path,
    ):
        """Nothing is wrong with the commit when the network is what broke."""
        mock_run.return_value = self._push_result(
            "ssh: Could not resolve hostname github.com\n"
            "fatal: Could not read from remote repository.\n"
            "error: failed to push some refs to 'github.com:o/r.git'"
        )
        review_pipeline._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "pre-push checks" not in msg
        assert "committed locally but not pushed" in msg

    @patch("review_pipeline.log")
    @patch("review_pipeline.subprocess.run")
    def test_successful_push_logs_no_error(self, mock_run, mock_log, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        review_pipeline._push_fixes(self._make_job(tmp_path))
        mock_log.error.assert_not_called()


class TestIsLocalHookRejection:
    """Which push failures came from the local gate rather than the remote."""

    def test_claims_a_bare_refusal_with_no_rejected_ref(self):
        assert review_pipeline._is_local_hook_rejection(
            "✗ Pytest failed\nerror: failed to push some refs to 'github.com:o/r.git'"
        )

    def test_disclaims_a_rejected_ref(self):
        """A per-ref rejection means git reached the remote and it said no."""
        assert not review_pipeline._is_local_hook_rejection(
            "! [rejected] main -> main (fetch first)\nerror: failed to push some refs"
        )

    def test_disclaims_an_auth_failure(self):
        assert not review_pipeline._is_local_hook_rejection(
            "fatal: Authentication failed for 'https://github.com/o/r.git/'\n"
            "error: failed to push some refs"
        )

    def test_disclaims_output_that_never_refused_the_push(self):
        assert not review_pipeline._is_local_hook_rejection("Everything up-to-date")


class TestHookOutput:

    def test_merges_both_streams_so_the_failing_gate_survives(self):
        result = MagicMock(stdout="running pytest", stderr="✗ Pytest failed")
        out = review_pipeline._hook_output(result)
        assert "running pytest" in out
        assert "✗ Pytest failed" in out

    def test_indents_every_line_under_the_error(self):
        result = MagicMock(stdout="a\nb", stderr="")
        assert review_pipeline._hook_output(result) == "  a\n  b"

    def test_keeps_only_the_tail_of_a_long_gate_dump(self):
        result = MagicMock(stdout="\n".join(str(n) for n in range(50)), stderr="")
        lines = review_pipeline._hook_output(result).splitlines()
        assert len(lines) == review_pipeline._HOOK_OUTPUT_LINES
        assert lines[-1] == "  49"

    def test_survives_a_stream_git_left_empty(self):
        result = MagicMock(stdout=None, stderr="✗ Pytest failed")
        assert review_pipeline._hook_output(result) == "  ✗ Pytest failed"


class TestHeadSha:

    @patch("review_pipeline.subprocess.run")
    def test_returns_the_short_sha(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="9bc3f64\n", stderr="")
        assert review_pipeline._head_sha("/wt") == "9bc3f64"

    @patch("review_pipeline.subprocess.run")
    def test_falls_back_to_head_when_rev_parse_fails(self, mock_run):
        """The repair instruction still reads correctly without a SHA."""
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal")
        assert review_pipeline._head_sha("/wt") == "HEAD"


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

    @patch("review_pipeline._changed_source_files")
    def test_checks_findings_on_extensionless_scripts(self, mock_changed, tmp_path):
        """A finding on a bin script must reconcile, or it reports as skipped."""
        mock_changed.return_value = {"ai/claude/bin/ci-check"}
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** `ai/claude/bin/ci-check:777` — No session_log\n"
        )
        review_pipeline._reconcile_checkboxes(str(review), str(tmp_path))
        assert "- [x] **[M1]**" in review.read_text()


class TestChangedSourceFiles:
    @patch("review_pipeline.subprocess.run")
    def test_includes_untracked_files(self, mock_run):
        """A fix that only adds a new test file still fixed the finding."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="src/auth.go\n"),
            MagicMock(returncode=0, stdout="tests/run_ai.bats\n"),
        ]
        assert review_pipeline._changed_source_files("/wt") == {
            "src/auth.go", "tests/run_ai.bats",
        }

    @patch("review_pipeline.subprocess.run")
    def test_untracked_query_excludes_ignored_files(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout=""),
        ]
        review_pipeline._changed_source_files("/wt")
        assert "--exclude-standard" in mock_run.call_args_list[1][0][0]

    @patch("review_pipeline.subprocess.run")
    def test_diff_failure_still_reports_untracked(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=128, stdout=""),
            MagicMock(returncode=0, stdout="tests/new.bats\n"),
        ]
        assert review_pipeline._changed_source_files("/wt") == {"tests/new.bats"}


class TestTurnBudgetScaling:
    def test_small_review_uses_default(self):
        turns = review_pipeline._fix_turn_budget(5)
        assert turns == review_pipeline.PHASES[Phase.FIX].max_turns

    def test_large_review_scales_up(self):
        turns = review_pipeline._fix_turn_budget(25)
        assert turns == 50

    def test_very_large_review_caps(self):
        turns = review_pipeline._fix_turn_budget(100)
        assert turns == review_pipeline.MAX_TURNS_FIX_CAP


class TestFixRetryBudget:
    def test_small_budget_gets_minimum_retry(self):
        assert review_pipeline._fix_retry_budget(20) == 40

    def test_medium_budget_adds_headroom(self):
        assert review_pipeline._fix_retry_budget(30) == 50

    def test_large_budget_caps_at_max(self):
        assert review_pipeline._fix_retry_budget(50) == 60

    def test_already_at_cap_stays_at_cap(self):
        assert review_pipeline._fix_retry_budget(60) == 60


class TestFixPassMadeProgress:
    def _finding(self, fid, checked=False, skip_reason=""):
        sev = fid[0]
        seq = int(fid[1:])
        return Finding(
            id=fid, severity=sev, seq=seq, path="file.go",
            line=1, end_line=None, body="body",
            checked=checked, skip_reason=skip_reason,
        )

    def test_fixed_finding_is_progress(self):
        result = review_pipeline.FixPassResult(
            fixed=[self._finding("M1", checked=True)],
            skipped=[], unchanged=[],
        )
        assert review_pipeline._fix_pass_made_progress(result) is True

    def test_annotated_skip_is_progress(self):
        result = review_pipeline.FixPassResult(
            fixed=[],
            skipped=[self._finding("S1", skip_reason="needs design")],
            unchanged=[],
        )
        assert review_pipeline._fix_pass_made_progress(result) is True

    def test_unannotated_skip_is_no_progress(self):
        result = review_pipeline.FixPassResult(
            fixed=[],
            skipped=[self._finding("N1")],
            unchanged=[],
        )
        assert review_pipeline._fix_pass_made_progress(result) is False

    def test_empty_result_is_no_skips(self):
        result = review_pipeline.FixPassResult(fixed=[], skipped=[], unchanged=[])
        assert review_pipeline._fix_pass_made_progress(result) is False

    def test_mixed_annotated_and_unannotated_is_progress(self):
        result = review_pipeline.FixPassResult(
            fixed=[],
            skipped=[
                self._finding("S1", skip_reason="design choice"),
                self._finding("N1"),
            ],
            unchanged=[],
        )
        assert review_pipeline._fix_pass_made_progress(result) is True


class TestRunFixPassRetry:
    REVIEW_CONTENT = (
        "## Should fix\n"
        "- [ ] **[S1]** `src/auth.go:10` — Missing nil check\n"
        "## Nit\n"
        "- [ ] **[N1]** `src/config.go:5` — Style issue\n"
    )

    def _make_job(self, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text(self.REVIEW_CONTENT)
        job = MagicMock()
        job.review_file = str(review_file)
        job.wt_path = str(tmp_path)
        job.model = None
        job.effort = Effort.MEDIUM
        return job

    @patch("review_pipeline._commit_fixes")
    @patch("review_pipeline._reconcile_checkboxes")
    @patch("review_pipeline.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_pipeline.build_prompt", return_value="prompt")
    def test_retries_on_zero_progress_max_turns(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)
        assert mock_invoke.call_count == 0
        review_pipeline.run_fix_pass(job)
        assert mock_invoke.call_count == 2
        retry_call = mock_invoke.call_args_list[1]
        assert retry_call[0][0].prompt.startswith("IMPORTANT: A previous attempt")

    @patch("review_pipeline._commit_fixes")
    @patch("review_pipeline._reconcile_checkboxes")
    @patch("review_phases.invoke_agent")
    @patch("review_pipeline.build_prompt", return_value="prompt")
    def test_no_retry_when_fixes_applied(
        self, mock_prompt, mock_invoke, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)

        def apply_fix(*args, **kwargs):
            text = Path(job.review_file).read_text()
            Path(job.review_file).write_text(text.replace("- [ ] **[S1]**", "- [x] **[S1]**"))

        mock_invoke.side_effect = apply_fix
        review_pipeline.run_fix_pass(job)
        assert mock_invoke.call_count == 1

    @patch("review_pipeline._commit_fixes")
    @patch("review_pipeline._reconcile_checkboxes")
    @patch("review_phases.invoke_agent")
    @patch("review_pipeline.build_prompt", return_value="prompt")
    def test_no_retry_when_skip_reasons_annotated(
        self, mock_prompt, mock_invoke, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)

        def annotate_skips(*args, **kwargs):
            text = Path(job.review_file).read_text()
            text = text.replace(
                "— Missing nil check\n",
                "— Missing nil check *(skipped — needs design)*\n",
            )
            Path(job.review_file).write_text(text)

        mock_invoke.side_effect = annotate_skips
        review_pipeline.run_fix_pass(job)
        assert mock_invoke.call_count == 1

    @patch("review_pipeline._commit_fixes")
    @patch("review_pipeline._reconcile_checkboxes")
    @patch("review_pipeline.diagnose_missing_output", return_value=_AGENT_ERROR)
    @patch("review_phases.invoke_agent")
    @patch("review_pipeline.build_prompt", return_value="prompt")
    def test_no_retry_on_non_retryable_reason(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)
        review_pipeline.run_fix_pass(job)
        assert mock_invoke.call_count == 1

    @patch("review_pipeline._commit_fixes")
    @patch("review_pipeline._reconcile_checkboxes")
    @patch("review_pipeline.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_pipeline.build_prompt", return_value="prompt")
    def test_retry_uses_increased_turns(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)
        review_pipeline.run_fix_pass(job)
        retry_call = mock_invoke.call_args_list[1]
        assert retry_call[0][0].max_turns == review_pipeline._fix_retry_budget(
            review_pipeline._fix_turn_budget(2),
        )

    @patch("review_pipeline._commit_fixes")
    @patch("review_pipeline._reconcile_checkboxes")
    @patch("review_pipeline.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_pipeline.build_prompt", return_value="prompt")
    def test_never_runs_under_a_read_only_reviewer_agent(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        """Both the first attempt and the retry must be able to edit the branch.

        Every AgentKind is a review persona told never to modify source files,
        which flatly contradicts the fix prompt's "apply the fix using the Edit
        tool". Passing one made the pass a coin-flip on whether the model obeyed
        the system prompt or the task.
        """
        job = self._make_job(tmp_path)
        review_pipeline.run_fix_pass(job)
        assert mock_invoke.call_count == 2
        agents = [c.args[0].agent for c in mock_invoke.call_args_list]
        assert agents == [None, None]
