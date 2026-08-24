import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import push
import review_common
import review_findings
import review_fix
from proc import TIMEOUT_RETURNCODE, CmdResult
from review_common import Diagnosis, DiagnosisKind, Effort, Phase
from review_findings import Finding

_MAX_TURNS = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)
_AGENT_ERROR = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="overloaded")


def _git(wt: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(wt), *args],
        capture_output=True, text=True,
    )
    # Raised rather than `check=True`: a CalledProcessError renders as its exit
    # code alone, so a broken fixture arrives without the git error that says
    # what broke.
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}) in {wt}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout


@pytest.fixture
def git_wt(tmp_path):
    """A real repo with one commit — the fix pass's staging is git behaviour."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    # Empty hooks dir: the developer's own `core.hooksPath` is global, so
    # without this the fixture runs their pre-commit hook and the suite passes
    # or fails on whatever that machine has installed.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "test@example.com")
    _git(wt, "config", "user.name", "Test")
    _git(wt, "config", "commit.gpgsign", "false")
    _git(wt, "config", "core.hooksPath", str(hooks))
    (wt / "src.py").write_text("original\n")
    (wt / ".gitignore").write_text("*.cache\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "initial")
    return wt


def _install_failing_pre_commit(tmp_path, message: str = "gate refused") -> None:
    """Make every later `git commit` in `git_wt` fail, the way a hook does."""
    hook = tmp_path / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n")
    hook.chmod(0o755)


def _committed_paths(wt: Path) -> set[str]:
    # quotePath=false for the same reason `_changed_source_files` sets it: git
    # escapes a non-ASCII name by default, and the assertion would compare the
    # escaped spelling against the real one.
    out = _git(
        wt, "-c", "core.quotePath=false",
        "show", "--name-only", "--pretty=format:", "HEAD",
    )
    return {line for line in out.strip().splitlines() if line}


class TestCommitFixes:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        job.review_file = str(tmp_path / "review.md")
        return job

    @patch("review_fix.git_client.run")
    def test_no_agent_changes_returns_early(self, mock_run, tmp_path):
        review_fix._commit_fixes(self._make_job(tmp_path), set(), fixed=3, skipped=1)
        mock_run.assert_not_called()

    @patch("review_fix._push_fixes")
    @patch("review_fix.git_client.run")
    def test_commits_with_counts(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [CmdResult(), CmdResult()]
        review_fix._commit_fixes(job, {"a.go"}, fixed=3, skipped=1)
        commit_args = mock_run.call_args_list[1].args
        msg = commit_args[commit_args.index("-m") + 1]
        assert "3 fixed, 1 skipped" in msg

    @patch("review_fix._push_fixes")
    @patch("review_fix.git_client.run")
    def test_zero_fixed_omits_count_from_message(
        self, mock_run, mock_push, tmp_path,
    ):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [CmdResult(), CmdResult()]
        review_fix._commit_fixes(job, {"a.go"}, fixed=0, skipped=2)
        commit_args = mock_run.call_args_list[1].args
        msg = commit_args[commit_args.index("-m") + 1]
        assert msg == "fix: self-review findings"

    @patch("review_fix._push_fixes")
    @patch("review_fix.git_client.run")
    def test_stages_only_the_named_paths(self, mock_run, mock_push, tmp_path):
        """`git add -A` swept up whatever else was sitting in the worktree."""
        job = self._make_job(tmp_path)
        mock_run.side_effect = [CmdResult(), CmdResult()]
        review_fix._commit_fixes(job, {"b.go", "a.go"}, fixed=1, skipped=0)
        add_call = list(mock_run.call_args_list[0].args)
        assert add_call[-3:] == ["--", ":(literal)a.go", ":(literal)b.go"]
        assert "-A" not in add_call
        assert mock_run.call_count == 2


class TestCommitFixesStaging:
    """End-to-end against a real repo — the leak was in what git ended up with."""

    def _make_job(self, wt):
        job = MagicMock()
        job.wt_path = str(wt)
        return job

    @patch("review_fix._push_fixes")
    def test_pre_existing_untracked_file_stays_out(self, mock_push, git_wt):
        (git_wt / "tsconfig.tsbuildinfo").write_text("stale cache\n")
        (git_wt / "tests_new.py").write_text("def test_x(): pass\n")

        review_fix._commit_fixes(
            self._make_job(git_wt), {"tests_new.py"}, fixed=1, skipped=0,
        )

        assert _committed_paths(git_wt) == {"tests_new.py"}
        assert "tsconfig.tsbuildinfo" in _git(git_wt, "status", "--porcelain")

    @patch("review_fix._push_fixes")
    def test_agent_created_file_is_committed(self, mock_push, git_wt):
        (git_wt / "fixture.json").write_text("{}\n")
        review_fix._commit_fixes(
            self._make_job(git_wt), {"fixture.json"}, fixed=1, skipped=0,
        )
        assert _committed_paths(git_wt) == {"fixture.json"}

    @patch("review_fix._push_fixes")
    def test_content_staged_before_the_pass_is_not_committed(self, mock_push, git_wt):
        (git_wt / "src.py").write_text("operator work in progress\n")
        _git(git_wt, "add", "src.py")
        (git_wt / "fixture.json").write_text("{}\n")

        review_fix._commit_fixes(
            self._make_job(git_wt), {"fixture.json"}, fixed=1, skipped=0,
        )

        assert _committed_paths(git_wt) == {"fixture.json"}
        assert _git(git_wt, "show", "HEAD:src.py") == "original\n"

    @patch("review_fix._push_fixes")
    def test_a_glob_metacharacter_in_a_name_matches_only_itself(
        self, mock_push, git_wt,
    ):
        """git reads the staged names as pathspecs, so a bracket is a glob.

        `report[1].md` as a pattern matches `report1.md` and not the file it was
        spelled from — so the decoy here is what a non-literal pathspec commits,
        while the file the agent actually changed is left behind.
        """
        (git_wt / "report[1].md").write_text("the agent's work\n")
        (git_wt / "report1.md").write_text("unrelated, still in progress\n")

        review_fix._commit_fixes(
            self._make_job(git_wt), {"report[1].md"}, fixed=1, skipped=0,
        )

        assert _committed_paths(git_wt) == {"report[1].md"}
        assert "report1.md" in _git(git_wt, "status", "--porcelain")

    @patch("review_fix.log")
    @patch("review_fix._push_fixes")
    def test_nothing_is_pushed_when_the_commit_fails(
        self, mock_push, mock_log, git_wt, tmp_path, live_git_hooks,
    ):
        """A commit git refused must not reach the push — there is nothing there.

        Driven by a real pre-commit hook rather than an empty path set: the
        empty set returns at the guard above, never reaching the commit whose
        failure this is about. `live_git_hooks` is what lets the hook run at
        all — the suite disowns hooks by default.
        """
        _install_failing_pre_commit(tmp_path)
        (git_wt / "fixture.json").write_text("{}\n")

        review_fix._commit_fixes(
            self._make_job(git_wt), {"fixture.json"}, fixed=1, skipped=0,
        )

        mock_push.assert_not_called()
        assert _git(git_wt, "log", "--oneline").count("\n") == 1
        assert "gate refused" in mock_log.warn.call_args[0][0]


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

    def test_a_skip_without_a_reason_still_registers(self):
        """Mirrors the decline case — a bare annotation is still a skip.

        Read as an ordinary open finding it would be auto-checked as fixed by
        any sibling fix in the same file.
        """
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped)* — Some finding body",
        )
        assert review_findings.match_skip(finding) is not None
        review_findings.extract_skip_reasons([finding])
        assert finding.skip_reason == ""

    def test_a_plain_finding_carries_no_skip(self):
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="Plain finding body",
        )
        assert review_findings.match_skip(finding) is None

    def test_a_checked_finding_carries_no_skip(self):
        finding = Finding(
            id="M1", severity="M", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped — stale)* — body", checked=True,
        )
        assert review_findings.match_skip(finding) is None


class TestParseDeclinedFindings:
    """`*(declined — reason)*` is where an adjudicated verdict survives.

    The `## Prior findings` ledger is stripped before the review file is
    finished, so a decline recorded only there would reach the next fix pass
    looking like an ordinary open finding.
    """

    def test_reads_the_reason_off_the_line(self):
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.go:1` — *(declined — documented `ceiling:` tradeoff)* "
            "— Global lock serialises writes\n"
        )
        findings = review_findings.parse_findings(text)
        assert findings[0].declined is True
        assert findings[0].decline_reason == "documented `ceiling:` tradeoff"

    def test_a_decline_without_a_reason_still_registers(self):
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(declined)* — Body\n"
        findings = review_findings.parse_findings(text)
        assert findings[0].declined is True
        assert findings[0].decline_reason == ""

    def test_a_trailing_annotation_registers(self):
        """The templates also let the annotation close the line."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.go:1` — Global lock *(declined — by design)*\n"
        )
        findings = review_findings.parse_findings(text)
        assert findings[0].declined is True
        assert findings[0].decline_reason == "by design"

    def test_a_finding_that_only_describes_the_annotation_is_not_declined(self):
        """Reviewing this parser writes the annotation into a finding's prose.

        Read as a decline, the finding leaves `run_fix_pass`'s work set and
        `_reconcile_checkboxes` permanently, and nothing warns that it did.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `review_findings.py:99` — The `*(declined — reason)*` "
            "annotation is matched anywhere in the line, so prose trips it\n"
        )
        findings = review_findings.parse_findings(text)
        assert findings[0].declined is False
        assert findings[0].decline_reason == ""

    def test_a_skip_is_not_a_decline(self):
        """A skip is work deferred; a decline is work rejected."""
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(skipped — needs design)* — Body\n"
        findings = review_findings.parse_findings(text)
        assert findings[0].declined is False

    def test_a_file_without_declines_parses_unchanged(self):
        """Review files predating `Declined` must keep parsing."""
        text = (
            "## Must fix\n"
            "- [x] **[M1]** `a.go:1` — Fixed\n"
            "- [ ] **[M2]** `b.go:2` — Still open\n"
        )
        findings = review_findings.parse_findings(text)
        assert [f.declined for f in findings] == [False, False]
        assert [f.decline_reason for f in findings] == ["", ""]


class TestDiffFindings:
    def _finding(self, fid, checked=False, skip_reason="", declined=False):
        sev = fid[0]
        seq = int(fid[1:])
        return Finding(
            id=fid, severity=sev, seq=seq, path="file.go",
            line=1, end_line=None, body="body",
            checked=checked, skip_reason=skip_reason, declined=declined,
        )

    def test_declined_is_bucketed_apart_from_skipped(self):
        """A skip gets retried next pass; a decline must not be."""
        before = [self._finding("M1", checked=False)]
        after = [self._finding("M1", checked=False, declined=True)]
        result = review_fix._diff_findings(before, after)
        assert [f.id for f in result.declined] == ["M1"]
        assert result.skipped == []
        assert result.fixed_count == 0

    def test_finding_fixed(self):
        before = [self._finding("M1", checked=False)]
        after = [self._finding("M1", checked=True)]
        result = review_fix._diff_findings(before, after)
        assert result.fixed_count == 1
        assert result.skipped_count == 0

    def test_finding_skipped_with_reason(self):
        before = [self._finding("S1", checked=False)]
        after = [self._finding("S1", checked=False, skip_reason="needs design")]
        result = review_fix._diff_findings(before, after)
        assert result.fixed_count == 0
        assert result.skipped_count == 1
        assert result.skipped[0].skip_reason == "needs design"

    def test_finding_skipped_without_reason(self):
        before = [self._finding("N1", checked=False)]
        after = [self._finding("N1", checked=False)]
        result = review_fix._diff_findings(before, after)
        assert result.fixed_count == 0
        assert result.skipped_count == 1

    def test_already_checked_is_unchanged(self):
        before = [self._finding("M1", checked=True)]
        after = [self._finding("M1", checked=True)]
        result = review_fix._diff_findings(before, after)
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
        result = review_fix._diff_findings(before, after)
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
        result = review_fix.FixPassResult(
            fixed=[self._finding("M1", body="corrected condition")],
            skipped=[self._finding("S1", body="body", skip_reason="needs design")],
            unchanged=[],
        )
        summary = review_fix._format_fix_summary(result)
        assert "Fixed:" in summary
        assert "[M1]" in summary
        assert "corrected condition" in summary
        assert "Skipped:" in summary
        assert "[S1]" in summary
        assert "needs design" in summary

    def test_empty_result(self):
        result = review_fix.FixPassResult(fixed=[], skipped=[], unchanged=[])
        assert review_fix._format_fix_summary(result) == ""

    def test_skipped_without_reason_uses_default(self):
        result = review_fix.FixPassResult(
            fixed=[],
            skipped=[self._finding("N1", body="body", skip_reason="")],
            unchanged=[],
        )
        summary = review_fix._format_fix_summary(result)
        assert "no auto-fix" in summary

    def test_declined_renders_under_its_own_heading(self):
        declined = self._finding("M2", body="body")
        declined.declined = True
        declined.decline_reason = "documented `ceiling:` tradeoff"
        result = review_fix.FixPassResult(
            fixed=[], skipped=[], unchanged=[], declined=[declined],
        )
        summary = review_fix._format_fix_summary(result)
        assert "Declined:" in summary
        assert "[M2] documented `ceiling:` tradeoff" in summary
        assert "Skipped:" not in summary

    def test_declined_without_a_reason_uses_default(self):
        declined = self._finding("N1", body="body")
        declined.declined = True
        result = review_fix.FixPassResult(
            fixed=[], skipped=[], unchanged=[], declined=[declined],
        )
        assert "adjudicated, not a defect" in review_fix._format_fix_summary(result)


class TestCommitFixesWithSummary:
    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        job.review_file = str(tmp_path / "review.md")
        return job

    @patch("review_fix._push_fixes")
    @patch("review_fix.git_client.run")
    def test_commit_includes_summary(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [CmdResult(), CmdResult()]
        summary = "Fixed:\n  - [M1] corrected condition\nSkipped:\n  - [S1] needs design"
        review_fix._commit_fixes(job, {"a.go"}, fixed=1, skipped=1, summary=summary)
        commit_args = mock_run.call_args_list[1].args
        msg = commit_args[commit_args.index("-m") + 1]
        assert "1 fixed, 1 skipped" in msg
        assert "corrected condition" in msg
        assert "needs design" in msg

    @patch("review_fix._push_fixes")
    @patch("review_fix.git_client.run")
    def test_commit_without_summary(self, mock_run, mock_push, tmp_path):
        job = self._make_job(tmp_path)
        mock_run.side_effect = [CmdResult(), CmdResult()]
        review_fix._commit_fixes(job, {"a.go"}, fixed=2, skipped=0, summary="")
        commit_args = mock_run.call_args_list[1].args
        msg = commit_args[commit_args.index("-m") + 1]
        assert "2 fixed, 0 skipped" in msg
        assert msg.count("\n\n") == 1


class TestHasUncommittedChanges:
    """The commit gate for every fix pass — pipeline, ci-check, review-threads.

    Against a real repo: what counts as dirty is git's answer, and a stubbed
    porcelain line would agree with whatever this test expected.
    """

    def test_a_clean_worktree_is_not_dirty(self, git_wt):
        assert review_common.has_uncommitted_changes(git_wt) is False

    def test_unstaged_changes(self, git_wt):
        (git_wt / "src.py").write_text("edited\n")
        assert review_common.has_uncommitted_changes(git_wt) is True

    def test_staged_only_changes(self, git_wt):
        (git_wt / "src.py").write_text("edited\n")
        _git(git_wt, "add", "src.py")
        assert review_common.has_uncommitted_changes(git_wt) is True

    def test_untracked_only_changes(self, git_wt):
        """A fix that only adds a test file still has to reach the commit."""
        (git_wt / "run_ai.bats").write_text("@test 'x' { true; }\n")
        assert review_common.has_uncommitted_changes(git_wt) is True

    def test_accepts_a_path_object(self, git_wt):
        (git_wt / "src.py").write_text("edited\n")
        assert review_common.has_uncommitted_changes(Path(git_wt)) is True

    def test_a_worktree_git_cannot_read_is_dirty(self, git_wt):
        """Regression: the gate may not answer "nothing to commit" on a failed read.

        A `status` that never completed says nothing about the tree. Read as
        clean, the fix pass returns before staging and reports the agent's
        edits as applied while they sit uncommitted in the worktree.
        """
        (git_wt / "src.py").write_text("edited\n")
        (git_wt / ".git" / "index").write_bytes(b"not an index")
        assert review_common.has_uncommitted_changes(git_wt) is True


class TestCommittedNothing:
    """The other half of the gate, which opens on a worktree git cannot read.

    Against a real `git commit` for the same reason as the class above: which
    failures mean "the change was empty" is git's vocabulary, not this repo's.
    """

    def test_an_empty_commit_is_not_a_rejection(self, git_wt):
        result = review_fix.git_client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert review_common.committed_nothing(result) is True

    def test_staged_but_unchanged_content_is_not_a_rejection(self, git_wt):
        """`add` of an unmodified file stages nothing, so the commit is empty."""
        _git(git_wt, "add", "src.py")
        result = review_fix.git_client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert review_common.committed_nothing(result) is True

    def test_a_hook_rejection_is_a_rejection(self, git_wt, tmp_path, live_git_hooks):
        """`live_git_hooks` is what lets the hook run — the suite disowns them."""
        _install_failing_pre_commit(tmp_path)
        (git_wt / "src.py").write_text("edited\n")
        _git(git_wt, "add", "src.py")
        result = review_fix.git_client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert review_common.committed_nothing(result) is False


class TestPushFixes:
    """The advice each refusal earns.

    The classifier itself moved to the push owner and is tested in
    `push_test.py`; what these assert is that the pass still says the right
    thing about each outcome the owner hands it — including the one it could
    not see before, a push that git reported as a success.
    """

    def _make_job(self, tmp_path):
        job = MagicMock()
        job.wt_path = str(tmp_path / "worktree")
        return job

    def _refused(self, refusal, output, sha="9bc3f64ab"):
        return push.PushResult(
            push.PushStatus.REFUSED, sha=sha, branch="feat/x",
            refusal=refusal, output=output,
        )

    @patch("review_fix.log")
    @patch("review_fix.push.push")
    def test_diverged_push_suggests_force_with_lease(self, mock_push, mock_log, tmp_path):
        mock_push.return_value = self._refused(
            push.Refusal.DIVERGED, "! [rejected] main -> main (non-fast-forward)"
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "diverged" in msg
        assert "--force-with-lease" in msg

    @patch("review_fix.log")
    @patch("review_fix.push.push")
    def test_hook_failure_does_not_suggest_force_push(self, mock_push, mock_log, tmp_path):
        """A pre-push hook rejection is not divergence — force-pushing is wrong advice."""
        mock_push.return_value = self._refused(
            push.Refusal.HOOK,
            "SC2164 (warning): Use 'cd ... || exit'\nerror: failed to push some refs",
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "--force-with-lease" not in msg

    @patch("review_fix.log")
    @patch("review_fix.push.push")
    def test_hook_rejection_names_the_gate_the_commit_and_the_repair(
        self, mock_push, mock_log, tmp_path,
    ):
        """The fixes are committed but failed the repo's own checks — say so."""
        mock_push.return_value = self._refused(
            push.Refusal.HOOK,
            "FAILED tests/test_review_threads.py::TestRunReply::test_errors\n"
            "✗ Pytest failed\nerror: failed to push some refs to 'github.com:o/r.git'",
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "pre-push checks" in msg
        assert "9bc3f64" in msg
        assert "Pytest failed" in msg
        assert "FAILED tests/test_review_threads.py" in msg
        assert "Repair, then: git -C" in msg

    @patch("review_fix.log")
    @patch("review_fix.push.push")
    def test_transport_failure_is_not_reported_as_a_failed_gate(
        self, mock_push, mock_log, tmp_path,
    ):
        """Nothing is wrong with the commit when the network is what broke."""
        mock_push.return_value = self._refused(
            push.Refusal.TRANSPORT,
            "ssh: Could not resolve hostname github.com\n"
            "fatal: Could not read from remote repository.\n"
            "error: failed to push some refs to 'github.com:o/r.git'",
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        msg = mock_log.error.call_args[0][0]
        assert "pre-push checks" not in msg
        assert "committed locally but not pushed" in msg

    @patch("review_fix.log")
    @patch("review_fix.push.push")
    def test_successful_push_logs_no_error(self, mock_push, mock_log, tmp_path):
        mock_push.return_value = push.PushResult(
            push.PushStatus.PUSHED, sha="9bc3f64ab", branch="feat/x",
            remote_sha="9bc3f64ab",
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        mock_log.error.assert_not_called()

    @patch("review_fix.push.push")
    def test_a_push_that_never_landed_is_reported(self, mock_push, tmp_path, capsys):
        """git exited zero, so the old code called this a success and moved on."""
        mock_push.return_value = push.PushResult(
            push.PushStatus.LOST, sha="9bc3f64ab", branch="feat/x",
            remote_sha="1111111aa",
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        printed = capsys.readouterr().err
        assert "the remote did not move" in printed
        assert "9bc3f64" in printed
        assert "1111111" in printed

    @patch("review_fix.push.push")
    def test_the_pass_pushes_ungated(self, mock_push, tmp_path):
        """The fix pass has no publishing gate; a gated call would draft instead."""
        mock_push.return_value = push.PushResult(
            push.PushStatus.PUSHED, sha="9bc3f64ab", branch="feat/x",
            remote_sha="9bc3f64ab",
        )
        review_fix._push_fixes(self._make_job(tmp_path))
        assert mock_push.call_args.kwargs["gated"] is False


class TestReconcileCheckboxes:
    def test_checks_matching_findings(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** **`src/auth.go:10`** — Missing nil check\n"
            "## Nit\n"
            "- [ ] **[N1]** **`src/unrelated.go:5`** — Style issue\n"
        )
        review_fix._reconcile_checkboxes(
            str(review), {"src/auth.go", "src/config.go"},
        )
        text = review.read_text()
        assert "- [x] **[M1]**" in text
        assert "- [ ] **[N1]**" in text

    def test_no_changes_is_noop(self, tmp_path):
        review = tmp_path / "review.md"
        original = "- [ ] **[M1]** **`src/auth.go:10`** — Bug\n"
        review.write_text(original)
        review_fix._reconcile_checkboxes(str(review), set())
        assert review.read_text() == original

    def test_already_checked_not_modified(self, tmp_path):
        review = tmp_path / "review.md"
        original = "- [x] **[M1]** **`src/auth.go:10`** — Already fixed\n"
        review.write_text(original)
        review_fix._reconcile_checkboxes(str(review), {"src/auth.go"})
        assert review.read_text() == original

    def test_a_declined_finding_is_never_checked_off(self, tmp_path):
        """An incidental edit to the same file is not a fix for a decline."""
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** `src/auth.go:10` — *(declined — documented tradeoff)* — Lock\n"
        )
        review_fix._reconcile_checkboxes(str(review), {"src/auth.go"})
        assert "- [ ] **[M1]**" in review.read_text()

    def test_a_skipped_finding_is_never_checked_off(self, tmp_path):
        """A sibling's fix in the same file is not a fix for a skip.

        Auto-checking matches on file path alone, so the second finding here is
        checked off by the edit the first one earned — and `_diff_findings`
        then reports it as fixed.
        """
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** `src/auth.go:10` — Missing nil check\n"
            "- [ ] **[M2]** `src/auth.go:40` — *(skipped — needs design)* — Lock\n"
        )
        review_fix._reconcile_checkboxes(str(review), {"src/auth.go"})
        text = review.read_text()
        assert "- [x] **[M1]**" in text
        assert "- [ ] **[M2]**" in text

    def test_a_skip_without_a_reason_is_never_checked_off(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** `src/auth.go:10` — Missing nil check\n"
            "- [ ] **[M2]** `src/auth.go:40` — *(skipped)* — Lock\n"
        )
        review_fix._reconcile_checkboxes(str(review), {"src/auth.go"})
        text = review.read_text()
        assert "- [x] **[M1]**" in text
        assert "- [ ] **[M2]**" in text

    def test_a_trailing_skip_annotation_is_honoured(self, tmp_path):
        """The template's example puts the annotation mid-line; agents also trail it."""
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** `src/auth.go:40` — Lock *(skipped — needs design)*\n"
        )
        review_fix._reconcile_checkboxes(str(review), {"src/auth.go"})
        assert "- [ ] **[M1]**" in review.read_text()

    def test_checks_findings_on_extensionless_scripts(self, tmp_path):
        """A finding on a bin script must reconcile, or it reports as skipped."""
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- [ ] **[M1]** `ai/claude/bin/ci-check:777` — No session_log\n"
        )
        review_fix._reconcile_checkboxes(str(review), {"ai/claude/bin/ci-check"})
        assert "- [x] **[M1]**" in review.read_text()


