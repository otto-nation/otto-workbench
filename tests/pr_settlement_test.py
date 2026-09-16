"""Tests for `pr.settlement` — the evidence rules nothing exercised directly.

`reconcile_fix_snapshot` and `run_settle` are covered heavily through
`test_review_threads.py`, which drives both ends to end. What had no test at all
is the grading underneath them: which evidence supports FIXED and which supports
only SETTLED_ELSEWHERE, how a location shared by two settled threads is broken,
and the three ways a decomposed comment item can be settled without a thread of
its own.

That grading is the module's whole reason for existing, and it decides whether
the tool publishes "someone fixed this" about code nobody fixed. Covered here
directly so a change to it fails on the rule rather than on a summary six layers
above it.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr import settlement  # noqa: E402
from pr import state as pr_state  # noqa: E402
from pr import thread_replies  # noqa: E402
from pr.comments_fix import FixSummary  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.fix import FixOutcome, FixRecord, ItemOutcome, SettledBy  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

# The reviewer's finding, shared across every test below that needs a body for
# the root comment — the wording itself is never the point of a test that uses
# it, only that a root comment exists for a reply to answer.
_FINDING = "please rename this"


def _thread(
    *, tid="t1", state=ThreadState.NEW, is_resolved=False, bodies=(),
    reviewer="kgn", file="a.py", line=10,
):
    return ReportThread(
        id=tid, state=state, is_resolved=is_resolved, reviewer=reviewer,
        file=file, line=line,
        comments=[{"body": b} for b in bodies],
    )


def _authored(*comments, my_login="me", **kw):
    """A thread whose comments name their authors — `(login, body)` pairs.

    `_thread` leaves the author out, which is the shape every test of the
    template arm wants. A hand-typed verdict is only ours if the login says so,
    so the tests of that arm need the field the grader reads.
    """
    return ReportThread(
        id=kw.pop("tid", "t1"), reviewer="kgn", file="a.py", line=10,
        my_login=my_login,
        comments=[{"author": {"login": who}, "body": body}
                  for who, body in comments],
        **kw,
    )


def _state(*items, reviewers=None):
    """A PRState whose fix snapshot holds exactly these outcomes."""
    return pr_state.PRState(
        identity=pr_state.PRIdentity(
            repo="owner/repo", branch="feat", pr_number=1, head_sha="abc1234",
            worktree_root="/tmp/wt",
        ),
        fix=FixSummary(
            fix=FixRecord(items=list(items)), reviewers=dict(reviewers or {}),
        ),
    )


# ── settlement_for: the two grades of evidence ────────────────────────────


class TestWhatGithubShowsBecameOfAThread:
    """A reply naming a verdict and a resolve button are not the same evidence.

    The distinction is the module's central rule. A standing reply of ours says
    what happened; the resolve button says only that the conversation is over,
    which covers a reviewer who withdrew the point as readily as one whose fix
    landed. Reading the second as the first publishes a claim about someone's
    code that nobody made.
    """

    def test_no_thread_shows_nothing(self):
        assert settlement.settlement_for(None) is None

    def test_an_open_thread_with_no_reply_of_ours_shows_nothing(self):
        assert settlement.settlement_for(_thread(bodies=["please fix this"])) is None

    @pytest.mark.parametrize("prefix, outcome", [
        (thread_replies.APPLIED_REPLY_PREFIX, FixOutcome.FIXED),
        (thread_replies.ADDRESSED_REPLY_PREFIX, FixOutcome.ALREADY_ADDRESSED),
        (thread_replies.DISMISSED_REPLY_PREFIX, FixOutcome.DISMISSED),
    ])
    def test_a_reply_naming_the_verdict_reads_as_the_verdict_it_names(
        self, prefix, outcome,
    ):
        thread = _thread(bodies=[f"{prefix} — see abc1234."])
        assert settlement.settlement_for(thread) is outcome

    def test_a_deferred_reply_names_no_ending(self):
        """Deferring says work is still owed, which is the opposite of settled."""
        thread = _thread(bodies=[f"{thread_replies.DEFERRED_REPLY_PREFIX} tracked."])
        assert settlement.settlement_for(thread) is None

    def test_the_resolve_button_alone_settles_without_crediting_a_fix(self):
        thread = _thread(is_resolved=True)
        assert settlement.settlement_for(thread) is FixOutcome.SETTLED_ELSEWHERE

    @pytest.mark.parametrize("state", [ThreadState.RESOLVED, ThreadState.ADDRESSED])
    def test_a_settled_lifecycle_state_counts_as_the_button(self, state):
        assert settlement.settlement_for(_thread(state=state)) is (
            FixOutcome.SETTLED_ELSEWHERE
        )

    def test_our_reply_outranks_an_unresolved_button(self):
        """The reply names the verdict however the button stands."""
        thread = _thread(
            is_resolved=False,
            bodies=[f"{thread_replies.APPLIED_REPLY_PREFIX}: dropped the guard."],
        )
        assert settlement.settlement_for(thread) is FixOutcome.FIXED


# ── settled_locations: which thread wins a shared location ────────────────


class TestALocationTwoSettledThreadsShare:
    """The better-evidenced settlement wins, whichever order the threads arrive.

    Two reviewers can leave a thread on one line, and a decomposed comment item
    restating either finds them both under one key. Taking the first would make
    the claim depend on dict order.
    """

    def test_an_unsettled_thread_contributes_no_location(self):
        located = settlement.settled_locations({"t1": _thread()})
        assert located == {}

    def test_a_thread_with_no_location_is_skipped(self):
        thread = _thread(is_resolved=True, file="", line=None)
        assert settlement.settled_locations({"t1": thread}) == {}

    def test_a_settled_thread_reports_its_location(self):
        thread = _thread(is_resolved=True)
        assert settlement.settled_locations({"t1": thread}) == {
            "kgn|a.py:10": FixOutcome.SETTLED_ELSEWHERE,
        }

    def test_a_reply_outranks_a_button_at_the_same_location(self):
        button = _thread(tid="t1", is_resolved=True)
        reply = _thread(
            tid="t2", bodies=[f"{thread_replies.APPLIED_REPLY_PREFIX}: done."],
        )
        assert settlement.settled_locations({"t1": button, "t2": reply}) == {
            "kgn|a.py:10": FixOutcome.FIXED,
        }

    def test_the_reply_still_wins_when_it_is_seen_first(self):
        reply = _thread(
            tid="t1", bodies=[f"{thread_replies.APPLIED_REPLY_PREFIX}: done."],
        )
        button = _thread(tid="t2", is_resolved=True)
        assert settlement.settled_locations({"t1": reply, "t2": button}) == {
            "kgn|a.py:10": FixOutcome.FIXED,
        }

    def test_two_reviewers_on_one_line_are_two_locations(self):
        mine = _thread(tid="t1", reviewer="kgn", is_resolved=True)
        theirs = _thread(tid="t2", reviewer="ana", is_resolved=True)
        assert set(settlement.settled_locations({"t1": mine, "t2": theirs})) == {
            "kgn|a.py:10", "ana|a.py:10",
        }


# ── a verdict a person typed rather than a template wrote ────────────────


class TestAReplyOfOursThatNamesAVerdictInItsOwnWords:
    """The contract is "names a verdict", not "came out of one of our templates".

    A reply written by hand — during a skill pass, or by the author answering a
    reviewer directly — says the same thing in different words, and testing the
    template openings made every one of them invisible. Five threads each
    carrying `Fixed — <what changed>` with a pinned permalink went unrecognised,
    unreconciled and unresolved.

    What keeps the widening safe is the author check. The words are ordinary
    English and a reviewer can type them too; reading theirs as ours would
    settle the thread on the strength of the complaint.
    """

    def test_our_hand_written_verdict_reads_as_fixed(self):
        thread = _authored(
            ("kgn", _FINDING),
            ("me", "Fixed — renamed the guard, see abc1234."),
        )
        assert settlement.settlement_for(thread) is FixOutcome.FIXED

    @pytest.mark.parametrize(
        "body",
        ["Fixed — renamed it.", "Fixed: renamed it.", "Fixed in abc1234.",
         "Done.", "Done — dropped the guard."],
    )
    def test_the_ways_a_person_spells_fixed(self, body):
        thread = _authored(("kgn", _FINDING), ("me", body))
        assert settlement.settlement_for(thread) is FixOutcome.FIXED

    def test_a_hand_typed_dismissal_reads_as_dismissed_not_fixed(self):
        """The bug this grading exists to prevent: a wave-off is not a fix.

        `verdict_kind` recognises "Dismissed" as a hand-typed opening the same
        way it recognises "Fixed" — collapsing both into FIXED would tell the
        reviewer code changed when the point was waved off instead.
        """
        thread = _authored(
            ("kgn", _FINDING), ("me", "Dismissed: the premise fails."),
        )
        assert settlement.settlement_for(thread) is FixOutcome.DISMISSED

    def test_a_reviewer_using_our_wording_is_not_our_verdict(self):
        """The negative the widening is bought with.

        "Fixed in my branch, please rebase" is the reviewer talking about their
        own tree. Grading it as our settlement resolves their thread and
        publishes a claim about code nobody here changed.
        """
        thread = _authored(
            ("kgn", _FINDING),
            ("kgn", "Fixed in my branch — please rebase onto it."),
        )
        assert settlement.settlement_for(thread) is None

    def test_a_reviewer_verdict_does_not_even_settle_the_thread(self):
        """Not merely 'not FIXED' — nothing about their comment ends the thread."""
        thread = _authored(
            ("kgn", _FINDING), ("kgn", "Done, on my side."),
        )
        assert settlement.settlement_for(thread) is None

    @pytest.mark.parametrize(
        "body",
        ["Fixing this now, one moment.", "Doneness is not a word.",
         "Addressed your first point but not the second.",
         "Resolved the conflict, but the API question stands.",
         "Good catch — will sort it.", "Agreed, that needs doing.",
         "This is fixed now.", "Should be fixed — have a look."],
    )
    def test_a_wording_that_settles_nothing_is_not_read_as_a_verdict(self, body):
        """Scope-ambiguous openings, acknowledgements, and work in flight.

        A false match publishes a claim about someone else's code; a miss only
        leaves the thread open. The vocabulary is sized for that asymmetry, and
        the anchor at the start of the body is half of what enforces it.
        """
        thread = _authored(("kgn", _FINDING), ("me", body))
        assert settlement.settlement_for(thread) is None

    def test_a_hand_written_deferral_still_says_the_opposite(self):
        """Counting it would settle every thread on the second --finish."""
        thread = _authored(
            ("kgn", _FINDING),
            ("me", f"{thread_replies.DEFERRED_REPLY_PREFIX} tracked in ENG-1."),
        )
        assert settlement.settlement_for(thread) is None

    def test_a_template_is_ours_even_with_no_login_to_check(self):
        """Nothing but this tool writes one, so authorship needs no second source."""
        thread = _authored(
            ("kgn", _FINDING),
            ("me", f"{thread_replies.APPLIED_REPLY_PREFIX}: renamed it."),
            my_login="",
        )
        assert settlement.settlement_for(thread) is FixOutcome.FIXED

    def test_without_a_login_a_typed_verdict_belongs_to_nobody(self):
        """Ours and the reviewer's are indistinguishable, so neither counts."""
        thread = _authored(
            ("kgn", _FINDING), ("me", "Fixed — renamed it."),
            my_login="",
        )
        assert settlement.settlement_for(thread) is None

    def test_the_login_match_ignores_case(self):
        thread = _authored(
            ("kgn", _FINDING), ("Me", "Fixed — renamed it."),
            my_login="me",
        )
        assert settlement.settlement_for(thread) is FixOutcome.FIXED

    def test_a_typed_verdict_outranks_an_unresolved_button(self):
        thread = _authored(
            ("kgn", _FINDING), ("me", "Fixed — renamed it."),
            is_resolved=False,
        )
        assert settlement.settlement_for(thread) is FixOutcome.FIXED

    def test_a_lone_self_authored_root_naming_a_verdict_is_not_our_reply(self):
        """On self-review the root comment is the finding, not an answer to one.

        `my_login` is the same account that authored the finding, so a single
        comment thread reads as `has_my_reply` and `last_comment_is_mine` both
        true. If that root's own wording happens to open with a verdict word,
        it must not be read as our reply confirming a fix that never happened.
        """
        thread = _authored(
            ("me", "Fixed casing is used inconsistently here."),
        )
        assert settlement.settlement_for(thread) is None


