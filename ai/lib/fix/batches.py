"""Batch, invoke, retry, and settle for a fix pass.

`fix.engine` owns the pass as a whole — lock, containment, gate, suite, land.
This module owns one agent's share of the work: how items are chunked, how one
invocation is prompted and observed, how a stalled batch is partitioned from
the remainder that still owes a retry, and how those answers become a settled
list.

Two rules the engine still states, implemented here:

**A batch that stalled has already had its retry.** ``agent.invoke.run_fix``
gives an unproductive pass a second attempt of its own, so handing that batch's
deferrals to the partial-progress retry buys a third identical run. One stalled
batch must not spend the whole pass's retry either, which is why the two are
partitioned rather than pooled.

**A retry re-decides the items it is handed.** Only ``DEFERRED`` items go into
it — an agent that declined an item or said it needs a person answered the
question it was asked — and its answers supersede the first pass's rather than
being reported alongside them.

**A decline owes a reason.** A DECLINED recorded with none is demoted to
NEEDS_HUMAN when the pass is recorded (`record_unevidenced`), so no reviewer's
finding is closed on a rejection nobody can weigh. Records stored earlier are
left alone.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# fix.engine is deliberately not imported: engine imports this module, and
# `fix.engine.FixAdapter` annotations resolve lazily (`from __future__ import annotations`).
import agent.invoke
import agent.phases
import agent.templates
import core.log
import fix.scope
import fix.tracking
from agent.diagnosis import Diagnosis
from agent.registry import PHASES
from core.trail import Trail
from fix.types import FixItem
from pr.fix import FixOutcome, ItemOutcome

# The checklist's name inside a pass's artifact directory. Published because a
# directory that sweeps a pass's leavings has to name the file, and one spelling
# of it is what keeps the sweep and the write from drifting apart.
TRACKING_FILENAME = "fix-tracking.md"

# The gate's own checklists, published for the same reason: a review's sweep
# removes every chunk file by this glob, and a gate running inside a review
# directory would otherwise leave its answers beside the deliverable. Always
# suffixed, including chunk 1, so every file this code writes is reached by one
# pattern. It does not match the unsuffixed `verify-tracking.md` earlier
# revisions wrote; review/gc.py removes that name explicitly alongside the
# glob, and can stop once no in-flight review predates the suffix.
VERIFY_TRACKING_GLOB = "verify-tracking-*.md"

# What a retry is told about the file it is handed. The first pass's settled
# items are not in it, and an agent that assumes otherwise re-reads work that is
# already done out of a budget raised precisely because the first one ran out.
RESUME_HINT = (
    "This is a RETRY of a prior fix pass. The tracking file below holds only the "
    "items that pass left unanswered — everything it settled is already gone "
    "from it. Work efficiently.\n\n"
)

# The outcomes that reject a finding without changing anything. Both close an
# item on the pass's say-so alone, so a reason is the only thing an operator can
# weigh them by — see `record_unevidenced`.
_OWES_A_REASON = frozenset({FixOutcome.DECLINED, FixOutcome.NEEDS_HUMAN})

# The reason an unreasoned decline carries once it is handed to a person.
_UNREASONED_DECLINE = "declined without giving a reason — handed to a person"


@dataclass(frozen=True)
class Batch:
    """One invocation's answers, and what it was given to produce them."""

    outcomes: list[ItemOutcome]
    # The guard's diagnosis when even its retry produced nothing, else None.
    unproductive: Diagnosis | None
    exit_code: int
    max_turns: int
    max_budget: float
    # What the worktree showed while this invocation ran, against which its
    # answers are reconciled. Defaulted to the unknown scope so a batch
    # assembled without one carries "nobody looked" rather than "nothing
    # changed" — the two differ in whether they can be held against the agent,
    # and only one of them is safe as a default.
    scope: fix.scope.BatchScope = fix.scope.UNKNOWN_SCOPE
    # MAX_TURNS when this invocation hit the cap, even if it ticked boxes.
    # Distinct from `unproductive`, which is the retry decision.
    stop: Diagnosis | None = None


