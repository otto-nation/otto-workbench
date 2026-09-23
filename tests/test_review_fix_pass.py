"""Tests for the review findings fix pass — `review_fix`'s half of the engine.

The pipeline is `fix_engine`'s and `fix_engine_test.py` holds it: the batching,
the retry, and what the landing owner is handed. What is here is the half only
a review can answer — which findings are open, which paths the commit may be
scoped to, and how `review.md` reads once the agent has answered.

The end-to-end cases run against a real repo, because attribution is a set of
path strings git produced and a stubbed `status` line would agree with whatever
the test expected. The agent is stubbed at `agent_invoke.run_fix`, which is
where the review's own boundary is: everything below it is the engine's, and
everything above it is what this module decided to ask for.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent import invoke as agent_invoke
from agent.registry import PHASES
from fix import engine as fix_engine
from git import land
from git import push
from review import document as review_document
from review import fix as review_fix
from review import grammar as review_grammar
from review import paths as review_paths
from review import types as review_types
from core.proc import TIMEOUT_RETURNCODE, CmdResult
from core.phases import Effort, Phase
from pr import attribution
from pr.fix import FixOutcome, ItemOutcome
from gh.types import PRContext, PRMetadata
from review.types import Finding, ReviewJob

# What the push owner answers when the fix pass's commit reached the remote.
# The pass no longer pushes for itself — `land` does — so stubbing the owner is
# how a test keeps a real commit and no network.
_PUSHED = push.PushResult(
    push.PushStatus.PUSHED, sha="9bc3f64ab", branch="feat/x", remote_sha="9bc3f64ab",
)


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


def _install_failing_pre_commit(tmp_path, message: str = "gate refused") -> None:
    """Make every later `git commit` in `git_wt` fail, the way a hook does."""
    hook = tmp_path / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n")
    hook.chmod(0o755)


def _committed_paths(wt: Path) -> set[str]:
    # quotePath=false for the same reason `_changed_source_files` sets it: git
    # escapes a non-ASCII name by default, and the assertion would compare the
    # escaped spelling against the real one.
    out = git_out(
        wt, "-c", "core.quotePath=false",
        "show", "--name-only", "--pretty=format:", "HEAD",
    )
    return {line for line in out.strip().splitlines() if line}


def _make_job(git_wt, tmp_path, review_content: str = "") -> ReviewJob:
    """A review whose deliverable is `review_content`, over `git_wt`.

    A real `ReviewJob` rather than a mock: the adapter reads the review's
    effort, model, config and artifact directory off it, and a mock answers
    every one of those with something that is not what a review holds.
    """
    review_file = tmp_path / "reviews" / "review.md"
    review_file.parent.mkdir(exist_ok=True)
    review_file.write_text(review_content)
    return ReviewJob(
        repo="owner/repo", pr_number="42",
        pr=PRMetadata(
            title="feat: thing", body="", head="user/feat/thing", base="main",
            head_sha="abc1234", additions=1, deletions=0, changed_files=1, files=[],
        ),
        ctx=PRContext(commits="abc1234 feat: thing"),
        wt_path=str(git_wt),
        review_file=str(review_file),
        session_log=str(review_file.parent / "session.jsonl"),
    )


def _tracking(job: ReviewJob) -> Path:
    return Path(job.artifact_dir) / fix_engine.TRACKING_FILENAME


def _is_heading(line: str) -> bool:
    return line.startswith("## <!-- fix:")


def _heading_id(line: str) -> str:
    return line.split("fix:")[1].split(" ")[0]


def _answer(job: ReviewJob, boxes: dict[str, str], *, work=None):
    """A `run_fix` stub that ticks `boxes` on the checklist it finds on disk.

    Keyed by finding id, valued with the whole box line the agent would leave
    behind — `"fixed"`, or `"declined — why"`. An id left out is an item the
    agent never answered, which is what the engine reads as still owed.

    `work` runs first and is where a test puts the edits the agent would have
    made to the worktree; the engine writes the checklist immediately before
    each invocation, so an answer written any earlier is thrown away.
    """
    tracking = _tracking(job)

    def run_fix(_phase, _prompt, **_kwargs):
        if work:
            work()
        text = tracking.read_text()
        owed = {_heading_id(ln) for ln in text.splitlines() if _is_heading(ln)} & set(boxes)
        item = ""
        out: list[str] = []
        for line in text.splitlines(keepends=True):
            if _is_heading(line):
                item = _heading_id(line)
            answer = boxes.get(item, "")
            label = answer.split(" — ")[0]
            if answer and line.startswith(f"- [ ] {label}"):
                line = f"- [x] {answer}\n"
                owed.discard(item)
            out.append(line)
        tracking.write_text("".join(out))
        # A label this checklist has no box for ticks nothing, and the engine
        # reads the silence as a deferral — which several assertions here would
        # take for the answer they asked for. Fail on the typo instead.
        assert not owed, f"no box matched the answer for: {sorted(owed)}"
        return agent_invoke.FixResult(0, None)

    return run_fix


def _run(
    job: ReviewJob, boxes: dict[str, str], *, work=None, verdicts=None, **kwargs,
):
    """Run the pass with the agent stubbed, and hand back the stub.

    The verify gate is stubbed alongside it, at `fix_verify.run` rather than at
    the agent: the two share one `run_fix`, so a single stub would hand the
    gate's checklist to a helper that answers in the fix pass's vocabulary and
    every case here would fail on a box the verify file does not carry.

    `verdicts` defaults to none at all, which the engine reads as a gate that
    answered nothing — unverified, still fixed, still committed. That keeps the
    cases below about what this module decides; the gate's own effect on them is
    `TestTheVerifyGate`'s.
    """
    with patch.object(review_fix.fix_verify, "run",
                      side_effect=lambda *a, **k: dict(verdicts or {})):
        with patch.object(fix_engine.agent_invoke, "run_fix",
                          side_effect=_answer(job, boxes, work=work)) as inv:
            review_fix.run_fix_pass(job, **kwargs)
    return inv


def _outcome(item_id: str, outcome: FixOutcome, reason: str = "", **kwargs) -> ItemOutcome:
    return ItemOutcome(id=item_id, outcome=outcome, reason=reason, **kwargs)


def _finding(fid: str, path: str = "a.py", body: str = "body", **kwargs) -> Finding:
    return Finding(
        id=fid, severity=fid[0], seq=int(fid[1:]), path=path,
        line=1, end_line=None, body=body, **kwargs,
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
        adapter = review_fix.ReviewFixAdapter(job, [_finding("N1")])

        assert adapter.items()[0].label == review_types.severity_by_key("N").section

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
        """`review_gc` finds a phase's log by the name the registry gives it.

        The engine's own default sits under a name the sweep never asks for, so
        a `--fix` pass would leave its session log behind in a finished review.
        """
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `src.py:1` — Style\n")
        adapter = review_fix.ReviewFixAdapter(job, [_finding("N1")])

        assert adapter.session_log == Path(
            review_paths.phase_log_path(job.review_file, Phase.FIX),
        )
        assert adapter.session_log.parent == Path(job.artifact_dir)

    def test_the_agent_may_read_the_review_directory_it_answers_in(
        self, git_wt, tmp_path,
    ):
        """The tracking file lives there, not in the worktree under review."""
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `src.py:1` — Style\n")
        adapter = review_fix.ReviewFixAdapter(job, [_finding("N1")])

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

    @patch("git.land.push.push", return_value=_PUSHED)
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
            verdicts={"M1": fix_engine.Verdict(
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

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_fix_the_gate_confirms_is_committed_as_fixed(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(
            job, {"M1": "fixed — test_helper"},
            work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"),
            verdicts={"M1": fix_engine.Verdict(ok=True, detail="suite green")},
        )

        assert "- [x] **[M1]**" in Path(job.review_file).read_text()
        assert _committed_paths(git_wt) == {"helper.py"}

    def test_the_pass_hands_the_engine_a_gate_at_all(self, git_wt, tmp_path):
        """No `verify=` is the whole bug: the engine's gate is opt-in per domain.

        Everything else here would pass against a pass that never gated — the
        stub in `_run` patches the runner, not the wiring — so this asserts the
        argument reaches `fix_engine.run`.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        with patch.object(review_fix.fix_engine, "run") as run:
            review_fix.run_fix_pass(job)

        assert run.call_args.kwargs["verify"] is review_fix.fix_verify.run

    def test_the_gate_is_prompted_as_the_gate_not_as_the_fix_pass(self):
        """Falling back to `phase` hands a checking agent fix-findings.md.

        That template tells it to edit source, which the gate's own rules
        forbid in as many words — the bug #1358 fixed for the comments domain.
        """
        phase = review_fix.ReviewFixAdapter.verify_phase
        assert phase is Phase.FIX_VERIFY
        assert PHASES[phase].template_for() == "verify-fixes.md"

    def test_the_gates_session_log_is_one_the_reviews_sweep_removes(
        self, git_wt, tmp_path,
    ):
        """Named from the registry, for the reason the fix pass's log is.

        The engine's default sits under a name `review_gc` never asks for, so a
        gated pass would leave its session log beside the deliverable.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        adapter = review_fix.ReviewFixAdapter(job, [_finding("M1")])

        assert adapter.verify_session_log == Path(
            review_paths.phase_log_path(job.review_file, Phase.FIX_VERIFY),
        )
        assert adapter.verify_session_log.parent == Path(job.artifact_dir)


# ── what the pass commits ───────────────────────────────────────────────────


class TestTheCommitScope:
    """What `landing` does with the scope; `fix_scope_test.py` holds the snapshot.

    The engine takes the two readings and hands over the difference, so what is
    left for the adapter to answer is which of them reaches `LandSpec.paths`
    and what it says when there is no answer at all.
    """

    def _adapter(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path, "## Must fix\n- [ ] **[M1]** `a.py:1` — Bug\n")
        return review_fix.ReviewFixAdapter(job, [_finding("M1")])

    def test_the_scope_is_what_the_engine_attributed_to_the_agent(
        self, git_wt, tmp_path,
    ):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)], {"helper.py"})

        assert spec.paths == {"helper.py"}

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


class TestTheSummary:
    """Three answers worth telling apart, in the terms each is worth reading."""

    FINDINGS = {"M1": _finding("M1", body="the guard is missing")}

    def test_a_fix_is_described_by_the_finding_it_answered(self):
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED)], self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing"

    def test_a_fix_for_a_finding_the_review_no_longer_holds_names_its_file(self):
        """The tracking file records a location, and nothing else about the item."""
        outcome = ItemOutcome(id="M9", outcome=FixOutcome.FIXED, file="gone.py")
        assert "[M9] gone.py" in review_fix._summary([outcome], self.FINDINGS)

    def test_a_multi_line_body_is_reported_by_its_first_line(self):
        findings = {"M1": _finding("M1", body="headline\n\nthe rest of it")}
        summary = review_fix._summary([_outcome("M1", FixOutcome.FIXED)], findings)
        assert summary == "Fixed:\n  - [M1] headline"

    def test_a_skip_is_reported_by_the_reason_the_agent_gave(self):
        summary = review_fix._summary(
            [_outcome("S1", FixOutcome.NEEDS_HUMAN, "needs a product decision")], {},
        )
        assert "Skipped:\n  - [S1] needs a product decision" in summary

    def test_a_deferral_is_reported_as_a_skip_with_no_reason(self):
        """The agent never reached it, so there is no reason it could have given."""
        summary = review_fix._summary([_outcome("N1", FixOutcome.DEFERRED)], {})
        assert "Skipped:\n  - [N1] no auto-fix" in summary

    def test_a_decline_has_its_own_heading(self):
        """A skip is retried next pass; a decline is work nobody is going to do."""
        summary = review_fix._summary(
            [_outcome("M2", FixOutcome.DECLINED, "documented `ceiling:` tradeoff")], {},
        )
        assert "Declined:\n  - [M2] documented `ceiling:` tradeoff" in summary
        assert "Skipped:" not in summary

    def test_a_decline_without_a_reason_says_what_it_still_means(self):
        summary = review_fix._summary([_outcome("N1", FixOutcome.DECLINED)], {})
        assert "adjudicated, not a defect" in summary

    def test_a_pass_that_settled_nothing_summarises_nothing(self):
        assert review_fix._summary([], {}) == ""

    def test_a_fix_the_gate_could_not_stand_behind_says_so(self):
        """Only a falsified fix is demoted, so an unverifiable one stays under Fixed."""
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False,
                      verify_detail="no runnable check")],
            self.FINDINGS,
        )
        assert summary == (
            "Fixed:\n  - [M1] the guard is missing "
            "(not verified automatically — no runnable check)"
        )

    def test_an_unverified_fix_with_no_detail_still_carries_the_caveat(self):
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False)], self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing (not verified automatically)"

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_fix_the_gate_confirmed_carries_no_caveat(self):
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=True,
                      verify_detail="pytest tests/a_test.py passed")],
            self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing"

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_pass_that_never_ran_the_gate_reads_as_it_always_did(self):
        """`verified is None` is nobody asking, which is not a caveat to print."""
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verify_detail="ignored")], self.FINDINGS,
        )
        assert summary == "Fixed:\n  - [M1] the guard is missing"


# ── what the review document ends up saying ─────────────────────────────────


class TestApplyOutcomes:
    """The re-render — the review document is written from the outcomes."""

    OPEN = (
        "## Must fix\n"
        "- [ ] **[M1]** `a.py:1` — Missing nil check\n"
        "- [ ] **[M2]** `b.py:2` — Retry budget is unbounded\n"
    )

    # What a PR-mode review writes: `review-templates/single-agent.md` asks for a
    # finding with no checkbox, and `FINDING_ID_RE` makes the box optional so the
    # line parses either way. A pass that only ever saw `OPEN` cannot see what
    # this shape does to a tick.
    NO_CHECKBOX = (
        "## Must fix\n"
        "- **[M1]** **`a.py:1`** — Missing nil check\n"
    )

    def test_a_fix_ticks_the_box(self):
        out = review_fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.FIXED)])
        assert "- [x] **[M1]**" in out
        assert "- [ ] **[M2]**" in out

    def test_an_unverified_fix_ticks_the_box_and_says_so(self):
        """The tick is honest — an edit landed — but nothing exercised it."""
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out.splitlines()[1].endswith("*(unverified — no runnable check)*")

    def test_a_skip_appended_to_prose_quoting_a_decline_stays_a_skip(self):
        """The verdict path has the same hazard and cannot answer it by refusing.

        A caveat withheld costs a caveat. A skip withheld costs the outcome: the
        finding parses as declined and `run_fix_pass` drops a declined finding
        from the work set, so no later round picks it up either.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — the `*(declined — x)*` annotation is read "
            "anywhere\n"
        )
        out = review_fix._apply_outcomes(
            text, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        doc = review_document.ReviewDocument.parse(out)
        assert doc.findings[0].declined is False
        assert review_document.is_skipped(doc.findings[0]) is True
        assert [f.id for f in doc.open_findings if not f.declined] == ["M1"]

    def test_a_reason_carrying_a_newline_does_not_fabricate_a_finding(self):
        """A split line's remainder is parsed as whatever it happens to look like.

        `fix.tracking` collapses whitespace on the engine's path, but an
        `ItemOutcome` built anywhere else does not pass through it.
        """
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "one\n- [ ] **[M9]** `b.py:2` — injected"),
        ])
        assert [f.id for f in review_document.ReviewDocument.parse(out).findings] == [
            "M1", "M2",
        ]

    # passes-at-base: base writes no caveat, so no detail reaches the document
    def test_a_verify_detail_carrying_a_newline_does_not_fabricate_a_finding(self):
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="one\n- [ ] **[S9]** `b.py:2` — injected"),
        ])
        assert [f.id for f in review_document.ReviewDocument.parse(out).findings] == [
            "M1", "M2",
        ]

    # passes-at-base: base rewrites nothing on the line, so the id is safe there for free
    def test_an_append_leaves_the_stable_id_alone(self):
        """`FindingIdentity` hashes the body's first eighty characters.

        An annotation lands past them, so appending one has never changed the
        id — and `reconcile` matches a prior round's finding by exactly that id.
        Anything this function rewrites *before* position eighty breaks the
        carry-forward silently, on the next run rather than this one.
        """
        body = (
            "the guard  is missing here and this body runs well past eighty "
            "characters so the append lands after it"
        )
        line = f"- [ ] **[M1]** `a.py:1` — {body}"
        out = review_fix._apply_outcomes(
            f"## Must fix\n{line}\n",
            [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        before = review_grammar.FindingIdentity.of(line)
        after = review_grammar.FindingIdentity.of(out.splitlines()[1])
        assert after.stable_id == before.stable_id

    # passes-at-base: base rewrites nothing on the line, so the spacing is safe for free
    def test_an_append_leaves_the_author_s_own_spacing_alone(self):
        """Inline code, table alignment and indentation are the author's."""
        line = "- [ ] **[M1]** `a.py:1` — compare `x  ==  y` and a | a  | b  | table"
        out = review_fix._apply_outcomes(
            f"## Must fix\n{line}\n",
            [_outcome("M1", FixOutcome.NEEDS_HUMAN, "nope")],
        )
        assert out.splitlines()[1].startswith(line)

    def test_a_verdict_does_not_overwrite_a_carried_caveat(self):
        """The finding stays open, so the next round retries it rather than losing it."""
        line = "- [ ] **[M1]** `a.py:1` — x *(unverified — no runnable check)*"
        out = review_fix._apply_outcomes(
            f"## Must fix\n{line}\n",
            [_outcome("M1", FixOutcome.NEEDS_HUMAN, "no auto-fix")],
        )
        doc = review_document.ReviewDocument.parse(out)
        assert out.splitlines()[1] == line
        assert [f.id for f in doc.open_findings if not f.declined] == ["M1"]

    # passes-at-base: base appends nothing after the quotation, so it stays mid-line
    def test_prose_quoting_an_annotation_is_not_turned_into_one(self):
        """The decline pattern runs from any `*(` to the last `)*` on the line.

        A quotation mid-line is safe until something is appended after it — the
        append supplies the close and the pattern spans the whole distance. The
        docs and tests of this module quote the annotation verbatim, so this is
        the shape a review of this very file takes.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — The `*(declined — reason)*` annotation "
            "is matched anywhere\n"
        )
        out = review_fix._apply_outcomes(text, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is False
        assert finding.checked is True

    # passes-at-base: base appends nothing after the quotation, so it stays mid-line
    def test_prose_quoting_a_skip_is_not_turned_into_one(self):
        """Asserted against the skip pattern, which `is_skipped` cannot answer.

        `is_skipped` short-circuits on a checked finding, so it reports False
        for any tick however the body reads — including one this append just
        turned into a skip annotation.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — prose about `*(skipped — x)*` here\n"
        )
        out = review_fix._apply_outcomes(text, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert review_document._SKIP_TAIL_RE.search(finding.body) is None
        assert finding.checked is True

    def test_a_box_quoted_in_prose_is_not_the_one_that_gets_ticked(self):
        """A finding about a template quotes the empty box in its own body.

        Ticking that occurrence corrupts the prose and annotates a line whose
        own declaration stays unchecked, so the finding never closes.
        """
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.py:1`** — the template writes `- [ ] **[M1]**` "
            "with no box\n"
        )
        out = review_fix._apply_outcomes(text, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out == text

    def test_a_skip_reason_quoting_a_decline_stays_a_skip(self):
        """`reason` is the gate's own prose, by way of the engine's verdict."""
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.NEEDS_HUMAN,
                     "as the docs say *(declined — adjudicated)*"),
        ])
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is False
        assert review_document.is_skipped(finding) is True

    def test_a_clip_with_no_room_yields_nothing(self):
        """`text[:n]` with a non-positive n counts from the end.

        The slice would hand back most of the string where the budget was
        tightest — longest output exactly where the caller had least room.
        """
        assert review_fix._clip("abcdefgh", 0) == ""
        assert review_fix._clip("abcdefgh", -5) == ""

    def test_a_clip_never_exceeds_the_limit_it_was_given(self):
        for limit in range(-2, 12):
            assert len(review_fix._clip("abcdefgh", limit)) <= max(limit, 0)

    # passes-at-base: base writes no caveat, so its lines are short for free
    def test_a_hedged_summary_line_fits_the_commit_body_limit(self):
        """These lines land in a commit body, and no hook on this path checks them."""
        findings = {"M1": _finding("M1", body="x" * 80)}
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False, verify_detail="y" * 60)],
            findings,
        )
        assert all(len(line) <= 100 for line in summary.splitlines())

    def test_a_long_detail_alone_cannot_overrun_the_line(self):
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False, verify_detail="y" * 90)],
            {"M1": _finding("M1", body="short")},
        )
        assert all(len(line) <= 100 for line in summary.splitlines())
        assert "not verified automatically" in summary

    def test_the_caveat_survives_a_description_long_enough_to_crowd_it(self):
        """The description gives way first — a half-printed caveat is the worse loss."""
        summary = review_fix._summary(
            [_outcome("M1", FixOutcome.FIXED, verified=False, verify_detail="no runnable check")],
            {"M1": _finding("M1", body="x" * 80)},
        )
        assert summary.endswith("(not verified automatically — no runnable check)")
        assert "…" in summary

    # passes-at-base: base writes no annotation, so the wording assertions hold vacuously there
    def test_an_unverified_tick_is_still_a_fix_to_the_parser(self):
        """The caveat is for the reader; it must not read back as a skip or decline.

        Asserted on the wording rather than only on `is_skipped`, which
        short-circuits on a checked finding and so answers False for any tick
        however the annotation reads.
        """
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert "*(skipped" not in out
        assert "*(declined" not in out
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert finding.checked is True
        assert finding.declined is False
        assert review_document.is_skipped(finding) is False

    # passes-at-base: base annotates nothing, so a checkbox-free line is untouched there anyway
    def test_a_finding_with_no_checkbox_is_not_annotated(self):
        """A PR-mode line has no box to tick, so there is no landed fix to hedge.

        The tick is a no-op on that shape and `checked` stays false, so the
        guard that makes this idempotent never engages — annotating anyway
        appends a caveat per round to a finding that never closes.
        """
        out = review_fix._apply_outcomes(self.NO_CHECKBOX, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out == self.NO_CHECKBOX

    # passes-at-base: base annotates nothing, so nothing can compound there
    def test_a_checkbox_free_finding_is_stable_across_rounds(self):
        outcome = _outcome("M1", FixOutcome.FIXED, verified=False,
                           verify_detail="no runnable check")
        text = self.NO_CHECKBOX
        for _ in range(3):
            text = review_fix._apply_outcomes(text, [outcome])
        assert text == self.NO_CHECKBOX

    # passes-at-base: base never interpolates verify_detail, so there is no quotation to escape
    def test_a_detail_quoting_an_annotation_does_not_become_one(self):
        """`verify_detail` is agent prose, and the gate reasons about this repo.

        The decline pattern is unanchored at its head, so a quotation inside the
        caveat is found there and the whole finding reads as adjudicated — which
        drops it from the next round's work set.
        """
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="the repro *(declined — see above)*"),
        ])
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is False
        assert finding.checked is True

    # passes-at-base: base never interpolates verify_detail, so there is no quotation to escape
    def test_a_detail_quoting_a_skip_does_not_become_one(self):
        """Asserted against the skip pattern itself, not `is_skipped`.

        `is_skipped` short-circuits on a checked finding, so it answers False
        for any tick however the annotation reads — it cannot see whether the
        quotation survived into the document.
        """
        out = review_fix._apply_outcomes(self.OPEN, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="see *(skipped — needs design)*"),
        ])
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert review_document._SKIP_TAIL_RE.search(finding.body) is None
        assert review_document.is_skipped(finding) is False
        assert finding.checked is True

    # passes-at-base: base leaves a carried-forward annotation alone by writing none of its own
    def test_an_already_hedged_line_gains_no_second_caveat(self):
        """A synthesis pass carries a trailing annotation forward intact.

        Asserted on the whole line, not on a count of the opening: the append
        path defuses a `*(` it finds in the line, so a second caveat arrives
        beside a first one that has been broken to `* (` — which a count of
        `*(unverified` reports as one, the same answer as leaving it alone.
        """
        hedged = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — x *(unverified — no runnable check)*\n"
        )
        out = review_fix._apply_outcomes(hedged, [
            _outcome("M1", FixOutcome.FIXED, verified=False,
                     verify_detail="no runnable check"),
        ])
        assert out.splitlines()[1] == (
            "- [x] **[M1]** `a.py:1` — x *(unverified — no runnable check)*"
        )

    def test_an_unverified_fix_with_no_detail_is_annotated_bare(self):
        out = review_fix._apply_outcomes(
            self.OPEN, [_outcome("M1", FixOutcome.FIXED, verified=False)],
        )
        assert out.splitlines()[1].endswith("*(unverified)*")

    # passes-at-base: base ticks and annotates nothing, idempotent for a reason this must keep
    def test_a_second_round_does_not_annotate_an_unverified_tick_twice(self):
        """A record accumulates across rounds, so the same outcome is re-applied."""
        outcome = _outcome("M1", FixOutcome.FIXED, verified=False,
                           verify_detail="no runnable check")
        once = review_fix._apply_outcomes(self.OPEN, [outcome])
        assert review_fix._apply_outcomes(once, [outcome]) == once

    # passes-at-base: asserts the silence this change was careful to preserve
    def test_a_verified_fix_ticks_the_box_and_says_nothing_more(self):
        out = review_fix._apply_outcomes(
            self.OPEN, [_outcome("M1", FixOutcome.FIXED, verified=True)],
        )
        assert out.splitlines()[1] == "- [x] **[M1]** `a.py:1` — Missing nil check"

    def test_a_needs_a_person_is_annotated_as_a_skip(self):
        """`*(skipped — reason)*` is the vocabulary the review's parser reads."""
        out = review_fix._apply_outcomes(
            self.OPEN, [_outcome("M2", FixOutcome.NEEDS_HUMAN, "needs design")],
        )
        assert out.splitlines()[2].endswith("*(skipped — needs design)*")
        assert review_document.ReviewDocument.parse(out).findings[1].checked is False

    def test_an_agent_s_decline_is_annotated_as_one(self):
        out = review_fix._apply_outcomes(
            self.OPEN, [_outcome("M1", FixOutcome.DECLINED, "documented tradeoff")],
        )
        finding = review_document.ReviewDocument.parse(out).findings[0]
        assert finding.declined is True
        assert finding.decline_reason == "documented tradeoff"

    def test_an_annotation_with_no_reason_still_registers(self):
        out = review_fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.DECLINED)])
        assert review_document.ReviewDocument.parse(out).findings[0].declined is True

    def test_a_finding_the_agent_never_reached_is_left_for_the_next_round(self):
        out = review_fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.DEFERRED)])
        assert out == self.OPEN

    def test_a_finding_no_outcome_names_is_left_alone(self):
        assert review_fix._apply_outcomes(self.OPEN, []) == self.OPEN

    def test_a_box_the_review_already_ticked_is_not_re_annotated(self):
        text = "## Must fix\n- [x] **[M1]** `a.py:1` — Already fixed\n"
        out = review_fix._apply_outcomes(
            text, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "needs design")],
        )
        assert out == text

    def test_a_finding_the_review_declined_keeps_that_verdict(self):
        """The decline outranks the pass: it was reached before the agent ran."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — *(declined — documented tradeoff)* — Lock\n"
        )
        out = review_fix._apply_outcomes(text, [_outcome("M1", FixOutcome.FIXED)])
        assert out == text

    def test_a_finding_already_carrying_a_skip_gains_no_second_annotation(self):
        """Two annotations on one line leave the document saying two things."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.py:1` — Lock *(skipped — needs design)*\n"
        )
        out = review_fix._apply_outcomes(
            text, [_outcome("M1", FixOutcome.NEEDS_HUMAN, "still needs design")],
        )
        assert out == text

    def test_prose_outside_a_finding_line_is_untouched(self):
        text = self.OPEN + "\n## Notes\n\nA paragraph about `- [ ] **[M1]**` syntax.\n"
        out = review_fix._apply_outcomes(text, [_outcome("M1", FixOutcome.FIXED)])
        assert out.endswith("A paragraph about `- [ ] **[M1]**` syntax.\n")
        assert "- [x] **[M1]**" in out


# ── end to end, against a real repo ─────────────────────────────────────────


class TestWhatALandedPassLeavesBehind:
    """One pass over a dirty worktree: what is committed, and what the review says.

    A `tsc` run before the review left a 272KB incremental cache untracked in
    the worktree; `git add -A` committed and pushed it, and the post-hoc scan
    checked off a finding on a file that was dirty before the agent started.
    """

    REVIEW = (
        "## Must fix\n"
        "- [ ] **[M1]** `src.py:1` — Was already being edited by hand\n"
        "- [ ] **[M2]** `helper.py:1` — Missing helper\n"
    )

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_only_the_agents_own_changes_are_committed_and_credited(
        self, mock_push, git_wt, tmp_path,
    ):
        (git_wt / "tsconfig.tsbuildinfo").write_text("272KB of cache\n")
        (git_wt / "src.py").write_text("hand-edited, not by the fix agent\n")
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            (git_wt / "build.cache").write_text("artifact\n")

        _run(job, {"M2": "fixed", "M1": "needs a person — hand edit in flight"},
             work=agent_run)

        assert _committed_paths(git_wt) == {"helper.py"}

        status = git_out(git_wt, "status", "--porcelain")
        assert "tsconfig.tsbuildinfo" in status
        assert " M src.py" in status
        assert "build.cache" not in status

        review = Path(job.review_file).read_text()
        assert "- [x] **[M2]**" in review
        assert "- [ ] **[M1]**" in review
        assert "*(skipped — hand edit in flight)*" in review
        mock_push.assert_called_once()

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_the_commit_message_reports_what_the_pass_settled(
        self, mock_push, git_wt, tmp_path,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")

        _run(job, {"M2": "fixed", "M1": "needs a person — needs a product decision"},
             work=agent_run)

        msg = git_out(git_wt, "log", "-1", "--format=%B")
        assert "1 fixed, 1 skipped" in msg
        assert "[M2] Missing helper" in msg
        assert "[M1] needs a product decision" in msg

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_the_summary_reaches_the_operator_s_terminal(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M1": "declined — by design", "M2": "declined — by design"})

        err = capsys.readouterr().err
        assert "Fix summary:" in err
        assert "[M1] by design" in err

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_pass_that_changed_no_files_commits_nothing(
        self, mock_push, git_wt, tmp_path,
    ):
        """Every finding declined is an answer, and answers are not edits."""
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M1": "declined — by design", "M2": "declined — by design"})

        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()
        assert "*(declined — by design)*" in Path(job.review_file).read_text()

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_commit_the_hook_refused_still_re_renders_the_review(
        self, mock_push, git_wt, tmp_path, live_git_hooks,
    ):
        """The agent's fix is real and in the worktree; only the commit failed.

        `live_git_hooks` is what lets the hook run at all — the suite disowns
        hooks by default.
        """
        _install_failing_pre_commit(tmp_path)
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M2": "fixed"},
             work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"))

        mock_push.assert_not_called()
        assert "- [x] **[M2]**" in Path(job.review_file).read_text()


class TestTheHeldCommitIsRecorded:
    """The push is gated and the commit is not, so a pass ordinarily leaves one.

    Before the sidecar recorded it, the only trace was a `resume` line on a
    terminal the operator may already have closed: no surface knew the commit
    existed, and the review directory that held the findings held nothing about
    the work done against them.
    """

    @staticmethod
    def _landing(status, sha="abc1234", resume=""):
        return fix_engine.FixRun(
            landed=land.LandResult(status=status, sha=sha, resume=resume),
        )

    REVIEW = (
        "## Must fix\n"
        "- [ ] **[M1]** `a.py:1` — Missing nil check\n"
    )

    def test_the_pass_records_what_it_landed(self, git_wt, tmp_path):
        """Asserted through `run_fix_pass`, not by calling the recorder.

        Every other case here drives `_record_commit` directly, so all of them
        pass against a pass that never calls it — which is the same wiring bug
        `test_the_pass_hands_the_engine_a_gate_at_all` exists to catch one
        argument along.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        landed = land.LandResult(status=land.CommitStatus.PUSH_HELD, sha="abc1234")
        with patch.object(review_fix.fix_engine, "run",
                          return_value=fix_engine.FixRun(landed=landed)):
            review_fix.run_fix_pass(job)

        meta = review_paths.read_review_meta(Path(job.artifact_dir))
        assert meta.unpushed_fix_commit == "abc1234"

    def test_a_held_commit_is_recorded_as_owed(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review_fix._record_commit(job, self._landing(land.CommitStatus.PUSH_HELD))

        meta = review_paths.read_review_meta(review_dir)
        assert meta.fix_commit_sha == "abc1234"
        assert meta.unpushed_fix_commit == "abc1234"

    def test_a_pushed_commit_owes_nothing(self, git_wt, tmp_path):
        job = _make_job(git_wt, tmp_path)
        review_fix._record_commit(job, self._landing(land.CommitStatus.PUSHED))

        meta = review_paths.read_review_meta(Path(job.artifact_dir))
        assert meta.fix_commit_sha == "abc1234"
        assert meta.unpushed_fix_commit == ""

    def test_every_unpushed_status_reads_as_owed(self, git_wt, tmp_path):
        """Which statuses mean "still local" is `pr.attribution`'s answer.

        Asserted over the whole enum rather than the one status the gate
        produces today, because a list kept here would be the second one and
        would drift — as a hand-written one already did, by omitting
        `push_unverified`.
        """
        job = _make_job(git_wt, tmp_path)
        for status in land.CommitStatus:
            review_fix._record_commit(job, self._landing(status))
            meta = review_paths.read_review_meta(Path(job.artifact_dir))
            expected = "abc1234" if attribution.commit_unpushed(status) else ""
            assert meta.unpushed_fix_commit == expected, status

    def test_recording_keeps_what_the_sidecar_already_held(self, git_wt, tmp_path):
        """The sidecar is the review's attribution; a fix pass adds to it."""
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review_paths.write_review_meta(
            review_dir, review_types.ReviewMeta(repo="o/r", head_sha="deadbeef"),
        )
        review_fix._record_commit(job, self._landing(land.CommitStatus.PUSH_HELD))

        meta = review_paths.read_review_meta(review_dir)
        assert (meta.repo, meta.head_sha) == ("o/r", "deadbeef")
        assert meta.unpushed_fix_commit == "abc1234"

    def test_a_pass_with_no_landing_leaves_the_record_alone(self, git_wt, tmp_path):
        """Nothing to commit is not a retraction of what an earlier round said."""
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review_fix._record_commit(job, self._landing(land.CommitStatus.PUSH_HELD))
        review_fix._record_commit(job, fix_engine.FixRun(landed=None))

        assert review_paths.read_review_meta(review_dir).unpushed_fix_commit == "abc1234"

    def test_a_pass_that_landed_nothing_new_leaves_the_record_alone(self, git_wt, tmp_path):
        """`NO_CHANGES`/`COMMIT_FAILED` carry no new sha and must not overwrite one.

        A round that defers or declines every open finding still calls `land`
        with an empty scope, which answers `NO_CHANGES` rather than `None` —
        and a `COMMIT_FAILED` round leaves the same empty sha behind. Neither
        is a retraction of the commit an earlier round actually made.
        """
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review_fix._record_commit(job, self._landing(land.CommitStatus.PUSH_HELD))
        for status in (land.CommitStatus.NO_CHANGES, land.CommitStatus.COMMIT_FAILED):
            review_fix._record_commit(
                job, fix_engine.FixRun(landed=land.LandResult(status=status, sha="")),
            )
            meta = review_paths.read_review_meta(review_dir)
            assert meta.fix_commit_sha == "abc1234", status
            assert meta.unpushed_fix_commit == "abc1234", status

    def test_an_unrecordable_commit_does_not_take_the_sidecar_with_it(self, git_wt, tmp_path):
        """Every review lookup on the machine walks these files.

        A value the schema cannot serialise would cost the whole sidecar — the
        attribution of a review that was written correctly — rather than the one
        field it arrived in.
        """
        job = _make_job(git_wt, tmp_path)
        review_dir = Path(job.artifact_dir)
        review_paths.write_review_meta(
            review_dir, review_types.ReviewMeta(repo="o/r", head_sha="deadbeef"),
        )
        review_fix._record_commit(job, fix_engine.FixRun(landed=MagicMock()))

        meta = review_paths.read_review_meta(review_dir)
        assert (meta.repo, meta.head_sha) == ("o/r", "deadbeef")
        assert meta.fix_commit_sha == ""

    def test_a_sidecar_predating_the_field_owes_nothing(self):
        """An unpushed commit is a positive fact, never inferred from silence."""
        assert review_types.ReviewMeta().unpushed_fix_commit == ""
        assert review_types.ReviewMeta(fix_commit_sha="abc1234").unpushed_fix_commit == ""


class TestSnapshotDiffStagesEveryShapeOfChange:
    """What the snapshot diff must survive besides a plain edit.

    Attribution is a set of path strings, so each case below is a different way
    the two snapshots can disagree about what a path is: gone, moved, or
    spelled with bytes git escapes before it prints them.
    """

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_file_the_agent_deletes_is_committed_as_a_deletion(
        self, mock_push, git_wt, tmp_path,
    ):
        (git_wt / "dead_code.py").write_text("unused = 1\n")
        git_out(git_wt, "add", "dead_code.py")
        git_out(git_wt, "commit", "-qm", "add dead code")

        job = _make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `dead_code.py:1` — Dead code, delete it\n",
        )
        _run(job, {"N1": "fixed"}, work=lambda: (git_wt / "dead_code.py").unlink())

        assert _committed_paths(git_wt) == {"dead_code.py"}
        assert git_out(git_wt, "status", "--porcelain").strip() == ""
        assert "- [x] **[N1]**" in Path(job.review_file).read_text()

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_rename_commits_both_halves(self, mock_push, git_wt, tmp_path):
        """The old path leaves via the diff, the new one via the untracked list."""
        job = _make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `src.py:1` — Misnamed module\n",
        )
        _run(job, {"N1": "fixed"},
             work=lambda: (git_wt / "src.py").rename(git_wt / "renamed.py"))

        tracked = git_out(git_wt, "ls-tree", "--name-only", "HEAD").split()
        assert "renamed.py" in tracked
        assert "src.py" not in tracked
        assert git_out(git_wt, "status", "--porcelain").strip() == ""
        assert "- [x] **[N1]**" in Path(job.review_file).read_text()

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_path_git_would_escape_is_staged_verbatim(
        self, mock_push, git_wt, tmp_path,
    ):
        """`core.quotePath=false` is what keeps the name a pathspec git resolves.

        Escaped, the name reaches `git add` as `caf\\303\\251...`, which matches
        nothing — and that `add` runs under `check=True`, so the whole pass dies
        on a file whose only crime is an accent.
        """
        job = _make_job(
            git_wt, tmp_path,
            "## Nit\n- [ ] **[N1]** `café brûlé.py:1` — Needs a docstring\n",
        )
        _run(job, {"N1": "fixed"},
             work=lambda: (git_wt / "café brûlé.py").write_text("crème\n"))

        assert _committed_paths(git_wt) == {"café brûlé.py"}

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_path_dirty_before_the_pass_is_not_credited_even_when_edited(
        self, mock_push, git_wt, tmp_path,
    ):
        """The `ceiling:` in `run_fix_pass`, asserted rather than only described.

        Attribution is by path, so a path in both snapshots is in neither
        delta — the agent's edit to it goes uncommitted. The day attribution
        compares content across the snapshot, this test is what says the
        tradeoff is gone.
        """
        (git_wt / "src.py").write_text("hand edit in progress\n")
        job = _make_job(
            git_wt, tmp_path,
            "## Must fix\n- [ ] **[M1]** `src.py:1` — Missing guard\n",
        )
        _run(job, {"M1": "fixed"}, work=lambda: (git_wt / "src.py").write_text(
            "hand edit in progress\nagent fix\n",
        ))

        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        assert " M src.py" in git_out(git_wt, "status", "--porcelain")
        mock_push.assert_not_called()


class TestRunFixPassWhenTheSnapshotFails:
    """A snapshot git could not take must never read as an unchanged worktree.

    The difference between the two snapshots is the only list of paths the pass
    commits, so an empty one is indistinguishable from a pass that did nothing —
    which is how a `git status` killed by a SIGPIPE or a locked index ends with
    the agent's fixes discarded and the run reported as a success.
    """

    REVIEW = "## Must fix\n- [ ] **[M1]** `helper.py:1` — Missing helper\n"

    @staticmethod
    def _corrupt_index(git_wt):
        """Make every later read of the worktree's state fail, as a lock would."""
        (git_wt / ".git" / "index").write_bytes(b"garbage")

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_an_unreadable_worktree_stops_the_pass_before_the_agent_runs(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        """Refusing here costs nothing: the agent has not done any work yet.

        With no baseline the pass cannot tell its own edits from what was
        already in the worktree, so running the agent only produces work it
        would have to either commit wholesale or throw away.
        """
        self._corrupt_index(git_wt)
        inv = _run(_make_job(git_wt, tmp_path, self.REVIEW), {"M1": "fixed"})

        inv.assert_not_called()
        mock_push.assert_not_called()
        assert "skipping fix pass" in capsys.readouterr().err

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_the_agents_work_is_not_dropped_when_the_second_snapshot_fails(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        """The regression: edits survive in the worktree and the run says so."""
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            self._corrupt_index(git_wt)

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert (git_wt / "helper.py").read_text() == "def helper(): pass\n"
        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()

        err = capsys.readouterr().err
        assert "nothing was committed or pushed" in err
        assert str(git_wt) in err

    @patch("git.land.push.push", return_value=_PUSHED)
    def test_a_pass_that_could_not_attribute_its_work_re_renders_nothing(
        self, mock_push, git_wt, tmp_path,
    ):
        """A ticked box over an uncommitted fix would retire the finding for good.

        The document still calling every finding open is what sends the next
        round back over them, which is right: the commit that would have made
        them done never happened.
        """
        job = _make_job(git_wt, tmp_path, self.REVIEW)

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            self._corrupt_index(git_wt)

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert Path(job.review_file).read_text() == self.REVIEW


# ── the parsers the pass reads its work set through ─────────────────────────


class TestParseCheckboxState:
    def test_unchecked_finding(self):
        text = "## Must fix\n- [ ] **[M1]** **`file.go:10`** — Bug found\n"
        findings = review_document.ReviewDocument.parse(text).findings
        assert len(findings) == 1
        assert findings[0].checked is False

    def test_checked_finding(self):
        text = "## Must fix\n- [x] **[M1]** **`file.go:10`** — Bug fixed\n"
        findings = review_document.ReviewDocument.parse(text).findings
        assert len(findings) == 1
        assert findings[0].checked is True

    def test_no_checkbox_finding(self):
        text = "## Must fix\n- **[M1]** **`file.go:10`** — Bug found\n"
        findings = review_document.ReviewDocument.parse(text).findings
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
        findings = review_document.ReviewDocument.parse(text).findings
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
        assert review_document.is_skipped(finding) is True

    def test_a_trailing_annotation_registers(self):
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="Some finding body *(skipped -- needs confirmation)*",
        )
        assert review_document.is_skipped(finding) is True

    def test_a_skip_without_a_reason_still_registers(self):
        """Mirrors the decline case — a bare annotation is still a skip."""
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped)* — Some finding body",
        )
        assert review_document.is_skipped(finding) is True

    def test_a_plain_finding_carries_no_skip(self):
        finding = Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1, end_line=None,
            body="Plain finding body",
        )
        assert review_document.is_skipped(finding) is False

    def test_a_checked_finding_carries_no_skip(self):
        finding = Finding(
            id="M1", severity="M", seq=1, path="a.go", line=1, end_line=None,
            body="*(skipped — stale)* — body", checked=True,
        )
        assert review_document.is_skipped(finding) is False


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
        findings = review_document.ReviewDocument.parse(text).findings
        assert findings[0].declined is True
        assert findings[0].decline_reason == "documented `ceiling:` tradeoff"

    def test_a_decline_without_a_reason_still_registers(self):
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(declined)* — Body\n"
        findings = review_document.ReviewDocument.parse(text).findings
        assert findings[0].declined is True
        assert findings[0].decline_reason == ""

    def test_a_trailing_annotation_registers(self):
        """The templates also let the annotation close the line."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.go:1` — Global lock *(declined — by design)*\n"
        )
        findings = review_document.ReviewDocument.parse(text).findings
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
        findings = review_document.ReviewDocument.parse(text).findings
        assert findings[0].declined is False
        assert findings[0].decline_reason == ""

    def test_a_skip_is_not_a_decline(self):
        """A skip is work deferred; a decline is work rejected."""
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(skipped — needs design)* — Body\n"
        findings = review_document.ReviewDocument.parse(text).findings
        assert findings[0].declined is False

    def test_a_file_without_declines_parses_unchanged(self):
        """Review files predating `Declined` must keep parsing."""
        text = (
            "## Must fix\n"
            "- [x] **[M1]** `a.go:1` — Fixed\n"
            "- [ ] **[M2]** `b.go:2` — Still open\n"
        )
        findings = review_document.ReviewDocument.parse(text).findings
        assert [f.declined for f in findings] == [False, False]
        assert [f.decline_reason for f in findings] == ["", ""]


# ── the gates every fix pass shares ─────────────────────────────────────────


class TestCommittedNothing:
    """The other half of the gate, which opens on a worktree git cannot read.

    Against a real `git commit` for the same reason as the class above: which
    failures mean "the change was empty" is git's vocabulary, not this repo's.
    """

    def test_an_empty_commit_is_not_a_rejection(self, git_wt):
        result = land.git_client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert land.committed_nothing(result) is True

    def test_staged_but_unchanged_content_is_not_a_rejection(self, git_wt):
        """`add` of an unmodified file stages nothing, so the commit is empty."""
        git_out(git_wt, "add", "src.py")
        result = land.git_client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert land.committed_nothing(result) is True

    def test_a_hook_rejection_is_a_rejection(self, git_wt, tmp_path, live_git_hooks):
        """`live_git_hooks` is what lets the hook run — the suite disowns them."""
        _install_failing_pre_commit(tmp_path)
        (git_wt / "src.py").write_text("edited\n")
        git_out(git_wt, "add", "src.py")
        result = land.git_client.run("commit", "-m", "x", cwd=git_wt)
        assert not result.ok
        assert land.committed_nothing(result) is False
