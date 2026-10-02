"""fix.comments: what a comment fix round persists, lands and reports."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _answering_the_owner, _fake_ctx, _fix_adapter, _git_ran, _no_published_summary,
    _tick_every_fix, _triaged_round, content,
)
import fix.comment_checklist
import fix.comments
import fix.engine
import fix.suite
from pr.comments_state import ThreadState
import git.client
import git.land
import git.topology
from git.land import CommitStatus
import pr.comments
import pr.attribution
import pr.thread_context
import pr.fix_state
import pr.summary_model
import pr.summary_publish
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import (
    CommentItem, PRReport, ReplyOutcome, ReportThread, TrackingResult, TriageResult,
)
import agent.backend


class TestWhatTheRoundPersistsAndReports:
    """The two projections `record` ends on, against the round that made them.

    Everything upstream of these has its own coverage; what these hold is the
    correspondence between one round and the two shapes it leaves behind — the
    state file and the stdout JSON. A field dropped on the way into either is
    invisible end to end: the pass still commits, replies and posts, and the
    loss shows up a round later as a closeout that re-renders work already done
    or a `pr status` missing a reviewer.
    """

    def _round(self, **kw):
        return _triaged_round(**kw)

    def _adapter(self, tmp_path, **kw):
        return _fix_adapter(tmp_path, round_=self._round(**kw))

    @staticmethod
    def _entry(eid="t1", reviewer="kgn"):
        return CommentItem(id=eid, file="f.go", line=3, reviewer=reviewer,
                           summary=f"{eid} summary")

    def _state(self, tmp_path, *, tracking=None, replies=None,
               summary=None, **round_kw):
        adapter = self._adapter(tmp_path, **round_kw)
        content = pr.summary_model.RoundContent(
            by_outcome=adapter.round.by_outcome(tracking or TrackingResult()),
            issue_comments=[], review_body_comments=[],
        )
        cp = pr.attribution.CommitPushResult("abc1234", CommitStatus.PUSHED, "")
        return adapter._state_for(
            content, cp, replies or ReplyOutcome(),
            summary or pr.summary_publish.SummaryOutcome("https://u", owed=False),
            tracking or TrackingResult(),
        )

    def test_the_reviewer_behind_each_entry_is_recorded(self, tmp_path):
        """`ItemOutcome` carries no login, so the map beside it is the only record.

        Dropped, every later surface that names a reviewer — the summary's
        Reviewer column, the reply's addressee — falls back to anonymous.
        """
        state = self._state(tmp_path, dismissed=[self._entry(reviewer="ana")])
        assert state.reviewers == {"t1": "ana"}

    def test_a_held_reply_leaves_the_queue_owed(self, tmp_path):
        """The gate shut on a fixed thread's reply, so `--finish` still owes it."""
        tracking = TrackingResult()
        tracking.add(FixOutcome.FIXED, self._entry())
        state = self._state(tmp_path, tracking=tracking, fixable=[self._entry()])
        assert state.replies_pending is True

    def test_a_delivered_reply_owes_nothing(self, tmp_path, publishing_on):
        """Pairs with the case above: proves the assertion is not vacuous."""
        tracking = TrackingResult()
        tracking.add(FixOutcome.FIXED, self._entry())
        state = self._state(tmp_path, tracking=tracking, fixable=[self._entry()])
        assert state.replies_pending is False

    def test_a_drafted_triage_reply_owes_on_its_own(self, tmp_path):
        """No fixed thread at all, and the queue is still owed.

        The triage replies go out before the pass knows whether anything is
        fixable, so a rule that asked only about the fixed queue reported a
        drained one and `--finish --post` published nothing.
        """
        state = self._state(tmp_path, dismissed=[self._entry()])
        assert state.replies_pending is True

    def test_a_failed_push_does_not_report_the_fixed_reply_delivered(
        self, tmp_path, publishing_on,
    ):
        """`settle_fixed` refuses to send the fixed bucket unless the commit
        reached `PUSHED`, so reporting delivery here would discharge a reply
        queue — this round's, or a stale `True` an earlier round left — that
        was never actually sent.
        """
        adapter = self._adapter(tmp_path, fixable=[self._entry()])
        cp = pr.attribution.CommitPushResult("abc1234", CommitStatus.PUSH_FAILED, "")
        assert adapter._replies_delivered([self._entry()], cp) is False

    def test_a_pushed_commit_reports_the_fixed_reply_delivered(
        self, tmp_path, publishing_on,
    ):
        """Pairs with the case above: proves the assertion is not vacuous."""
        adapter = self._adapter(tmp_path, fixable=[self._entry()])
        cp = pr.attribution.CommitPushResult("abc1234", CommitStatus.PUSHED, "")
        assert adapter._replies_delivered([self._entry()], cp) is True

    def test_a_round_that_ran_names_the_commit_it_made(self, tmp_path):
        """HEAD after the pass, which is the commit the outcomes were measured against."""
        adapter = self._adapter(tmp_path, fixable=[self._entry()])
        with patch.object(git.client, "head_sha", return_value="fff9999") as head:
            assert adapter._snapshot_sha() == "fff9999"
        assert head.called

    def test_a_round_that_did_not_run_asks_no_subprocess(self, tmp_path):
        """Nothing committed, so HEAD has not moved and the context already knows it."""
        adapter = self._adapter(tmp_path, dismissed=[self._entry()])
        with patch.object(git.client, "head_sha") as head:
            assert adapter._snapshot_sha() == adapter.ctx.head_sha
        assert not head.called

    def test_the_result_carries_what_the_agent_was_given(self, tmp_path):
        """The batch statistics are the run's, not the adapter's.

        They are what `pr comments` reports about cost, and an adapter that
        answered from its own state would report the same numbers for every
        round.
        """
        content = pr.summary_model.RoundContent(
            by_outcome={}, issue_comments=[], review_body_comments=[])
        run = fix.engine.FixRun(batches=3, max_turns=17, max_budget=2.5)
        result = fix.comments._result_for(
            content, pr.attribution.CommitPushResult(None, CommitStatus.NO_CHANGES, ""),
            ReplyOutcome(posted=4),
            pr.summary_publish.SummaryOutcome(None, owed=True), run,
        )
        assert (result.batches, result.max_turns, result.max_budget) == (3, 17, 2.5)
        assert result.replies_posted == 4
        assert result.summary_deferred is True

    def test_the_result_projects_every_bucket(self, tmp_path):
        """Five fields off one content, so none can disagree with the table."""
        content = pr.summary_model.RoundContent(
            by_outcome={
                FixOutcome.FIXED: [self._entry("t1")],
                FixOutcome.DEFERRED: [self._entry("t2")],
                FixOutcome.NEEDS_HUMAN: [self._entry("t3")],
                FixOutcome.DECLINED: [self._entry("t4")],
                FixOutcome.DISMISSED: [self._entry("t5")],
                FixOutcome.ALREADY_ADDRESSED: [self._entry("t6")],
            },
            issue_comments=[], review_body_comments=[],
        )
        result = fix.comments._result_for(
            content, pr.attribution.CommitPushResult("abc1234", CommitStatus.PUSHED, ""),
            ReplyOutcome(), pr.summary_publish.SummaryOutcome("https://u", owed=False),
            fix.engine.FixRun(),
        )
        assert [e.id for e in result.fixed] == ["t1"]
        assert [e.id for e in result.deferred] == ["t2"]
        assert [e.id for e in result.dismissed] == ["t5"]
        assert [e.id for e in result.already_addressed] == ["t6"]
        # The one folded field: a thread the agent argued against and one it
        # could not decide both end with a person, and the summary shows them
        # together even though the state file keeps them apart.
        assert [e.id for e in result.needs_human] == ["t3", "t4"]