@dataclass(frozen=True)
class Settled:
    """Every item's final answer, and the worst exit code behind them."""

    outcomes: list[ItemOutcome]
    exit_code: int
    # Which invocation's observation answers for each id. Keyed by id rather
    # than carried on the outcome because an outcome is persisted state and a
    # file list is not: the scope is evidence for a decision taken during the
    # pass, and what survives into the record is the decision.
    scopes: dict[str, fix.scope.BatchScope] = field(default_factory=dict)
    stop: Diagnosis | None = None

    def scope_for(self, outcome: ItemOutcome) -> fix.scope.BatchScope:
        """The observation behind one answer, or the unknown scope.

        Unknown for an id no batch claims — an answer the agent invented, or one
        carried from a state file — which is the right default twice over: there
        is genuinely no observation, and the consumers all treat unknown as
        "draw no conclusion".
        """
        return self.scopes.get(outcome.id, fix.scope.UNKNOWN_SCOPE)


def chunks(items: list[FixItem], size: int) -> list[list[FixItem]]:
    """Split the work into runs a single agent pass can actually finish.

    A phase that bounds no chunk — one scaling with neither turns nor dollars —
    answers zero, which is one batch holding everything rather than an infinite
    loop. That is the pre-batching behaviour, kept for a phase that has not
    declared what one agent's share of its work is.
    """
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


def render_prompt(adapter: fix.engine.FixAdapter, turns: int, *, resume: bool = False) -> str:
    """Render this domain's template around the tracking file as it now stands."""
    text = agent.templates.render(
        PHASES[adapter.phase].template_for(),
        branch_name=adapter.branch,
        repo=adapter.repo,
        tracking_content=adapter.tracking_path.read_text(),
        tracking_file=str(adapter.tracking_path),
        answer_format=fix.tracking.instructions(adapter.item_noun),
        execution_claim_guard=agent.templates.build_execution_claim_guard(
            occasion=agent.templates.ClaimOccasion.FIX_EVIDENCE,
        ),
        worktree_block=agent.templates.build_worktree_block(str(adapter.workdir)),
        generated_block=agent.templates.GENERATED_BLOCK,
        role_block=agent.templates.ROLE_BLOCK,
        max_turns=str(turns),
        **adapter.template_vars(),
    )
    return RESUME_HINT + text if resume else text


def _invoke(
    adapter: fix.engine.FixAdapter, items: list[FixItem], *,
    label: str, turns: int, budget: float | None, resume: bool = False,
    before: set[str] | None = None,
) -> Batch:
    """Write the tracking file for `items`, run the agent, read back its answers.

    The file is rebuilt per invocation so the agent is handed only the items its
    budget covers — inlining the whole list is what let a 169 KB checklist reach
    a single 60-turn pass.

    The worktree is read either side of the agent, and the difference is this
    batch's own doing. Per invocation rather than once around the pass because
    attribution is by path: a thirty-item pass observed once can only say a file
    belongs to one of thirty items, where the same observation taken per batch
    narrows it to the ten the batch carried. The retry is an invocation too and
    is observed on the same terms, which matters because its items are exactly
    the ones whose first answer was `deferred`.

    `before` is the reading the caller already holds, for the first batch — the
    pass's own baseline is taken moments earlier with nothing in between, and
    reading it twice would be two names for one fact. A caller with nothing to
    offer passes None and this brackets itself.

    None is therefore overloaded: it is both "no reading supplied" and the
    failed reading `changed_files` returns. Conflating them is safe in exactly
    one direction — a caller whose baseline failed has already stopped the pass
    (`run` refuses without one), and a re-read that fails in turn produces the
    unknown scope, which contradicts nothing and is rendered as nothing. Both
    roads lead to "draw no conclusion", which is the answer an unreadable
    worktree should produce.
    """
    if before is None:
        before = fix.scope.changed_files(adapter.workdir)
    fix.tracking.write(adapter.tracking_path, adapter.title, items)
    core.log.info(f"{label} — {adapter.action}...")
    result = agent.invoke.run_fix(
        adapter.phase, render_prompt(adapter, turns, resume=resume),
        cwd=adapter.workdir,
        session_log=str(adapter.session_log),
        produced=lambda: fix.tracking.checked(adapter.tracking_path) > 0,
        add_dirs=adapter.add_dirs(),
        max_turns=turns,
        max_budget=budget,
        label=label,
        hint_select=lambda _diagnosis: adapter.fix_hint,
        repo=adapter.repo or None,
        pr=adapter.pr or None,
        branch=adapter.branch or None,
        config=adapter.config,
        effort=adapter.effort,
        model=adapter.model or None,
    )
    core.log.blank()
    return Batch(
        outcomes=fix.tracking.parse(adapter.tracking_path),
        unproductive=result.unproductive,
        exit_code=result.exit_code,
        max_turns=turns,
        max_budget=budget or 0.0,
        scope=batch_scope(adapter, before),
        stop=result.stop,
    )