# ── adopt_settled_threads: the thread no round ever saw ───────────────────


class TestAThreadNoRoundEverGaveADispositionTo:
    """An answered-but-unresolved thread must not be dropped by every stage.

    `run_triage` excludes ADDRESSED threads, rightly — re-triaging one writes a
    fresh reply over the answer already standing. What had no owner was the
    thread afterwards: it reached no bucket, so it was never a snapshot row, so
    reconciliation had nothing to rewrite and resolution was never handed it.
    Five hand-answered threads stayed open for the life of a PR that way, with
    every run reporting `0 fixable` and `5 open`.
    """

    def _addressed(self, **kw):
        kw.setdefault("bodies", [_FINDING, "done by hand"])
        return _thread(state=ThreadState.ADDRESSED, **kw)

    def test_an_answered_thread_becomes_a_row_nothing_had_before(self):
        state = _state()
        assert settlement.adopt_settled_threads(
            state, {"t1": self._addressed()},
        ) == 1
        assert [o.id for o in state.fix.fix.items] == ["t1"]

    def test_the_row_claims_only_what_the_evidence_supports(self):
        """Speaking last is not a fix — it says the thread is nobody's to answer."""
        state = _state()
        settlement.adopt_settled_threads(state, {"t1": self._addressed()})
        assert state.fix.fix.items[0].outcome is FixOutcome.SETTLED_ELSEWHERE

    def test_a_reply_naming_the_verdict_is_graded_as_a_fix(self):
        """The grade is `settlement_for`'s, not a constant this stage picks."""
        state = _state()
        thread = self._addressed(bodies=[
            _FINDING,
            f"{thread_replies.APPLIED_REPLY_PREFIX}: renamed the guard.",
        ])
        settlement.adopt_settled_threads(state, {"t1": thread})
        assert state.fix.fix.items[0].outcome is FixOutcome.FIXED

    def test_the_row_is_not_credited_to_the_running_pass(self):
        """`attribution.handled_outside` reads this, and the Action cell reads that."""
        state = _state()
        settlement.adopt_settled_threads(state, {"t1": self._addressed()})
        outcome = state.fix.fix.items[0]
        assert outcome.settled_by is SettledBy.RECONCILIATION
        assert "reconciled" in outcome.reason
        assert not outcome.commit_sha

    def test_the_row_carries_the_location_the_table_renders(self):
        state = _state()
        settlement.adopt_settled_threads(state, {"t1": self._addressed()})
        outcome = state.fix.fix.items[0]
        assert (outcome.file, outcome.line) == ("a.py", 10)

    def test_the_summary_comes_off_the_reviewer_s_own_words(self):
        """No round ran, so there is no model-written summary to read."""
        state = _state()
        settlement.adopt_settled_threads(state, {"t1": self._addressed()})
        assert state.fix.fix.items[0].summary == _FINDING

    def test_a_thread_with_no_line_is_still_recorded(self):
        """`ReportThread.line` is optional; `ItemOutcome.line` is not."""
        state = _state()
        settlement.adopt_settled_threads(
            state, {"t1": self._addressed(file="", line=None)},
        )
        assert state.fix.fix.items[0].line == 0

    def test_the_reviewer_login_lands_where_the_row_can_find_it(self):
        state = _state()
        settlement.adopt_settled_threads(state, {"t1": self._addressed()})
        assert state.fix.reviewers == {"t1": "kgn"}

    def test_a_thread_github_named_no_reviewer_for_contributes_no_key(self):
        """A missing key misses; an empty one asserts an anonymous reviewer."""
        state = _state()
        settlement.adopt_settled_threads(
            state, {"t1": self._addressed(reviewer="")},
        )
        assert state.fix.reviewers == {}

    def test_a_resolved_thread_is_left_where_github_already_shows_it(self):
        """Adopting every resolved thread would add a permanent row per thread.

        The button collapsed the thread and the person who pressed it already
        said how it ended. The defect is the answered thread still open.
        """
        state = _state()
        resolved = _thread(state=ThreadState.RESOLVED, is_resolved=True,
                           bodies=[_FINDING])
        assert settlement.adopt_settled_threads(state, {"t1": resolved}) == 0
        assert state.fix.fix.items == []

    def test_a_thread_awaiting_a_reviewer_is_not_adopted(self):
        """Nobody has answered it, so there is nothing for this stage to record."""
        state = _state()
        open_thread = _thread(bodies=[_FINDING])
        assert settlement.adopt_settled_threads(state, {"t1": open_thread}) == 0

    def test_a_thread_the_round_already_recorded_is_left_alone(self):
        """The round's own verdict outranks a grade inferred after the fact."""
        state = _state(ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED))
        assert settlement.adopt_settled_threads(
            state, {"t1": self._addressed()},
        ) == 0
        assert state.fix.fix.items[0].outcome is FixOutcome.DEFERRED

    def test_a_second_run_adopts_nothing(self):
        """`--finish` runs repeatedly, and this path saves state directly.

        `FixRecord.merge_into` keys by id, but `_finish_deferred_work` mutates
        the record in place and saves it rather than folding it through
        `pr_state.apply`, so the guard has to be this function's own.
        """
        state = _state()
        threads = {"t1": self._addressed()}
        settlement.adopt_settled_threads(state, threads)
        assert settlement.adopt_settled_threads(state, threads) == 0
        assert len(state.fix.fix.items) == 1

    def test_a_row_this_wrote_is_never_reconciled_over(self):
        """Both grades sit outside UNSETTLED_OUTCOMES, so the row is stable."""
        state = _state()
        threads = {"t1": self._addressed()}
        settlement.adopt_settled_threads(state, threads)
        assert settlement.reconcile_fix_snapshot(state, threads) == 0

    def test_a_hand_answered_thread_keeps_the_reply_a_person_wrote(self):
        """The one outward act this stage enables, and its guard.

        A thread adopted as FIXED joins the reply queue, and `resolve_fixed_threads`
        closes it. What must not happen alongside is the three-line template
        being written over the answer a person typed — there is no undo but the
        edit history. `has_hand_written_reply` is the guard, and it holds here
        because a typed verdict matches none of the generated openings.
        """
        thread = _authored(
            ("kgn", _FINDING), ("me", "Fixed — renamed the guard."),
            state=ThreadState.ADDRESSED,
        )
        state = _state()
        settlement.adopt_settled_threads(state, {"t1": thread})
        assert state.fix.fix.items[0].outcome is FixOutcome.FIXED
        assert thread_replies.has_hand_written_reply(thread)

    def test_our_own_template_is_still_ours_to_rewrite(self):
        """The converse: a generated reply may be replaced, so the guard is off."""
        thread = _authored(
            ("kgn", _FINDING),
            ("me", f"{thread_replies.APPLIED_REPLY_PREFIX}: renamed the guard."),
            state=ThreadState.ADDRESSED,
        )
        assert not thread_replies.has_hand_written_reply(thread)