class TestChangedSourceFiles:
    @patch("review_fix.git_client.run")
    def test_includes_untracked_files(self, mock_run):
        """A fix that only adds a new test file still fixed the finding."""
        mock_run.side_effect = [
            CmdResult(0, "src/auth.go\n"),
            CmdResult(0, "tests/run_ai.bats\n"),
        ]
        assert review_fix._changed_source_files("/wt") == {
            "src/auth.go", "tests/run_ai.bats",
        }

    @patch("review_fix.git_client.run")
    def test_untracked_query_excludes_ignored_files(self, mock_run):
        mock_run.side_effect = [CmdResult(), CmdResult()]
        review_fix._changed_source_files("/wt")
        assert "--exclude-standard" in mock_run.call_args_list[1].args

    @patch("review_fix.git_client.run")
    def test_a_failed_diff_is_not_a_partial_snapshot(self, mock_run):
        """Half a snapshot omits the tracked edits, silently and permanently.

        The untracked half answering is not a reason to keep going: every path
        the failed half would have named is a path the pass never commits.
        """
        mock_run.side_effect = [
            CmdResult(128),
            CmdResult(0, "tests/new.bats\n"),
        ]
        assert review_fix._changed_source_files("/wt") is None

    @patch("review_fix.git_client.run")
    def test_a_failed_untracked_listing_is_not_a_partial_snapshot(self, mock_run):
        mock_run.side_effect = [
            CmdResult(0, "src/auth.go\n"),
            CmdResult(128),
        ]
        assert review_fix._changed_source_files("/wt") is None

    @patch("review_fix.git_client.run")
    def test_a_killed_snapshot_is_not_an_empty_one(self, mock_run):
        mock_run.side_effect = [CmdResult(TIMEOUT_RETURNCODE, "", "")]
        assert review_fix._changed_source_files("/wt") is None

    def test_a_path_that_is_not_a_repo_has_no_snapshot(self, tmp_path):
        assert review_fix._changed_source_files(str(tmp_path)) is None

    def test_gitignored_paths_are_in_neither_snapshot(self, git_wt):
        (git_wt / "build.cache").write_text("artifact\n")
        (git_wt / "real.py").write_text("x = 1\n")
        assert review_fix._changed_source_files(str(git_wt)) == {"real.py"}