def batch_scope(
    adapter: fix.engine.FixAdapter, before: set[str] | None,
) -> fix.scope.BatchScope:
    """This batch's difference, with the pass's own artifacts taken back out.

    Defence in depth rather than a live fix: every adapter today puts its
    artifact directory outside the worktree, and two of them carry a docstring
    saying that is deliberate and why. The subtraction is here because the
    tracking file is the one path the agent is *required* to edit every run, so
    an adapter that ever sited its artifacts inside the worktree would not
    merely dirty the commit — it would make every batch look like it changed
    something and let an item anchored at the checklist contradict itself. The
    cost of holding that closed is one set difference.

    Filtered by resolved path rather than by name, so a domain whose items
    legitimately live in a file called `fix-tracking.md` somewhere else in the
    tree keeps its observation.
    """
    scope = fix.scope.batch_scope(adapter.workdir, before)
    if not scope.known or not scope.files:
        return scope
    artifacts = {
        relative_to(adapter.workdir, path)
        for path in (
            [adapter.tracking_path]
            + sorted(adapter.artifacts.glob(VERIFY_TRACKING_GLOB))
        )
    }
    return fix.scope.BatchScope(files=scope.files - {a for a in artifacts if a})


def relative_to(workdir: Path, path: Path) -> str:
    """`path` as git would name it inside `workdir`, or empty when it is outside.

    Empty rather than an absolute path for the outside case, because the caller
    is building a set to subtract from git's own output: a path git would never
    print cannot match one, and an absolute string sitting in that set reads as
    a filter that does nothing.
    """
    try:
        return str(Path(path).resolve().relative_to(Path(workdir).resolve()))
    except (ValueError, OSError):
        return ""


def run_batch(
    adapter: fix.engine.FixAdapter, items: list[FixItem], label: str,
    before: set[str] | None = None,
) -> Batch:
    """Run one batch at the budget the phase gives work of that size."""
    return _invoke(
        adapter, items, label=label,
        turns=agent.phases.phase_turns(adapter.phase, items=len(items)),
        budget=agent.phases.phase_budget(
            adapter.phase, adapter.effort, items=len(items),
        ),
        before=before,
    )


def _retry(
    adapter: fix.engine.FixAdapter, items: list[FixItem], trail: Trail | None,
) -> list[Batch]:
    """Run the deferred remainder again, chunked, at the phase's retry budget.

    Sized per chunk rather than against the first pass's largest batch: a
    remainder of three items is not owed the retry of a sixteen-item cap, and
    sending every leftover in one invoke is how a partial-progress retry
    collapsed back to ~2 turns an item against the cap.
    """
    chunk_size = agent.phases.phase_chunk_size(adapter.phase)
    batched = chunks(items, chunk_size)
    name = f"{PHASES[adapter.phase].label} retry"
    if len(batched) > 1:
        core.log.info(
            f"Retry pass — {len(items)} deferred item(s) in {len(batched)} "
            f"batches of up to {chunk_size}..."
        )
    single = len(batched) == 1
    batches = [
        _retry_chunk(
            adapter, chunk,
            name if single else f"{name} (batch {n}/{len(batched)})",
            announce=single,
        )
        for n, chunk in enumerate(batched, start=1)
    ]
    if trail:
        outcomes = [o for b in batches for o in b.outcomes]
        trail.info(
            "fix_retry", "retry pass complete",
            data={
                "fixed": sum(1 for o in outcomes if o.outcome.counts_as_fixed),
                "still_deferred": _count(outcomes, FixOutcome.DEFERRED),
            },
        )
    return batches


def _retry_chunk(
    adapter: fix.engine.FixAdapter, items: list[FixItem], label: str, *,
    announce: bool,
) -> Batch:
    """One retry invoke, budgeted for this chunk's size rather than the pass's."""
    original = agent.phases.phase_turns(adapter.phase, items=len(items))
    retry_turns = agent.phases.phase_retry_turns(adapter.phase, original)
    if announce:
        core.log.info(
            f"Retry pass — {len(items)} deferred item(s) "
            f"(max_turns={retry_turns})..."
        )
    return _invoke(
        adapter, items,
        label=label,
        turns=retry_turns,
        budget=agent.phases.phase_budget(
            adapter.phase, adapter.effort, items=len(items),
        ),
        resume=True,
    )


def _count(outcomes: list[ItemOutcome], outcome: FixOutcome) -> int:
    return sum(1 for o in outcomes if o.outcome is outcome)


