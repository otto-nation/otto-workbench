"""Tests for fix.engine's verify gate — what the gate is asked, what its
verdicts do to the agent's claims, the unevidenced and unreasoned claims
reported to the trail, and the domain's after-verify hook.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.engine  # noqa: E402
import fix.gate  # noqa: E402
import git.land  # noqa: E402
from agent.registry import PHASES  # noqa: E402
from core.phases import Phase  # noqa: E402
from git.land import CommitStatus  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402

from fix_engine_support import StubAdapter, _answer, landed, head, _run, _verdicts
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401


def test_a_reasoned_decline_is_put_to_the_gate(tmp_path, landed, head):
    """A decline is a claim nobody checked, so it goes where claims are checked.

    The failure behind this: a pass fixed a finding, then ticked `declined`
    describing the tree its own edit had just produced. Staging reads the
    worktree rather than the boxes, so the edit was committed and the fix
    shipped recorded as "not a defect".
    """
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["ids"] = [i.id for i in kwargs["items"]]
        seen["body"] = kwargs["items"][0].body
        return {}

    _run(adapter, verify=run_verify,
         run_fix=_answer(adapter, tick="declined",
                         reason="the code already does this"))

    assert seen["ids"] == ["i0"]
    assert f"rejected this {adapter.item_noun}" in seen["body"]
    assert "rejected this finding" not in seen["body"]
    assert "the code already does this" in seen["body"]


def test_a_decline_the_gate_falsifies_becomes_a_person_s_call(tmp_path, landed, head):
    """A decline disproved is not a decline; it is an item nobody has settled."""
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(
            ok=False, detail="true only with the pass's own diff applied"))),
        run_fix=_answer(adapter, tick="declined",
                        reason="the code already does this"),
    )

    assert run.outcomes[0].outcome is FixOutcome.NEEDS_HUMAN
    assert run.outcomes[0].reason == "true only with the pass's own diff applied"


def test_a_decline_the_gate_upholds_stays_declined(tmp_path, landed, head):
    """The negative control: an honest decline survives the gate unchanged."""
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=True, detail="scope holds"))),
        run_fix=_answer(adapter, tick="declined", reason="out of scope here"),
    )

    assert run.outcomes[0].outcome is FixOutcome.DECLINED
    assert run.outcomes[0].reason == "out of scope here"


def test_a_decline_with_no_reason_is_not_put_to_the_gate(tmp_path, landed, head):
    """There is no claim to check, and `_record_unevidenced` reports it instead."""
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, **kwargs):
        seen["ids"] = [i.id for i in kwargs["items"]]
        return {}

    _run(adapter, verify=run_verify, run_fix=_answer(adapter, tick="declined"))

    assert "ids" not in seen


def test_a_fix_the_gate_falsifies_does_not_reach_the_commit(tmp_path, landed, head):
    """The defect this gate exists for: a wrong fix reported as fixed.

    Demotion happens before `landing` is asked for a spec, so the outcome the
    domain records and the outcome the commit carries cannot disagree.
    """
    adapter = StubAdapter(tmp_path, count=1)
    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=False, detail="repro still exits 3"))),
    )

    assert run.outcomes[0].outcome is FixOutcome.NEEDS_HUMAN
    assert "repro still exits 3" in run.outcomes[0].reason
    assert all(o.outcome is not FixOutcome.FIXED for o in adapter.landing_saw)


def test_a_fix_the_gate_confirms_stays_fixed(tmp_path, landed, head):
    adapter = StubAdapter(tmp_path, count=1)
    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix.gate.Verdict(ok=True, detail="suite green"))),
    )

    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].verified is True


def test_a_fix_the_gate_cannot_judge_stays_fixed_but_unverified(tmp_path, landed, head):
    """Inconclusive is not falsified.

    Blocking every fix the gate cannot exercise would make the pass useless on
    any project without a runnable check. The fix stands; what is withheld is
    the claim that anything ran.
    """
    adapter = StubAdapter(tmp_path, count=1)
    run, _ = _run(adapter, verify=_verdicts(("i0", fix.gate.Verdict(ok=None, detail="no runnable check"))))

    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].verified is False
    assert "no runnable check" in run.outcomes[0].verify_detail


def test_an_id_the_gate_never_answered_is_unverified_not_falsified(tmp_path, landed, head):
    """Silence is not a verdict.

    An agent that answers two of three items has not falsified the third, and
    demoting on absence would punish a fix for the gate running out of turns.
    """
    adapter = StubAdapter(tmp_path, count=2)
    run, _ = _run(adapter, verify=_verdicts(("i0", fix.gate.Verdict(ok=True))))

    by_id = {o.id: o for o in run.outcomes}
    assert by_id["i1"].outcome is FixOutcome.FIXED
    assert by_id["i1"].verified is False


def test_only_fixed_items_are_sent_to_the_gate(tmp_path, landed, head):
    """Verifying a declined item asks the gate about work nobody did."""
    adapter = StubAdapter(tmp_path, count=2)
    seen = {}

    def run_verify(_phase, _prompt, *, items=None, **_kwargs):
        seen["ids"] = [i.id for i in (items or [])]
        return {}

    _run(adapter, verify=run_verify, run_fix=_answer(adapter, ids={"i0"}))

    assert seen["ids"] == ["i0"]


def test_no_fixed_items_means_the_gate_never_runs(tmp_path, landed, head):
    """A pass that fixed nothing has nothing to verify, and pays for nothing."""
    adapter = StubAdapter(tmp_path, count=1)
    calls = []

    def run_verify(*a, **k):
        calls.append(1)
        return {}

    _run(adapter, verify=run_verify, run_fix=_answer(adapter, tick="declined"))

    assert calls == []


def test_the_gate_is_off_by_default(tmp_path, landed, head):
    """Opt-in, like the disprove gate it mirrors.

    Every pass sharing this engine would otherwise start paying for an extra
    agent call the moment this lands.
    """
    adapter = StubAdapter(tmp_path, count=1)
    run, _ = _run(adapter)

    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].verified is None, "a pass that never gated claims nothing either way"
    assert run.outcomes[0].verify_detail == ""


def test_the_gate_is_told_what_the_reviewer_asked_for(tmp_path, landed, head):
    """The gate judges a fix against the ask, so it has to be given the ask.

    An outcome carries a location and a verdict: `parse` reads the anchor back
    out of the section heading and never the label, so a gate handed only
    outcomes sees `a.py:1` and three empty boxes. Its own prompt opens by
    telling it to run the reviewer's repro — which is in the body.
    """
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, *, items=None, **_kwargs):
        seen["items"] = list(items or [])
        return {}

    _run(adapter, verify=run_verify)

    assert seen["items"][0].body.startswith("body 0"), (
        "the reviewer's words never reached the gate"
    )
    assert seen["items"][0].label == "item 0"


def test_the_gate_looks_where_the_fix_landed(tmp_path, landed, head):
    """The anchor is the outcome's, since the agent may have moved the code."""
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, *, items=None, **_kwargs):
        seen["items"] = list(items or [])
        return {}

    _run(adapter, verify=run_verify)

    assert seen["items"][0].file == "a.py"
    assert seen["items"][0].line == 1


