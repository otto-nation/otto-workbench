"""review.closeout: pushing a held commit and posting the replies it owes."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import (  # noqa: E402
    _PASS_SHA, _ROUND_1_SHA, _answering_the_owner, _fix, _git_ran, _make_state,
    _no_published_summary,
)
from pr.comments_state import ThreadState
import git.client
import git.push
import pr.attribution
import pr.summary_row
from pr.fix import FixOutcome, ItemOutcome, SettledBy
from pr.thread_models import ReportThread
import review.closeout


_ROUND_2_SHA = "2222222"


# What the remote answers with when it did not keep the push: some other commit
# than the one the pass just made. Any SHA but the pushed one would do — this is
# named so the assertion reads as "the remote moved on without it".
_LOST_SHA = "0000000"


def _gated(*_args, **_kwargs):
    """Stand in for a GitHub write, reporting what the real one would.

    `pr.comments.post_thread_reply` and `resolve_thread` both refuse and return
    False when the gate is shut. A mock hardwired to True would report a drafted
    run as having published, which is the exact confusion these tests exist to
    catch.
    """
    import core.publishing
    return core.publishing.enabled()


class TestPushHeldCommit:
    """--finish --post is the human saying the held work may land."""

    @staticmethod
    def _state(status="push_held", sha="abc1234"):
        return _make_state(_fix(commit_sha=sha, commit_status=status))

    def test_pushes_and_marks_it_pushed(self, publishing_on):
        state = self._state()
        with patch.object(git.push, "holds", return_value=False), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))) as run:
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "pushed"
        assert ("push",) in [call.args for call in run.call_args_list]

    def test_a_push_the_remote_never_took_is_push_lost_for_a_held_commit(
        self, publishing_on,
    ):
        """The held commit was released, retried once, and still did not arrive."""
        def clean_tree(*cmd, **kwargs):
            # The porcelain read has to come back empty. A blanket stub answers
            # it with a SHA, which reads as a dirty tree — and the owner refuses
            # to retry into one, so the retry this test is about never runs.
            if cmd[:2] == ("status", "--porcelain"):
                return _git_ran(0)
            return _git_ran(0, stdout="abc1234\n")

        state = self._state()
        with patch.object(git.push, "holds", return_value=False), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              clean_tree, _LOST_SHA)) as run:
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_lost"
        pushes = [c.args for c in run.call_args_list if c.args[:1] == ("push",)]
        assert pushes == [("push",), ("push", "--no-verify")]

    def test_a_draft_finish_still_holds_it(self):
        """--finish without --post is not the human saying go."""
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        state = self._state()
        with patch.object(git.push, "holds", return_value=False), \
             patch.object(git.client, "run", boom):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_held"

    def test_a_hold_placed_this_run_outranks_post(self, publishing_on):
        """--fix --finish --post in one run: the discussion is still open."""
        import core.publishing
        core.publishing.hold("discussion open")

        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        state = self._state()
        with patch.object(git.push, "holds", return_value=False), \
             patch.object(git.client, "run", boom):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_held"

    def test_a_failed_push_is_recorded_as_such(self, publishing_on):
        state = self._state()
        with patch.object(git.push, "holds", return_value=False), \
             patch.object(git.client, "run",
                          return_value=_git_ran(1, stderr="rejected\n")):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_failed"

    def test_a_failed_push_reaches_the_trail(self, publishing_on):
        """Same as the two sibling push paths — a failure here is not silent."""
        trail = MagicMock()
        trail.failure.return_value = Path("/trail/push.log")
        state = self._state()
        with patch.object(git.push, "holds", return_value=False), \
             patch.object(git.client, "run",
                          return_value=_git_ran(1, stderr="rejected\n")):
            review.closeout.push_held_commit(state, Path("/fake"), trail)
        trail.failure.assert_called_once()
        assert trail.failure.call_args.kwargs["output"] == "rejected\n"

    def test_a_commit_already_on_the_remote_is_just_marked(self, publishing_on):
        """Someone pushed by hand between the two runs."""
        def boom(*a, **kw):
            raise AssertionError(f"pushed a commit the remote already had: {a}")

        state = self._state()
        with patch.object(git.push, "holds", return_value=True), \
             patch.object(git.client, "run", boom):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "pushed"

    def test_noop_when_the_commit_already_went_out(self, publishing_on):
        def boom(*a, **kw):
            raise AssertionError(f"pushed an already-pushed commit: {a}")

        state = self._state(status="pushed")
        with patch.object(git.client, "run", boom):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "pushed"

    def test_noop_when_the_pass_made_no_commit(self, publishing_on):
        def boom(*a, **kw):
            raise AssertionError(f"pushed with no commit to push: {a}")

        state = self._state(status="no_changes", sha="")
        with patch.object(git.client, "run", boom):
            review.closeout.push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "no_changes"


class TestPendingFixReplies:
    """--finish is the second chance for fix replies the fix pass didn't send."""

    # id, summary, file, line, root comment databaseId
    _SEEDS = [
        ("t1", "fix it", "x.py", 1, 100),
        ("t2", "and this", "y.py", 2, 200),
    ]

    def _queue(self, count=1, **fix_kw):
        """A queue of `count` fixed threads: the FixSummary and its threads_by_id.

        Which thread is which never matters here — every test in this class turns
        on the queue's state (pushed, drafted, already drained), not its contents.
        So the seeds stay fixed and each test names only the fields it turns on.
        """
        seeds = self._SEEDS[:count]
        fix_kw.setdefault("commit_sha", "abc1234")
        fix = _fix(
            items=[
                ItemOutcome(id=tid, summary=summary, file=path, line=line,
                              outcome=FixOutcome.FIXED)
                for tid, summary, path, line, _ in seeds
            ],
            **fix_kw,
        )
        threads_by_id = {
            tid: ReportThread(id=tid, is_resolved=False, comments=[{"databaseId": db}])
            for tid, _, _, _, db in seeds
        }
        return fix, threads_by_id

    def test_posts_fix_replies_and_resolves_when_push_confirmed(self, publishing_on):
        fix, threads_by_id = self._queue(2, commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True) as mock_resolve:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 2
        assert mock_resolve.call_count == 2
        assert fix.fix.commit_status == "pushed"

    def test_skips_when_still_unpushed(self):
        fix, _ = self._queue(commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=False), \
             patch("pr.comments.post_thread_reply") as mock_reply:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()
        assert fix.fix.commit_status == "push_failed"

    def test_noop_when_not_push_failed(self):
        fix = _fix(commit_status="pushed", summary_deferred=True)
        state = _make_state(fix)
        with patch("pr.comments.post_thread_reply") as mock_reply:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()

    def test_a_thread_settled_on_the_forge_is_neither_replied_to_nor_resolved(
        self, publishing_on,
    ):
        """We know nothing about it beyond that somebody else closed it.

        A reply would claim a verdict the pass never reached, and resolving a
        thread the reviewer may have deferred by hand would close their own
        open question on their behalf.
        """
        fix, threads_by_id = self._queue(commit_status="pushed", replies_pending=True)
        fix.fix.items[0].outcome = FixOutcome.SETTLED_ELSEWHERE
        fix.fix.items[0].settled_by = SettledBy.RECONCILIATION
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True) as mock_resolve:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_reply.assert_not_called()
        mock_resolve.assert_not_called()

    def test_draft_run_keeps_the_queue_for_a_later_post(self):
        fix, threads_by_id = self._queue(commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.fix.commit_status == "push_failed"

    def test_drains_the_queue_a_drafted_fix_pass_left_behind(self, publishing_on):
        """A drafted --fix commits and sends nothing; --post must catch up.

        The `pushed` status here is a run whose push landed before the gate
        applied to it — the queue survives on `replies_pending` alone.
        """
        fix, threads_by_id = self._queue(commit_status="pushed", replies_pending=True)
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 1
        assert fix.replies_pending is False

    def test_counts_the_replies_it_drained(self, publishing_on):
        """The drafted pass recorded 0 sent; the run that sends them owns the count."""
        fix, threads_by_id = self._queue(
            2, commit_status="pushed", replies_pending=True, replies_posted=0,
        )
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 2

    def test_draft_drain_counts_nothing(self):
        """A draft sends nothing, so the counter must not move on its way past."""
        fix, threads_by_id = self._queue(
            commit_status="pushed", replies_pending=True, replies_posted=0,
        )
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 0

    def test_noop_once_the_replies_have_gone_out(self, publishing_on):
        fix, _ = self._queue(commit_status="pushed", replies_pending=False)
        state = _make_state(fix)
        with patch("pr.comments.post_thread_reply") as mock_reply:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()

    def test_a_hand_landed_commit_pins_the_tree_without_being_credited(
        self, publishing_on,
    ):
        """A hook-rejected commit records no SHA, and the moved HEAD is a tree.

        The pass edits the files, a pre-commit hook rejects the commit, and the
        operator commits and pushes the same work themselves. The reply reads
        that HEAD for the permalink, which is a claim about where the file now
        stands — but not for the citation, which is a claim about which commit
        carried this thread's fix. This queue spans rounds, so some of its rows
        landed long before the commit that just moved HEAD.

        Resolving the difference is per row and needs a line history; the fake
        worktree here has none, so nothing is cited. `TestOneHandLandedCommit
        IsStillAskedOfEachRow` is the same shape over a real one.
        """
        fix, threads_by_id = self._queue(
            commit_status="commit_failed", commit_sha="", replies_pending=True,
            head_sha="abc1234",
        )
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="def5678"), \
             patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        body = mock_reply.call_args[0][3]
        assert "Fixed in" not in body
        assert "owner/repo/blob/def5678/x.py" in body
        assert fix.replies_pending is False

    def test_falls_back_to_the_linkless_shape_when_no_commit_can_be_named(
        self, publishing_on,
    ):
        """HEAD never moved, so there is no commit to cite and none is invented."""
        fix, threads_by_id = self._queue(
            commit_status="commit_failed", commit_sha="", replies_pending=True,
            head_sha="abc1234",
        )
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="abc1234"), \
             patch.object(git.push, "holds", return_value=True), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        body = mock_reply.call_args[0][3]
        assert "Fixed in" not in body
        assert "/commit/)" not in body
        assert "/blob//" not in body
        assert fix.replies_pending is False