class TestTurnBudgetScaling:
    def test_small_review_uses_default(self):
        turns = review_fix._fix_turn_budget(5)
        assert turns == review_fix.PHASES[Phase.FIX].max_turns

    def test_large_review_scales_up(self):
        turns = review_fix._fix_turn_budget(25)
        assert turns == 50

    def test_very_large_review_caps(self):
        turns = review_fix._fix_turn_budget(100)
        assert turns == review_fix.MAX_TURNS_FIX_CAP


class TestFixRetryBudget:
    def test_small_budget_gets_minimum_retry(self):
        assert review_fix._fix_retry_budget(20) == 40

    def test_medium_budget_adds_headroom(self):
        assert review_fix._fix_retry_budget(30) == 50

    def test_large_budget_caps_at_max(self):
        assert review_fix._fix_retry_budget(50) == 60

    def test_already_at_cap_stays_at_cap(self):
        assert review_fix._fix_retry_budget(60) == 60


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
        result = review_fix.FixPassResult(
            fixed=[self._finding("M1", checked=True)],
            skipped=[], unchanged=[],
        )
        assert review_fix._fix_pass_made_progress(result) is True

    def test_annotated_skip_is_progress(self):
        result = review_fix.FixPassResult(
            fixed=[],
            skipped=[self._finding("S1", skip_reason="needs design")],
            unchanged=[],
        )
        assert review_fix._fix_pass_made_progress(result) is True

    def test_unannotated_skip_is_no_progress(self):
        result = review_fix.FixPassResult(
            fixed=[],
            skipped=[self._finding("N1")],
            unchanged=[],
        )
        assert review_fix._fix_pass_made_progress(result) is False

    def test_empty_result_is_no_skips(self):
        result = review_fix.FixPassResult(fixed=[], skipped=[], unchanged=[])
        assert review_fix._fix_pass_made_progress(result) is False

    def test_mixed_annotated_and_unannotated_is_progress(self):
        result = review_fix.FixPassResult(
            fixed=[],
            skipped=[
                self._finding("S1", skip_reason="design choice"),
                self._finding("N1"),
            ],
            unchanged=[],
        )
        assert review_fix._fix_pass_made_progress(result) is True


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
        # A real repo with a commit behind it: the pass takes a snapshot of the
        # worktree before it invokes the agent and refuses to run when it cannot,
        # so a bare tmp_path no longer stands in for a worktree.
        wt = tmp_path / "worktree"
        wt.mkdir()
        _git(wt, "init", "-q", "-b", "main")
        _git(wt, "-c", "user.email=t@t", "-c", "user.name=t",
             "-c", "commit.gpgsign=false",
             "commit", "-q", "--allow-empty", "--no-verify", "-m", "initial")
        job.wt_path = str(wt)
        job.model = None
        job.effort = Effort.MEDIUM
        return job

    @patch("review_fix._commit_fixes")
    @patch("review_fix._reconcile_checkboxes")
    @patch("review_fix.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_retries_on_zero_progress_max_turns(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)
        assert mock_invoke.call_count == 0
        review_fix.run_fix_pass(job)
        assert mock_invoke.call_count == 2
        retry_call = mock_invoke.call_args_list[1]
        assert retry_call[0][0].prompt.startswith("IMPORTANT: A previous attempt")

    @patch("review_fix._commit_fixes")
    @patch("review_fix._reconcile_checkboxes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_no_retry_when_fixes_applied(
        self, mock_prompt, mock_invoke, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)

        def apply_fix(*args, **kwargs):
            text = Path(job.review_file).read_text()
            Path(job.review_file).write_text(text.replace("- [ ] **[S1]**", "- [x] **[S1]**"))

        mock_invoke.side_effect = apply_fix
        review_fix.run_fix_pass(job)
        assert mock_invoke.call_count == 1

    @patch("review_fix._commit_fixes")
    @patch("review_fix._reconcile_checkboxes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
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
        review_fix.run_fix_pass(job)
        assert mock_invoke.call_count == 1

    @patch("review_fix._commit_fixes")
    @patch("review_fix._reconcile_checkboxes")
    @patch("review_fix.diagnose_missing_output", return_value=_AGENT_ERROR)
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_no_retry_on_non_retryable_reason(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)
        review_fix.run_fix_pass(job)
        assert mock_invoke.call_count == 1

    @patch("review_fix._commit_fixes")
    @patch("review_fix._reconcile_checkboxes")
    @patch("review_fix.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_retry_uses_increased_turns(
        self, mock_prompt, mock_invoke, mock_diag, mock_reconcile, mock_commit, tmp_path,
    ):
        job = self._make_job(tmp_path)
        review_fix.run_fix_pass(job)
        retry_call = mock_invoke.call_args_list[1]
        assert retry_call[0][0].max_turns == review_fix._fix_retry_budget(
            review_fix._fix_turn_budget(2),
        )

    @patch("review_fix._commit_fixes")
    @patch("review_fix._reconcile_checkboxes")
    @patch("review_fix.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
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
        review_fix.run_fix_pass(job)
        assert mock_invoke.call_count == 2
        agents = [c.args[0].agent for c in mock_invoke.call_args_list]
        assert agents == [None, None]


class TestRunFixPassOnADirtyWorktree:
    """The pass must attribute only its own work.

    A `tsc` run before the review left a 272KB incremental cache untracked in
    the worktree; `git add -A` committed and pushed it, and the post-hoc scan
    checked off a finding on a file that was dirty before the agent started.
    """

    REVIEW_CONTENT = (
        "## Must fix\n"
        "- [ ] **[M1]** `src.py:1` — Was already being edited by hand\n"
        "- [ ] **[M2]** `helper.py:1` — Missing helper\n"
    )

    def _make_job(self, git_wt, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text(self.REVIEW_CONTENT)
        job = MagicMock()
        job.review_file = str(review_file)
        job.wt_path = str(git_wt)
        job.model = None
        job.effort = Effort.MEDIUM
        return job

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_only_the_agents_own_changes_are_committed_and_credited(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path,
    ):
        (git_wt / "tsconfig.tsbuildinfo").write_text("272KB of cache\n")
        (git_wt / "src.py").write_text("hand-edited, not by the fix agent\n")

        def agent_run(*args, **kwargs):
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            (git_wt / "build.cache").write_text("artifact\n")

        mock_invoke.side_effect = agent_run
        job = self._make_job(git_wt, tmp_path)
        review_fix.run_fix_pass(job)

        assert _committed_paths(git_wt) == {"helper.py"}

        status = _git(git_wt, "status", "--porcelain")
        assert "tsconfig.tsbuildinfo" in status
        assert " M src.py" in status
        assert "build.cache" not in status

        review = Path(job.review_file).read_text()
        assert "- [ ] **[M1]**" in review
        assert "- [x] **[M2]**" in review
        mock_push.assert_called_once()


class TestRunFixPassWhenTheSnapshotFails:
    """A snapshot git could not take must never read as an unchanged worktree.

    The difference between the two snapshots is the only list of paths the pass
    commits, so an empty one is indistinguishable from a pass that did nothing —
    which is how a `git status` killed by a SIGPIPE or a locked index ends with
    the agent's fixes discarded and the run reported as a success.
    """

    REVIEW_CONTENT = (
        "## Must fix\n"
        "- [ ] **[M1]** `helper.py:1` — Missing helper\n"
    )

    def _make_job(self, git_wt, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text(self.REVIEW_CONTENT)
        job = MagicMock()
        job.review_file = str(review_file)
        job.wt_path = str(git_wt)
        job.model = None
        job.effort = Effort.MEDIUM
        return job

    @staticmethod
    def _corrupt_index(git_wt):
        """Make every later read of the worktree's state fail, as a lock would."""
        (git_wt / ".git" / "index").write_bytes(b"garbage")

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_an_unreadable_worktree_stops_the_pass_before_the_agent_runs(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path, capsys,
    ):
        """Refusing here costs nothing: the agent has not done any work yet.

        With no baseline the pass cannot tell its own edits from what was
        already in the worktree, so running the agent only produces work it
        would have to either commit wholesale or throw away.
        """
        self._corrupt_index(git_wt)
        review_fix.run_fix_pass(self._make_job(git_wt, tmp_path))

        mock_invoke.assert_not_called()
        mock_push.assert_not_called()
        assert "skipping fix pass" in capsys.readouterr().err

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_the_agents_work_is_not_dropped_when_the_second_snapshot_fails(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path, capsys,
    ):
        """The regression: edits survive in the worktree and the run says so."""
        def agent_run(*args, **kwargs):
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            self._corrupt_index(git_wt)

        mock_invoke.side_effect = agent_run
        job = self._make_job(git_wt, tmp_path)
        review_fix.run_fix_pass(job)

        assert (git_wt / "helper.py").read_text() == "def helper(): pass\n"
        assert _git(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()

        err = capsys.readouterr().err
        assert "nothing was committed or pushed" in err
        assert str(git_wt) in err

    @patch("review_fix._push_fixes")
    @patch("review_fix.diagnose_missing_output", return_value=_MAX_TURNS)
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_the_retrys_work_is_not_dropped_when_the_snapshot_fails(
        self, mock_prompt, mock_invoke, mock_diag, mock_push, git_wt, tmp_path,
        capsys,
    ):
        """The retry path re-reads the worktree, so it can fail the same way."""
        def agent_run(*args, **kwargs):
            if mock_invoke.call_count < 2:
                return
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            self._corrupt_index(git_wt)

        mock_invoke.side_effect = agent_run
        review_fix.run_fix_pass(self._make_job(git_wt, tmp_path))

        assert mock_invoke.call_count == 2
        assert (git_wt / "helper.py").read_text() == "def helper(): pass\n"
        assert _git(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()
        assert "nothing was committed or pushed" in capsys.readouterr().err


class TestSnapshotDiffStagesEveryShapeOfChange:
    """What the snapshot diff must survive besides a plain edit.

    Attribution is a set of path strings, so each case below is a different way
    the two snapshots can disagree about what a path is: gone, moved, or
    spelled with bytes git escapes before it prints them.
    """

    def _make_job(self, git_wt, tmp_path=None, review_content=""):
        job = MagicMock()
        job.wt_path = str(git_wt)
        job.model = None
        job.effort = Effort.MEDIUM
        if tmp_path is not None:
            review_file = tmp_path / "review.md"
            review_file.write_text(review_content)
            job.review_file = str(review_file)
        return job

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_a_file_the_agent_deletes_is_committed_as_a_deletion(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path,
    ):
        (git_wt / "dead_code.py").write_text("unused = 1\n")
        _git(git_wt, "add", "dead_code.py")
        _git(git_wt, "commit", "-qm", "add dead code")

        def agent_run(*args, **kwargs):
            (git_wt / "dead_code.py").unlink()

        mock_invoke.side_effect = agent_run
        job = self._make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `dead_code.py:1` — Dead code, delete it\n",
        )
        review_fix.run_fix_pass(job)

        assert _committed_paths(git_wt) == {"dead_code.py"}
        assert _git(git_wt, "status", "--porcelain").strip() == ""
        assert "- [x] **[N1]**" in Path(job.review_file).read_text()

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_a_rename_commits_both_halves(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path,
    ):
        """The old path leaves via the diff, the new one via the untracked list."""
        def agent_run(*args, **kwargs):
            (git_wt / "src.py").rename(git_wt / "renamed.py")

        mock_invoke.side_effect = agent_run
        job = self._make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `src.py:1` — Misnamed module\n",
        )
        review_fix.run_fix_pass(job)

        tracked = _git(git_wt, "ls-tree", "--name-only", "HEAD").split()
        assert "renamed.py" in tracked
        assert "src.py" not in tracked
        assert _git(git_wt, "status", "--porcelain").strip() == ""
        assert "- [x] **[N1]**" in Path(job.review_file).read_text()

    @patch("review_fix._push_fixes")
    def test_a_path_git_would_escape_is_staged_verbatim(self, mock_push, git_wt):
        """`core.quotePath=false` is what keeps the name a pathspec git resolves.

        Escaped, the name reaches `git add` as `caf\\303\\251...`, which matches
        nothing — and that `add` runs under `check=True`, so the whole pass dies
        on a file whose only crime is an accent.
        """
        before = review_fix._changed_source_files(str(git_wt))
        (git_wt / "café brûlé.py").write_text("crème\n")
        agent_changed = review_fix._changed_source_files(str(git_wt)) - before

        assert agent_changed == {"café brûlé.py"}
        review_fix._commit_fixes(
            self._make_job(git_wt), paths=agent_changed, fixed=1, skipped=0,
        )
        assert _committed_paths(git_wt) == {"café brûlé.py"}

    @patch("review_fix._push_fixes")
    @patch("review_fix.diagnose_missing_output", return_value=_AGENT_ERROR)
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_a_path_dirty_before_the_pass_is_not_credited_even_when_edited(
        self, mock_prompt, mock_invoke, mock_diag, mock_push, git_wt, tmp_path,
    ):
        """The `ceiling:` in `run_fix_pass`, asserted rather than only described.

        Attribution is by path, so a path in both snapshots is in neither
        delta — the agent's edit to it is left uncommitted and the finding on
        it is left unchecked. The day attribution compares content across the
        snapshot, this test is what says the tradeoff is gone.
        """
        (git_wt / "src.py").write_text("hand edit in progress\n")

        def agent_run(*args, **kwargs):
            (git_wt / "src.py").write_text("hand edit in progress\nagent fix\n")

        mock_invoke.side_effect = agent_run
        job = self._make_job(
            git_wt, tmp_path,
            "## Must fix\n- [ ] **[M1]** `src.py:1` — Missing guard\n",
        )
        review_fix.run_fix_pass(job)

        assert _git(git_wt, "log", "--oneline").strip().count("\n") == 0
        assert " M src.py" in _git(git_wt, "status", "--porcelain")
        assert "- [ ] **[M1]**" in Path(job.review_file).read_text()
        mock_push.assert_not_called()


class TestRunFixPassLeavesDeclinedFindingsAlone:
    """`--fix` must not act on a finding a review already adjudicated."""

    def _make_job(self, git_wt, tmp_path, review_content):
        review_file = tmp_path / "review.md"
        review_file.write_text(review_content)
        job = MagicMock()
        job.review_file = str(review_file)
        job.wt_path = str(git_wt)
        job.model = None
        job.effort = Effort.MEDIUM
        return job

    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_a_review_of_only_declines_never_runs_the_agent(
        self, mock_prompt, mock_invoke, git_wt, tmp_path,
    ):
        job = self._make_job(
            git_wt, tmp_path,
            "## Must fix\n"
            "- [ ] **[M1]** `src.py:1` — *(declined — documented tradeoff)* — Lock\n",
        )
        review_fix.run_fix_pass(job)
        mock_invoke.assert_not_called()

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_a_decline_survives_a_pass_that_touches_its_file(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path,
    ):
        job = self._make_job(
            git_wt, tmp_path,
            "## Must fix\n"
            "- [ ] **[M1]** `src.py:1` — *(declined — documented tradeoff)* — Lock\n"
            "- [ ] **[M2]** `helper.py:1` — Missing helper\n",
        )

        def agent_run(*args, **kwargs):
            (git_wt / "src.py").write_text("agent touched this for M2's sake\n")
            (git_wt / "helper.py").write_text("def helper(): pass\n")

        mock_invoke.side_effect = agent_run
        review_fix.run_fix_pass(job)

        review = Path(job.review_file).read_text()
        assert "- [ ] **[M1]**" in review
        assert "*(declined — documented tradeoff)*" in review
        assert "- [x] **[M2]**" in review


class TestRunFixPassLeavesSkippedFindingsOpen:
    """Two findings in one file, one fixed — the skip must survive intact.

    Auto-checking attributes by file path, so the edit M1 earned reaches every
    finding in `src.py`. Checked off, M2 reads as unchecked→checked to
    `_diff_findings`, which reports it fixed in `review.md`, in the counts, and
    in the commit message, and drops the reason the agent wrote.
    """

    REVIEW_CONTENT = (
        "## Must fix\n"
        "- [ ] **[M1]** `src.py:1` — Missing nil check\n"
        "- [ ] **[M2]** `src.py:9` — Retry budget is unbounded\n"
    )

    def _make_job(self, git_wt, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text(self.REVIEW_CONTENT)
        job = MagicMock()
        job.review_file = str(review_file)
        job.wt_path = str(git_wt)
        job.model = None
        job.effort = Effort.MEDIUM
        return job

    @patch("review_fix._push_fixes")
    @patch("review_phases.invoke_agent")
    @patch("review_fix.build_prompt", return_value="prompt")
    def test_a_skip_survives_a_fix_to_its_own_file(
        self, mock_prompt, mock_invoke, mock_push, git_wt, tmp_path,
    ):
        job = self._make_job(git_wt, tmp_path)

        def agent_run(*args, **kwargs):
            (git_wt / "src.py").write_text("original\nif x is None: return\n")
            text = Path(job.review_file).read_text()
            text = text.replace("- [ ] **[M1]**", "- [x] **[M1]**")
            text = text.replace(
                "— Retry budget is unbounded\n",
                "— *(skipped — needs a product decision on the ceiling)* "
                "— Retry budget is unbounded\n",
            )
            Path(job.review_file).write_text(text)

        mock_invoke.side_effect = agent_run
        review_fix.run_fix_pass(job)

        review = Path(job.review_file).read_text()
        assert "- [x] **[M1]**" in review
        assert "- [ ] **[M2]**" in review

        msg = _git(git_wt, "log", "-1", "--format=%B")
        assert "1 fixed, 1 skipped" in msg
        assert "[M2] needs a product decision on the ceiling" in msg