# ── entry_settlement: the three ways a row can be settled ─────────────────


class TestWhatSettledOneSnapshotRow:
    """A thread reads its own evidence; a comment item has none of its own.

    An item is a fragment of a top-level comment, so looking its synthetic id up
    among review threads can only ever miss. It is settled either by its source
    comment having been answered, or by an inline thread about the same line
    being settled — and in the second case it inherits that thread's grade
    rather than being promoted to FIXED.
    """

    def test_a_row_with_a_thread_reads_the_thread(self):
        thread = _thread(tid="c1", is_resolved=True)
        entry = CommentItem(id="c1", file="a.py", line=10, reviewer="kgn")
        assert settlement.entry_settlement(
            entry, {"c1": thread}, frozenset(), {},
        ) is FixOutcome.SETTLED_ELSEWHERE

    def test_a_row_with_neither_thread_nor_source_shows_nothing(self):
        entry = CommentItem(id="c1", file="a.py", line=10, reviewer="kgn")
        assert settlement.entry_settlement(entry, {}, frozenset(), {}) is None

    def test_an_answered_source_reads_as_fixed(self):
        entry = CommentItem(id="ic-77-1", file="a.py", line=10, reviewer="kgn")
        assert settlement.entry_settlement(
            entry, {}, frozenset({"77"}), {},
        ) is FixOutcome.FIXED

    def test_an_unanswered_source_falls_through_to_the_location(self):
        entry = CommentItem(id="ic-77-1", file="a.py", line=10, reviewer="kgn")
        assert settlement.entry_settlement(
            entry, {}, frozenset(), {"kgn|a.py:10": FixOutcome.SETTLED_ELSEWHERE},
        ) is FixOutcome.SETTLED_ELSEWHERE

    def test_an_item_settled_through_a_thread_inherits_its_grade(self):
        """The evidence is the thread's, so the claim it supports is too."""
        entry = CommentItem(id="ic-77-1", file="a.py", line=10, reviewer="kgn")
        settled = settlement.entry_settlement(
            entry, {}, frozenset(), {"kgn|a.py:10": FixOutcome.SETTLED_ELSEWHERE},
        )
        assert settled is not FixOutcome.FIXED

    def test_a_location_nothing_settled_shows_nothing(self):
        entry = CommentItem(id="ic-77-1", file="a.py", line=10, reviewer="kgn")
        assert settlement.entry_settlement(
            entry, {}, frozenset(), {"ana|b.py:3": FixOutcome.FIXED},
        ) is None


