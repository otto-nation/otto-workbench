"""Tests for fix.engine against the worktree it shares — reconciling the
agent's boxes with what the tree shows, recording dirt found before the pass,
and refusing a worktree an interactive session holds.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.engine  # noqa: E402
import fix.gate  # noqa: E402
from fix.types import FixItem  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
import core.session_lock

from fix_engine_support import (
    StubAdapter,
    _answer,
    landed,
    head,
    _reads,
    _settles_at,
    _run,
    _verdicts,
)
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401


# ── reconciling the boxes against the tree ──────────────────────────────────
#
# The pass has two accounts of itself: what the agent ticked, and what the
# worktree shows. Both were computed before this and compared nowhere, so an
# agent that edited a file and recorded `deferred` had its edit committed and
# reported as work still owed.


def test_a_deferral_whose_own_file_changed_is_put_to_the_gate(
    tmp_path, landed, head, snapshots,
):
    """The contradiction that reached a real run: applied, recorded as not done.

    `deferred` is not a claim the gate saw before this — it is the outcome that
    asks for nothing and so was never checked — and that is exactly why the
    edit rode into the commit under a row saying no work was done.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["ids"] = [i.id for i in kwargs["items"]]
        seen["body"] = kwargs["items"][0].body
        return {}

    _run(adapter, verify=run_verify, run_fix=_answer(adapter, tick="deferred"))

    assert seen["ids"] == ["i0"]
    # Worded as the inverse question. Asked under the fix wording, the gate
    # would be checking a fix the pass never claimed and would rightly call it
    # broken — the wrong answer to "is the work there at all?".
    assert "recorded this item as work it did not do" in seen["body"]
    assert "`a.py`" in seen["body"]


def test_a_deferral_the_gate_confirms_becomes_a_fix(tmp_path, landed, head, snapshots):
    """The only promotion in the engine, and it takes a verdict to get it.

    The observation alone cannot do this: a file moving says something happened
    in that batch, never that this item is what happened.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=True, detail="the repro passes"))),
        run_fix=_answer(adapter, tick="deferred"),
    )

    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].verified is True
    assert "the repro passes" in run.outcomes[0].reason


def test_a_deferral_the_gate_cannot_settle_stands_as_recorded(
    tmp_path, landed, head, snapshots,
):
    """No verdict, no change — the file moved for a reason nobody established.

    The common honest case: two items share a batch and the edit belongs to the
    other one. Demoting or promoting on the observation alone would rewrite a
    correct answer every time that happens.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=None, detail="belongs to i1"))),
        run_fix=_answer(adapter, tick="deferred"),
    )

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED
    # Not False. The surfaces print that as a hedge beside a claimed fix, and
    # this row claims nothing to hedge.
    assert run.outcomes[0].verified is None
    # The gate's own explanation for why it couldn't settle this is recorded,
    # the same as the main verify loop does for an uncontradicted item the
    # gate could not verify.
    assert run.outcomes[0].verify_detail == "belongs to i1"


def test_a_deferral_the_gate_finds_half_applied_goes_to_a_person(
    tmp_path, landed, head, snapshots,
):
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=False, detail="half applied"))),
        run_fix=_answer(adapter, tick="deferred"),
    )

    assert run.outcomes[0].outcome is FixOutcome.NEEDS_HUMAN
    assert "half applied" in run.outcomes[0].reason


# passes-at-base: guards against over-firing, and base fires never
def test_a_deferral_in_a_batch_that_changed_nothing_is_left_alone(
    tmp_path, landed, head, snapshots,
):
    """An honest deferral must not be dragged in front of the gate.

    Every pass defers something, and gating all of them would spend the gate's
    budget on the outcome least likely to be wrong.
    """
    snapshots.side_effect = _settles_at(set(), set())
    adapter = StubAdapter(tmp_path, count=1)

    def run_verify(*_a, **_k):
        raise AssertionError("the gate was asked about an uncontradicted deferral")

    run, _ = _run(adapter, verify=run_verify,
                  run_fix=_answer(adapter, tick="deferred"))

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED


# passes-at-base: guards against over-firing, and base fires never
def test_a_deferral_is_not_contradicted_by_another_item_s_file(
    tmp_path, landed, head, snapshots,
):
    """Anchored on the item's own path, not on the batch having done anything."""
    snapshots.side_effect = _settles_at(set(), {"somewhere/else.py"})
    adapter = StubAdapter(tmp_path, count=1)

    def run_verify(*_a, **_k):
        raise AssertionError("the gate was asked about an unrelated file")

    run, _ = _run(adapter, verify=run_verify,
                  run_fix=_answer(adapter, tick="deferred"))

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED


def test_a_hand_off_whose_own_file_changed_is_put_to_the_gate(
    tmp_path, landed, head, snapshots,
):
    """`needs a person` claims no work just as loudly as a deferral does.

    It reads as a hand-off rather than a claim, which is why it was outside the
    checked set, but the sentence it publishes is the same one: the surfaces
    print it under "Skipped", so an agent that edited the code and then asked
    for a person put "no work was done" on a commit carrying the edit.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["ids"] = [i.id for i in kwargs["items"]]
        seen["body"] = kwargs["items"][0].body
        return {}

    _run(adapter, verify=run_verify,
         run_fix=_answer(adapter, tick="needs a person"))

    assert seen["ids"] == ["i0"]
    assert "recorded this item as work it did not do" in seen["body"]


def test_a_contradiction_the_gate_never_answered_says_so_in_its_reason(
    tmp_path, landed, head, snapshots,
):
    """An empty reason renders as "no auto-fix", which is the one wrong reading.

    The outcome stands as the agent recorded it — no verdict, no demotion — but
    the row must not go on to assert that nothing happened, because the commit
    it rides in contains the edit that contradicted it.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(adapter, verify=lambda *_a, **_k: {},
                  run_fix=_answer(adapter, tick="deferred"))

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED
    assert "the gate reached no verdict" in run.outcomes[0].reason
    assert run.outcomes[0].reason != ""


def test_a_contradiction_the_gate_could_not_settle_keeps_the_gate_s_words(
    tmp_path, landed, head, snapshots,
):
    """The gate's own explanation beats the generic one where there is one."""
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=None, detail="belongs to i1"))),
        run_fix=_answer(adapter, tick="deferred"),
    )

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED
    assert run.outcomes[0].reason == "belongs to i1"


# passes-at-base: guards against over-firing, and base fires never
def test_an_unreadable_worktree_contradicts_nothing(
    tmp_path, landed, head, snapshots,
):
    """A failed git call must not become an accusation against the agent.

    Unknown is the state in which every item looks untouched, so reading it as
    "nothing changed" would contradict every deferral in the pass at once.
    """
    snapshots.side_effect = _settles_at(set(), None)
    adapter = StubAdapter(tmp_path, count=1)

    def run_verify(*_a, **_k):
        raise AssertionError("the gate was asked on the strength of a failed read")

    run, _ = _run(adapter, verify=run_verify,
                  run_fix=_answer(adapter, tick="deferred"))

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED


def test_the_gate_is_told_what_the_tree_shows_for_a_claimed_fix(
    tmp_path, landed, head, snapshots,
):
    """The half that was missing when a fix's stated reason described other work.

    The claim is prose written by the agent being checked. Held against nothing,
    the gate can only ask whether it reads like a fix — and a fluent sentence
    about an unrelated change reads exactly like a fluent sentence about a real
    one. The file list is what the prose now has to agree with.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py", "a_test.py"})
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["body"] = kwargs["items"][0].body
        return {}

    _run(adapter, verify=run_verify,
         run_fix=_answer(adapter, tick="fixed", reason="added a regression test"))

    assert "`a_test.py`" in seen["body"]
    assert "added a regression test" in seen["body"]
    # Evidence, not a verdict: the list is per batch and says so, because a
    # reader told otherwise would convict on a shared file.
    assert "per batch" in seen["body"]


def test_a_claimed_fix_off_its_anchor_is_reported_without_a_verdict(
    tmp_path, landed, head, snapshots,
):
    """A fix landing in a caller is normal, so the block informs and concludes nothing.

    This is the case a mechanical "claimed a fix, changed nothing" rule would
    get wrong more often than right, which is why the engine does not have one.
    """
    snapshots.side_effect = _settles_at(set(), {"caller.py"})
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["body"] = kwargs["items"][0].body
        return {}

    run, _ = _run(adapter, verify=run_verify, run_fix=_answer(adapter, tick="fixed"))

    assert "is **not** among them" in seen["body"]
    assert "not on its own wrong" in seen["body"]
    assert run.outcomes[0].outcome is FixOutcome.FIXED


def test_an_edit_the_first_batch_made_survives_a_retry_that_touched_nothing(
    tmp_path, landed, head, snapshots,
):
    """The reported shape: edited, deferred, retried, and the evidence vanished.

    Attribution is by path against a baseline, so a file already dirty when the
    retry starts is not in the retry's own difference. An implementation that
    kept only the most recent reading — which is what superseding the outcomes
    suggests — would see an empty scope here and report an honest deferral,
    committing the edit underneath it. That is the original defect, reappearing
    in the path most likely to produce it.
    """
    # The first batch dirties a.py; the retry adds nothing of its own.
    readings = iter([set(), {"a.py"}, {"a.py"}, {"a.py"}])
    snapshots.side_effect = lambda *_a, **_k: next(readings, {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["ids"] = [i.id for i in kwargs["items"]]
        return {}

    _run(adapter, verify=run_verify, run_fix=_answer(adapter, tick="deferred"))

    assert seen["ids"] == ["i0"]


def test_a_pass_with_no_gate_still_reports_a_contradiction(
    tmp_path, landed, head, snapshots, capsys,
):
    """CI and pre-push run no gate, and silence there is what the bug looked like.

    The contradiction is established from the tree alone; only settling it needs
    an agent. A domain that opted out of the gate has opted out of the
    resolution, not out of being told.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(adapter, run_fix=_answer(adapter, tick="deferred"))

    assert "check these by hand: i0" in capsys.readouterr().err
    # Reported, not resolved: nothing about the tree says this item is what
    # moved the file, and no gate ran to find out.
    assert run.outcomes[0].outcome is FixOutcome.DEFERRED


# passes-at-base: guards against over-firing, and base fires never
def test_an_item_with_no_file_is_never_contradicted(tmp_path, landed, head, snapshots):
    """An empty anchor must not match a changed path.

    `FixItem.location` already treats a domain with no path to give as legal, so
    the reconciler meets empty anchors in normal use. A containment test that
    counted the empty string as a hit would contradict every such deferral in
    the pass at once.
    """
    snapshots.side_effect = _settles_at(set(), {"a.py"})
    adapter = StubAdapter(tmp_path, count=1)
    adapter.items = lambda: [FixItem(id="i0", file="", line=0, label="no anchor",
                                     body="body")]

    def run_verify(*_a, **_k):
        raise AssertionError("an item with no file was treated as contradicted")

    run, _ = _run(adapter, verify=run_verify,
                  run_fix=_answer(adapter, tick="deferred"))

    assert run.outcomes[0].outcome is FixOutcome.DEFERRED



# ── evidence for a pass that shared its worktree ────────────────────────────


def test_a_pass_records_the_dirt_it_found_before_it_ran(
    tmp_path, landed, head, snapshots,
):
    """The evidence a diagnosis needs, from the one moment it can be taken.

    A fix pass committed into a worktree somebody was editing and the only
    surviving trace was a commit on the branch. Nothing here can refuse that
    case — a dirty tree is the ordinary one for a self-review — so the pass
    records what was already in the tree instead of guessing about it.
    """
    trail = MagicMock()
    snapshots.side_effect = _reads({"theirs.py"}, {"theirs.py", "ours.py"})
    _run(StubAdapter(tmp_path), trail=trail)

    recorded = [c for c in trail.info.call_args_list
                if c.args and c.args[0] == "dirty_baseline"]
    assert len(recorded) == 1
    assert recorded[0].kwargs["data"]["paths"] == ["theirs.py"]


