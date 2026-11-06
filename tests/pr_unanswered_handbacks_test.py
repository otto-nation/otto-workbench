"""A handed-back thread is unpublished until something of ours answers it.

`--finish` used to render NEEDS_HUMAN and DECLINED rows with nothing on the
thread backing them. `pr.settlement.unanswered_handbacks` is the one gate both
publish paths ask.
"""

import sys

import pytest

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.settlement  # noqa: E402
import pr.summary_model  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.fix import FixOutcome, FixRecord, ItemOutcome  # noqa: E402
from pr.thread_models import ReportThread  # noqa: E402

DECIDED_AT = "2026-03-01T12:00:00+00:00"
BEFORE = "2026-03-01T11:00:00Z"
AFTER = "2026-03-01T13:00:00Z"


def _comment(who, created, edited=None, body="x"):
    return {"author": {"login": who}, "body": body,
            "createdAt": created, "lastEditedAt": edited}


def _outcome(tid="t1", outcome=FixOutcome.DECLINED, decided_at=DECIDED_AT,
             summary="premise is wrong"):
    return ItemOutcome(id=tid, outcome=outcome, decided_at=decided_at,
                       summary=summary, file="a.py", line=3)


def _thread(tid="t1", comments=None, *, is_resolved=False, my_login="me"):
    return ReportThread(
        id=tid, my_login=my_login, is_resolved=is_resolved,
        state=ThreadState.RESOLVED if is_resolved else ThreadState.NEW,
        comments=list(comments or [_comment("kgn", BEFORE)]),
    )


def _owed(items, threads, answered_now=frozenset()):
    return [o.id for o in pr.settlement.unanswered_handbacks(
        FixRecord(items=list(items)), threads, answered_now,
    )]


class TestUnansweredHandbacks:

    @pytest.mark.parametrize("outcome", pr.summary_model.NEEDS_A_PERSON)
    def test_a_reviewer_only_thread_is_returned(self, outcome):
        assert _owed([_outcome(outcome=outcome)], {"t1": _thread()}) == ["t1"]

    def test_our_reply_after_decided_at_is_not_returned(self):
        comments = [_comment("kgn", BEFORE), _comment("me", AFTER)]
        assert _owed([_outcome()], {"t1": _thread(comments=comments)}) == []

    def test_our_reply_before_decided_at_is_still_returned(self):
        comments = [_comment("kgn", BEFORE), _comment("me", BEFORE)]
        assert _owed([_outcome()], {"t1": _thread(comments=comments)}) == ["t1"]

    def test_our_edit_after_the_stamp_is_not_returned(self):
        comments = [_comment("kgn", BEFORE), _comment("me", BEFORE, edited=AFTER)]
        assert _owed([_outcome()], {"t1": _thread(comments=comments)}) == []

    def test_our_own_root_comment_after_the_stamp_is_still_returned(self):
        """Index 0 is the reviewer's original comment, never our answer."""
        comments = [_comment("me", AFTER)]
        assert _owed([_outcome()], {"t1": _thread(comments=comments)}) == ["t1"]

    def test_a_resolved_thread_is_not_returned(self):
        assert _owed([_outcome()], {"t1": _thread(is_resolved=True)}) == []

    def test_an_id_in_answered_now_is_not_returned(self):
        assert _owed([_outcome()], {"t1": _thread()}, answered_now={"t1"}) == []

    def test_a_review_body_id_is_not_returned(self):
        assert _owed([_outcome(tid="rb-88-1")], {}) == []

    def test_an_id_missing_from_threads_by_id_is_not_returned(self):
        assert _owed([_outcome()], {}) == []

    @pytest.mark.parametrize("outcome", (
        FixOutcome.FIXED, FixOutcome.DISMISSED, FixOutcome.DEFERRED,
    ))
    def test_a_terminal_or_deferred_outcome_is_not_returned(self, outcome):
        assert _owed([_outcome(outcome=outcome)], {"t1": _thread()}) == []