class TestCommentFixLanding:
    """The pass's boundary onto the landing owner.

    What the commit, the push, the regeneration retry and the recovery each do
    is the owner's, and `land_test.py` holds it against a real repo; asking for
    them is `fix.engine`'s, and `fix_engine_test.py` holds that. What is left to
    this command is the spec it hands over and the record it keeps of the answer.
    """

    @staticmethod
    def _spec(tmp_path, *, fixed=1, deferred=0, changed=frozenset({"a.py"}),
              suite=None):
        outcomes = (
            [ItemOutcome(id=f"f{n}", outcome=FixOutcome.FIXED) for n in range(fixed)]
            + [ItemOutcome(id=f"d{n}", outcome=FixOutcome.DEFERRED)
               for n in range(deferred)]
        )
        adapter = _fix_adapter(tmp_path)
        if suite is not None:
            adapter.suite = suite
        return adapter.landing(
            outcomes, set(changed) if changed is not None else None)

    @staticmethod
    def _recorded(landed, *, short="abc1234"):
        with patch.object(git.client, "run",
                          return_value=_git_ran(0, stdout=f"{short}\n")):
            return pr.attribution.pass_commit(Path("/fake"), landed)

    def test_the_owner_is_asked_for_the_retry_and_the_recovery(self, tmp_path):
        """Both are options, and a pass that did not ask would get neither."""
        spec = self._spec(tmp_path)

        assert spec.recover is True
        assert spec.regen

    def test_the_counts_ride_in_the_commit_message(self, tmp_path):
        spec = self._spec(tmp_path, fixed=2, deferred=3)

        subject, _, body = spec.message.partition("\n\n")
        assert subject == "fix: address review comments"
        assert body == "2 fixed, 3 deferred"

    def test_a_red_suite_qualifies_the_comments_tally(self, tmp_path):
        """A reviewer reads this body. It must not claim fixes over a red tree.

        The comments pass kept its own copy of the tally and rendered it bare
        while the suite ran, so the verdict reached the terminal and the
        outcomes but never the commit a reviewer sees.
        """
        spec = self._spec(tmp_path, fixed=2, deferred=3, suite=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.RED, command="checks",
            output_tail="E   boom"))

        assert "2 fixed, 3 deferred — but the repo's checks are RED" in spec.message
        assert "E   boom" in spec.message

    def test_an_undeclared_command_qualifies_the_comments_tally(self, tmp_path):
        spec = self._spec(tmp_path, fixed=2, deferred=3, suite=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.NOT_DECLARED))

        assert "unverified: no fix.verify_command declared" in spec.message

    def test_a_pass_that_fixed_nothing_says_only_what_it_did(self, tmp_path):
        spec = self._spec(tmp_path, fixed=0, deferred=4)

        assert spec.message == "fix: address review comments"

    def test_the_commit_is_scoped_to_what_the_agent_changed(self, tmp_path):
        """Not the whole tree: this branch is under review by somebody else."""
        spec = self._spec(tmp_path, changed={"src/a.py", "src/a_test.py"})

        assert spec.paths == {"src/a.py", "src/a_test.py"}

    def test_a_pass_that_cannot_say_what_it_changed_commits_nothing(self, tmp_path):
        """An empty scope commits nothing; None would commit the whole tree."""
        assert self._spec(tmp_path, changed=None).paths == set()

    def test_the_sha_is_recorded_at_the_width_the_state_file_uses(self):
        """A commit recorded twice at two widths reads as two commits."""
        landed = git.land.LandResult(CommitStatus.PUSHED, sha="abc1234def56789")
        result = self._recorded(landed)

        assert result.sha == "abc1234"
        assert result.status == "pushed"

    def test_a_landing_with_no_commit_records_no_sha(self):
        result = self._recorded(git.land.LandResult(CommitStatus.NO_CHANGES))

        assert result.sha is None
        assert result.status == "no_changes"

    def test_what_went_wrong_is_carried_through(self):
        landed = git.land.LandResult(
            CommitStatus.PUSH_FAILED, sha="abc1234def", error="rejected",
        )
        result = self._recorded(landed)

        assert result.status == "push_failed"
        assert result.sha == "abc1234"
        assert "rejected" in result.error


