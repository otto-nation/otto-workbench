"""Tests for the review findings fix pass — `review.fix`'s half of the engine.

The pipeline is `fix.engine`'s and `fix_engine_test.py` holds it: the batching,
the retry, and what the landing owner is handed. What is here is the half only
a review can answer — which findings are open, which paths the commit may be
scoped to, and how `review.md` reads once the agent has answered.

The end-to-end cases (`review_fix_pass_landing_test.py`) run against a real
repo, because attribution is a set of path strings git produced and a stubbed
`status` line would agree with whatever the test expected. The agent is stubbed at `agent.invoke.run_fix`, which is
where the review's own boundary is: everything below it is the engine's, and
everything above it is what this module decided to ask for.
"""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.registry import PHASES
import fix.engine
import fix.gate
import review.document
import fix.suite
import review.fix
import review.paths
import review.types
from core.phases import Effort, Phase
from pr.fix import FixOutcome
from review.types import Finding
import fix.verify

from review_fix_pass_support import (
    _PUSHED,
    git_wt,
    _committed_paths,
    _make_job,
    _run,
    _outcome,
    _finding,
)


# ── what reaches the agent ──────────────────────────────────────────────────


class TestTheWorkSet:
    """Which findings the pass hands over, and which it never mentions."""

    def test_only_open_findings_become_items(self, git_wt, tmp_path):
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n"
            "- [x] **[M1]** `a.py:1` — Already fixed\n"
            "- [ ] **[M2]** `b.py:2` — *(declined — documented tradeoff)* — Lock\n"
            "- [ ] **[M3]** `c.py:3` — Still open\n",
        )
        inv = _run(job, {"M3": "fixed"})

        assert "<!-- fix:M3 -->" in inv.call_args.args[1]
        assert "<!-- fix:M1 -->" not in inv.call_args.args[1]
        assert "<!-- fix:M2 -->" not in inv.call_args.args[1]

    def test_an_item_is_labelled_with_its_severity_section(self, git_wt, tmp_path):
        """The agent orders its work by severity, so the section has to reach it."""
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `a.py:1` — Style\n")
        adapter = review.fix.ReviewFixAdapter(job, [_finding("N1")])

        assert adapter.items()[0].label == review.types.severity_by_key("N").section

    def test_a_review_with_nothing_open_never_runs_the_agent(self, git_wt, tmp_path):
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n"
            "- [ ] **[M1]** `src.py:1` — *(declined — documented tradeoff)* — Lock\n",
        )
        inv = _run(job, {})
        inv.assert_not_called()

    def test_no_review_file_never_runs_the_agent(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path, "")
        inv = _run(job, {})
        inv.assert_not_called()


class TestWhatTheReviewLendsTheAgentCall:
    """The knobs a fix pass inside a review inherits rather than re-resolving."""

    def test_the_review_s_effort_model_and_config_reach_the_invocation(
        self, git_wt, tmp_path,
    ):
        """Resolved from the process cwd, these answered for the wrong worktree."""
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `src.py:1` — Style\n")
        job.model = "claude-opus-5"
        job.effort = Effort.HIGH
        inv = _run(job, {"N1": "fixed"})

        kwargs = inv.call_args.kwargs
        assert kwargs["effort"] is Effort.HIGH
        assert kwargs["model"] == "claude-opus-5"
        assert kwargs["config"] is job.config

    def test_the_session_log_is_the_one_the_review_s_sweep_removes(
        self, git_wt, tmp_path,
    ):
        """`review.gc` finds a phase's log by the name the registry gives it.

        The engine's own default sits under a name the sweep never asks for, so
        a `--fix` pass would leave its session log behind in a finished review.
        """
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `src.py:1` — Style\n")
        adapter = review.fix.ReviewFixAdapter(job, [_finding("N1")])

        assert adapter.session_log == Path(
            review.paths.phase_log_path(job.review_file, Phase.FIX),
        )
        assert adapter.session_log.parent == Path(job.artifact_dir)

    def test_the_agent_may_read_the_review_directory_it_answers_in(
        self, git_wt, tmp_path,
    ):
        """The tracking file lives there, not in the worktree under review."""
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `src.py:1` — Style\n")
        adapter = review.fix.ReviewFixAdapter(job, [_finding("N1")])

        assert adapter.tracking_path.parent in adapter.add_dirs()
        assert adapter.workdir in adapter.add_dirs()


