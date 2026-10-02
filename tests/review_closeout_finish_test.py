"""review.closeout: --finish, reconciling before it writes."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import _fetches, _fix, _no_published_summary, _our_reply  # noqa: E402
from conftest import make_ctx
import pr.state
from pr.comments_state import ThreadState
import core.log
import git.client
from git.land import CommitStatus
import pr.comments
import pr.thread_replies
import pr.summary_publish
from pr.fix import FixOutcome, ItemOutcome, SettledBy
from pr.state import PRIdentity, PRState
from pr.thread_models import PRReport, ReportThread
import review.closeout
import review.deferred_issue


# ── _finish_deferred_work ─────────────────────────────────────────────────


class TestFinishDeferredWork:
    """The close-out phase: push-deferred replies, tracking issue, summary."""

    def _ctx(self, worktree):
        return make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                        target_dir=worktree / "target")

    def _save(self, worktree, **fix_kw):
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
            ),
            fix=_fix(**fix_kw),
        ))

    def test_all_three_steps_run_in_order(self, worktree):
        self._save(worktree)
        order = []
        with patch.object(review.closeout, "post_pending_fix_replies",
                          side_effect=lambda *a, **k: order.append("replies")), \
                patch.object(review.deferred_issue, "finalize_deferred",
                             side_effect=lambda *a, **k: order.append("issue") or True), \
                patch.object(pr.summary_publish, "render_deferred_summary",
                             side_effect=lambda *a, **k: order.append("summary")):
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        assert order == ["replies", "issue", "summary"]

    def test_a_refused_track_stops_before_the_summary_and_reports(self, worktree):
        """The refusal reaches the caller as a value, not as a process exit.

        The summary and the unfiled list both describe a filing run, so neither
        may render once the tracking ids were rejected and nothing was filed.
        """
        self._save(worktree)
        with patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred",
                             return_value=False), \
                patch.object(review.deferred_issue, "report_unfiled_deferrals") as unfiled, \
                patch.object(pr.summary_publish, "render_deferred_summary") as summary:
            assert review.closeout.finish_deferred_work(
                self._ctx(worktree), PRReport()) is False
        unfiled.assert_not_called()
        summary.assert_not_called()

    def test_state_written_by_the_steps_is_persisted(self, worktree):
        """The steps mutate in place; this phase is the one that saves."""
        self._save(worktree)

        def mark(state, *a, **k):
            state.fix.fix.commit_status = CommitStatus.PUSHED

        with patch.object(review.closeout, "post_pending_fix_replies", side_effect=mark), \
                patch.object(review.deferred_issue, "finalize_deferred", return_value=True), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        on_disk = pr.state.load_state(worktree / "target")
        assert on_disk.fix.fix.commit_status == CommitStatus.PUSHED

    def test_it_reads_state_from_disk_not_from_the_caller(self, worktree):
        """The fix pass writes its outcomes there; a stale copy would miss them."""
        self._save(worktree, items=[
            ItemOutcome(id="t9", outcome=FixOutcome.DEFERRED, reason="r"),
        ])
        seen = []
        with patch.object(review.closeout, "post_pending_fix_replies",
                          side_effect=lambda st, *a, **k: seen.extend(st.fix.fix.items)), \
                patch.object(review.deferred_issue, "finalize_deferred", return_value=True), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        assert [t.id for t in seen] == ["t9"]

    def test_no_state_on_disk_is_a_no_op(self, worktree):
        with patch.object(review.closeout, "post_pending_fix_replies") as replies:
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        replies.assert_not_called()

    def test_a_held_pr_description_is_delivered_and_the_debt_cleared(
        self, worktree, publishing_on,
    ):
        self._save(worktree, pr_body_pending=True)
        # Where the fix pass left it: the run's artifact directory, which is
        # keyed off the target dir rather than sitting in the worktree.
        draft = pr.comments.pr_body_draft(
            pr.comments.artifacts_dir(worktree / "target"))
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("A rewritten description.\n")
        with patch.object(pr.comments, "update_pr_body", return_value=True) as update, \
                patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred", return_value=True), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        update.assert_called_once_with(
            "owner/repo", 42, "A rewritten description.",
        )
        assert pr.state.load_state(worktree / "target").fix.pr_body_pending is False

    def test_a_description_nobody_drafted_is_not_looked_for(self, worktree):
        self._save(worktree)
        with patch.object(pr.comments, "deliver_pr_body") as deliver, \
                patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred", return_value=True), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        deliver.assert_not_called()

    def test_a_failing_step_propagates(self, worktree):
        """A caller closing the loop needs a failure to be an error, not a log line."""
        self._save(worktree)
        with patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(review.deferred_issue, "finalize_deferred",
                             side_effect=RuntimeError("gh down")), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            with pytest.raises(RuntimeError):
                review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())


class TestReconcileRunsBeforeTheWrites:
    """Within one invocation the two must not disagree about the same thread."""

    def test_reconciled_thread_never_reaches_the_tracking_issue(self, worktree):
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", items=[
                ItemOutcome(id="t1", file="a.go", line=1,
                            summary="one", outcome=FixOutcome.DEFERRED),
            ], reviewers={"t1": "kgn"}),
        ))
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                       target_dir=worktree / "target")
        report = PRReport(threads=[ReportThread(
            id="t1", state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "x"}, {"body": "Applied: one\n\nFixed in `abc1234`."}],
        )])
        with patch.object(git.client, "head_sha", return_value="aaaaaaa"), \
                patch.object(review.deferred_issue, "create_or_update_deferred_issue") as create, \
                patch.object(pr.thread_replies, "post_deferred_replies") as reply, \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(ctx, report, track=review.deferred_issue.TRACK_ALL)
        create.assert_not_called()
        reply.assert_not_called()

    def test_the_flip_is_persisted(self, worktree):
        """Otherwise the next --finish re-derives it from the same stale row."""
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", items=[
                ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED),
            ]),
        ))
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                       target_dir=worktree / "target")
        report = PRReport(threads=[ReportThread(
            id="t1", state=ThreadState.RESOLVED, is_resolved=True,
            comments=[{"body": "x"}],
        )])
        with patch.object(git.client, "head_sha", return_value="aaaaaaa"), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(ctx, report)
        on_disk = pr.state.load_state(worktree / "target")
        assert on_disk.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE
        assert on_disk.fix.fix.items[0].settled_by is SettledBy.RECONCILIATION


class TestStaleSnapshotIsAnnounced:
    """A snapshot from a different HEAD is a record of the past, not a plan."""

    def _state(self, worktree, snapshot_sha):
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha=snapshot_sha, worktree_root=str(worktree)),
            fix=_fix(
                head_sha=snapshot_sha,
                items=[ItemOutcome(id="t1", file="a.go", line=7,
                                   summary="rename the guard",
                                   outcome=FixOutcome.DEFERRED,
                                   reason="agent could not auto-fix")],
                reviewers={"t1": "kgn"},
            ),
        )
        pr.state.save_state(worktree / "target", state)
        return state

    def _ctx(self, worktree):
        return make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                        target_dir=worktree / "target")

    def _warnings(self, worktree, current_sha):
        seen = []
        with patch.object(git.client, "head_sha", return_value=current_sha), \
                patch.object(core.log, "warn", side_effect=seen.append), \
                patch.object(review.closeout, "post_pending_fix_replies"), \
                patch.object(pr.summary_publish, "render_deferred_summary"), \
                patch.object(review.deferred_issue, "finalize_deferred"):
            review.closeout.finish_deferred_work(self._ctx(worktree), PRReport())
        return seen

    def test_head_moved_is_announced(self, worktree):
        self._state(worktree, "aaaaaaa")
        warned = self._warnings(worktree, "bbbbbbb")
        assert any("aaaaaaa" in w and "bbbbbbb" in w for w in warned)

    def test_head_unchanged_says_nothing(self, worktree):
        self._state(worktree, "aaaaaaa")
        assert self._warnings(worktree, "aaaaaaa") == []

    def test_missing_snapshot_sha_is_treated_as_stale(self, worktree):
        """Legacy state predates the field; it cannot be vouched for."""
        state = self._state(worktree, "aaaaaaa")
        state.fix.fix.head_sha = ""
        pr.state.save_state(worktree / "target", state)
        assert any("(unrecorded)" in w for w in self._warnings(worktree, "aaaaaaa"))

    def test_an_empty_snapshot_has_nothing_to_be_stale_about(self, worktree):
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(),
        ))
        assert self._warnings(worktree, "bbbbbbb") == []


class TestFinishReconcilesCommentItems:
    """The wiring: --finish is what asks GitHub about the source comments."""

    def _save(self, worktree):
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", items=[
                ItemOutcome(id="ic-77-0", file="a.go", line=7,
                            summary="drop the retry",
                            outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
            ], reviewers={"ic-77-0": "kgn"}),
        ))
        return make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                        target_dir=worktree / "target")

    def _run(self, ctx, comments):
        with patch.object(git.client, "head_sha", return_value="aaaaaaa"), \
                _fetches(comments), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(ctx, PRReport(my_login="me"))

    def test_the_answered_item_is_persisted_as_fixed(self, worktree):
        ctx = self._save(worktree)
        self._run(ctx, [_our_reply("#issuecomment-77")])
        saved = pr.state.load_state(worktree / "target")
        assert saved.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_an_unanswered_item_survives_the_round(self, worktree):
        ctx = self._save(worktree)
        self._run(ctx, [_our_reply("#issuecomment-99")])
        saved = pr.state.load_state(worktree / "target")
        assert saved.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_a_reconciled_row_re_arms_the_summary(self, worktree):
        """The corrected row has to reach the comment the reviewer reads.

        `render_deferred_summary` early-returns unless a summary is deferred, so
        a --finish that reconciled a row on a PR whose summary already went out
        saved the correction locally and left the published comment saying
        whatever the round before it said.
        """
        ctx = self._save(worktree)
        self._run(ctx, [_our_reply("#issuecomment-77")])
        assert pr.state.load_state(worktree / "target").fix.summary_deferred

    def test_a_round_that_reconciled_nothing_owes_no_summary(self, worktree):
        """Re-arming on a no-op would leave a closeout owed on every run."""
        ctx = self._save(worktree)
        self._run(ctx, [_our_reply("#issuecomment-99")])
        assert not pr.state.load_state(worktree / "target").fix.summary_deferred


class TestFinishAdoptsThreadsNoRoundSaw:
    """The wiring: --finish is the stage an answered thread finally reaches.

    Triage excludes an ADDRESSED thread from the round and nothing downstream
    picked it up, so it reached no bucket, was never a snapshot row, and every
    later stage read past it. Five hand-answered threads stayed open through
    three consecutive runs that way.
    """

    def _save(self, worktree):
        pr.state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", items=[]),
        ))
        return make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                        target_dir=worktree / "target")

    def _report(self, *, bodies=("rename this", "done by hand")):
        return PRReport(my_login="me", threads=[ReportThread(
            id="t1", state=ThreadState.ADDRESSED, reviewer="kgn",
            file="a.go", line=7, my_login="me",
            comments=[{"body": b} for b in bodies],
        )])

    def _run(self, ctx, report):
        with patch.object(git.client, "head_sha", return_value="aaaaaaa"), \
                _fetches([]), \
                patch.object(pr.summary_publish, "render_deferred_summary"):
            review.closeout.finish_deferred_work(ctx, report)

    def test_the_answered_thread_is_persisted_rather_than_dropped(self, worktree):
        ctx = self._save(worktree)
        self._run(ctx, self._report())
        saved = pr.state.load_state(worktree / "target")
        assert [o.id for o in saved.fix.fix.items] == ["t1"]
        assert saved.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE

    def test_the_summary_is_re_armed_so_the_row_reaches_a_reader(self, worktree):
        """A row nobody has published is a summary the PR is still owed."""
        ctx = self._save(worktree)
        self._run(ctx, self._report())
        assert pr.state.load_state(worktree / "target").fix.summary_deferred

    def test_a_second_finish_adds_no_second_row(self, worktree):
        ctx = self._save(worktree)
        self._run(ctx, self._report())
        self._run(ctx, self._report())
        saved = pr.state.load_state(worktree / "target")
        assert len(saved.fix.fix.items) == 1

    def test_a_thread_still_awaiting_a_reviewer_is_reported_not_recorded(
        self, worktree,
    ):
        """NEW is a thread nobody has answered — there is no ending to record."""
        ctx = self._save(worktree)
        report = PRReport(my_login="me", threads=[ReportThread(
            id="t1", state=ThreadState.NEW, reviewer="kgn", my_login="me",
            file="a.go", line=7, comments=[{"body": "rename this"}],
        )])
        self._run(ctx, report)
        saved = pr.state.load_state(worktree / "target")
        assert saved.fix.fix.items == []