class TestFixPassHoldsWhenContested:
    """The whole point of the hold, asserted through `_run_comment_fix` itself.

    `TestHoldWhileContested` and `TestCommitAndPush` each cover one half. Neither
    catches a reorder that puts the commit before the hold, which is precisely
    how the bug works — so this drives the real entry point with one contested
    thread and one fixable one, and asserts nothing was pushed.
    """

    @staticmethod
    def _item(id, verification, **kw):
        return CommentItem(
            id=id, file="f.go", line=10, reviewer="kgn", summary=f"{id} summary",
            classification="actionable_suggestion", verification=verification,
            complexity="low", state=ThreadState.NEW, **kw,
        )

    def _run(self, tmp_path, *, contested, publishing_on_):
        threads = [self._item("t1", "valid")]
        if contested:
            threads.append(self._item("t2", "needs_discussion"))

        # Real comment IDs, so the reply path can actually fire — without them
        # `_post_fix_replies` finds nothing to reply to and returns 0 whether or
        # not the gate is shut, which would make the reply assertion vacuous.
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[
                ReportThread(id=t.id, file=t.file, line=t.line,
                             comments=[{"databaseId": 100 + n}])
                for n, t in enumerate(threads)
            ],
        )
        ctx = _fake_ctx(tmp_path)
        pushes = []
        commits = []

        def mock_run(*cmd, **kwargs):
            if "push" in cmd:
                pushes.append(cmd)
            if "commit" in cmd:
                commits.append(cmd)
            return _git_ran(0, stdout="abc1234\n")

        with patch.object(agent.backend, "invoke_fix",
                          side_effect=_tick_every_fix(tmp_path)), \
             patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist"), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(mock_run)), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.post_issue_comment", return_value="u"), \
             patch("pr.comments.resolve_thread", return_value=True):
            result = fix.comments.run_pass(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )
        return SimpleNamespace(result=result, pushes=pushes, commits=commits)

    def test_a_contested_thread_stops_the_push(self, tmp_path, publishing_on):
        run = self._run(tmp_path, contested=True, publishing_on_=True)
        assert run.result.commit_status == CommitStatus.PUSH_HELD
        assert run.pushes == []

    def test_the_commit_is_still_made(self, tmp_path, publishing_on):
        """Holding must not cost the work — only its publication.

        A local commit asserts nothing to a reviewer, since
        only the push makes it visible, and the push is what the hold stops.
        """
        run = self._run(tmp_path, contested=True, publishing_on_=True)
        assert run.result.commit_sha == "abc1234"
        assert run.commits

    def test_the_fixes_are_still_applied(self, tmp_path, publishing_on):
        """Holding must not cost the work — only the acts that assert it."""
        run = self._run(tmp_path, contested=True, publishing_on_=True)
        assert [t.id for t in run.result.fixed] == ["t1"]

    def test_no_fixed_replies_go_out_while_held(self, tmp_path, publishing_on):
        run = self._run(tmp_path, contested=True, publishing_on_=True)
        assert run.result.replies_posted == 0
        assert run.result.summary_url is None
        assert run.result.summary_deferred is True

    def test_an_uncontested_pass_still_pushes(self, tmp_path, publishing_on):
        """The gate must not have closed on the common case."""
        run = self._run(tmp_path, contested=False, publishing_on_=True)
        assert run.result.commit_status == CommitStatus.PUSHED
        assert run.pushes
        assert run.commits

    def test_an_uncontested_pass_still_replies(self, tmp_path, publishing_on):
        """Pairs with the held case: proves that assertion is not vacuous."""
        run = self._run(tmp_path, contested=False, publishing_on_=True)
        assert run.result.replies_posted == 1


