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
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from agent import invoke as agent_invoke  # noqa: E402
from fix import engine as fix_engine  # noqa: E402
from fix import gate as fix_gate  # noqa: E402
from fix import tracking as fix_tracking  # noqa: E402
from git import land  # noqa: E402
from agent.diagnosis import Diagnosis, DiagnosisKind  # noqa: E402
from agent.registry import PHASES  # noqa: E402
from core.phases import Phase  # noqa: E402
from fix.types import FixItem  # noqa: E402
from git.land import CommitStatus  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402


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

    def landing(self, outcomes, changed):
        self.landing_saw = list(outcomes)
        self.landing_scope = changed
        return self._spec

    def record(self, run):
        self.recorded = run


def _answer(adapter, *, tick="fixed", ids=None, reason=None):
    """A `run_fix` stub that answers the checklist it finds on disk.

    The engine rewrites the file immediately before each invocation, so an
    answer written any earlier is thrown away before an agent would see it.
    `ids` limits the answer to those items; the rest are left as work owed.

    `reason` writes the agent's words after the tick, replacing the `<why>` the
    render leaves there. Left unset, the placeholder stands and the parse reads
    an empty reason — the evidence-less path, which is what an agent that ticks
    the box and says nothing produces.
    """
    def run_fix(_phase, _prompt, **_kwargs):
        text = adapter.tracking_path.read_text()
        out = []
        keep = True
        for line in text.splitlines(keepends=True):
            if line.startswith("## <!-- fix:"):
                keep = ids is None or line.split("fix:")[1].split(" ")[0] in ids
            if keep and line.startswith(f"- [ ] {tick}"):
                line = (f"- [x] {tick} — {reason}\n" if reason is not None
                        else line.replace("- [ ]", "- [x]", 1))
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


@pytest.fixture(autouse=True)
def snapshots():
    """An empty worktree before the agent and after it, unless a test says otherwise.

    Autouse because every run now reads the dirty set on both sides of the
    agent, and `tmp_path` is not a repo — an unstubbed read fails, which the
    engine correctly treats as a reason not to run the pass at all.
    """
    with patch.object(fix_engine.fix_scope, "changed_files",
                      return_value=set()) as m:
        yield m


# How many times a one-batch pass reads the worktree: the shared baseline, the
# reading that closes the batch's own observation, and the final one the commit
# scope is taken from. Named because the tests below drive `changed_files` by a
# list of answers, and a list the wrong length fails as a StopIteration inside
# mock rather than as anything about fix passes.
PASS_READS = 3


def _reads(baseline, *rest):
    """Snapshot answers for a one-batch pass, padded to the reads it makes.

    A test cares about the baseline and the final reading — the difference
    between them is the commit scope. The batch-level reading in between is the
    engine's own bookkeeping, and every test here would otherwise have to
    restate it to keep the list long enough.

    The last answer given is repeated to fill, so a test naming two readings
    gets its second one at the position the commit scope is taken from. Each
    repetition is its own set object — callers only ever combine a reading
    with `-`/`|`, but a copy per slot keeps a future mutating caller from
    corrupting every other stubbed reading in the list.

    Sized to `PASS_READS`, which is a one-batch pass's own read count — a
    multi-batch or retried pass reads the tree more times than this pads for,
    and needs `_settles_at` instead.
    """
    answers = [baseline, *rest]
    padding = PASS_READS - len(answers) + 1
    return [*answers[:-1], *(_copy(answers[-1]) for _ in range(padding))]


def _copy(reading):
    """A fresh copy of a stubbed `changed_files` reading, or `None` unchanged."""
    return set(reading) if reading is not None else None


def _settles_at(baseline, final):
    """A snapshot stub that answers `baseline` once and `final` ever after.

    For a pass whose read count is not fixed in advance. A deferral triggers the
    retry, which is a second invocation with two readings of its own, so a test
    about deferrals cannot state a list of the right length without encoding how
    many times the engine retries — which is not what those tests are about, and
    would fail them for a change to the retry rather than to reconciliation.

    The worktree it describes is one an agent edited once and then left alone:
    every reading after the first shows the same difference from the baseline.
    Each reading returned is its own set object, so a caller that mutated one
    in place could not corrupt a later stubbed reading.
    """
    answers = iter([baseline])
    return lambda *_a, **_k: _copy(next(answers, final))


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


