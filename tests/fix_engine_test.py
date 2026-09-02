"""Tests for fix_engine — the pipeline every fix pass runs.

The domain halves live with their commands (`ci_check_test.py`,
`test_review_threads.py`). What is held here is the half neither of them owns
any more: how the work is batched, what a retry is handed, and what the landing
is asked for.

The adapter below is a stub rather than a real one so a change to either
domain's items or commit message cannot make these pass or fail — the engine's
contract is with `FixAdapter`, not with CI or comments.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent_invoke  # noqa: E402
import fix_engine  # noqa: E402
import fix_tracking  # noqa: E402
import land  # noqa: E402
from agent_diagnosis import Diagnosis, DiagnosisKind  # noqa: E402
from agent_registry import PHASES  # noqa: E402
from agent_types import Phase  # noqa: E402
from fix_types import FixItem  # noqa: E402
from land import CommitStatus  # noqa: E402
from pr_fix import FixOutcome  # noqa: E402


# ── the stub domain ─────────────────────────────────────────────────────────


class StubAdapter(fix_engine.FixAdapter):
    """A domain that hands over `count` items and records what came back.

    `ci_fix` is borrowed as the phase because its template asks for nothing
    beyond the substitutions the engine supplies — `template_vars` returning
    nothing is then a real statement rather than a stub's convenience.
    """

    phase = Phase.CI_FIX
    title = "Stub Fix Tracking"
    action = "fixing things"
    item_noun = "item"

    def __init__(self, wt_path, count=1, *, spec=None):
        self.workdir = Path(wt_path)
        self.artifacts = self.workdir / "artifacts"
        self.branch = "isaac/feat/x"
        self.repo = "owner/repo"
        self._count = count
        self._spec = spec or fix_engine.LandSpec(message="fix: stub")
        self.recorded = None

    def items(self):
        return [
            FixItem(id=f"i{n}", file="a.py", line=n + 1, label=f"item {n}",
                    body=f"body {n}")
            for n in range(self._count)
        ]

    def template_vars(self):
        return {}

    def landing(self, outcomes):
        self.landing_saw = list(outcomes)
        return self._spec

    def record(self, run):
        self.recorded = run


def _answer(adapter, *, tick="fixed", ids=None):
    """A `run_fix` stub that answers the checklist it finds on disk.

    The engine rewrites the file immediately before each invocation, so an
    answer written any earlier is thrown away before an agent would see it.
    `ids` limits the answer to those items; the rest are left as work owed.
    """
    def run_fix(_phase, _prompt, **_kwargs):
        text = adapter.tracking_path.read_text()
        out = []
        keep = True
        for line in text.splitlines(keepends=True):
            if line.startswith("## <!-- fix:"):
                keep = ids is None or line.split("fix:")[1].split(" ")[0] in ids
            if keep and line.startswith(f"- [ ] {tick}"):
                line = line.replace("- [ ]", "- [x]", 1)
            out.append(line)
        adapter.tracking_path.write_text("".join(out))
        return agent_invoke.FixResult(0, None)
    return run_fix


@pytest.fixture
def landed():
    """Stub the landing owner out; `land_test.py` holds what it really does."""
    with patch.object(fix_engine.land, "land",
                      return_value=land.LandResult(CommitStatus.PUSHED, "abc1234")) as m:
        yield m


@pytest.fixture
def head():
    with patch.object(fix_engine.git_client, "head_sha", return_value="9999999"):
        yield


def _run(adapter, **kwargs):
    with patch.object(fix_engine.agent_invoke, "run_fix",
                      side_effect=kwargs.pop("run_fix", _answer(adapter))) as inv:
        run = fix_engine.run(adapter, **kwargs)
    return run, inv


# ── nothing to do ───────────────────────────────────────────────────────────


def test_a_pass_with_no_items_runs_nothing(tmp_path, landed, head):
    """No agent, no commit, and no record — there was nothing to say."""
    adapter = StubAdapter(tmp_path, count=0)
    run, inv = _run(adapter)

    assert inv.call_count == 0
    assert landed.call_count == 0
    assert adapter.recorded is None
    assert run.landed is None
    assert run.outcomes == []


# ── batching ────────────────────────────────────────────────────────────────


def test_work_over_the_chunk_size_is_split(tmp_path, landed, head):
    """One prompt holding every item is what starved the pass of turns."""
    chunk = fix_engine.agent_phases.phase_chunk_size(StubAdapter.phase)
    adapter = StubAdapter(tmp_path, count=chunk + 1)
    run, inv = _run(adapter)

    assert inv.call_count == 2
    assert run.batches == 2


def test_every_item_is_answered_exactly_once_across_batches(tmp_path, landed, head):
    """A batch rewrites the shared file, so a lost batch would read as deferred."""
    chunk = fix_engine.agent_phases.phase_chunk_size(StubAdapter.phase)
    adapter = StubAdapter(tmp_path, count=chunk + 3)
    run, _ = _run(adapter)

    assert [o.id for o in run.outcomes] == [f"i{n}" for n in range(chunk + 3)]
    assert all(o.outcome is FixOutcome.FIXED for o in run.outcomes)


def test_a_batch_is_named_by_its_position(tmp_path, landed, head):
    """An operator watching the log has to be able to tell them apart."""
    chunk = fix_engine.agent_phases.phase_chunk_size(StubAdapter.phase)
    adapter = StubAdapter(tmp_path, count=chunk + 1)
    _, inv = _run(adapter)

    label = PHASES[StubAdapter.phase].label
    labels = [c.kwargs["label"] for c in inv.call_args_list]
    assert labels == [f"{label} (batch 1/2)", f"{label} (batch 2/2)"]


def test_a_single_batch_is_not_numbered(tmp_path, landed, head):
    _, inv = _run(StubAdapter(tmp_path, count=2))
    assert inv.call_args.kwargs["label"] == PHASES[StubAdapter.phase].label


def test_the_batch_budget_is_sized_to_the_batch(tmp_path, landed, head):
    """The remainder chunk must not be charged for the whole pass's items."""
    chunk = fix_engine.agent_phases.phase_chunk_size(StubAdapter.phase)
    adapter = StubAdapter(tmp_path, count=chunk + 1)
    run, inv = _run(adapter)

    turns = [c.kwargs["max_turns"] for c in inv.call_args_list]
    assert turns[0] == fix_engine.agent_phases.phase_turns(
        StubAdapter.phase, items=chunk)
    assert turns[1] == fix_engine.agent_phases.phase_turns(
        StubAdapter.phase, items=1)
    # The pass reports the largest batch's budget, not the remainder's.
    assert run.max_turns == max(turns)


