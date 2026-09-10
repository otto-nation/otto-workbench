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
from pr import thread_replies  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

_REPO = "owner/repo"
_PR = 42


def _thread(
    *, tid="t1", state=ThreadState.NEW, is_resolved=False, bodies=(),
    reviewer="kgn", file="a.py", line=10,
):
    return ReportThread(
        id=tid, state=state, is_resolved=is_resolved, reviewer=reviewer,
        file=file, line=line,
        comments=[{"body": b} for b in bodies],
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

    @pytest.mark.parametrize("prefix", thread_replies.HANDLED_REPLY_PREFIXES)
    def test_a_reply_naming_the_verdict_reads_as_fixed(self, prefix):
        thread = _thread(bodies=[f"{prefix} — see abc1234."])
        assert settlement.settlement_for(thread) is FixOutcome.FIXED

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
