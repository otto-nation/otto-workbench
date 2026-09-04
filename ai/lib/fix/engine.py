"""The pipeline every fix pass runs: batch, invoke, retry, land, record.

`fix.types` says what an item is, `fix.tracking` says how the agent is asked
about it, `agent.invoke` runs the agent and `land` commits what it produced.
This is the order those happen in, written once. Three passes sequenced them
themselves and the sequences disagreed — one batched its checklist and two
inlined it whole, two retried the items left over and disagreed about which
ones, and one of them had no partial-progress retry at all.

A domain supplies a :class:`FixAdapter` and nothing else: which phase sizes the
pass, the items, the prompt substitutions its template needs, the commit it
wants and what to do with the outcomes. Everything between those is here.

Two rules the passes disagreed on, settled here:

**A batch that stalled has already had its retry.** ``agent_invoke.run_fix``
gives an unproductive pass a second attempt of its own, so handing that batch's
deferrals to the partial-progress retry buys a third identical run. One stalled
batch must not spend the whole pass's retry either, which is why the two are
partitioned rather than pooled.

**A retry re-decides the items it is handed.** Only ``DEFERRED`` items go into
it — an agent that declined an item or said it needs a person answered the
question it was asked — and its answers supersede the first pass's rather than
being reported alongside them.

The gate is not a parameter. Every pass here runs on an operator's behalf, so
the commit is unconditional and the push waits for ``--post``; :mod:`land`'s
module docstring makes that argument, and a pass that wanted the other split would
be a fix pass asserting something outward nobody approved.
"""

# doc-group: pipeline

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from agent import invoke as agent_invoke
from agent import phases as agent_phases
from agent import retry as agent_retry
from agent import templates as agent_templates
from fix import tracking as fix_tracking
from git import client as git_client
from git import land
from core import log
from agent.diagnosis import Diagnosis
from agent.registry import PHASES
from core.phases import Effort, Phase
from fix.types import FixItem
from pr.fix import FixOutcome, ItemOutcome
from core.trail import Trail
from config.workbench_config import WorkbenchConfig

# The checklist's name inside a pass's artifact directory. Published because a
# directory that sweeps a pass's leavings has to name the file, and one spelling
# of it is what keeps the sweep and the write from drifting apart.
TRACKING_FILENAME = "fix-tracking.md"

# What a retry is told about the file it is handed. The first pass's settled
# items are not in it, and an agent that assumes otherwise re-reads work that is
# already done out of a budget raised precisely because the first one ran out.
_RESUME_HINT = (
    "This is a RETRY of a prior fix pass. The tracking file below holds only the "
    "items that pass left unanswered — everything it settled is already gone "
    "from it. Work efficiently.\n\n"
)


@dataclass(frozen=True)
class LandSpec:
    """The commit a domain asks the engine to make for it.

    `message` is the full commit message, subject and body. `regen` is the
    message for files a pre-push hook rewrote, and asks `land` for the retry
    that recovers from one. `recover` asks it to account for a commit the fix
    agent made itself — the engine supplies the HEAD to compare against, which
    is the one thing a domain assembling this cannot know.

    `paths` scopes the commit to exactly those files, for a domain that owns
    less than the worktree it edits in; None commits the whole tree, and an
    empty set commits nothing at all. Only the domain can tell the two apart —
    a pass that could not work out what its agent touched has an empty scope,
    not a licence to stage everything.
    """

    message: str
    regen: str | None = None
    recover: bool = False
    paths: Iterable[str] | None = None