def test_a_phase_that_bounds_no_chunk_gets_one_batch(tmp_path, landed, head):
    """Zero is "undeclared", not "batch of zero" — which would never terminate."""
    adapter = StubAdapter(tmp_path, count=25)
    with patch.object(fix_engine.agent_phases, "phase_chunk_size", return_value=0):
        run, inv = _run(adapter)

    assert inv.call_count == 1
    assert run.batches == 1


# ── the retry ───────────────────────────────────────────────────────────────


def test_deferred_items_are_handed_back_for_a_second_look(tmp_path, landed, head):
    """An item the agent never got to is work still owed, not a verdict."""
    adapter = StubAdapter(tmp_path, count=3)
    calls = []

    def run_fix(phase, prompt, **kwargs):
        calls.append(prompt)
        answered = ["i0"] if len(calls) == 1 else ["i1", "i2"]
        return _answer(adapter, ids=answered)(phase, prompt, **kwargs)

    run, inv = _run(adapter, run_fix=run_fix)

    assert inv.call_count == 2
    assert {o.id for o in run.outcomes if o.outcome is FixOutcome.FIXED} == {
        "i0", "i1", "i2",
    }


def test_the_retry_is_handed_only_what_is_left(tmp_path, landed, head):
    """A retry re-reading settled work spends a budget raised for the rest."""
    adapter = StubAdapter(tmp_path, count=3)
    seen = []

    def run_fix(phase, prompt, **kwargs):
        seen.append(fix_tracking.parse(adapter.tracking_path))
        return _answer(adapter, ids=["i0"])(phase, prompt, **kwargs)

    _run(adapter, run_fix=run_fix)

    assert [o.id for o in seen[0]] == ["i0", "i1", "i2"]
    assert [o.id for o in seen[1]] == ["i1", "i2"]