# ── settled_commits: all or nothing ───────────────────────────────────────


class TestOneCitationPerSettlement:
    """Every commit resolves before any outcome is written, or none is.

    A run that settles two of three and stops on the third leaves the operator
    to work out which half landed, which is the state surgery `--settle` exists
    to replace.
    """

    @pytest.fixture
    def picked(self):
        return [ItemOutcome(id="c1"), ItemOutcome(id="c2")]

    @pytest.mark.parametrize(
        "kind", [FixOutcome.DISMISSED, FixOutcome.ALREADY_ADDRESSED],
    )
    def test_a_settlement_that_cites_nothing_never_asks_git(self, picked, kind):
        with patch.object(settlement, "resolve_settled_commit") as resolve:
            assert settlement.settled_commits(None, picked, kind, "") == ["", ""]
        assert not resolve.called

    def test_a_fix_cites_one_commit_per_row(self, picked, tmp_path):
        answers = [
            settlement.SettledCommit(sha="aaa1111"),
            settlement.SettledCommit(sha="bbb2222"),
        ]
        with patch.object(settlement, "resolve_settled_commit", side_effect=answers):
            shas = settlement.settled_commits(
                tmp_path, picked, FixOutcome.FIXED, "",
            )
        assert shas == ["aaa1111", "bbb2222"]

    def test_one_unresolvable_commit_discards_the_whole_run(self, picked, tmp_path):
        answers = [
            settlement.SettledCommit(sha="aaa1111"),
            settlement.SettledCommit(error="--commit names no commit"),
        ]
        with patch.object(settlement, "resolve_settled_commit", side_effect=answers):
            assert settlement.settled_commits(
                tmp_path, picked, FixOutcome.FIXED, "",
            ) is None

    def test_the_failure_is_reported_rather_than_swallowed(self, picked, tmp_path):
        answers = [settlement.SettledCommit(error="named no commit"), None]
        with patch.object(settlement, "resolve_settled_commit", side_effect=answers), \
             patch("pr.settlement.log.error") as err:
            settlement.settled_commits(tmp_path, picked, FixOutcome.FIXED, "")
        assert "named no commit" in err.call_args[0][0]