@dataclass
class FixRun:
    """What one fix pass did, in the terms its domain records.

    `outcomes` holds one entry per item handed over, across every batch and
    whatever the retry re-decided. `landed` is None only for a pass with no
    items — there was nothing to commit and nothing was attempted.

    `max_turns` and `max_budget` are the largest batch's, not the last one's:
    the remainder chunk is usually the smallest and would understate what the
    pass was given. The retry's own raised budget is not folded in — it is a
    second attempt at one batch rather than a batch of its own, and the retry
    log line and trail entry are where it is reported.

    `exit_code` is non-zero when any batch's backend call was, which is not the
    same question as whether the pass did work: an agent can exit non-zero
    having ticked boxes, and it can exit clean having ticked none. A caller that
    reports process success reads this one; a caller reporting what got fixed
    reads `outcomes`.
    """

    outcomes: list[ItemOutcome] = field(default_factory=list)
    landed: land.LandResult | None = None
    exit_code: int = 0
    # HEAD before the agent ran, which is what `LandSpec.recover` compares
    # against and what a domain stamps its pre-pass anchors with.
    head_before: str = ""
    batches: int = 0
    max_turns: int = 0
    max_budget: float = 0.0


class FixAdapter(ABC):
    """One domain's half of a fix pass.

    Five things, and the engine owns everything else. `phase` is the pass —
    turns, dollars, chunk size, retry ceiling and the prompt template all come
    from the registry entry it names. `title` is the heading the tracking file
    is written under, and `action` how the pass announces itself.

    `workdir` is the worktree the agent edits and `artifacts` the directory the
    pass writes into; the tracking file and the session log are named inside it
    rather than by each domain, so an operator finds them in the same place
    whichever pass wrote them.

    `item_noun` is what this domain calls one item, and it is the only part of
    the answer-format instruction a domain supplies — the boxes themselves are
    rendered from the format's own definition.

    `fix_hint` is what the unproductive-pass guard prepends before a second
    attempt, and `add_dirs` the directories the agent may read; both have an
    answer that suits most domains and neither is worth declaring when it does.
    """

    phase: Phase
    title: str
    action: str
    item_noun: str
    workdir: Path
    artifacts: Path
    # For the prompt and the usage ledger. `pr` is empty off a PR.
    branch: str = ""
    repo: str = ""
    pr: str = ""
    # The layers above the phase's own resolution. A pass running inside a
    # review has all three — the worktree's config, the effort preset the review
    # was launched at, the model its operator typed — and one running on its own
    # entry point has none of them and takes what the phase resolves to. Effort
    # is also the dollar cap for a phase that pins no `max_budget` of its own.
    config: WorkbenchConfig | None = None
    effort: Effort | None = None
    model: str = ""
    # Deliberately not `agent_retry.hint_for`, whose hints are written for a
    # phase that produces a document out of nothing: one tells the agent to
    # write its findings file immediately, the other that the file exists and is
    # empty. Neither is true of a tracking file that arrives populated, so a fix
    # pass is told to fix things rather than to write the file it already has.
    fix_hint: str = agent_retry.FIX_RETRY_HINT

    @property
    def tracking_path(self) -> Path:
        """The checklist the agent answers on."""
        return self.artifacts / TRACKING_FILENAME

    @property
    def session_log(self) -> Path:
        """Where the agent streams its session, so a thrash can be diagnosed."""
        return self.artifacts / "fix-session.jsonl"

    @abstractmethod
    def items(self) -> list[FixItem]:
        """The work this pass is handing over, already rendered.

        Items the domain never intends to attempt do not appear here — it
        records those itself. What the agent is shown is what it is being asked
        about.
        """

    @abstractmethod
    def template_vars(self) -> dict[str, str]:
        """The substitutions this domain's template needs beyond the shared ones.

        The engine supplies `branch_name`, `repo`, `tracking_content`,
        `tracking_file`, `answer_format`, `worktree_block`, `generated_block`
        and `max_turns`.
        """

    @abstractmethod
    def landing(self, outcomes: list[ItemOutcome]) -> LandSpec:
        """The commit this pass wants for what its agent produced."""

    @abstractmethod
    def record(self, run: FixRun) -> None:
        """Everything after the landing: state, replies, and what the run returns.

        The engine stops at the commit because that is where the domains stop
        agreeing. What one pass owes afterwards — a thread reply, a summary
        comment, a PR body — is not work the pipeline can order for it, so the
        whole of it hangs here off the one `FixRun` the caller also reads.
        """

    def add_dirs(self) -> list[Path]:
        """The directories the agent may read. The worktree alone, by default."""
        return [self.workdir]