class TestAnAlreadyAddressedDraftRoundOwesItsSummary:
    """The reported drop, driven through `_run_comment_fix` itself.

    Every thread settled before the pass reached it, so there is nothing to fix
    and the round takes the early return, which had a narrower rule for what it
    owed than the pass that commits. The draft printed a full table and recorded
    that it owed nothing, so `--finish --post` returned at its
    `summary_deferred` guard and the published comment kept the previous
    round's rows. Asserted end to end because that is where it is invisible:
    the closeout exits 0 and `pr status` reports a clean PR.
    """

    def _run(self, tmp_path):
        threads = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="kgn", summary="t1 summary",
            classification="actionable_suggestion",
            verification="already_addressed",
            complexity="low", state=ThreadState.NEW,
        )]
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[ReportThread(id="t1", file="f.go", line=10,
                                  comments=[{"databaseId": 100}])],
        )
        ctx = _fake_ctx(tmp_path)
        with patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist"), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.resolve_thread", return_value=True):
            return fix.comments.run_pass(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )

    def test_the_draft_leaves_its_table_owed(self, tmp_path):
        result = self._run(tmp_path)
        assert [t.id for t in result.already_addressed] == ["t1"]
        # Neither of the two buckets the old rule named, so the round it
        # described looked like a round with nothing to say.
        assert not result.fixed
        assert not result.dismissed
        assert result.summary_url is None
        assert result.summary_deferred is True

    def test_a_published_round_owes_nothing(self, tmp_path, publishing_on):
        """The other half: once the table is out, it is not owed again."""
        with patch("pr.comments.post_issue_comment", return_value="https://u"):
            result = self._run(tmp_path)
        assert result.summary_url == "https://u"
        assert result.summary_deferred is False