# ── the gate over the pass's own claims ────────────────────────────────


class TestTheVerifyGate:
    """A ticked `fixed` box is an edit claim; the gate is what checks it.

    `fix_engine_test` holds what a verdict does to an outcome. What is here is
    the review's own half: that the gate runs at all, that it is the gate's
    prompt the agent is handed, and that its leavings are named where the
    review's sweep looks for them.
    """

    REVIEW = "## Must fix\n- [ ] **[M1]** `helper.py:1` — Missing helper\n"

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_fix_the_gate_falsifies_is_not_committed_as_fixed(
        self, mock_push, git_wt, tmp_path,
    ):
        """The defect: a broken fix committed and ticked off on the strength of a box.

        Two passes shipped exactly this — a suite left failing, and a behaviour
        change with no assertion behind it — and both were truthful under the
        box's own contract.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(
            job, {"M1": "fixed — test_helper"},
            work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"),
            verdicts={"M1": fix.gate.Verdict(
                ok=False, detail="test_helper does not exist",
            )},
        )

        review = Path(job.review_file).read_text()
        assert "- [x] **[M1]**" not in review
        assert "test_helper does not exist" in review
        # The edit itself still lands — it is in the worktree either way, and
        # throwing it away would cost the next round the work. What the gate
        # changes is what the commit and the document call it.
        msg = git_out(git_wt, "log", "-1", "--format=%B")
        assert "fixed" not in msg
        assert "Skipped:\n  - [M1] test_helper does not exist" in msg

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_fix_the_gate_confirms_is_committed_as_fixed(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(
            job, {"M1": "fixed — test_helper"},
            work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"),
            verdicts={"M1": fix.gate.Verdict(ok=True, detail="suite green")},
        )

        assert "- [x] **[M1]**" in Path(job.review_file).read_text()
        assert _committed_paths(git_wt) == {"helper.py"}

    def test_the_pass_hands_the_engine_a_gate_at_all(self, git_wt, tmp_path):
        """No `verify=` is the whole bug: the engine's gate is opt-in per domain.

        Everything else here would pass against a pass that never gated — the
        stub in `_run` patches the runner, not the wiring — so this asserts the
        argument reaches `fix.engine.run`.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        with patch.object(fix.engine, "run") as run:
            review.fix.run_fix_pass(job)

        assert run.call_args.kwargs["verify"] is fix.verify.run

    def test_the_gate_is_prompted_as_the_gate_not_as_the_fix_pass(self):
        """Falling back to `phase` hands a checking agent fix-findings.md.

        That template tells it to edit source, which the gate's own rules
        forbid in as many words — the bug #1358 fixed for the comments domain.
        """
        phase = review.fix.ReviewFixAdapter.verify_phase
        assert phase is Phase.FIX_VERIFY
        assert PHASES[phase].template_for() == "verify-fixes.md"

    def test_the_gates_session_log_is_one_the_reviews_sweep_removes(
        self, git_wt, tmp_path,
    ):
        """Named from the registry, for the reason the fix pass's log is.

        The engine's default sits under a name `review.gc` never asks for, so a
        gated pass would leave its session log beside the deliverable.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        adapter = review.fix.ReviewFixAdapter(job, [_finding("M1")])

        assert adapter.verify_session_log(1) == Path(
            review.paths.phase_log_path(job.review_file, Phase.FIX_VERIFY, 1),
        )
        assert adapter.verify_session_log(1).parent == Path(job.artifact_dir)


# ── what the pass commits ───────────────────────────────────────────────────


class TestTheCommitScope:
    """What `landing` does with the scope; `fix_scope_test.py` holds the snapshot.

    The engine takes the two readings and hands over the difference, so what is
    left for the adapter to answer is which of them reaches `LandSpec.paths`
    and what it says when there is no answer at all.
    """

    def _adapter(self, git_wt, tmp_path, *, files=None):
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n- [ ] **[M1]** `a.py:1` — Bug\n",
            files=files,
        )
        return review.fix.ReviewFixAdapter(job, [_finding("M1")])

    def test_the_scope_is_what_the_engine_attributed_to_the_agent(
        self, git_wt, tmp_path,
    ):
        adapter = self._adapter(git_wt, tmp_path, files=["helper.py"])
        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"helper.py"})

        assert spec.paths == {"helper.py"}

    def test_an_out_of_branch_edit_is_dropped_from_the_commit(
        self, git_wt, tmp_path, capsys,
    ):
        adapter = self._adapter(git_wt, tmp_path, files=["a.py"])
        spec = adapter.landing(
            [_outcome("M1", FixOutcome.FIXED)],
            {"a.py", "lib/nesting/bash.py"},
        )

        assert spec.paths == {"a.py"}
        err = capsys.readouterr().err
        assert "lib/nesting/bash.py" in err
        assert "not committing" in err

    def test_a_snapshot_that_failed_scopes_the_commit_to_nothing(
        self, git_wt, tmp_path,
    ):
        """An empty scope commits nothing; `None` would commit the whole tree."""
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], None)

        assert spec.paths == set()
        assert adapter.changed is None

    def test_an_agent_that_changed_nothing_scopes_the_commit_to_nothing(
        self, git_wt, tmp_path,
    ):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([_outcome("M1", FixOutcome.DECLINED, "by design")], set())

        assert spec.paths == set()

    def test_the_message_counts_what_the_pass_settled(self, git_wt, tmp_path):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([
            _outcome("M1", FixOutcome.FIXED),
            _outcome("M2", FixOutcome.NEEDS_HUMAN, "needs design"),
            _outcome("M3", FixOutcome.DEFERRED),
        ], {"a.py"})

        assert spec.message.startswith("fix: self-review findings")
        assert "1 fixed, 2 skipped" in spec.message

    def test_the_tally_says_so_when_the_repo_s_checks_are_red(
        self, git_wt, tmp_path,
    ):
        """The line that gets quoted is the line that has to carry the caveat.

        `4 fixed, 0 skipped` over a red suite is the sentence this whole
        mechanism exists to stop being written. A caveat one line further down
        does not travel into the terminal, the PR body, or the summary
        somebody writes from memory.
        """
        adapter = self._adapter(git_wt, tmp_path)
        adapter.suite = fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.RED, command="bin/local/run-tests --changed",
            output_tail="E   AttributeError: no attribute 'EXIT_BUDGET_EXHAUSTED'",
        )

        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"a.py"})

        assert "1 fixed, 0 skipped" in spec.message
        assert "RED" in spec.message.splitlines()[2]
        assert "AttributeError" in spec.message

    def test_the_tally_says_so_when_nothing_was_declared_to_run(
        self, git_wt, tmp_path,
    ):
        adapter = self._adapter(git_wt, tmp_path)
        adapter.suite = fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.NOT_DECLARED)

        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"a.py"})

        assert "unverified: no fix.verify_command declared" in spec.message
        assert fix.suite.NOT_DECLARED_NOTE in spec.message

    def test_a_green_run_leaves_the_tally_as_it_was(self, git_wt, tmp_path):
        """Green means the checks pass, not that each fix is right.

        Spending the tally's words on the weaker claim is how the stronger one
        stops being read.
        """
        adapter = self._adapter(git_wt, tmp_path)
        adapter.suite = fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.GREEN, command="checks", duration_s=12.0)

        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"a.py"})

        assert "1 fixed, 0 skipped" in spec.message
        assert "unverified" not in spec.message
        assert "Checks green" in spec.message

    def test_checks_that_did_not_answer_are_not_reported_as_passing(
        self, git_wt, tmp_path,
    ):
        adapter = self._adapter(git_wt, tmp_path)
        adapter.suite = fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.TIMED_OUT, command="checks", duration_s=900.0)

        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"a.py"})

        assert "unverified: the repo's checks did not answer" in spec.message

    def test_a_pass_with_nothing_to_check_carries_no_note_at_all(
        self, git_wt, tmp_path,
    ):
        """A caveat on every pass is a caveat read on none of them."""
        adapter = self._adapter(git_wt, tmp_path)

        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"a.py"})

        assert "1 fixed, 0 skipped" in spec.message
        assert "unverified" not in spec.message
        assert "Checks" not in spec.message

    def test_a_pass_that_fixed_nothing_omits_the_count(self, git_wt, tmp_path):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing(
            [_outcome("M1", FixOutcome.DECLINED, "by design")], set())

        assert "fixed," not in spec.message
        assert "Declined:" in spec.message

    def test_the_summary_rides_in_the_commit_message(self, git_wt, tmp_path):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([
            _outcome("M1", FixOutcome.FIXED),
            _outcome("S1", FixOutcome.NEEDS_HUMAN, "needs design"),
        ], {"a.py"})

        assert "[M1] body" in spec.message
        assert "[S1] needs design" in spec.message

    def test_a_truncated_pass_commit_differs_from_a_complete_pass(
        self, git_wt, tmp_path,
    ):
        outcomes = [
            _outcome("M1", FixOutcome.FIXED),
            _outcome("N1", FixOutcome.DEFERRED),
        ]
        complete = self._adapter(git_wt, tmp_path).landing(outcomes, {"a.py"})
        truncated = self._adapter(git_wt, tmp_path)
        truncated.stop = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=30)
        truncated_spec = truncated.landing(outcomes, {"a.py"})

        assert complete.message != truncated_spec.message
        assert "max turns" in truncated_spec.message
        assert "30" in truncated_spec.message


class TestBranchScope:
    """A fix pass cannot commit a file the branch never touched.

    Attribution still reports the edit — `agent_changed` is not a statement
    about the branch — and landing drops it so the file stays dirty.
    """

    REVIEW = "## Must fix\n- [ ] **[M1]** `helper.py:1` — Missing helper\n"

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_out_of_branch_edit_is_reported_and_left_uncommitted(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW, files=["helper.py"])

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            nesting = git_wt / "lib" / "nesting"
            nesting.mkdir(parents=True)
            (nesting / "bash.py").write_text("def too_deep(): pass\n")

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert _committed_paths(git_wt) == {"helper.py"}
        status = git_out(git_wt, "status", "--porcelain", "-uall")
        assert "lib/nesting/bash.py" in status
        err = capsys.readouterr().err
        assert "not committing" in err
        assert "lib/nesting/bash.py" in err

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_in_branch_edit_is_committed(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW, files=["helper.py"])
        _run(
            job, {"M1": "fixed"},
            work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"),
        )

        assert _committed_paths(git_wt) == {"helper.py"}
        assert "outside this branch" not in capsys.readouterr().err

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_colocated_test_of_an_in_branch_file_is_committed(
        self, mock_push, git_wt, tmp_path,
    ):
        src = git_wt / "src"
        src.mkdir()
        (src / "foo.py").write_text("x = 1\n")
        git_out(git_wt, "add", "src/foo.py")
        git_out(git_wt, "commit", "-qm", "add foo")
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n- [ ] **[M1]** `src/foo.py:1` — Needs a test\n",
            files=["src/foo.py"],
        )

        def agent_run():
            (src / "foo.py").write_text("x = 2\n")
            (src / "foo_test.py").write_text("def test_foo(): assert True\n")

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert _committed_paths(git_wt) == {"src/foo.py", "src/foo_test.py"}

    def test_the_prompt_carries_the_branch_file_list(self, git_wt, tmp_path):
        job = _make_job(
            git_wt, tmp_path, self.REVIEW,
            files=["src/auth.go", "pkg/util.go"],
            base="feat/parent",
        )
        adapter = review.fix.ReviewFixAdapter(
            job, [_finding("M1", path="helper.py")],
        )
        adapter.tracking_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.tracking_path.write_text("- [ ] fixed\n")
        prompt = fix.engine._prompt(adapter, 15)

        assert "src/auth.go" in prompt
        assert "pkg/util.go" in prompt
        assert "helper.py" in prompt
        assert "feat/parent" in prompt
        assert "Never bundle unrelated fixes" in prompt
        assert "${branch_files}" not in prompt
        assert "${finding_anchors}" not in prompt
        assert "${branch_base}" not in prompt


# ── the parsers the pass reads its work set through ─────────────────────────


class TestParseCheckboxState:
    def test_unchecked_finding(self):
        text = "## Must fix\n- [ ] **[M1]** **`file.go:10`** — Bug found\n"
        findings = review.document.ReviewDocument.parse(text).findings
        assert len(findings) == 1
        assert findings[0].checked is False

    def test_checked_finding(self):
        text = "## Must fix\n- [x] **[M1]** **`file.go:10`** — Bug fixed\n"
        findings = review.document.ReviewDocument.parse(text).findings
        assert len(findings) == 1
        assert findings[0].checked is True

    def test_no_checkbox_finding(self):
        text = "## Must fix\n- **[M1]** **`file.go:10`** — Bug found\n"
        findings = review.document.ReviewDocument.parse(text).findings
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
        findings = review.document.ReviewDocument.parse(text).findings
        assert len(findings) == 3
        by_id = {f.id: f for f in findings}
        assert by_id["M1"].checked is True
        assert by_id["M2"].checked is False
        assert by_id["N1"].checked is True


class TestIsSkipped:
    """`*(skipped — reason)*` is the fix pass's record of work it did not do.

    `run_fix_pass` reads it to leave the line alone rather than re-annotating
    it, so a skip it fails to recognise ends up saying two things about one
    finding.
    """

    def test_a_leading_annotation_registers(self):
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped — requires design decision)* — Some finding body",
        )
        assert review.document.is_skipped(finding) is True

    def test_a_trailing_annotation_registers(self):
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="Some finding body *(skipped -- needs confirmation)*",
        )
        assert review.document.is_skipped(finding) is True

    def test_a_skip_without_a_reason_still_registers(self):
        """Mirrors the decline case — a bare annotation is still a skip."""
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped)* — Some finding body",
        )
        assert review.document.is_skipped(finding) is True

    def test_a_plain_finding_carries_no_skip(self):
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="Plain finding body",
        )
        assert review.document.is_skipped(finding) is False

    def test_a_checked_finding_carries_no_skip(self):
        finding = Finding(
            id="M1", severity="M", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped — stale)* — body", checked=True,
        )
        assert review.document.is_skipped(finding) is False


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
        findings = review.document.ReviewDocument.parse(text).findings
        assert findings[0].declined is True
        assert findings[0].decline_reason == "documented `ceiling:` tradeoff"

    def test_a_decline_without_a_reason_still_registers(self):
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(declined)* — Body\n"
        findings = review.document.ReviewDocument.parse(text).findings
        assert findings[0].declined is True
        assert findings[0].decline_reason == ""

    def test_a_trailing_annotation_registers(self):
        """The templates also let the annotation close the line."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.go:1` — Global lock *(declined — by design)*\n"
        )
        findings = review.document.ReviewDocument.parse(text).findings
        assert findings[0].declined is True
        assert findings[0].decline_reason == "by design"

    def test_a_finding_that_only_describes_the_annotation_is_not_declined(self):
        """Reviewing this parser writes the annotation into a finding's prose.

        Read as a decline, the finding leaves `run_fix_pass`'s work set
        permanently, and nothing warns that it did.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `review_document.py:99` — The `*(declined — reason)*` "
            "annotation is matched anywhere in the line, so prose trips it\n"
        )
        findings = review.document.ReviewDocument.parse(text).findings
        assert findings[0].declined is False
        assert findings[0].decline_reason == ""

    def test_a_skip_is_not_a_decline(self):
        """A skip is work deferred; a decline is work rejected."""
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(skipped — needs design)* — Body\n"
        findings = review.document.ReviewDocument.parse(text).findings
        assert findings[0].declined is False

    def test_a_file_without_declines_parses_unchanged(self):
        """Review files predating `Declined` must keep parsing."""
        text = (
            "## Must fix\n"
            "- [x] **[M1]** `a.go:1` — Fixed\n"
            "- [ ] **[M2]** `b.go:2` — Still open\n"
        )
        findings = review.document.ReviewDocument.parse(text).findings
        assert [f.declined for f in findings] == [False, False]
        assert [f.decline_reason for f in findings] == ["", ""]