def test_the_retry_says_it_is_one(tmp_path, landed, head):
    """Without it the agent reads a short file as the whole job and stops early."""
    adapter = StubAdapter(tmp_path, count=2)
    prompts = []

    def run_fix(phase, prompt, **kwargs):
        prompts.append(prompt)
        return _answer(adapter, ids=["i0"])(phase, prompt, **kwargs)

    _run(adapter, run_fix=run_fix)

    assert not prompts[0].startswith(fix_engine._RESUME_HINT)
    assert prompts[1].startswith(fix_engine._RESUME_HINT)


def test_a_settled_verdict_is_not_retried(tmp_path, landed, head):
    """Declined and needs-a-person are answers; only deferral is an absence."""
    adapter = StubAdapter(tmp_path, count=2)
    run, inv = _run(adapter, run_fix=_answer(adapter, tick="declined"))

    assert inv.call_count == 1
    assert all(o.outcome is FixOutcome.DECLINED for o in run.outcomes)


def test_an_entry_no_item_stands_behind_survives_the_retry(tmp_path, landed, head):
    """A section the pass cannot re-ask is still an answer the file gave.

    The retry replaces the checklist with the items it re-asks, so an entry
    dropped here is one the record never hears about at all.
    """
    adapter = StubAdapter(tmp_path, count=2)
    first = [True]

    def run_fix(phase, prompt, **kwargs):
        if first[0]:
            first[0] = False
            text = adapter.tracking_path.read_text().replace("fix:i1", "fix:ghost")
            adapter.tracking_path.write_text(text)
            return agent_invoke.FixResult(0, None)
        return _answer(adapter)(phase, prompt, **kwargs)

    run, inv = _run(adapter, run_fix=run_fix)

    assert inv.call_count == 2
    assert {o.id for o in run.outcomes} == {"i0", "ghost"}


def test_a_stalled_batch_has_already_had_its_retry(tmp_path, landed, head):
    """`run_fix` retries an unproductive pass itself — a third run is waste."""
    adapter = StubAdapter(tmp_path, count=2)

    def run_fix(_phase, _prompt, **_kwargs):
        return agent_invoke.FixResult(0, Diagnosis(DiagnosisKind.MAX_TURNS))

    run, inv = _run(adapter, run_fix=run_fix)

    assert inv.call_count == 1
    assert all(o.outcome is FixOutcome.DEFERRED for o in run.outcomes)