class TestARoundWhoseOnlyContentIsAnUnreadComment:
    """No thread settled either way, and still a table to publish.

    An unseen issue or review-body comment is a row the summary renders, so a
    round that settled no thread at all can still owe one. The gate on whether
    to attempt the post named the four thread buckets instead of asking
    `RoundContent.has_content`, so this round was recorded as owing a summary it
    never tried to publish — recoverable on the next `--finish`, a cycle late.
    """

    def _run(self, tmp_path):
        report = PRReport(
            repo="owner/repo", pr_number=1,
            issue_comments=[{"id": "c1", "author": "kgn", "body": "one thought",
                             "seen": False}],
        )
        ctx = _fake_ctx(tmp_path)
        with patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist"), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))):
            return fix.comments.run_pass(TriageResult(), report, tmp_path, ctx)

    def test_the_round_publishes_its_table(self, tmp_path, publishing_on):
        with patch("pr.comments.post_issue_comment", return_value="https://u"):
            result = self._run(tmp_path)
        assert result.summary_url == "https://u"
        assert result.summary_deferred is False

    def test_a_draft_still_owes_it(self, tmp_path):
        result = self._run(tmp_path)
        assert result.summary_url is None
        assert result.summary_deferred is True


class TestTheRoundWithNothingToFixTakesTheSameTail:
    """One `record`, whether or not the agent ran.

    `fix.engine.run` declines a pass with no items and never calls `record`,
    so the round with nothing fixable used to run a second copy of the tail
    written out in the entry function. The two drifted in three ways before
    anyone noticed — a dropped `has_comment_items`, a summary the fix path
    posted and this one skipped, and a result built by mutation rather than
    projected from the round.

    What is asserted here is the equivalence the collapse rests on: with no
    outcomes, the shared tail produces what the hand-written one did. The state
    write is the place to check it — the return value is the same object either
    way, so a tail that quietly persisted less would not show there.
    """

    def _persisted(self, tmp_path, *, threads=(), comment_items=()):
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[ReportThread(id="t1", file="f.go", line=10,
                                  comments=[{"databaseId": 100}])],
        )
        ctx = _fake_ctx(tmp_path)
        with patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist") as persist, \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.post_issue_comment", return_value="https://u"), \
             patch("pr.comments.resolve_thread", return_value=True):
            result = fix.comments.run_pass(
                TriageResult(threads=list(threads),
                             comment_items=list(comment_items)),
                report, tmp_path, ctx,
            )
        return persist.call_args[0][0], result

    @staticmethod
    def _dismissed(eid="t1"):
        return CommentItem(
            id=eid, file="f.go", line=10, reviewer="kgn", summary="a point",
            classification="actionable_suggestion", verification="invalid",
            complexity="low", state=ThreadState.NEW,
        )

    def test_the_record_carries_the_rounds_own_outcome(self, tmp_path,
                                                       publishing_on):
        """The buckets triage filled reach the state file with no agent involved."""
        persisted, _ = self._persisted(tmp_path, threads=[self._dismissed()])
        assert [o.outcome for o in persisted.fix.items] == [FixOutcome.DISMISSED]
        assert persisted.fix.commit_sha == ""
        assert persisted.fix.commit_status == CommitStatus.NO_CHANGES

    def test_the_identity_sha_stands_in_for_an_unmoved_head(self, tmp_path,
                                                            publishing_on):
        """No agent ran, so HEAD did not move and the context already knows it."""
        persisted, _ = self._persisted(tmp_path, threads=[self._dismissed()])
        assert persisted.fix.head_sha == "aaa1111"

    def test_no_description_draft_is_delivered(self, tmp_path, publishing_on):
        """The draft on disk is an earlier round's, and `--finish` owns it.

        Going through the shared tail put this round in reach of a delivery it
        never used to make: `deliver_pr_body` sends whatever file is there, and
        a round that ran no agent wrote none of it. The draft has to exist for
        the assertion to mean anything — with no file the delivery declines on
        its own and the gate under test is never reached.
        """
        draft = pr.comments.pr_body_draft(pr.comments.artifacts_dir(tmp_path))
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("a description an earlier round drafted\n")
        with patch("pr.comments.update_pr_body", return_value=True) as update:
            persisted, _ = self._persisted(
                tmp_path, threads=[self._dismissed()])
        assert persisted.pr_body_pending is False
        assert not update.called
        assert draft.exists(), "the draft stays for --finish to deliver"

    def test_the_result_is_projected_from_the_round(self, tmp_path,
                                                    publishing_on):
        """Not a pre-built object mutated on the way out."""
        _, result = self._persisted(tmp_path, threads=[self._dismissed()])
        assert [e.id for e in result.dismissed] == ["t1"]
        assert result.fixed == []
        assert result.batches == 0
        assert result.commit_status == CommitStatus.NO_CHANGES

    def test_the_replies_triage_sent_are_counted(self, tmp_path, publishing_on):
        """The round's own replies reach the tail that did not send them."""
        persisted, result = self._persisted(
            tmp_path, threads=[self._dismissed()])
        assert result.replies_posted == 1
        assert persisted.replies_posted == 1