def test_a_productive_turn_limit_is_recorded_without_stalling(
    tmp_path, landed, head,
):
    """One ticked box is still production, and the pass still owes the rest."""
    adapter = StubAdapter(tmp_path, count=2)
    stop = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)

    def run_fix(phase, prompt, **kwargs):
        _answer(adapter, ids={"i0"})(phase, prompt, **kwargs)
        return agent_invoke.FixResult(0, None, stop=stop)

    run, inv = _run(adapter, run_fix=run_fix)

    assert run.stop == stop
    assert adapter.stop == stop
    # unproductive stays None, so the deferred remainder still gets its retry.
    assert inv.call_count == 2


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


def test_deferred_work_over_the_chunk_size_is_retried_in_batches(
    tmp_path, landed, head,
):
    """Sending every leftover in one invoke is how a retry starved at ~2/item."""
    adapter = StubAdapter(tmp_path, count=7)
    first_pass = []
    retries = []

    def run_fix(phase, prompt, **kwargs):
        ids = [o.id for o in fix_tracking.parse(adapter.tracking_path)]
        if prompt.startswith(fix_engine._RESUME_HINT):
            retries.append(ids)
            return _answer(adapter)(phase, prompt, **kwargs)
        first_pass.append(ids)
        return _answer(adapter, ids=[ids[0]])(phase, prompt, **kwargs)

    with patch.object(fix_engine.agent_phases, "phase_chunk_size", return_value=2):
        _run(adapter, run_fix=run_fix)

    assert first_pass == [["i0", "i1"], ["i2", "i3"], ["i4", "i5"], ["i6"]]
    assert retries == [["i1", "i3"], ["i5"]]


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


def test_the_domain_s_scope_reaches_the_land_owner(tmp_path, landed, head):
    """The paths a domain names are the paths that get staged.

    Asserted because the omission is silent: `paths=None` is a legal spec and
    stages the whole tree, so a domain whose scope was dropped on the way down
    commits everything dirty in the worktree and reports success.
    """
    adapter = StubAdapter(tmp_path, spec=fix_engine.LandSpec(
        message="fix: the thing", paths={"a.py"},
    ))
    _run(adapter)

    assert landed.call_args.kwargs["paths"] == {"a.py"}


def test_the_engine_hands_the_domain_what_the_agent_changed(
    tmp_path, landed, head, snapshots,
):
    """The snapshot difference, not either snapshot on its own.

    The engine takes both readings because it is the only layer that sees the
    two moments that bracket the agent — a domain taking its own baseline can
    take it late and attribute somebody else's dirt to its agent.
    """
    snapshots.side_effect = _reads({"theirs.py"}, {"theirs.py", "ours.py"})
    adapter = StubAdapter(tmp_path)
    _run(adapter)

    assert adapter.landing_scope == {"ours.py"}


def test_every_verify_chunk_is_taken_back_out_of_the_batch_scope(
    tmp_path, landed, head, snapshots,
):
    """The subtraction globs the chunk files; it does not name one.

    A gate that ran in two chunks leaves two checklists. Subtracting only the
    first lets the second read as a code change the agent made, and the item
    anchored at that file can then contradict itself — a verdict derived from
    the checklist rather than from the fix. The failure is silent, so this
    pins the difference rather than the reconciler's eventual answer.
    """
    adapter = StubAdapter(tmp_path)
    adapter.artifacts.mkdir(parents=True, exist_ok=True)
    for chunk in (1, 2):
        adapter.verify_tracking_path(chunk).write_text("## answers\n")
    inside = {
        fix_engine._relative_to(adapter.workdir, adapter.verify_tracking_path(n))
        for n in (1, 2)
    }
    assert all(inside), "the stub's artifacts must sit inside the worktree"
    snapshots.return_value = {"ours.py", *inside}

    scope = fix_engine._batch_scope(adapter, before=set())

    assert scope.files == {"ours.py"}


def test_an_unreadable_second_snapshot_reaches_the_domain_as_none(
    tmp_path, landed, head, snapshots,
):
    """None is not an empty set, and only the domain can say what to do with it."""
    snapshots.side_effect = _reads(set(), None)
    adapter = StubAdapter(tmp_path)
    _run(adapter)

    assert adapter.landing_scope is None


def test_the_engine_says_where_unattributable_work_was_left(
    tmp_path, landed, head, snapshots, capsys,
):
    """Reported once here, not once per adapter.

    The fixes are loose in the worktree and this line is the only thing that
    says so. Leaving it to each domain is four chances for the next one to be
    the adapter that stays quiet.
    """
    snapshots.side_effect = _reads(set(), None)
    _run(StubAdapter(tmp_path))

    assert "could not read what the fix pass changed" in capsys.readouterr().err


def test_an_attributable_pass_reports_nothing_of_the_kind(
    tmp_path, landed, head, snapshots, capsys,
):
    snapshots.side_effect = _reads(set(), {"a.py"})
    _run(StubAdapter(tmp_path))

    assert "could not read what the fix pass changed" not in capsys.readouterr().err