@dataclass(frozen=True)
class _Batch:
    """One invocation's answers, and what it was given to produce them."""

    outcomes: list[ItemOutcome]
    # The guard's diagnosis when even its retry produced nothing, else None.
    unproductive: Diagnosis | None
    exit_code: int
    max_turns: int
    max_budget: float


@dataclass(frozen=True)
class _Settled:
    """Every item's final answer, and the worst exit code behind them."""

    outcomes: list[ItemOutcome]
    exit_code: int


def _chunks(items: list[FixItem], size: int) -> list[list[FixItem]]:
    """Split the work into runs a single agent pass can actually finish.

    A phase that bounds no chunk — one scaling with neither turns nor dollars —
    answers zero, which is one batch holding everything rather than an infinite
    loop. That is the pre-batching behaviour, kept for a phase that has not
    declared what one agent's share of its work is.
    """
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


def _prompt(adapter: FixAdapter, turns: int, *, resume: bool = False) -> str:
    """Render this domain's template around the tracking file as it now stands."""
    text = agent_templates.render(
        PHASES[adapter.phase].template_for(),
        branch_name=adapter.branch,
        repo=adapter.repo,
        tracking_content=adapter.tracking_path.read_text(),
        tracking_file=str(adapter.tracking_path),
        answer_format=fix_tracking.instructions(adapter.item_noun),
        worktree_block=agent_templates.build_worktree_block(str(adapter.workdir)),
        generated_block=agent_templates.GENERATED_BLOCK,
        max_turns=str(turns),
        **adapter.template_vars(),
    )
    return _RESUME_HINT + text if resume else text


def _invoke(
    adapter: FixAdapter, items: list[FixItem], *,
    label: str, turns: int, budget: float | None, resume: bool = False,
) -> _Batch:
    """Write the tracking file for `items`, run the agent, read back its answers.

    The file is rebuilt per invocation so the agent is handed only the items its
    budget covers — inlining the whole list is what let a 169 KB checklist reach
    a single 60-turn pass.
    """
    fix_tracking.write(adapter.tracking_path, adapter.title, items)
    log.info(f"{label} — {adapter.action}...")
    result = agent_invoke.run_fix(
        adapter.phase, _prompt(adapter, turns, resume=resume),
        cwd=adapter.workdir,
        session_log=str(adapter.session_log),
        produced=lambda: fix_tracking.checked(adapter.tracking_path) > 0,
        add_dirs=adapter.add_dirs(),
        max_turns=turns,
        max_budget=budget,
        label=label,
        hint_select=lambda _diagnosis: adapter.fix_hint,
        repo=adapter.repo or None,
        pr=adapter.pr or None,
        config=adapter.config,
        effort=adapter.effort,
        model=adapter.model or None,
    )
    log.blank()
    return _Batch(
        outcomes=fix_tracking.parse(adapter.tracking_path),
        unproductive=result.unproductive,
        exit_code=result.exit_code,
        max_turns=turns,
        max_budget=budget or 0.0,
    )


def _run_batch(adapter: FixAdapter, items: list[FixItem], label: str) -> _Batch:
    """Run one batch at the budget the phase gives work of that size."""
    return _invoke(
        adapter, items, label=label,
        turns=agent_phases.phase_turns(adapter.phase, items=len(items)),
        budget=agent_phases.phase_budget(
            adapter.phase, adapter.effort, items=len(items),
        ),
    )


def _retry(
    adapter: FixAdapter, items: list[FixItem], turns: int, trail: Trail | None,
) -> _Batch:
    """Run the deferred remainder again, at the phase's retry budget."""
    retry_turns = agent_phases.phase_retry_turns(adapter.phase, turns)
    log.info(
        f"Retry pass — {len(items)} deferred item(s) (max_turns={retry_turns})..."
    )
    batch = _invoke(
        adapter, items,
        label=f"{PHASES[adapter.phase].label} retry",
        turns=retry_turns,
        budget=agent_phases.phase_budget(
            adapter.phase, adapter.effort, items=len(items),
        ),
        resume=True,
    )
    if trail:
        trail.info(
            "fix_retry", "retry pass complete",
            data={
                "fixed": sum(1 for o in batch.outcomes if o.outcome.counts_as_fixed),
                "still_deferred": _count(batch.outcomes, FixOutcome.DEFERRED),
            },
        )
    return batch


