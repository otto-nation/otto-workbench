"""A settled thread stays settled across a re-triage until someone speaks on it.

A settlement lives in the fix record until `--finish` publishes its reply, so
GitHub shows the thread as unanswered in between. These cover the one rule every
reader now shares — `pr.settlement.settled_ids` — and the places that act on it:
triage leaves the thread out of its prompt and drops any verdict on it, the
record keeps the settled outcome, and `pr batch` counts it as owed only once the
reviewer has replied since.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend  # noqa: E402
import batch.plan  # noqa: E402
import pr.comments  # noqa: E402
import pr.fix_state  # noqa: E402
import pr.settlement  # noqa: E402
import pr.state  # noqa: E402
import pr.thread_context  # noqa: E402
import pr.triage  # noqa: E402
from batch.model import Step  # noqa: E402
from pr.comments_fix import FixSummary  # noqa: E402
from pr.fix import FixOutcome, FixRecord, ItemOutcome, SettledBy  # noqa: E402
from pr.thread_models import CommentItem, PRReport, ReportThread  # noqa: E402

SETTLED_AT = "2026-03-01T12:00:00+00:00"
BEFORE = "2026-03-01T11:00:00Z"
AFTER = "2026-03-01T13:00:00Z"


def _comment(who, created, edited=None):
    return {"author": {"login": who}, "body": "x",
            "createdAt": created, "lastEditedAt": edited}


def _outcome(tid="t1", outcome=FixOutcome.FIXED, decided_at=SETTLED_AT):
    return ItemOutcome(id=tid, outcome=outcome, decided_at=decided_at,
                       settled_by=SettledBy.OPERATOR, commit_sha="abc1234",
                       file="a.py", line=3, summary="rename it")


def _settled(record, comments, my_login="me"):
    return pr.settlement.settled_ids(record, {"t1": comments}, my_login)


# ── spoken_since / settled_ids: the rule ──────────────────────────────────


class TestTheSettledRule:

    def test_a_terminal_outcome_nobody_answered_is_settled(self):
        record = FixRecord(items=[_outcome()])
        assert _settled(record, [_comment("kgn", BEFORE)]) == {"t1"}

    def test_a_reviewer_reply_after_the_settlement_reopens_it(self):
        record = FixRecord(items=[_outcome()])
        comments = [_comment("kgn", BEFORE), _comment("kgn", AFTER)]
        assert _settled(record, comments) == set()

    def test_a_reviewer_edit_after_the_settlement_reopens_it(self):
        record = FixRecord(items=[_outcome()])
        assert _settled(record, [_comment("kgn", BEFORE, edited=AFTER)]) == set()

    def test_our_own_reply_after_the_settlement_does_not_reopen_it(self):
        """The reply that announces a settlement must not undo it."""
        record = FixRecord(items=[_outcome()])
        comments = [_comment("kgn", BEFORE), _comment("me", AFTER)]
        assert _settled(record, comments) == {"t1"}

    def test_an_outcome_still_owed_is_never_settled(self):
        for owed in (FixOutcome.DEFERRED, FixOutcome.NEEDS_HUMAN, FixOutcome.DECLINED):
            record = FixRecord(items=[_outcome(outcome=owed)])
            assert _settled(record, [_comment("kgn", BEFORE)]) == set(), owed

    def test_a_legacy_outcome_is_dated_by_the_record(self):
        record = FixRecord(items=[_outcome(decided_at="")], updated_at=SETTLED_AT)
        assert _settled(record, [_comment("kgn", BEFORE)]) == {"t1"}
        assert _settled(record, [_comment("kgn", AFTER)]) == set()

    def test_no_date_at_all_reopens_rather_than_closes(self):
        record = FixRecord(items=[_outcome(decided_at="")])
        assert _settled(record, [_comment("kgn", BEFORE)]) == set()

    def test_stamps_are_compared_as_instants(self):
        """`Z` and `+00:00` spell one instant; a lexical compare would split them."""
        record = FixRecord(items=[_outcome(decided_at="2026-03-01T13:00:00Z")])
        assert _settled(record, [_comment("kgn", "2026-03-01T13:00:00+00:00")]) == {"t1"}


# ── who stamps decided_at ─────────────────────────────────────────────────


class TestEveryDecisionIsDated:

    def test_record_settlement_dates_the_operator_s_decision(self):
        outcome = _outcome(outcome=FixOutcome.NEEDS_HUMAN, decided_at="")
        pr.settlement.record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert pr.comments._instant(outcome.decided_at) is not None

    def test_reconciliation_dates_the_flip(self):
        record = FixRecord(items=[_outcome(outcome=FixOutcome.DEFERRED, decided_at="")])
        record.reconcile({"t1": FixOutcome.SETTLED_ELSEWHERE})
        assert pr.comments._instant(record.items[0].decided_at) is not None

    def test_the_fix_record_keeps_a_triage_stamp_and_fills_a_missing_one(self):
        triaged = CommentItem(id="t1", decided_at=SETTLED_AT)
        unstamped = CommentItem(id="t2")
        record = pr.fix_state.fix_record_for({
            FixOutcome.DISMISSED: [triaged], FixOutcome.FIXED: [unstamped],
        })
        by_id = {o.id: o for o in record.items}
        assert by_id["t1"].decided_at == SETTLED_AT
        assert by_id["t2"].decided_at == record.updated_at

    def test_a_replayed_entry_keeps_its_stamp(self):
        outcome = _outcome()
        assert CommentItem.from_outcome(outcome).to_outcome().decided_at == SETTLED_AT


# ── run_triage: the settled thread never reaches the model's verdicts ─────


def _saved_record(tmp_path, *outcomes):
    state = pr.state.new_state("o/a", "b", 1, "h", str(tmp_path))
    state.fix = FixSummary(fix=FixRecord(items=list(outcomes), updated_at=SETTLED_AT))
    pr.state.save_state(tmp_path, state)


def _triage(tmp_path, report, reply):
    prompts = []

    def prompt(text, **kw):
        prompts.append(text)
        return (reply, 0)

    with (
        patch.object(agent.backend, "prompt", side_effect=prompt),
        patch.object(pr.thread_context, "branch_commit_log", return_value=""),
    ):
        result, rc = pr.triage.run_triage(
            report, tmp_path, {"target_dir": tmp_path})
    return result, rc, prompts


def _report(*threads):
    return PRReport(repo="o/a", pr_number=1, my_login="me", threads=list(threads))


def _open(tid, *comments):
    return ReportThread(id=tid, reviewer="kgn", file="a.py", line=3,
                        my_login="me", comments=list(comments))


_VALID = ('{{"threads": [{{"id": "{tid}", "classification": "actionable_suggestion",'
          ' "verification": "valid", "complexity": "low", "summary": "s"}}]}}')


class TestTriageLeavesSettledThreadsAlone:

    def test_a_settled_thread_is_not_put_to_the_model(self, tmp_path):
        _saved_record(tmp_path, _outcome("t1"))
        report = _report(_open("t1", _comment("kgn", BEFORE)),
                         _open("t2", _comment("kgn", BEFORE)))
        result, rc, prompts = _triage(tmp_path, report, _VALID.format(tid="t2"))
        assert rc == 0
        assert '"t1"' not in prompts[0] and '"t2"' in prompts[0]
        assert [t.id for t in result.threads] == ["t2"]
        assert result.settled == ["t1"]

    def test_a_verdict_on_a_settled_thread_is_dropped(self, tmp_path):
        """The record merge would replace the settled outcome with this one."""
        _saved_record(tmp_path, _outcome("t1"))
        report = _report(_open("t1", _comment("kgn", BEFORE)),
                         _open("t2", _comment("kgn", BEFORE)))
        result, rc, _ = _triage(tmp_path, report, _VALID.format(tid="t1"))
        assert rc == 0
        assert result.threads == []

    def test_a_settled_thread_the_reviewer_answered_is_triaged_again(self, tmp_path):
        _saved_record(tmp_path, _outcome("t1"))
        report = _report(_open("t1", _comment("kgn", BEFORE), _comment("kgn", AFTER)))
        result, rc, prompts = _triage(tmp_path, report, _VALID.format(tid="t1"))
        assert rc == 0
        assert '"t1"' in prompts[0]
        assert [t.id for t in result.threads] == ["t1"]
        assert result.settled == []

    def test_only_settled_threads_skips_the_model(self, tmp_path):
        _saved_record(tmp_path, _outcome("t1"))
        report = _report(_open("t1", _comment("kgn", BEFORE)))
        result, rc, prompts = _triage(tmp_path, report, "{}")
        assert (rc, prompts, result.settled) == (0, [], ["t1"])

    def test_every_verdict_is_dated_before_the_model_was_asked(self, tmp_path):
        report = _report(_open("t2", _comment("kgn", BEFORE)))
        result, _, _ = _triage(tmp_path, report, _VALID.format(tid="t2"))
        assert pr.comments._instant(result.threads[0].decided_at) is not None


# ── pr batch: one rule, read over the plan query ──────────────────────────


def _planned_need(monkeypatch, tmp_path, thread_comments):
    state = pr.state.new_state("o/a", "b", 7, "h", str(tmp_path))
    state.fix = FixSummary(fix=FixRecord(items=[_outcome("T1")], updated_at=SETTLED_AT))
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: state)
    monkeypatch.setattr(batch.plan, "_review_file",
                        lambda repo, branch: tmp_path / "missing.md")
    node = {"number": 7, "title": "t", "isDraft": False, "headRefName": "b",
            "headRefOid": "h", "mergeStateStatus": "CLEAN",
            "repository": {"nameWithOwner": "o/a"},
            "reviewThreads": {"nodes": [{"id": "T1", "isResolved": False,
                                         "comments": {"nodes": thread_comments}}]}}
    data = {"viewer": {"login": "me"}, "search": {"nodes": [node]}}
    [row] = batch.plan.rows_from_search(data, {"o/a": str(tmp_path)})
    return row.needs[Step.COMMENTS]


def test_batch_owes_nothing_on_a_settled_thread(monkeypatch, tmp_path):
    need = _planned_need(monkeypatch, tmp_path, [_comment("kgn", BEFORE)])
    assert need.needed is False


def test_batch_owes_a_settled_thread_the_reviewer_answered(monkeypatch, tmp_path):
    need = _planned_need(monkeypatch, tmp_path,
                         [_comment("kgn", BEFORE), _comment("kgn", AFTER)])
    assert need == batch.plan.StepNeed(True, "1 unresolved thread")