def record_unevidenced(outcomes: list[ItemOutcome], trail: Trail | None) -> None:
    """Record the fixes claimed with no evidence behind them.

    A FIXED box asks for the test that holds the change, and a pass may tick it
    anyway — the ask is a prompt contract, not a parse-time gate. The parse says
    so on stderr as it reads each one, which serves the operator watching the
    run and nobody afterwards. This is the durable half: one event per pass,
    naming the items, so `otto-log show` can answer what a pass claimed without
    evidence long after the terminal it scrolled past is gone.

    Emitted here rather than from the parse because this is where the trail is,
    and because the question is about the pass rather than about any one box:
    `tracking._record_verdict` decides a single verdict and is kept to that.

    A decline and a `needs a person` are recorded the same way and for a
    stronger reason. Every box in the vocabulary asks for the agent's words, but
    only FIXED leaves a diff an operator can read instead: an unreasoned decline
    renders as a bare `*(declined)*` against a finding nobody acted on, which is
    the pass rejecting a reviewer's claim and declining to say why.

    A decline that gives no reason is demoted to NEEDS_HUMAN here, whether or
    not there is a trail: nothing may close a reviewer's thread on a rejection
    that cannot be weighed, and a person can still weigh it. The demotion is
    at record time only — a record already stored with a reasonless DECLINED
    is left as it is and keeps the stand-in wording `pr.thread_models` gives
    it. The `fix_unreasoned` event still names the demoted items.
    """
    unreasoned = [
        o.id for o in outcomes
        if o.outcome in _OWES_A_REASON and not o.reason
    ]
    for o in outcomes:
        if o.outcome is FixOutcome.DECLINED and not o.reason:
            o.outcome = FixOutcome.NEEDS_HUMAN
            o.reason = _UNREASONED_DECLINE
    if not trail:
        return
    unevidenced = [
        o.id for o in outcomes
        if o.outcome.counts_as_fixed and not o.reason
    ]
    if unevidenced:
        trail.warn(
            "fix_unevidenced",
            f"{len(unevidenced)} fix(es) ticked with no test evidence",
            data={"items": unevidenced},
        )
    if unreasoned:
        trail.warn(
            "fix_unreasoned",
            f"{len(unreasoned)} finding(s) rejected with no reason given",
            data={"items": unreasoned},
        )


def settle(
    adapter: fix.engine.FixAdapter, batches: list[Batch], by_id: dict[str, FixItem],
    trail: Trail | None,
) -> Settled:
    """Every item's final answer, once the deferred remainder has had its retry.

    A batch the guard already retried to no effect contributes its answers as
    they stand: it has had its second attempt, and one stalled batch must not
    spend the retry the rest of the pass is owed.
    """
    stalled = [o for b in batches if b.unproductive for o in b.outcomes]
    live = [o for b in batches if not b.unproductive for o in b.outcomes]
    deferred = [o for o in live if o.outcome is FixOutcome.DEFERRED]
    settled = [o for o in live if o.outcome is not FixOutcome.DEFERRED]
    worst = max((b.exit_code for b in batches), default=0)
    scopes = {o.id: b.scope for b in batches for o in b.outcomes}

    again = [by_id[o.id] for o in deferred if o.id in by_id]
    if not again:
        return Settled(
            stalled + settled + deferred, worst, scopes,
            stop=_first_stop(batches),
        )
    # An id the pass never handed out cannot be re-asked — there is no item
    # behind it to render. It is still an answer the file gave, so it is
    # carried rather than dropped: every entry the pass parsed reaches the
    # record, and a domain that cannot place one says so itself.
    unknown = [o for o in deferred if o.id not in by_id]
    # The retry re-decided every item it was handed, so the first pass's
    # deferrals are superseded rather than reported alongside the second's.
    retried = _retry(adapter, again, trail)
    return Settled(
        stalled + settled + unknown + [o for b in retried for o in b.outcomes],
        max(worst, max((b.exit_code for b in retried), default=0)),
        fix.scope.merge_by_id(
            scopes, {o.id: b.scope for b in retried for o in b.outcomes}),
        stop=_first_stop(retried),
    )


def _first_stop(batches: list[Batch]) -> Diagnosis | None:
    """The first stop reason among `batches`, or None if none of them stopped.

    A chunked pass is truncated when any one of its chunks ran out, not only
    when the last did: the chunks after it were still handed their own budget
    and may have finished cleanly, so reading the final batch would report a
    pass that lost items as complete.
    """
    return next((b.stop for b in batches if b.stop is not None), None)