def test_one_stalled_batch_does_not_cost_the_others_their_retry(tmp_path, landed, head):
    """The bug the partition exists for: a stalled batch swallowing the retry."""
    chunk = fix_engine.agent_phases.phase_chunk_size(StubAdapter.phase)
    adapter = StubAdapter(tmp_path, count=chunk + 1)
    calls = []

    def run_fix(phase, prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return agent_invoke.FixResult(0, Diagnosis(DiagnosisKind.MAX_TURNS))
        return _answer(adapter)(phase, prompt, **kwargs)

    run, inv = _run(adapter, run_fix=run_fix)

    # Batch one stalled and stays deferred; batch two answered, so nothing is
    # left to retry — three calls here would mean the stall was retried.
    assert inv.call_count == 2
    by_id = {o.id: o.outcome for o in run.outcomes}
    assert by_id["i0"] is FixOutcome.DEFERRED
    assert by_id[f"i{chunk}"] is FixOutcome.FIXED


# ── the landing ─────────────────────────────────────────────────────────────


def test_the_domain_s_spec_reaches_the_land_owner(tmp_path, landed, head):
    adapter = StubAdapter(tmp_path, spec=fix_engine.LandSpec(
        message="fix: the thing", regen="chore: regenerate",
    ))
    _run(adapter)

    kwargs = landed.call_args.kwargs
    assert kwargs["message"] == "fix: the thing"
    assert kwargs["regen"] == "chore: regenerate"
    assert kwargs["gated"] is True


def test_the_commit_is_always_gated(tmp_path, landed, head):
    """Not a parameter: a fix pass may not publish what nobody approved."""
    _run(StubAdapter(tmp_path))
    assert landed.call_args.kwargs["gated"] is True


def test_recovery_compares_against_the_head_the_pass_started_from(tmp_path, landed, head):
    """The one thing a domain assembling a LandSpec cannot know for itself."""
    adapter = StubAdapter(tmp_path, spec=fix_engine.LandSpec(
        message="fix: the thing", recover=True,
    ))
    _run(adapter)

    assert landed.call_args.kwargs["recover_from"] == "9999999"


def test_a_pass_that_wants_no_recovery_asks_for_none(tmp_path, landed, head):
    _run(StubAdapter(tmp_path))
    assert landed.call_args.kwargs["recover_from"] is None


def test_the_spec_is_written_against_the_settled_outcomes(tmp_path, landed, head):
    """A message counting the first pass's deferrals would overstate them."""
    adapter = StubAdapter(tmp_path, count=2)
    calls = []

    def run_fix(phase, prompt, **kwargs):
        calls.append(prompt)
        answered = ["i0"] if len(calls) == 1 else ["i1"]
        return _answer(adapter, ids=answered)(phase, prompt, **kwargs)

    _run(adapter, run_fix=run_fix)

    assert all(o.outcome is FixOutcome.FIXED for o in adapter.landing_saw)


# ── what the record is handed ───────────────────────────────────────────────


def test_a_fix_is_anchored_to_the_commit_it_landed_in(tmp_path, landed, head):
    run, _ = _run(StubAdapter(tmp_path, count=2))

    assert all(o.commit_sha == "abc1234" for o in run.outcomes)
    assert all(o.read_sha == "9999999" for o in run.outcomes)


def test_a_verdict_that_changed_nothing_carries_no_commit(tmp_path, landed, head):
    """A declined item is not in the commit, so naming it would be a false claim."""
    adapter = StubAdapter(tmp_path, count=1)
    run, _ = _run(adapter, run_fix=_answer(adapter, tick="declined"))

    assert run.outcomes[0].commit_sha == ""
    assert run.outcomes[0].read_sha == "9999999"


def test_the_record_gets_what_the_caller_gets(tmp_path, landed, head):
    """One object, so a caller reads the outcome without a second channel."""
    adapter = StubAdapter(tmp_path)
    run, _ = _run(adapter)

    assert adapter.recorded is run
    assert run.landed.sha == "abc1234"
    assert run.head_before == "9999999"


def test_a_backend_failure_reaches_the_caller(tmp_path, landed, head):
    """Ticking boxes and crashing is not a clean pass, whatever the file says."""
    adapter = StubAdapter(tmp_path)

    def run_fix(phase, prompt, **kwargs):
        _answer(adapter)(phase, prompt, **kwargs)
        return agent_invoke.FixResult(2, None)

    run, _ = _run(adapter, run_fix=run_fix)

    assert run.exit_code == 2


def test_the_worst_batch_s_exit_code_wins(tmp_path, landed, head):
    """A clean second batch must not paper over a first one that died."""
    chunk = fix_engine.agent_phases.phase_chunk_size(StubAdapter.phase)
    adapter = StubAdapter(tmp_path, count=chunk + 1)
    calls = []

    def run_fix(phase, prompt, **kwargs):
        calls.append(prompt)
        _answer(adapter)(phase, prompt, **kwargs)
        return agent_invoke.FixResult(3 if len(calls) == 1 else 0, None)

    run, _ = _run(adapter, run_fix=run_fix)

    assert run.exit_code == 3


# ── what the agent is handed ────────────────────────────────────────────────


def test_the_prompt_carries_the_checklist_and_where_it_lives(tmp_path, landed, head):
    adapter = StubAdapter(tmp_path, count=1)
    _, inv = _run(adapter)

    prompt = inv.call_args.args[1]
    assert "## <!-- fix:i0 --> a.py:1 — item 0" in prompt
    assert str(adapter.tracking_path) in prompt
    assert "${" not in prompt


def test_the_prompt_asks_for_the_boxes_the_checklist_actually_has(tmp_path, landed, head):
    """The format is described once, by the module that writes and reads it.

    A template that spells the ask itself drifts from the parse the moment a box
    is renamed, and the failure is silent: nothing ticks, and every item comes
    back as work still owed. Only the domain's word for one item is the domain's.
    """
    adapter = StubAdapter(tmp_path, count=1)
    _, inv = _run(adapter)

    prompt = inv.call_args.args[1]
    assert fix_tracking.instructions(adapter.item_noun) in prompt


def test_the_session_log_sits_beside_the_checklist(tmp_path, landed, head):
    """Both are the pass's artifacts, so an operator finds them together."""
    adapter = StubAdapter(tmp_path)
    _, inv = _run(adapter)

    assert inv.call_args.kwargs["session_log"] == str(adapter.session_log)
    assert adapter.session_log.parent == adapter.tracking_path.parent


def test_a_ticked_box_is_what_counts_as_work(tmp_path, landed, head):
    """The guard's `produced` — an untouched file is a pass that did nothing."""
    adapter = StubAdapter(tmp_path, count=1)
    _, inv = _run(adapter)
    produced = inv.call_args.kwargs["produced"]

    fix_tracking.write(adapter.tracking_path, adapter.title, adapter.items())
    assert produced() is False
    _answer(adapter)(None, None)
    assert produced() is True


def test_the_domain_s_own_hint_wins_over_the_diagnosis(tmp_path, landed, head):
    """`hint_for` is written for a phase producing a file, not answering one."""
    adapter = StubAdapter(tmp_path)
    adapter.fix_hint = "DO THE THING"
    _, inv = _run(adapter)

    select = inv.call_args.kwargs["hint_select"]
    assert select(Diagnosis(DiagnosisKind.MAX_TURNS)) == "DO THE THING"
    assert select(None) == "DO THE THING"


def test_the_agent_reads_where_the_domain_says_it_may(tmp_path, landed, head):
    adapter = StubAdapter(tmp_path)
    adapter.add_dirs = lambda: [adapter.workdir, Path("/elsewhere")]
    _, inv = _run(adapter)

    assert inv.call_args.kwargs["add_dirs"] == [adapter.workdir, Path("/elsewhere")]


def test_the_pass_is_billed_to_the_phase_and_the_pr(tmp_path, landed, head):
    """The usage ledger keys on all three; an empty PR must not reach it as ''."""
    adapter = StubAdapter(tmp_path)
    adapter.pr = "42"
    _, inv = _run(adapter)

    assert inv.call_args.args[0] is StubAdapter.phase
    assert inv.call_args.kwargs["repo"] == "owner/repo"
    assert inv.call_args.kwargs["pr"] == "42"


def test_a_pass_off_a_pr_names_no_pr(tmp_path, landed, head):
    _, inv = _run(StubAdapter(tmp_path))
    assert inv.call_args.kwargs["pr"] is None
