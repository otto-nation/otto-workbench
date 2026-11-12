"""pr.fix_checkpoint: schema, partial summary, settle/finish from a snapshot."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import _fix, _make_state, _no_published_summary  # noqa: F401
from conftest import make_ctx
from agent.diagnosis import Diagnosis, DiagnosisKind
from git.land import CommitStatus
import fix.batches
import fix.tracking
from fix.types import FixItem
import pr.comments
import pr.comments_state
import pr.fix_checkpoint
import pr.settlement
import pr.state
from pr.comments_state import ThreadState
from pr.fix import FixOutcome, FixRecord, ItemOutcome
from pr.thread_models import (
    Classification, CommentItem, Complexity, ReplyOutcome, TriageResult,
    Verification,
)
from pr.thread_models import PRReport
import review.closeout
import review.deferred_issue
import pr.summary_publish

_SUMMARY_NOT_REFUSED = pr.summary_publish.DeferredRender(None)


def _fixable(tid="t1", **kw) -> CommentItem:
    kw.setdefault("file", "a.py")
    kw.setdefault("line", 1)
    kw.setdefault("summary", "rename the guard")
    kw.setdefault("reviewer", "alice")
    return CommentItem(
        id=tid,
        classification=Classification.ACTIONABLE_SUGGESTION,
        verification=Verification.VALID,
        complexity=Complexity.LOW,
        **kw,
    )


def _dismissed(tid="t2") -> CommentItem:
    return CommentItem(
        id=tid, file="b.py", line=2, summary="not our layer",
        classification=Classification.ACTIONABLE_SUGGESTION,
        verification=Verification.INVALID,
    )


def _addressed(tid="t3") -> CommentItem:
    return CommentItem(
        id=tid, file="c.py", line=3, summary="already does this",
        classification=Classification.ACTIONABLE_SUGGESTION,
        verification=Verification.ALREADY_ADDRESSED,
    )


def _filled(ctx, **kw) -> pr.fix_checkpoint.CommentsFixCheckpoint:
    kw.setdefault("version", pr.fix_checkpoint.CHECKPOINT_VERSION)
    kw.setdefault("phase", pr.fix_checkpoint.CheckpointPhase.TRIAGED)
    kw.setdefault("repo", ctx.repo)
    kw.setdefault("pr", ctx.pr_number or 0)
    kw.setdefault("branch", ctx.branch)
    kw.setdefault("head_sha", ctx.head_sha)
    ckpt = pr.fix_checkpoint.CommentsFixCheckpoint(**kw)
    # `load` discards a checkpoint at AGENT_RUNNING or later that has no
    # `head_before`, so fill one in for those phases.
    if (
        ckpt.phase.reached(pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING)
        and not ckpt.engine.head_before
    ):
        ckpt = replace(
            ckpt, engine=replace(ckpt.engine, head_before="abc1234"),
        )
    return ckpt


def _write(ctx, ckpt) -> None:
    assert pr.fix_checkpoint.save(ctx.target_dir, ckpt)


def _by_id(summary) -> dict[str, ItemOutcome]:
    return {o.id: o for o in summary.fix.items}


# ── schema ────────────────────────────────────────────────────────────────


class TestSchema:
    def test_start_records_supplied_head_sha_not_ctx(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path, head_sha="githubsha")
        ckpt = pr.fix_checkpoint.start(ctx, head_sha="localsha")
        assert ckpt.head_sha == "localsha"
        loaded = pr.fix_checkpoint.load(tmp_path)
        assert loaded is not None
        assert loaded.head_sha == "localsha"

    def test_round_trip(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        original = _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable(), _dismissed()]),
            outcomes=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED, reason="held")],
            replies=ReplyOutcome(posted=2, resolved=(ThreadState.ADDRESSED,)),
            engine=pr.fix_checkpoint.EngineProgress(
                answers=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED)],
                agent_files=["a.py"],
                suite={"status": "green"},
                diagnosis=Diagnosis(kind=DiagnosisKind.MAX_TURNS, num_turns=3),
                commit_sha="def5678",
                commit_status=CommitStatus.PUSH_HELD,
                head_before="abc1234",
                dirty_before=["wip.py"],
            ),
            seen_issue_comments={11: "stamp"},
        )
        _write(ctx, original)
        loaded = pr.fix_checkpoint.load(tmp_path)
        assert loaded is not None
        assert loaded.phase is pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING
        assert loaded.triage.threads[0].id == "t1"
        assert loaded.triage.threads[0].classification is Classification.ACTIONABLE_SUGGESTION
        assert loaded.outcomes[0].outcome is FixOutcome.FIXED
        assert loaded.replies.posted == 2
        assert loaded.replies.resolved == (ThreadState.ADDRESSED,)
        assert loaded.engine.diagnosis.kind is DiagnosisKind.MAX_TURNS
        assert loaded.engine.diagnosis.num_turns == 3
        assert loaded.engine.commit_status is CommitStatus.PUSH_HELD
        assert loaded.engine.suite == {"status": "green"}
        assert loaded.seen_issue_comments == {11: "stamp"}
        assert loaded.updated_at

    def test_version_mismatch_is_none(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        _write(ctx, _filled(ctx))
        path = pr.fix_checkpoint.checkpoint_path(tmp_path)
        raw = json.loads(path.read_text())
        raw["version"] = pr.fix_checkpoint.CHECKPOINT_VERSION + 1
        path.write_text(json.dumps(raw))
        assert pr.fix_checkpoint.load(tmp_path) is None

    def test_failed_replace_returns_false_and_leaves_the_old_file(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        first = _filled(ctx, phase=pr.fix_checkpoint.CheckpointPhase.TRIAGED)
        _write(ctx, first)
        path = pr.fix_checkpoint.checkpoint_path(tmp_path)
        before = path.read_text()
        later = _filled(ctx, phase=pr.fix_checkpoint.CheckpointPhase.LANDED)
        with patch("core.serde.replace_file", side_effect=OSError("disk full")):
            assert pr.fix_checkpoint.save(tmp_path, later) is False
        assert path.read_text() == before
        assert pr.fix_checkpoint.load(tmp_path).phase is pr.fix_checkpoint.CheckpointPhase.TRIAGED

    def test_advance_is_monotonic(self):
        ckpt = pr.fix_checkpoint.CommentsFixCheckpoint(
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
        )
        back = pr.fix_checkpoint.advance(ckpt, pr.fix_checkpoint.CheckpointPhase.TRIAGED)
        assert back.phase is pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING
        forward = pr.fix_checkpoint.advance(ckpt, pr.fix_checkpoint.CheckpointPhase.LANDED)
        assert forward.phase is pr.fix_checkpoint.CheckpointPhase.LANDED
        same = pr.fix_checkpoint.advance(ckpt, pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING)
        assert same.phase is pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING

    def test_complete_and_adopted_are_terminal(self):
        assert pr.fix_checkpoint.CheckpointPhase.COMPLETE.terminal
        assert pr.fix_checkpoint.CheckpointPhase.ADOPTED.terminal
        assert not pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING.terminal
        assert pr.fix_checkpoint.CheckpointPhase.LANDED.reached(
            pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING)
        assert not pr.fix_checkpoint.CheckpointPhase.TRIAGED.reached(
            pr.fix_checkpoint.CheckpointPhase.LANDED)


# ── partial_summary ───────────────────────────────────────────────────────


class TestPartialSummary:
    def test_triaged_unanswered_fixable_needs_a_person(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        summary = pr.fix_checkpoint.partial_summary(_filled(
            ctx,
            triage=TriageResult(threads=[_fixable(), _dismissed(), _addressed()]),
        ))
        by_id = _by_id(summary)
        assert by_id["t1"].outcome is FixOutcome.NEEDS_HUMAN
        assert by_id["t1"].reason == pr.fix_checkpoint.INTERRUPTED_REASON
        assert by_id["t2"].outcome is FixOutcome.DISMISSED
        assert by_id["t3"].outcome is FixOutcome.ALREADY_ADDRESSED

    def test_agent_running_unlanded_fixed_needs_a_person(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        summary = pr.fix_checkpoint.partial_summary(_filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable("t1"), _fixable("t4")]),
            engine=pr.fix_checkpoint.EngineProgress(answers=[
                ItemOutcome(id="t1", outcome=FixOutcome.FIXED, reason="held"),
            ]),
        ))
        by_id = _by_id(summary)
        assert by_id["t1"].outcome is FixOutcome.NEEDS_HUMAN
        assert by_id["t1"].reason == pr.fix_checkpoint.UNLANDED_REASON
        assert by_id["t4"].outcome is FixOutcome.NEEDS_HUMAN
        assert by_id["t4"].reason == pr.fix_checkpoint.INTERRUPTED_REASON

    def test_landed_keeps_fixed(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        summary = pr.fix_checkpoint.partial_summary(_filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.LANDED,
            triage=TriageResult(threads=[_fixable()]),
            engine=pr.fix_checkpoint.EngineProgress(
                answers=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED)],
                commit_sha="landed1",
                commit_status=CommitStatus.PUSH_HELD,
            ),
        ))
        by_id = _by_id(summary)
        assert by_id["t1"].outcome is FixOutcome.FIXED
        assert summary.fix.commit_sha == "landed1"
        assert summary.fix.commit_status is CommitStatus.PUSH_HELD


# ── adopt_partial ─────────────────────────────────────────────────────────


class TestAdoptPartial:
    def test_merges_keeps_prior_commit_and_marks_adopted(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        prior = _make_state(_fix(
            [ItemOutcome(id="old", outcome=FixOutcome.DISMISSED)],
            commit_sha="abc1234", commit_status="pushed",
        ))
        pr.state.save_state(tmp_path, prior)
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
            engine=pr.fix_checkpoint.EngineProgress(
                head_before="abc1234", agent_files=["a.py"],
            ),
        ))
        state = pr.fix_checkpoint.adopt_partial(ctx, pr.state.load_state(tmp_path))
        assert {o.id for o in state.fix.fix.items} >= {"t1", "old"}
        assert state.fix.fix.commit_sha == "abc1234"
        assert state.fix.fix.commit_status is CommitStatus.PUSHED
        loaded = pr.fix_checkpoint.load(tmp_path)
        assert loaded.phase is pr.fix_checkpoint.CheckpointPhase.ADOPTED

    def test_second_call_does_not_overwrite_a_later_settlement(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        state = pr.fix_checkpoint.adopt_partial(ctx, None)
        item = next(o for o in state.fix.fix.items if o.id == "t1")
        item.outcome = FixOutcome.DISMISSED
        item.reason = "not our layer"
        pr.state.save_state(tmp_path, state)
        again = pr.fix_checkpoint.adopt_partial(ctx, pr.state.load_state(tmp_path))
        settled = next(o for o in again.fix.fix.items if o.id == "t1")
        assert settled.outcome is FixOutcome.DISMISSED
        assert settled.reason == "not our layer"

    def test_mismatch_leaves_state_unchanged(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path, branch="feat/test")
        other = make_ctx(target_dir=tmp_path, branch="other")
        _write(other, _filled(other, triage=TriageResult(threads=[_fixable()])))
        assert pr.fix_checkpoint.adopt_partial(ctx, None) is None


# ── settle / finish from a partial ────────────────────────────────────────


class TestSettleAndFinishFromPartial:
    def test_settle_from_partial(self, tmp_path, capsys):
        ctx = make_ctx(
            branch="feature", worktree_root=tmp_path / "wt",
            head_sha="abc1234", target_dir=tmp_path / "target",
        )
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        assert pr.state.load_state(ctx.target_dir) is None
        assert pr.settlement.run_settle(
            ctx, ["t1"], "dismissed", "not our layer", "",
        ) == 0
        state = pr.state.load_state(ctx.target_dir)
        assert state.fix.fix.items[0].outcome is FixOutcome.DISMISSED
        assert pr.fix_checkpoint.load(ctx.target_dir).phase is pr.fix_checkpoint.CheckpointPhase.ADOPTED
        assert "pr comments --finish" in capsys.readouterr().err

    def test_finish_from_partial(self, worktree):
        ctx = make_ctx(
            branch="b", worktree_root=worktree, head_sha="abc1234",
            target_dir=worktree / "target",
        )
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        assert pr.state.load_state(ctx.target_dir) is None
        with patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred",
                             return_value=True), \
                patch.object(review.deferred_issue, "report_unfiled_deferrals"), \
                patch.object(pr.summary_publish, "render_deferred_summary",
                             return_value=_SUMMARY_NOT_REFUSED):
            assert review.closeout.finish_deferred_work(ctx, PRReport()) is True
        state = pr.state.load_state(ctx.target_dir)
        assert state is not None
        assert state.fix.fix.items[0].outcome is FixOutcome.NEEDS_HUMAN
        assert pr.fix_checkpoint.load(ctx.target_dir).phase is pr.fix_checkpoint.CheckpointPhase.ADOPTED

    def test_finish_warns_the_stopped_phase_and_resume_flags(self, worktree, capsys):
        ctx = make_ctx(
            branch="b", worktree_root=worktree, head_sha="abc1234",
            target_dir=worktree / "target",
        )
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        with patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred",
                             return_value=True), \
                patch.object(review.deferred_issue, "report_unfiled_deferrals"), \
                patch.object(pr.summary_publish, "render_deferred_summary",
                             return_value=_SUMMARY_NOT_REFUSED):
            assert review.closeout.finish_deferred_work(ctx, PRReport()) is True
        err = capsys.readouterr().err
        assert "agent_running" in err
        assert "--no-resume" in err
        assert "to continue one" not in err
        assert "instead of" not in err

    def test_ticked_answer_with_no_checkpointed_outcome_from_tracking(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        path = pr.comments.artifacts_dir(tmp_path) / fix.batches.TRACKING_FILENAME
        fix.tracking.write(path, "threads", [FixItem(id="t1", file="a.py", line=1)])
        assert "- [ ] needs a person — <why>" in path.read_text()
        text = path.read_text().replace(
            "- [ ] needs a person — <why>",
            "- [x] needs a person — wants a design call",
            1,
        )
        path.write_text(text)
        answers = review.closeout.tracking_answers(tmp_path)
        state = pr.fix_checkpoint.adopt_partial(ctx, None, answers=answers)
        item = next(o for o in state.fix.fix.items if o.id == "t1")
        assert item.outcome is FixOutcome.NEEDS_HUMAN
        assert item.reason == "wants a design call"

    def test_unknown_settle_id_does_not_adopt(self, tmp_path):
        ctx = make_ctx(
            branch="feature", worktree_root=tmp_path / "wt",
            head_sha="abc1234", target_dir=tmp_path / "target",
        )
        (tmp_path / "wt").mkdir()
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        assert pr.settlement.run_settle(
            ctx, ["typo"], "dismissed", "not our layer", "",
        ) == 1
        loaded = pr.fix_checkpoint.load(ctx.target_dir)
        assert loaded is not None
        assert loaded.phase is pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING

    def test_failed_adopted_save_raises(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        _write(ctx, _filled(
            ctx,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            triage=TriageResult(threads=[_fixable()]),
        ))
        real = pr.fix_checkpoint.save

        def boom(target_dir, ckpt):
            if ckpt.phase is pr.fix_checkpoint.CheckpointPhase.ADOPTED:
                return False
            return real(target_dir, ckpt)

        with patch.object(pr.fix_checkpoint, "save", side_effect=boom):
            try:
                pr.fix_checkpoint.adopt_partial(ctx, None)
            except pr.fix_checkpoint.CheckpointAdoptError:
                pass
            else:
                raise AssertionError("expected CheckpointAdoptError")
        assert pr.fix_checkpoint.load(tmp_path).phase is pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING


class TestFoldAndLoad:
    def test_triaged_stale_tracking_does_not_leak(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        summary = pr.fix_checkpoint.partial_summary(
            _filled(
                ctx,
                phase=pr.fix_checkpoint.CheckpointPhase.TRIAGED,
                triage=TriageResult(threads=[_fixable()]),
            ),
            answers=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED, reason="stale")],
        )
        item = next(o for o in summary.fix.items if o.id == "t1")
        assert item.outcome is FixOutcome.NEEDS_HUMAN
        assert item.reason == pr.fix_checkpoint.INTERRUPTED_REASON

    def test_landed_keeps_engine_demotion_over_tracking_fixed(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        demoted = ItemOutcome(
            id="t1", outcome=FixOutcome.FIXED, verified=False,
            verify_detail="the repo's checks are red with this pass's changes in the tree",
        )
        summary = pr.fix_checkpoint.partial_summary(
            _filled(
                ctx,
                phase=pr.fix_checkpoint.CheckpointPhase.LANDED,
                triage=TriageResult(threads=[_fixable()]),
                engine=pr.fix_checkpoint.EngineProgress(
                    answers=[demoted],
                    commit_sha="landed1",
                    commit_status=CommitStatus.PUSH_HELD,
                    head_before="abc1234",
                ),
            ),
            answers=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED, reason="raw")],
        )
        item = next(o for o in summary.fix.items if o.id == "t1")
        assert item.outcome is FixOutcome.FIXED
        assert item.reason != "raw"

    def test_agent_running_without_head_before_is_unreadable(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        ckpt = pr.fix_checkpoint.CommentsFixCheckpoint(
            version=pr.fix_checkpoint.CHECKPOINT_VERSION,
            phase=pr.fix_checkpoint.CheckpointPhase.AGENT_RUNNING,
            repo=ctx.repo, pr=ctx.pr_number or 0, branch=ctx.branch,
            head_sha=ctx.head_sha,
        )
        _write(ctx, ckpt)
        assert pr.fix_checkpoint.load(tmp_path) is None

    def test_save_stamps_checkpoint_version(self, tmp_path):
        ctx = make_ctx(target_dir=tmp_path)
        ckpt = pr.fix_checkpoint.CommentsFixCheckpoint(
            version=0, phase=pr.fix_checkpoint.CheckpointPhase.TRIAGED,
            repo=ctx.repo, pr=ctx.pr_number or 0, branch=ctx.branch,
        )
        _write(ctx, ckpt)
        raw = json.loads(pr.fix_checkpoint.checkpoint_path(tmp_path).read_text())
        assert raw["version"] == pr.fix_checkpoint.CHECKPOINT_VERSION