class TestTriageOnlyPassQueue:
    """A pass with nothing to fix dropped every reply it drafted.

    The already-addressed and dismissed replies are sent during triage, before
    the pass knows whether anything is fixable, so a drafted run rendered them
    to stderr and kept no record. When the same pass then found nothing fixable
    it took the early return, which recorded neither a commit nor a pending
    queue — and `--finish --post` exited 0 having published nothing.

    Every test here therefore carries no fixed entry and no commit SHA, which
    is precisely the shape the old `if not fix.fix.commit_sha: return` swallowed.
    """

    _ADDRESSED = f"t-{FixOutcome.ALREADY_ADDRESSED}"

    def _queue(self, *outcomes, **fix_kw):
        fix_kw.setdefault("replies_pending", True)
        fix = _fix(
            items=[
                ItemOutcome(id=f"t-{o}", summary=f"the {o} one",
                            file="x.py", line=1, outcome=o,
                            reason=f"because the {o} premise says so")
                for o in outcomes
            ],
            commit_status="no_changes",
            **fix_kw,
        )
        threads_by_id = {
            f"t-{o}": ReportThread(id=f"t-{o}", is_resolved=False,
                                   comments=[{"databaseId": 100 + n}])
            for n, o in enumerate(outcomes)
        }
        return fix, threads_by_id

    def test_drains_replies_a_pass_that_committed_nothing_left_behind(
        self, publishing_on,
    ):
        fix, threads_by_id = self._queue(
            FixOutcome.ALREADY_ADDRESSED, FixOutcome.DISMISSED,
        )
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="deadbee"), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 2
        assert fix.replies_posted == 2
        assert fix.replies_pending is False

    def test_only_the_already_addressed_thread_is_resolved(self, publishing_on):
        """A dismissal is the reply most likely to be argued with — leave it open."""
        fix, threads_by_id = self._queue(
            FixOutcome.ALREADY_ADDRESSED, FixOutcome.DISMISSED,
        )
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="deadbee"), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.resolve_thread", return_value=True) as mock_resolve:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert [c.args[0] for c in mock_resolve.call_args_list] == [self._ADDRESSED]

    def test_a_drained_dismissal_still_carries_its_reasoning(self, publishing_on):
        """`to_outcome` folds `reasoning` into `reason`; the drain must fold it back.

        Without that, the reply degrades to the bare "reviewed and determined to
        be inapplicable" fallback — telling a reviewer their premise fails and
        giving them nothing to argue with.
        """
        fix, threads_by_id = self._queue(FixOutcome.DISMISSED)
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="deadbee"), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert "because the dismissed premise says so" in mock_reply.call_args.args[3]

    def test_a_commitless_queue_does_not_wait_on_a_push(self, publishing_on):
        """These replies cite HEAD, not a fix commit, so there is nothing to wait for."""
        fix, threads_by_id = self._queue(FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(git.push, "holds", return_value=False) as mock_pushed, \
             patch.object(git.client, "head_sha", return_value="deadbee"), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_pushed.assert_not_called()
        assert mock_reply.call_count == 1

    def test_no_changes_is_not_rewritten_as_pushed(self, publishing_on):
        """The pass committed nothing; saying it pushed would invent a commit."""
        fix, threads_by_id = self._queue(FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="deadbee"), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.fix.commit_status == "no_changes"

    def test_a_draft_drain_keeps_the_queue(self):
        """post_thread_reply is left real here — the draft gate lives inside it."""
        fix, threads_by_id = self._queue(FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(git.client, "head_sha", return_value="deadbee"), \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 0
        assert fix.replies_pending is True

    def test_a_settled_queue_is_left_alone(self, publishing_on):
        fix, threads_by_id = self._queue(
            FixOutcome.ALREADY_ADDRESSED, replies_pending=False,
        )
        state = _make_state(fix)
        with patch("pr.comments.post_thread_reply") as mock_reply:
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_reply.assert_not_called()


class TestResolutionsReachThePersistedTally:
    """The closeout resolves threads after the counts were written.

    `pr status` reads `comments.by_state`, and that snapshot is saved at fetch
    time — before the fix pass or the drain runs. Without the delta a fully
    closed-out PR keeps reporting the threads it just resolved as open. This
    class covers the drain; `TestFixPassResolutionsReachTheTally` covers the
    fix pass, which resolves through the same helper on the commoner path.
    """

    def _drain(self, by_state, *, prior=ThreadState.NEW, count=2):
        ids = [f"t{n}" for n in range(1, count + 1)]
        fix = _fix(
            items=[
                ItemOutcome(id=tid, summary="s", file="x.py", line=1,
                              outcome=FixOutcome.FIXED)
                for tid in ids
            ],
            commit_sha="abc1234", commit_status="pushed", replies_pending=True,
        )
        threads_by_id = {
            tid: ReportThread(id=tid, state=prior, is_resolved=False,
                              comments=[{"databaseId": 100 + n}])
            for n, tid in enumerate(ids)
        }
        state = _make_state(fix)
        state.comments.by_state = dict(by_state)
        with patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", side_effect=_gated), \
             patch("pr.comments.resolve_thread", side_effect=_gated):
            review.closeout.post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        return state.comments

    def test_resolved_threads_leave_their_prior_bucket(self, publishing_on):
        comments = self._drain({"new": 3, "resolved": 1})
        assert comments.by_state[ThreadState.NEW] == 1
        assert comments.by_state[ThreadState.RESOLVED] == 3

    def test_the_first_resolution_opens_the_bucket(self, publishing_on):
        """A PR with nothing resolved yet has no `resolved` key to increment."""
        comments = self._drain({"addressed": 2}, prior=ThreadState.ADDRESSED)
        assert comments.by_state[ThreadState.RESOLVED] == 2
        assert comments.by_state[ThreadState.ADDRESSED] == 0

    def test_a_draft_moves_nothing(self):
        """Nothing was resolved on GitHub, so the tally must not claim it was."""
        comments = self._drain({"new": 3, "resolved": 1})
        assert comments.by_state == {"new": 3, "resolved": 1}

    def test_the_tally_is_stamped_only_when_it_moves(self, publishing_on):
        assert self._drain({"new": 2}).updated_at
        assert not self._drain({"new": 2}, count=0).updated_at

    def test_counts_never_go_negative(self, publishing_on):
        """A bucket the snapshot under-counts must not wrap past zero."""
        comments = self._drain({"new": 1})
        assert comments.by_state[ThreadState.NEW] == 0
        assert comments.by_state[ThreadState.RESOLVED] == 2

    def test_an_undercounted_bucket_says_so(self, publishing_on, capsys):
        """The clamp is a floor, not a reason to stay quiet about the mismatch."""
        self._drain({"new": 1})
        assert "no new left to move" in capsys.readouterr().err


class TestReplyAttributionAcrossRounds:
    """The reply cited the running pass's commit, whatever fixed the thread.

    A single-round fixture cannot tell per-entry attribution from pass-level —
    they agree — which is exactly why this went unnoticed. So every test here
    drains a queue whose entries were fixed by different commits than the pass
    that is now sending their replies.
    """

    def _drain(self, *outcomes, pass_sha=_PASS_SHA):
        """Send the deferred replies for `outcomes`; return body by thread id."""
        fix = _fix(
            items=list(outcomes), commit_sha=pass_sha,
            commit_status="pushed", replies_pending=True,
        )
        threads_by_id = {
            o.id: ReportThread(id=o.id, is_resolved=False,
                               comments=[{"databaseId": 100 + n}])
            for n, o in enumerate(outcomes)
        }
        with patch.object(git.push, "holds", return_value=True), \
             patch("pr.comments.post_thread_reply", return_value=True) as reply, \
             patch("pr.comments.resolve_thread", return_value=True):
            review.closeout.post_pending_fix_replies(_make_state(fix), "owner/repo", 1, threads_by_id)
        bodies = [call[0][3] for call in reply.call_args_list]
        return dict(zip([o.id for o in outcomes], bodies))

    @staticmethod
    def _fixed(tid, sha, path):
        return ItemOutcome(id=tid, summary=f"{tid} summary", file=path, line=1,
                             outcome=FixOutcome.FIXED, commit_sha=sha)

    def test_each_reply_cites_the_commit_that_fixed_it(self, publishing_on):
        bodies = self._drain(
            self._fixed("t1", _ROUND_1_SHA, "a.py"),
            self._fixed("t2", _ROUND_2_SHA, "b.py"),
        )
        assert _ROUND_1_SHA in bodies["t1"]
        assert _ROUND_2_SHA not in bodies["t1"]
        assert _ROUND_2_SHA in bodies["t2"]
        assert _PASS_SHA not in bodies["t1"] + bodies["t2"]

    def test_each_permalink_is_pinned_to_that_commit(self, publishing_on):
        """The blob link is evidence — pinned to the wrong SHA it shows the wrong code."""
        bodies = self._drain(
            self._fixed("t1", _ROUND_1_SHA, "a.py"),
            self._fixed("t2", _ROUND_2_SHA, "b.py"),
        )
        assert f"/blob/{_ROUND_1_SHA}/a.py" in bodies["t1"]
        assert f"/blob/{_ROUND_2_SHA}/b.py" in bodies["t2"]

    def test_an_entry_with_no_commit_of_its_own_borrows_none(self, publishing_on):
        """An entry the pass never recorded must not be credited to the pass.

        The pass committed, and this entry is not in that commit — it was
        replayed from a round that recorded nothing. Citing the pass's SHA
        sends the reviewer to a commit their thread is not in.
        """
        outcome = ItemOutcome(id="t1", summary="t1 summary", file="a.py", line=1,
                                outcome=FixOutcome.FIXED)
        bodies = self._drain(outcome)
        assert _PASS_SHA not in bodies["t1"]
        assert "t1 summary" in bodies["t1"]

    def test_the_summary_row_and_the_reply_agree(self, publishing_on):
        """One precedence rule, two renderers — they must not disagree."""
        outcome = self._fixed("t1", _ROUND_1_SHA, "a.py")
        bodies = self._drain(outcome)
        cell = pr.summary_row.fixed_status_for(outcome, pr.attribution.CommitPushResult(_PASS_SHA, "pushed", ""),
                                    "owner/repo")
        assert _ROUND_1_SHA in cell
        assert _ROUND_1_SHA in bodies["t1"]