def _count(outcomes: list[ItemOutcome], outcome: FixOutcome) -> int:
    return sum(1 for o in outcomes if o.outcome is outcome)


def _settle(
    adapter: FixAdapter, batches: list[_Batch], by_id: dict[str, FixItem],
    turns: int, trail: Trail | None,
) -> _Settled:
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

    again = [by_id[o.id] for o in deferred if o.id in by_id]
    if not again:
        return _Settled(stalled + settled + deferred, worst)
    # An id the pass never handed out cannot be re-asked — there is no item
    # behind it to render. It is still an answer the file gave, so it is
    # carried rather than dropped: every entry the pass parsed reaches the
    # record, and a domain that cannot place one says so itself.
    unknown = [o for o in deferred if o.id not in by_id]
    # The retry re-decided every item it was handed, so the first pass's
    # deferrals are superseded rather than reported alongside the second's.
    retried = _retry(adapter, again, turns, trail)
    return _Settled(
        stalled + settled + unknown + retried.outcomes,
        max(worst, retried.exit_code),
    )


def _stamp(outcomes: list[ItemOutcome], read_sha: str, commit_sha: str) -> None:
    """Anchor each outcome to the tree it was decided in and the commit it landed in.

    Both SHAs are the engine's to supply: an adapter assembling them would be
    reading the branch a second time, and a fix pass is the only thing that
    knows which commit its own work went into. Only an outcome that may cite a
    commit carries one — a declined item is not in it.
    """
    for outcome in outcomes:
        outcome.read_sha = read_sha
        if outcome.outcome.may_cite_a_commit and commit_sha:
            outcome.commit_sha = commit_sha


def run(adapter: FixAdapter, *, trail: Trail | None = None) -> FixRun:
    """Run `adapter`'s fix pass end to end and hand it back what happened.

    Batches the adapter's items at the phase's chunk size, runs each under the
    unproductive-pass guard, re-runs whatever came back deferred, lands the
    result and calls `record`. The same `FixRun` is what `record` is handed and
    what this returns, so a caller reads the pass's outcome without the adapter
    having to publish it a second way.

    A pass with no items runs nothing and commits nothing: an empty `FixRun` is
    the honest answer, and a domain that wants to say something about having had
    no work says it before calling here.
    """
    items = adapter.items()
    if not items:
        return FixRun()

    head_before = git_client.head_sha(cwd=adapter.workdir)
    chunk_size = agent_phases.phase_chunk_size(adapter.phase)
    batched = _chunks(items, chunk_size)
    name = PHASES[adapter.phase].label
    if len(batched) > 1:
        log.info(
            f"{name} — {len(items)} items in {len(batched)} batches "
            f"of up to {chunk_size}..."
        )

    results = [
        _run_batch(
            adapter, chunk,
            name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})",
        )
        for n, chunk in enumerate(batched, start=1)
    ]
    max_turns = max((b.max_turns for b in results), default=0)

    settled = _settle(
        adapter, results, {item.id: item for item in items}, max_turns, trail,
    )

    spec = adapter.landing(settled.outcomes)
    landed = land.land(
        adapter.workdir,
        message=spec.message,
        gated=True,
        trail=trail,
        regen=spec.regen,
        recover_from=head_before if spec.recover else None,
        paths=spec.paths,
    )
    _stamp(settled.outcomes, head_before, landed.sha)

    finished = FixRun(
        outcomes=settled.outcomes,
        landed=landed,
        exit_code=settled.exit_code,
        head_before=head_before,
        batches=len(batched),
        max_turns=max_turns,
        max_budget=max((b.max_budget for b in results), default=0.0),
    )
    adapter.record(finished)
    return finished