# passes-at-base: the silent case, asserted so the new record cannot start firing on every pass
def test_a_pass_with_the_tree_to_itself_records_no_such_thing(
    tmp_path, landed, head, snapshots,
):
    """The common case says nothing, so the record means something when it
    does appear."""
    trail = MagicMock()
    snapshots.side_effect = _reads(set(), {"ours.py"})
    _run(StubAdapter(tmp_path), trail=trail)

    assert not [c for c in trail.info.call_args_list
                if c.args and c.args[0] == "dirty_baseline"]


# passes-at-base: holds the decision not to refuse — base commits here and must keep doing so
def test_the_pass_still_commits_over_a_tree_it_shares(
    tmp_path, landed, head, snapshots,
):
    """Recorded, never refused. The pre-push pass exists to repair a dirty
    tree and a self-review is documented to run against one; a refusal here
    would break both to guess at a case it cannot identify."""
    snapshots.side_effect = _reads({"theirs.py"}, {"theirs.py", "ours.py"})
    run, _ = _run(StubAdapter(tmp_path))

    assert landed.call_count == 1
    assert run.landed is not None


# ── an interactive session holding the worktree ─────────────────────────────


def _holder(pid=4242, command="pi"):
    """One foreign session, as held_by_others would report it."""
    return core.session_lock.SessionHolder(
        pid=pid,
        started="Tue Sep 29 10:00:00 2026",
        harness="pi",
        session_id="",
        command=command,
    )


def test_a_pass_refuses_a_worktree_an_interactive_session_holds(
    tmp_path, landed, head, capsys,
):
    """No agent, no commit, and the holder named.

    The incident behind #1453 was an investigation because nothing said who
    else was writing. A refusal that does not name the session is the same
    failure with an error message.
    """
    adapter = StubAdapter(tmp_path, count=2)
    with patch.object(core.session_lock, "held_by_others",
                      return_value=[_holder(command="pi --resume")]):
        run, inv = _run(adapter)

    assert inv.call_count == 0
    assert landed.call_count == 0
    assert adapter.recorded is None
    assert run.outcomes == []
    logged = capsys.readouterr().err
    assert "4242" in logged
    assert "pi --resume" in logged
    assert "Tue Sep 29 10:00:00 2026" in logged


def test_a_pass_runs_when_the_only_session_is_its_own(tmp_path, landed, head):
    """The self-exemption, asserted on the pass having actually run.

    held_by_others is what does the exempting, so an empty list here is a
    session that holds the tree and is the caller's own. Asserted on the agent
    running and the work landing rather than on the absence of an error: a
    pass that silently did nothing would satisfy the weaker check.
    """
    adapter = StubAdapter(tmp_path, count=2)
    with patch.object(core.session_lock, "held_by_others",
                      return_value=[]):
        run, inv = _run(adapter)

    assert inv.call_count == 1
    assert landed.call_count == 1
    assert adapter.recorded is not None


def test_the_override_commits_past_a_held_worktree(
    tmp_path, landed, head, monkeypatch,
):
    """The escape hatch the refusal names, for a known-stale session."""
    monkeypatch.setenv(fix.engine._LOCK_OVERRIDE_ENV, "1")
    adapter = StubAdapter(tmp_path, count=2)
    with patch.object(core.session_lock, "held_by_others",
                      return_value=[_holder()]):
        run, inv = _run(adapter)

    assert inv.call_count == 1
    assert landed.call_count == 1


def test_an_unset_override_does_not_count_as_set(
    tmp_path, landed, head, monkeypatch,
):
    """`VAR=` is how a shell exports an override nobody asked for.

    A bare `in os.environ` check would read the empty string as consent, which
    is the difference between a guard and a guard-shaped comment.
    """
    monkeypatch.setenv(fix.engine._LOCK_OVERRIDE_ENV, "")
    adapter = StubAdapter(tmp_path, count=2)
    with patch.object(core.session_lock, "held_by_others",
                      return_value=[_holder()]):
        run, inv = _run(adapter)

    assert inv.call_count == 0
    assert landed.call_count == 0