# ── report_settlement: what the operator is told ──────────────────────────


class TestWhatTheOperatorIsToldTheyRecorded:
    """The uncited case earns its own line, because their next move depends on it.

    A row settled as fixed with no commit to cite is recorded either way, but it
    stays uncited until the fix is pushed or `--commit` names the one carrying
    it — and nothing else on the run says so.
    """

    @pytest.fixture
    def outcome(self):
        return ItemOutcome(id="c1", file="a.py", line=10)

    def test_the_prior_outcome_is_named_when_it_changed(self, outcome):
        with patch("pr.settlement.log.info") as info:
            settlement.report_settlement(
                outcome, FixOutcome.FIXED, FixOutcome.DEFERRED, "abc1234",
            )
        assert "was deferred" in info.call_args_list[0][0][0]

    def test_nothing_is_named_when_the_outcome_is_unchanged(self, outcome):
        with patch("pr.settlement.log.info") as info:
            settlement.report_settlement(
                outcome, FixOutcome.FIXED, FixOutcome.FIXED, "abc1234",
            )
        assert "was " not in info.call_args_list[0][0][0]

    def test_a_cited_commit_is_quoted_back(self, outcome):
        with patch("pr.settlement.log.info") as info:
            settlement.report_settlement(
                outcome, FixOutcome.FIXED, FixOutcome.DEFERRED, "abc1234",
            )
        assert "fixed in abc1234" in info.call_args_list[0][0][0]

    def test_an_uncited_fix_says_how_to_cite_it(self, outcome):
        with patch("pr.settlement.log.info") as info:
            settlement.report_settlement(
                outcome, FixOutcome.FIXED, FixOutcome.DEFERRED, "",
            )
        assert len(info.call_args_list) == 2
        assert "--commit" in info.call_args_list[1][0][0]

    def test_a_settlement_that_cites_nothing_owes_no_such_line(self, outcome):
        with patch("pr.settlement.log.info") as info:
            settlement.report_settlement(
                outcome, FixOutcome.DISMISSED, FixOutcome.DEFERRED, "",
            )
        assert len(info.call_args_list) == 1