def test_an_unreadable_baseline_stops_the_pass_before_the_agent_runs(
    tmp_path, landed, head, snapshots,
):
    """No baseline means no attribution, so the agent's turns would buy nothing.

    Either outcome available without one is wrong — commit the worktree
    wholesale, or commit none of what the agent did — so the honest move is to
    spend nothing and say so.
    """
    snapshots.return_value = None
    adapter = StubAdapter(tmp_path)
    _, inv = _run(adapter)

    inv.assert_not_called()
    landed.assert_not_called()
    assert adapter.recorded is None


def test_a_domain_that_rewrote_its_branch_pushes_with_its_own_args(tmp_path, landed, head):
    """A replayed branch's push is non-fast-forward and has to say so.

    The engine cannot know this for the domain: only the pass that rewrote the
    branch knows the push needs a lease rather than an append.
    """
    adapter = StubAdapter(tmp_path, spec=fix_engine.LandSpec(
        message="fix: the thing", args=("--force-with-lease",),
    ))
    _run(adapter)

    assert landed.call_args.kwargs["args"] == ("--force-with-lease",)


def test_a_domain_that_rewrote_nothing_adds_no_push_args(tmp_path, landed, head):
    """The default is a no-op, so the three passes predating it are unchanged."""
    _run(StubAdapter(tmp_path))
    assert landed.call_args.kwargs["args"] == ()


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


# ── the verify gate ─────────────────────────────────────────────────────────
#
# A fix the agent ticked is a claim it edited something, not a claim the edit
# works. The gate is what turns the first into evidence for the second before a
# commit is made or a reviewer is told anything.


def _verdicts(*pairs):
    """A `run_verify` stub answering the ids it was given, in one dict."""
    def run_verify(_phase, _prompt, **_kwargs):
        return dict(pairs)
    return run_verify


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
        verify=_verdicts(("i0", fix_gate.Verdict(
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
        verify=_verdicts(("i0", fix_gate.Verdict(ok=True, detail="scope holds"))),
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
        verify=_verdicts(("i0", fix_gate.Verdict(ok=False, detail="repro still exits 3"))),
    )

    assert run.outcomes[0].outcome is FixOutcome.NEEDS_HUMAN
    assert "repro still exits 3" in run.outcomes[0].reason
    assert all(o.outcome is not FixOutcome.FIXED for o in adapter.landing_saw)


def test_a_fix_the_gate_confirms_stays_fixed(tmp_path, landed, head):
    adapter = StubAdapter(tmp_path, count=1)
    run, _ = _run(
        adapter,
        verify=_verdicts(("i0", fix_gate.Verdict(ok=True, detail="suite green"))),
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
    run, _ = _run(adapter, verify=_verdicts(("i0", fix_gate.Verdict(ok=None, detail="no runnable check"))))

    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].verified is False
    assert "no runnable check" in run.outcomes[0].verify_detail


def test_an_id_the_gate_never_answered_is_unverified_not_falsified(tmp_path, landed, head):
    """Silence is not a verdict.

    An agent that answers two of three items has not falsified the third, and
    demoting on absence would punish a fix for the gate running out of turns.
    """
    adapter = StubAdapter(tmp_path, count=2)
    run, _ = _run(adapter, verify=_verdicts(("i0", fix_gate.Verdict(ok=True))))

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
    assert fix_gate._CLAIM_HEADING in body


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
    assert fix_gate._CLAIM_HEADING not in body
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

    item = fix_gate._verify_item(outcome, None, StubAdapter.item_noun)

    assert "test_orphan" in item.body
    assert fix_gate._CLAIM_HEADING in item.body


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
            or land.LandResult(CommitStatus.PUSHED, "abc1234")
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
            ("i0", fix_gate.Verdict(ok=False, detail="the repro fails"))))

        assert [o.outcome for o in seen[0]] == [FixOutcome.NEEDS_HUMAN]

    def test_the_default_hook_is_a_no_op(self, tmp_path, landed, head):
        """Every other domain runs the same engine and must be unaffected."""
        adapter = StubAdapter(tmp_path)
        _run(adapter)
        assert adapter.recorded is not None


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
        verify=_verdicts(("i0", fix_gate.Verdict(ok=True, detail="the repro passes"))),
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
        verify=_verdicts(("i0", fix_gate.Verdict(ok=None, detail="belongs to i1"))),
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
        verify=_verdicts(("i0", fix_gate.Verdict(ok=False, detail="half applied"))),
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
        verify=_verdicts(("i0", fix_gate.Verdict(ok=None, detail="belongs to i1"))),
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
