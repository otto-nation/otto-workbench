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
from unittest.mock import patch

import pytest
from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent_invoke
import fix_engine
import land
import push
import review_document
import review_fix
import review_paths
import review_types
from proc import TIMEOUT_RETURNCODE, CmdResult
from phases import Effort, Phase
from pr_fix import FixOutcome, ItemOutcome
from gh_types import PRContext, PRMetadata
from review_types import Finding, ReviewJob

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


def _run(job: ReviewJob, boxes: dict[str, str], *, work=None, **kwargs):
    """Run the pass with the agent stubbed, and hand back the stub."""
    with patch.object(fix_engine.agent_invoke, "run_fix",
                      side_effect=_answer(job, boxes, work=work)) as inv:
        review_fix.run_fix_pass(job, **kwargs)
    return inv


def _outcome(item_id: str, outcome: FixOutcome, reason: str = "") -> ItemOutcome:
    return ItemOutcome(id=item_id, outcome=outcome, reason=reason)


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
        adapter = review_fix.ReviewFixAdapter(job, [_finding("N1")], set())

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
        adapter = review_fix.ReviewFixAdapter(job, [_finding("N1")], set())

        assert adapter.session_log == Path(
            review_paths.phase_log_path(job.review_file, Phase.FIX),
        )
        assert adapter.session_log.parent == Path(job.artifact_dir)

    def test_the_agent_may_read_the_review_directory_it_answers_in(
        self, git_wt, tmp_path,
    ):
        """The tracking file lives there, not in the worktree under review."""
        job = _make_job(git_wt, tmp_path, "## Nit\n- [ ] **[N1]** `src.py:1` — Style\n")
        adapter = review_fix.ReviewFixAdapter(job, [_finding("N1")], set())

        assert adapter.tracking_path.parent in adapter.add_dirs()
        assert adapter.workdir in adapter.add_dirs()


# ── what the pass commits ───────────────────────────────────────────────────


class TestTheCommitScope:
    """`landing` names the paths; `land_test.py` holds what git does with them."""

    def _adapter(self, git_wt, tmp_path, before=frozenset()):
        job = _make_job(git_wt, tmp_path, "## Must fix\n- [ ] **[M1]** `a.py:1` — Bug\n")
        return review_fix.ReviewFixAdapter(job, [_finding("M1")], set(before))

    def test_the_scope_is_what_the_agent_added_to_the_dirty_set(
        self, git_wt, tmp_path,
    ):
        adapter = self._adapter(git_wt, tmp_path)
        (git_wt / "helper.py").write_text("def helper(): pass\n")
        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)])

        assert spec.paths == {"helper.py"}

    def test_a_snapshot_that_failed_scopes_the_commit_to_nothing(
        self, git_wt, tmp_path,
    ):
        """An empty scope commits nothing; `None` would commit the whole tree."""
        adapter = self._adapter(git_wt, tmp_path)
        (git_wt / ".git" / "index").write_bytes(b"garbage")
        spec = adapter.landing([_outcome("M1", FixOutcome.FIXED)])

        assert spec.paths == set()
        assert adapter.changed is None

    def test_the_message_counts_what_the_pass_settled(self, git_wt, tmp_path):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([
            _outcome("M1", FixOutcome.FIXED),
            _outcome("M2", FixOutcome.NEEDS_HUMAN, "needs design"),
            _outcome("M3", FixOutcome.DEFERRED),
        ])

        assert spec.message.startswith("fix: self-review findings")
        assert "1 fixed, 2 skipped" in spec.message

    def test_a_pass_that_fixed_nothing_omits_the_count(self, git_wt, tmp_path):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([_outcome("M1", FixOutcome.DECLINED, "by design")])

        assert "fixed," not in spec.message
        assert "Declined:" in spec.message

    def test_the_summary_rides_in_the_commit_message(self, git_wt, tmp_path):
        adapter = self._adapter(git_wt, tmp_path)
        spec = adapter.landing([
            _outcome("M1", FixOutcome.FIXED),
            _outcome("S1", FixOutcome.NEEDS_HUMAN, "needs design"),
        ])

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


# ── what the review document ends up saying ─────────────────────────────────


class TestApplyOutcomes:
    """The re-render — the review document is written from the outcomes."""

    OPEN = (
        "## Must fix\n"
        "- [ ] **[M1]** `a.py:1` — Missing nil check\n"
        "- [ ] **[M2]** `b.py:2` — Retry budget is unbounded\n"
    )

    def test_a_fix_ticks_the_box(self):
        out = review_fix._apply_outcomes(self.OPEN, [_outcome("M1", FixOutcome.FIXED)])
        assert "- [x] **[M1]**" in out
        assert "- [ ] **[M2]**" in out

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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
    def test_the_summary_reaches_the_operator_s_terminal(
        self, mock_push, git_wt, tmp_path, capsys,
    ):
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M1": "declined — by design", "M2": "declined — by design"})

        err = capsys.readouterr().err
        assert "Fix summary:" in err
        assert "[M1] by design" in err

    @patch("land.push.push", return_value=_PUSHED)
    def test_a_pass_that_changed_no_files_commits_nothing(
        self, mock_push, git_wt, tmp_path,
    ):
        """Every finding declined is an answer, and answers are not edits."""
        job = _make_job(git_wt, tmp_path, self.REVIEW)
        _run(job, {"M1": "declined — by design", "M2": "declined — by design"})

        assert git_out(git_wt, "log", "--oneline").strip().count("\n") == 0
        mock_push.assert_not_called()
        assert "*(declined — by design)*" in Path(job.review_file).read_text()

    @patch("land.push.push", return_value=_PUSHED)
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


class TestSnapshotDiffStagesEveryShapeOfChange:
    """What the snapshot diff must survive besides a plain edit.

    Attribution is a set of path strings, so each case below is a different way
    the two snapshots can disagree about what a path is: gone, moved, or
    spelled with bytes git escapes before it prints them.
    """

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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

    @patch("land.push.push", return_value=_PUSHED)
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


# ── the snapshot itself ─────────────────────────────────────────────────────


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

    def test_an_unchanged_worktree_is_an_empty_delta_not_a_failed_one(self, git_wt):
        """Empty says the agent changed nothing; None says the pass cannot tell."""
        before = review_fix._changed_source_files(str(git_wt))
        assert review_fix._agent_changed(str(git_wt), before) == set()


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