# ── The claim the fix pass made reaches the gate ────────────────────────────
#
# The fix box asks what test holds the change. Parsing that into
# `ItemOutcome.reason` and never showing it to the gate would leave the claim
# unchecked by anything — which is the whole reason the box asks.


def test_the_gate_is_told_what_the_fix_pass_claimed(tmp_path, landed, head):
    """The named test reaches the gate, or nothing ever checks the claim."""
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, *, items=None, **_kwargs):
        seen["items"] = list(items or [])
        return {}

    _run(adapter, verify=run_verify,
         run_fix=_answer(adapter, reason="test_foo_rejects_an_empty_name"))

    body = seen["items"][0].body
    assert "test_foo_rejects_an_empty_name" in body
    assert fix.gate._CLAIM_HEADING in body


def test_the_reviewers_words_survive_beside_the_claim(tmp_path, landed, head):
    """The claim is appended, not substituted.

    The gate judges the fix against what was asked for and checks the claim
    besides. A claim that replaced the ask would trade one blind spot for the
    other.
    """
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, *, items=None, **_kwargs):
        seen["items"] = list(items or [])
        return {}

    _run(adapter, verify=run_verify, run_fix=_answer(adapter, reason="test_bar"))

    body = seen["items"][0].body
    assert "body 0" in body
    assert "test_bar" in body
    # The ask first, the claim under it: the claim sits immediately above the
    # verdict boxes that answer it.
    assert body.index("body 0") < body.index("test_bar")


