"""fix.comments: an already-addressed verdict is checked before its reply goes out.

The verdict tells a reviewer their point was moot and resolves their thread, and
until it went through the verify gate it was the least-checked outcome the pass
produced — triage's own check asks only whether the cited line resolves. These
hold the three halves of putting it through the existing gate: the gate asks the
right question of it, `run_pass` asks before anything is posted, and a verdict
the gate took back is recorded so `--finish` cannot publish it later.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import (  # noqa: E402
    _answering_the_owner, _fake_ctx, _git_ran, _make_state, _no_published_summary,
)
import core.publishing  # noqa: E402
import fix.comment_checklist  # noqa: E402
import fix.comments  # noqa: E402
import fix.gate  # noqa: E402
import fix.verify  # noqa: E402
import git.client  # noqa: E402
import git.push  # noqa: E402
import git.topology  # noqa: E402
import pr.fix_state  # noqa: E402
import pr.thread_context  # noqa: E402
import review.closeout  # noqa: E402
from core.phases import Phase  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.thread_models import CommentItem, PRReport, ReportThread, TriageResult  # noqa: E402


_GATE_ADAPTER = SimpleNamespace(
    item_noun="thread", phase=Phase.COMMENTS_FIX,
    verify_phase=Phase.COMMENTS_VERIFY,
)


def _addressed(oid="t1", reason="the guard at a.py:9 already rejects it"):
    return ItemOutcome(
        id=oid, outcome=FixOutcome.ALREADY_ADDRESSED, summary="reject empty input",
        reason=reason, file="a.py", line=9, evidence_file="a.py", evidence_line=9,
    )


class TestTheGateChecksAnAlreadyAddressedVerdict:
    """`verify_claims` selects the verdict and asks it the addressed question."""

    def test_it_is_put_to_the_gate_with_the_addressed_question(self):
        seen = {}

        def gate(phase, _prompt, **kwargs):
            seen["phase"] = phase
            seen["items"] = kwargs["items"]
            return {}

        fix.gate.verify_claims([_addressed()], gate, _GATE_ADAPTER, {}, None)

        assert seen["phase"] is Phase.COMMENTS_VERIFY
        [item] = seen["items"]
        assert item.id == "t1"
        assert "actually do what the reviewer asked" in item.body
        assert "`a.py:9`" in item.body
        assert "the guard at a.py:9 already rejects it" in item.body
        # Not asked as a fix or a decline: neither question fits.
        assert fix.gate._CLAIM_HEADING not in item.body
        assert "rejected this thread" not in item.body

    def test_it_is_not_asked_to_recheck_the_citation(self):
        item = fix.gate._verify_item(_addressed(), None, "thread")
        assert "do not spend turns confirming the line exists" in item.body

    def test_a_falsified_verdict_goes_to_a_person(self):
        outcome = _addressed()
        fix.gate.verify_claims(
            [outcome],
            lambda *_a, **_k: {"t1": fix.gate.Verdict(
                ok=False, detail="the guard is on the other caller")},
            _GATE_ADAPTER, {}, None,
        )
        assert outcome.outcome is FixOutcome.NEEDS_HUMAN
        assert outcome.reason == "the guard is on the other caller"
        assert outcome.verified is False

    def test_a_bare_broken_still_says_what_was_wrong(self):
        outcome = _addressed()
        fix.gate.verify_claims(
            [outcome], lambda *_a, **_k: {"t1": fix.gate.Verdict(ok=False)},
            _GATE_ADAPTER, {}, None,
        )
        assert "already addressed" in outcome.reason

    def test_silence_does_not_demote_it(self):
        outcome = _addressed()
        fix.gate.verify_claims(
            [outcome], lambda *_a, **_k: {}, _GATE_ADAPTER, {}, None,
        )
        assert outcome.outcome is FixOutcome.ALREADY_ADDRESSED
        assert outcome.verified is False


def _entry(eid, line, **kw):
    return CommentItem(
        id=eid, file="f.go", line=line, reviewer="kgn", summary=f"{eid} summary",
        classification="actionable_suggestion", verification="already_addressed",
        complexity="low", state=ThreadState.NEW,
        evidence_file="f.go", evidence_line=line + 1,
        reasoning=f"{eid} is already handled", **kw,
    )


def _report():
    return PRReport(
        repo="owner/repo", pr_number=1,
        threads=[
            ReportThread(id="t1", file="f.go", line=10, comments=[{"databaseId": 100}]),
            ReportThread(id="t2", file="f.go", line=20, comments=[{"databaseId": 200}]),
        ],
    )


def _gated(*_args, **_kwargs):
    """A GitHub write that answers the way the real one does under the gate."""
    return core.publishing.enabled()


class _Run:
    """One `run_pass` over two already-addressed threads, the gate stubbed.

    The stub records whether any reply had gone out by the time it was asked,
    which is the ordering under test: a reply posted first has already resolved
    the thread the gate is about to take back.
    """

    def __init__(self, tmp_path, verdicts, *, verify=True):
        self.posted: list[int] = []
        self.resolved: list[str] = []
        self.posted_before_gate: list[int] | None = None
        self.gate_ids: list[str] = []

        def gate(_phase, _prompt, **kwargs):
            self.posted_before_gate = list(self.posted)
            self.gate_ids = [i.id for i in kwargs["items"]]
            return verdicts

        def post(_repo, _pr, root_id, _body):
            if core.publishing.enabled():
                self.posted.append(root_id)
                return True
            return False

        def resolve(thread_id, *_a, **_k):
            if core.publishing.enabled():
                self.resolved.append(thread_id)
                return True
            return False

        with patch.object(fix.verify, "run", side_effect=gate), \
             patch.object(pr.thread_context, "diff_context_for_file", return_value=""), \
             patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None), \
             patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch.object(pr.fix_state, "persist") as persist, \
             patch.object(git.client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr.comments.post_thread_reply", side_effect=post), \
             patch("pr.comments.post_issue_comment", side_effect=_gated), \
             patch("pr.comments.resolve_thread", side_effect=resolve):
            self.result = fix.comments.run_pass(
                TriageResult(threads=[_entry("t1", 10), _entry("t2", 20)]),
                _report(), tmp_path, _fake_ctx(tmp_path), verify=verify,
            )
        self.persisted = persist.call_args[0][0]


_T1_BROKEN = {
    "t1": fix.gate.Verdict(ok=False, detail="the guard is on the other caller"),
    "t2": fix.gate.Verdict(ok=True, detail="ran it with empty input; rejected"),
}


class TestRunPassChecksBeforeItReplies:

    def test_the_gate_runs_before_any_reply(self, tmp_path, publishing_on):
        run = _Run(tmp_path, _T1_BROKEN)
        assert run.gate_ids == ["t1", "t2"]
        assert run.posted_before_gate == []

    def test_a_falsified_verdict_is_never_posted_or_resolved(
        self, tmp_path, publishing_on,
    ):
        run = _Run(tmp_path, _T1_BROKEN)
        assert 100 not in run.posted
        assert "t1" not in run.resolved

    def test_it_lands_in_needs_human(self, tmp_path, publishing_on):
        run = _Run(tmp_path, _T1_BROKEN)
        assert [e.id for e in run.result.needs_human] == ["t1"]
        assert [e.id for e in run.result.already_addressed] == ["t2"]

    def test_publishing_is_held(self, tmp_path, publishing_on):
        """One falsified verdict holds the round, as a falsified fix does.

        So the surviving verdict's reply waits too — for `--finish --post`.
        """
        run = _Run(tmp_path, _T1_BROKEN)
        assert "falsified by the verify gate" in core.publishing.held()
        assert run.posted == []
        assert run.persisted.replies_pending is True

    def test_an_addressed_only_round_is_still_checked(self, tmp_path):
        """No fixable entry, so the engine never runs; the check must not ride on it."""
        run = _Run(tmp_path, _T1_BROKEN)
        assert run.result.batches == 0
        assert run.gate_ids == ["t1", "t2"]

    def test_the_demotion_is_what_the_state_records(self, tmp_path):
        run = _Run(tmp_path, _T1_BROKEN)
        by_id = {o.id: o for o in run.persisted.fix.items}
        assert by_id["t1"].outcome is FixOutcome.NEEDS_HUMAN
        assert by_id["t1"].verify_detail == "the guard is on the other caller"
        assert by_id["t2"].outcome is FixOutcome.ALREADY_ADDRESSED
        assert by_id["t2"].verified is True

    def test_a_bare_broken_keeps_the_fallback_sentence_on_the_demoted_row(self, tmp_path):
        run = _Run(tmp_path, {
            "t1": fix.gate.Verdict(ok=False),
            "t2": fix.gate.Verdict(ok=True, detail="ok"),
        })
        by_id = {o.id: o for o in run.persisted.fix.items}
        assert by_id["t1"].outcome is FixOutcome.NEEDS_HUMAN
        assert by_id["t1"].verify_detail == fix.gate._FALSIFIED_REASON[
            FixOutcome.ALREADY_ADDRESSED]

    def test_an_upheld_round_replies_and_resolves(self, tmp_path, publishing_on):
        """Pairs with the falsified cases: the gate passing changes nothing."""
        run = _Run(tmp_path, {k: fix.gate.Verdict(ok=True, detail="ok")
                              for k in ("t1", "t2")})
        assert sorted(run.posted) == [100, 200]
        assert sorted(run.resolved) == ["t1", "t2"]
        assert not core.publishing.held()

    def test_no_verify_keeps_todays_behaviour(self, tmp_path, publishing_on):
        run = _Run(tmp_path, _T1_BROKEN, verify=False)
        assert run.gate_ids == []
        assert sorted(run.posted) == [100, 200]
        assert [e.id for e in run.result.already_addressed] == ["t1", "t2"]


def test_finish_does_not_republish_a_demoted_verdict(tmp_path, monkeypatch):
    """The drafted round's record, drained by `--finish --post`.

    Only the verdict the gate upheld is replied to and resolved; the one it
    falsified is recorded as needing a person, which the closeout never drains.
    """
    run = _Run(tmp_path, _T1_BROKEN)
    state = _make_state(run.persisted)
    monkeypatch.setattr(core.publishing, "_enabled", True)
    monkeypatch.setattr(core.publishing, "_held", "")
    posted, resolved = [], []
    with patch.object(git.push, "holds", return_value=True), \
         patch.object(git.client, "run",
                      side_effect=_answering_the_owner(
                          lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
         patch("pr.comments.post_thread_reply",
               side_effect=lambda *a: posted.append(a[2]) or True), \
         patch("pr.comments.resolve_thread",
               side_effect=lambda tid, *a, **k: resolved.append(tid) or True):
        review.closeout.post_pending_fix_replies(
            state, "owner/repo", 1, {t.id: t for t in _report().threads},
        )
    assert posted == [200]
    assert resolved == ["t2"]