class TestARoundWithNoFixablesRecordsItsCommentItems:
    """What the round persists about comment items survives into `--finish`.

    `has_comment_items` decides whether the render appends the raw comment
    sections under the table — an entry decomposed out of a top-level comment
    is already a row, so repeating its body below the table reports it twice.
    The round with nothing to fix computed the value and then left the field off
    its `FixSummary`, so it persisted false and the deferred render on
    `--finish` duplicated every comment-item row.
    """

    def _persisted(self, tmp_path, *, comment_items):
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[ReportThread(id="t1", file="f.go", line=10,
                                  comments=[{"databaseId": 100}])],
        )
        ctx = _fake_ctx(tmp_path)
        with patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist") as persist, \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.post_issue_comment", return_value="https://u"), \
             patch("pr.comments.resolve_thread", return_value=True):
            fix.comments.run_pass(
                TriageResult(threads=[], comment_items=comment_items),
                report, tmp_path, ctx,
            )
        return persist.call_args[0][0]

    @staticmethod
    def _item(verification):
        return CommentItem(
            id="ic-1", file="f.go", line=10, reviewer="kgn", summary="a thought",
            classification="actionable_suggestion", verification=verification,
            complexity="low", state=ThreadState.NEW,
        )

    def test_a_round_carrying_comment_items_says_so(self, tmp_path,
                                                    publishing_on):
        persisted = self._persisted(
            tmp_path, comment_items=[self._item("invalid")])
        assert persisted.has_comment_items is True

    def test_a_round_without_them_does_not(self, tmp_path, publishing_on):
        """Pairs with the case above: proves the assertion is not vacuous."""
        persisted = self._persisted(tmp_path, comment_items=[])
        assert persisted.has_comment_items is False