def test_a_fix_with_no_claim_says_so_to_the_gate(tmp_path, landed, head):
    """Silence is reported as silence, not rendered as nothing.

    A gate shown no claim block cannot tell "the pass was never asked" from
    "the pass was asked and answered nothing", and only the second is worth
    reporting to an operator.
    """
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(_phase, _prompt, *, items=None, **_kwargs):
        seen["items"] = list(items or [])
        return {}

    # No reason= : the agent ticked the box and left `<why>` standing.
    _run(adapter, verify=run_verify, run_fix=_answer(adapter))

    body = seen["items"][0].body
    assert fix.gate._CLAIM_HEADING not in body
    assert "named nothing that holds this change" in body
    # And it is not on its own grounds to call the fix broken.
    assert "not on its own a reason to call the fix broken" in body


def test_an_id_the_pass_never_handed_out_still_carries_its_claim():
    """The source-less branch is the one that is easy to forget.

    `_verify_item` falls back to the outcome alone for an id the pass answered
    but never handed out. The claim comes from the outcome, so it is exactly
    the half that should still arrive.
    """
    outcome = ItemOutcome(id="x", file="a.py", line=2,
                          outcome=FixOutcome.FIXED, reason="test_orphan")

    item = fix.gate._verify_item(outcome, None, StubAdapter.item_noun)

    assert "test_orphan" in item.body
    assert fix.gate._CLAIM_HEADING in item.body


def test_the_gate_is_asked_under_its_own_phase(tmp_path, landed, head):
    """The gate renders its own template, not the fix pass's.

    Sizing and prompting both key off the phase handed to the verify function.
    Passing the fix pass's phase gave the gate `fix-comments.md` — a prompt
    telling it to edit source, which its own rules forbid.
    """
    adapter = StubAdapter(tmp_path, count=1)
    adapter.verify_phase = Phase.COMMENTS_VERIFY
    seen = {}

    def run_verify(phase, _prompt, **_kwargs):
        seen["phase"] = phase
        return {}

    _run(adapter, verify=run_verify)

    assert seen["phase"] is Phase.COMMENTS_VERIFY
    assert PHASES[seen["phase"]].template_for() == "verify-fixes.md"


def test_a_domain_that_declares_no_gate_phase_still_runs(tmp_path, landed, head):
    """The fallback keeps a domain without a declared gate phase working."""
    adapter = StubAdapter(tmp_path, count=1)
    seen = {}

    def run_verify(phase, _prompt, **_kwargs):
        seen["phase"] = phase
        return {}

    _run(adapter, verify=run_verify)

    assert seen["phase"] is adapter.phase


# ── the unevidenced claim ───────────────────────────────────────────────────


class _RecordingTrail:
    """A trail that keeps what it was told, so a test can read it back."""

    def __init__(self):
        self.events = []

    def info(self, action, detail, data=None):
        self.events.append(("info", action, detail, data or {}))

    def warn(self, action, detail, data=None):
        self.events.append(("warn", action, detail, data or {}))

    def error(self, action, detail, data=None):
        self.events.append(("error", action, detail, data or {}))


def _warned(trail):
    return [e for e in trail.events if e[0] == "warn" and e[1] == "fix_unevidenced"]


def test_a_fix_claimed_without_evidence_reaches_the_trail(tmp_path, landed, head):
    """The durable half of the record, which stderr alone does not give.

    A FIXED box ticked with the `<why>` placeholder still standing is read as
    fixed, so the pass claims work it offered nothing to support. That claim is
    the one an operator most wants to audit later, and before this it existed
    only as a line on a terminal nobody kept.
    """
    adapter = StubAdapter(tmp_path, count=1)
    trail = _RecordingTrail()

    _run(adapter, trail=trail, run_fix=_answer(adapter))

    warned = _warned(trail)
    assert len(warned) == 1
    assert warned[0][3]["items"] == ["i0"]


def _unreasoned(trail):
    return [e for e in trail.events if e[0] == "warn" and e[1] == "fix_unreasoned"]


def test_a_decline_with_no_reason_reaches_the_trail(tmp_path, landed, head):
    """The weakest-evidence outcome the vocabulary has, and it said nothing.

    A decline rejects a reviewer's finding on the pass's say-so and leaves no
    diff to read instead, so the reason is the whole of the record. Ticked with
    the placeholder standing it renders as a bare `*(declined)*` against a
    finding nobody acted on — which `_record_unevidenced` did not cover, because
    it asked only about FIXED.
    """
    adapter = StubAdapter(tmp_path, count=1)
    trail = _RecordingTrail()

    _run(adapter, trail=trail, run_fix=_answer(adapter, tick="declined"))

    warned = _unreasoned(trail)
    assert len(warned) == 1
    assert warned[0][3]["items"] == ["i0"]


