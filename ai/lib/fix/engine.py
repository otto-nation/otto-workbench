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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agent import invoke as agent_invoke
from agent import phases as agent_phases
from agent import retry as agent_retry
from agent import templates as agent_templates
from fix import scope as fix_scope
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

    `args` is passed through to `git push`, and is the domain's to supply
    because only it knows what it did to the branch underneath the commit. A
    pass that appends to its branch needs nothing here; one repairing a branch
    it has just replayed is pushing a non-fast-forward and has to say so, or
    the remote rejects the work. Empty by default, so a domain that rewrote
    nothing pushes exactly as it did before this existed.
    """

    message: str
    regen: str | None = None
    recover: bool = False
    paths: Iterable[str] | None = None
    args: Sequence[str] = ()


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
    # Which phase sizes and prompts the verify gate, for a domain that runs one.
    # Separate from `phase` because the gate is a different agent asking a
    # different question: sizing it as the fix pass gives it the fix pass's
    # budget, and prompting it as the fix pass hands it a template that tells it
    # to edit source — which the gate's own rules forbid. A domain that passes
    # no `verify=` never reaches the gate and leaves this alone.
    verify_phase: Phase | None = None

    @property
    def tracking_path(self) -> Path:
        """The checklist the agent answers on."""
        return self.artifacts / TRACKING_FILENAME

    @property
    def session_log(self) -> Path:
        """Where the agent streams its session, so a thrash can be diagnosed."""
        return self.artifacts / "fix-session.jsonl"

    @property
    def verify_tracking_path(self) -> Path:
        """The checklist the verify gate answers on.

        Beside the fix pass's rather than replacing it: the two are answered in
        different vocabularies, and the fix file is the evidence for what the
        gate was asked about. Overwriting it would destroy the record of what
        was claimed at the moment the claim is being checked.
        """
        return self.artifacts / "verify-tracking.md"

    @property
    def verify_session_log(self) -> Path:
        return self.artifacts / "verify-session.jsonl"

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
    def landing(self, outcomes: list[ItemOutcome], changed: set[str] | None) -> LandSpec:
        """The commit this pass wants for what its agent produced.

        ``changed`` is what the agent added to the worktree's dirty set, from
        the snapshot the engine takes on either side of the run — the files a
        scoped commit stages. It is a parameter rather than something each
        domain works out because every domain needs it and the two snapshots
        have to bracket the agent exactly: the engine is the only layer that
        sees both moments, and a domain taking its own baseline can take it
        late and attribute someone else's dirt to its agent.

        None is not an empty set. Empty says the agent changed nothing; None
        says the worktree could not be read, and a domain that scopes its
        commit must answer that with an empty scope rather than with the whole
        tree — see `fix.scope`. The engine has already told the operator where
        the work was left by the time this is called, so a domain handles the
        commit and not the reporting.
        """

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


@dataclass(frozen=True)
class Verdict:
    """What the gate established about one fix.

    ``ok`` is three-valued on purpose. True is "something ran against the
    changed path and passed", False is "something ran and it failed", and None
    is "nothing could be run". Collapsing the last two would demote a fix on a
    project with no runnable check, which is the whole class of work the gate is
    least able to judge and has the least right to overrule.

    ``detail`` is what ran and what came of it, in the words a reply prints. It
    matters most when ``ok`` is None: an unverified row is only actionable if it
    says why nobody could check it.
    """

    ok: bool | None
    detail: str = ""


# What the gate is handed and what it gives back: the fixed items, and a verdict
# per item id. An id the gate does not answer is not a verdict — see `_verify`.
VerifyFn = Callable[..., dict[str, Verdict]]


# What the fix pass said holds its change, as the gate is shown it. Composed
# here rather than carried on `FixItem`: that type is the question a pass asks,
# the reason is the answer it got back, and this adapter is the one place that
# legitimately holds both.
#
# Worded for any domain's evidence, not just a test name — a CI fix names the
# check it re-ran. "held by" reads correctly for both; "the test for this fix"
# would not.
_CLAIM_HEADING = "**The fix pass claims this change is held by:**"

_NO_CLAIM = (
    "**The fix pass named nothing that holds this change.** That is a claim "
    "nobody made rather than a claim that failed, so it is not on its own a "
    "reason to call the fix broken — judge it on what you can run."
)


def _claim_block(reason: str) -> str:
    """What the fix pass said holds this change, framed as a claim to check.

    The empty case says so in words rather than rendering nothing: a gate shown
    no claim block cannot tell "the pass was never asked" from "the pass was
    asked and declined to answer", and only the second is worth reporting.
    """
    return f"{_CLAIM_HEADING} {reason}" if reason else _NO_CLAIM


def _verify_item(outcome: ItemOutcome, source: FixItem | None) -> FixItem:
    """One claimed fix as the gate is asked about it.

    The body is two things joined: the domain's own rendering of what the
    reviewer said, carried over verbatim, and the fix pass's claim about what
    now holds the change. The gate judges the fix against the ask, and the ask
    is not recoverable from the outcome; it also checks the claim, and the claim
    is not recoverable from the source item.

    The claim goes last so the ask is read first, and so it sits immediately
    above the verdict boxes answering it. Falling back to the outcome alone
    keeps a gate that is merely under-informed rather than one that crashes, for
    an id the pass answered but never handed out — the claim still reaches it,
    since that half comes from the outcome.
    """
    claim = _claim_block(outcome.reason)
    if source is None:
        return FixItem(id=outcome.id, file=outcome.file, line=outcome.line,
                       label=outcome.summary, body=claim)
    body = source.body.rstrip()
    return FixItem(
        id=outcome.id,
        # The outcome's anchor, not the source's: the agent may have moved the
        # code, and the gate should look where the fix landed.
        file=outcome.file or source.file,
        line=outcome.line or source.line,
        label=source.label or outcome.summary,
        body=f"{body}\n\n{claim}" if body else claim,
    )


def _verify(
    outcomes: list[ItemOutcome], verify: VerifyFn | None, adapter: FixAdapter,
    by_id: dict[str, FixItem], trail: Trail | None,
) -> None:
    """Hold each claimed fix against what actually runs, before anything lands.

    A ticked `fixed` box is the agent saying it applied an edit. That is not the
    same claim as the edit working, and the two are indistinguishable in a fix
    pass's output: both produce a ticked box, a commit, and a summary row. This
    is where they stop being indistinguishable.

    Only falsification demotes. A verdict of None, and an id the gate never
    answered at all, both leave the outcome FIXED and unverified — silence is
    not evidence, and a gate that ran out of turns has not established that a
    fix is wrong. Demoting on absence would make the gate's own flakiness look
    like the fix's.

    Mutates in place, before `landing` is asked for a spec, so the outcome the
    domain records and the outcome the commit carries cannot disagree.

    `by_id` is the items as the domain rendered them, which is where the
    reviewer's own words are. An outcome carries a location and a verdict and
    nothing else — `parse` reads the anchor back out of the section heading and
    never the label — so a gate handed only outcomes would be asked whether a
    fix at `a.py:2` works without being told what it was meant to do. The
    prompt's first instruction is to run the reviewer's repro; this is what
    puts that repro in front of it.
    """
    if verify is None:
        return
    claimed = [o for o in outcomes if o.outcome.counts_as_fixed]
    if not claimed:
        return

    items = [_verify_item(o, by_id.get(o.id)) for o in claimed]
    # The gate's own phase where the domain declared one. Falling back to the
    # fix pass's phase keeps a domain that has not declared one working, but it
    # prompts the gate with the fix pass's template — so a domain running a gate
    # is expected to set `verify_phase`.
    verdicts = verify(
        adapter.verify_phase or adapter.phase, "", items=items, adapter=adapter,
    ) or {}

    falsified = 0
    for outcome in claimed:
        verdict = verdicts.get(outcome.id)
        if verdict is None:
            # The gate ran and this id was not in its answer. That is a fix
            # nothing established, which is what False means — distinct from the
            # None of a pass that never gated at all.
            outcome.verified = False
            continue
        outcome.verify_detail = verdict.detail
        if verdict.ok is True:
            outcome.verified = True
            continue
        if verdict.ok is None:
            outcome.verified = False
            continue
        # Falsified. NEEDS_HUMAN rather than DEFERRED: the pass already had its
        # retry, and an edit that is present but wrong is not work the next
        # identical attempt gets right — it is a call for a person, and the
        # reason carries what the gate saw so they do not start from nothing.
        outcome.outcome = FixOutcome.NEEDS_HUMAN
        outcome.reason = verdict.detail or "the fix did not hold up when run"
        outcome.verified = False
        falsified += 1

    if falsified:
        log.warn(
            f"Verify gate: {falsified} of {len(claimed)} claimed "
            f"fix{'es' if len(claimed) != 1 else ''} did not hold up — "
            "demoted, not committed as fixed"
        )
    if trail:
        trail.info(
            "fix_verify", "verify gate complete",
            data={
                "claimed": len(claimed),
                "falsified": falsified,
                "verified": sum(1 for o in claimed if o.verified),
            },
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


def run(
    adapter: FixAdapter, *, trail: Trail | None = None,
    verify: VerifyFn | None = None,
) -> FixRun:
    """Run `adapter`'s fix pass end to end and hand it back what happened.

    Batches the adapter's items at the phase's chunk size, runs each under the
    unproductive-pass guard, re-runs whatever came back deferred, lands the
    result and calls `record`. The same `FixRun` is what `record` is handed and
    what this returns, so a caller reads the pass's outcome without the adapter
    having to publish it a second way.

    A pass with no items runs nothing and commits nothing: an empty `FixRun` is
    the honest answer, and a domain that wants to say something about having had
    no work says it before calling here. A worktree whose dirty set cannot be
    read stops the pass on the same terms and for a sharper reason — see below.
    """
    items = adapter.items()
    if not items:
        return FixRun()

    head_before = git_client.head_sha(cwd=adapter.workdir)
    # The pre-agent half of the commit scope, taken at the same moment as the
    # HEAD it is the counterpart of: everything dirty here is somebody else's,
    # and what appears after the agent runs is the pass's own.
    dirty_before = fix_scope.changed_files(adapter.workdir)
    if dirty_before is None:
        # Refused before the agent runs, so nothing is lost by refusing. With no
        # baseline the pass could not tell its own work from what was already
        # here, so every outcome is either committing the worktree wholesale or
        # committing none of it — and the agent's turns would be spent either
        # way. Better to spend nothing and say so.
        log.error(
            f"could not read the state of {adapter.workdir} — skipping fix pass"
        )
        return FixRun()

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

    # The items as the domain rendered them, which both the retry and the gate
    # key back into: one asks for the item behind a deferred id, the other for
    # the reviewer's own words behind a fixed one.
    by_id = {item.id: item for item in items}

    settled = _settle(adapter, results, by_id, max_turns, trail)

    # Before the scope is read and before anything is committed: a fix the gate
    # falsifies must not reach `landing` as a fix, or the commit and the record
    # would disagree about what the pass did.
    _verify(settled.outcomes, verify, adapter, by_id, trail)

    # After the agent and before the commit — the one moment the difference is
    # the agent's work and nothing else's.
    changed = fix_scope.agent_changed(adapter.workdir, dirty_before)
    if changed is None:
        # Reported here rather than by each adapter. Every one of them owes the
        # operator this line — the fixes are loose in the worktree and only
        # this says so — and four copies of it is four chances for the next
        # adapter to be the one that stays quiet.
        fix_scope.report_unattributable(adapter.workdir)
    spec = adapter.landing(settled.outcomes, changed)
    landed = land.land(
        adapter.workdir,
        message=spec.message,
        gated=True,
        trail=trail,
        regen=spec.regen,
        recover_from=head_before if spec.recover else None,
        paths=spec.paths,
        args=spec.args,
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