class TestARoundWithUnaccountedThreadsStillPublishes:
    """A thread this pass never reached does not silence the rows it did settle.

    The round with nothing to fix used to skip the post outright when any open
    thread went undisposed. The summary is one marker comment edited in place,
    so skipping it is not silence: the *previous* round's table stays as the
    newest thing on the PR, and the dismissals this round made are invisible
    until a `--finish` that may never run. The pass that commits has always
    posted here; the two paths asked the same question and answered it
    differently.

    `summary_still_owed` returns True either way, so nothing is lost by
    posting: the closeout re-renders whatever the interim table missed.
    """

    def _run(self, tmp_path):
        """One thread dismissed, one open thread the pass never sees.

        `t2` is on the report and absent from triage, which is what makes
        `has_unaccounted` true — the condition the old guard turned on.
        """
        threads = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="kgn", summary="t1 summary",
            classification="actionable_suggestion", verification="invalid",
            complexity="low", state=ThreadState.NEW,
        )]
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[
                ReportThread(id="t1", file="f.go", line=10,
                             comments=[{"databaseId": 100}]),
                ReportThread(id="t2", file="g.go", line=20,
                             comments=[{"databaseId": 200}]),
            ],
        )
        ctx = _fake_ctx(tmp_path)
        with patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist"), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.resolve_thread", return_value=True):
            return fix.comments.run_pass(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )

    def test_the_interim_table_goes_out(self, tmp_path, publishing_on):
        with patch("pr.comments.post_issue_comment", return_value="https://u") as post:
            result = self._run(tmp_path)
        assert result.summary_url == "https://u"
        assert post.called

    def test_the_dismissal_is_on_the_table_that_went_out(self, tmp_path,
                                                         publishing_on):
        """Not a vacuous post: the round's own row is in the published body."""
        with patch("pr.comments.post_issue_comment", return_value="https://u") as post:
            self._run(tmp_path)
        body = post.call_args[0][2]
        assert "t1 summary" in body

    def test_a_draft_still_owes_it(self, tmp_path):
        """The gate declining the write leaves the round owed, as it always did."""
        result = self._run(tmp_path)
        assert result.summary_url is None
        assert result.summary_deferred is True

    def test_a_round_with_nothing_to_say_posts_nothing(self, tmp_path,
                                                       publishing_on):
        """The deleted `has_content` guard was `post_fix_summary`'s own question.

        Removing it must not have started publishing empty tables — the callee
        asks the same thing and returns None.
        """
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[ReportThread(id="t2", file="g.go", line=20,
                                  comments=[{"databaseId": 200}])],
        )
        ctx = _fake_ctx(tmp_path)
        with patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist"), \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr.comments.post_issue_comment",
                   return_value="https://u") as post:
            result = fix.comments.run_pass(TriageResult(), report, tmp_path, ctx)
        assert result.summary_url is None
        assert not post.called