def test_a_reasoned_decline_is_not_reported_as_unreasoned(tmp_path, landed, head):
    """The negative control: an ordinary decline must not fire the event."""
    adapter = StubAdapter(tmp_path, count=1)
    trail = _RecordingTrail()

    _run(adapter, trail=trail,
         run_fix=_answer(adapter, tick="declined", reason="out of scope here"))

    assert _unreasoned(trail) == []


def test_a_needs_a_person_with_no_reason_reaches_the_trail(tmp_path, landed, head):
    """The other outcome that closes an item without changing anything."""
    adapter = StubAdapter(tmp_path, count=1)
    trail = _RecordingTrail()

    _run(adapter, trail=trail, run_fix=_answer(adapter, tick="needs a person"))

    assert _unreasoned(trail)[0][3]["items"] == ["i0"]


def test_an_unevidenced_fix_is_not_reported_as_unreasoned(tmp_path, landed, head):
    """The two events stay distinct: a fix with no test is not a bare rejection."""
    adapter = StubAdapter(tmp_path, count=1)
    trail = _RecordingTrail()

    _run(adapter, trail=trail, run_fix=_answer(adapter))

    assert _warned(trail)
    assert _unreasoned(trail) == []


def test_an_evidenced_fix_is_not_reported_as_unevidenced(tmp_path, landed, head):
    """The negative control: the event must not fire on the ordinary path."""
    adapter = StubAdapter(tmp_path, count=1)
    trail = _RecordingTrail()

    _run(adapter, trail=trail,
         run_fix=_answer(adapter, reason="covered by test_x"))

    assert _warned(trail) == []


def test_every_unevidenced_item_is_named_not_just_counted(tmp_path, landed, head):
    """One event per pass, naming each item — a count alone is not auditable."""
    adapter = StubAdapter(tmp_path, count=3)
    trail = _RecordingTrail()

    _run(adapter, trail=trail, run_fix=_answer(adapter))

    warned = _warned(trail)
    assert len(warned) == 1
    assert warned[0][3]["items"] == ["i0", "i1", "i2"]


def test_a_pass_with_no_trail_still_runs(tmp_path, landed, head):
    """The trail is optional everywhere else in the engine; it stays so here."""
    adapter = StubAdapter(tmp_path, count=1)

    run, _ = _run(adapter, run_fix=_answer(adapter))

    assert [o.outcome for o in run.outcomes] == [FixOutcome.FIXED]


class TestAfterVerifyRunsBeforeTheCommit:
    """The hook's whole value is where it sits in the order.

    A domain uses it to close the publishing gate on what the verify gate just
    decided. `land` reads that gate, so a hook called after the landing would
    be a hook that changed nothing — the push would already have gone.
    """

    def test_the_hook_sees_the_final_outcomes(self, tmp_path, landed, head):
        adapter = StubAdapter(tmp_path)
        seen = []
        adapter.after_verify = lambda outcomes: seen.append(list(outcomes))

        _run(adapter)

        assert len(seen) == 1
        assert [o.outcome for o in seen[0]] == [FixOutcome.FIXED]

    def test_the_hook_precedes_the_landing(self, tmp_path, landed, head):
        """Ordering asserted directly, not inferred from a side effect."""
        adapter = StubAdapter(tmp_path)
        calls = []
        adapter.after_verify = lambda outcomes: calls.append("after_verify")
        landed.side_effect = lambda *a, **k: (
            calls.append("land")
            or git.land.LandResult(CommitStatus.PUSHED, "abc1234")
        )

        _run(adapter)

        assert calls == ["after_verify", "land"]

    def test_the_hook_sees_a_falsified_fix_as_needs_human(self, tmp_path, landed, head):
        """The case the comments domain holds on.

        The gate demotes a falsified fix out of FIXED before this runs, which
        is what the domain's hold reads. A hook placed before `_verify` would
        see FIXED here and hold nothing.
        """
        adapter = StubAdapter(tmp_path)
        seen = []
        adapter.after_verify = lambda outcomes: seen.append(list(outcomes))

        _run(adapter, verify=_verdicts(
            ("i0", fix.gate.Verdict(ok=False, detail="the repro fails"))))

        assert [o.outcome for o in seen[0]] == [FixOutcome.NEEDS_HUMAN]

    def test_the_default_hook_is_a_no_op(self, tmp_path, landed, head):
        """Every other domain runs the same engine and must be unaffected."""
        adapter = StubAdapter(tmp_path)
        _run(adapter)
        assert adapter.recorded is not None
