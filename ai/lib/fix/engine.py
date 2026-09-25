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
from collections.abc import Iterable, Sequence
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
from core.trail import Trail, tinfo
from config.workbench_config import WorkbenchConfig
from fix import gate as fix_gate
from fix.gate import VerifyFn

# The checklist's name inside a pass's artifact directory. Published because a
# directory that sweeps a pass's leavings has to name the file, and one spelling
# of it is what keeps the sweep and the write from drifting apart.
TRACKING_FILENAME = "fix-tracking.md"

# The gate's own checklists, published for the same reason: a review's sweep
# removes every chunk file by this glob, and a gate running inside a review
# directory would otherwise leave its answers beside the deliverable. Always
# suffixed, including chunk 1, so the sweep is one pattern rather than two names.
VERIFY_TRACKING_GLOB = "verify-tracking-*.md"

# What a retry is told about the file it is handed. The first pass's settled
# items are not in it, and an agent that assumes otherwise re-reads work that is
# already done out of a budget raised precisely because the first one ran out.
_RESUME_HINT = (
    "This is a RETRY of a prior fix pass. The tracking file below holds only the "
    "items that pass left unanswered — everything it settled is already gone "
    "from it. Work efficiently.\n\n"
)

# The outcomes that reject a finding without changing anything. Both close an
# item on the pass's say-so alone, so a reason is the only thing an operator can
# weigh them by — see `_record_unevidenced`.
_OWES_A_REASON = frozenset({FixOutcome.DECLINED, FixOutcome.NEEDS_HUMAN})


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
    `stop` is MAX_TURNS when the last invocation hit the turn cap, even if it
    ticked boxes. Landing reads it so the commit body can tell a truncated pass
    from a finished one without consulting the trail. A stalled pass (see
    `_Batch.unproductive`) can also have `stop` set to MAX_TURNS — a batch the
    guard already gave up retrying is also the one whose last invocation ran
    out of turns, and both signals firing together is expected: a stalled pass
    is a truncated pass too.
    """

    outcomes: list[ItemOutcome] = field(default_factory=list)
    landed: land.LandResult | None = None
    exit_code: int = 0
    stop: Diagnosis | None = None
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
    # Set by the engine before `landing`: MAX_TURNS when the last attempt hit
    # the cap, else None. The commit body is assembled in `landing`, which runs
    # before `FixRun` exists, so this is how a domain names a truncated pass.
    stop: Diagnosis | None = None
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

    def verify_tracking_path(self, chunk: int) -> Path:
        """The checklist this verify chunk answers on.

        Indexed so two chunks cannot share a file. Always suffixed, including
        chunk 1, so gc and _batch_scope glob one pattern.
        """
        if chunk < 1:
            raise ValueError(f"verify chunk is 1-based, got {chunk}")
        return self.artifacts / f"verify-tracking-{chunk}.md"

    def verify_session_log(self, chunk: int) -> Path:
        if chunk < 1:
            raise ValueError(f"verify chunk is 1-based, got {chunk}")
        return self.artifacts / f"verify-session-{chunk}.jsonl"

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
        `tracking_file`, `answer_format`, `worktree_block`, `generated_block`,
        `role_block` and `max_turns`. A domain that withholds a list the
        template names — the branch's files, for one — leaves the agent
        unable to tell an in-scope path from an out-of-scope one.
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

    def after_verify(self, outcomes: list[ItemOutcome]) -> None:
        """A domain's last word before the commit is landed and pushed.

        Called once the gate has spoken and the outcomes are final, and before
        `landing`. The window matters: a domain that wants to stop the pass
        asserting anything outward — because the gate falsified a fix, or the
        agent handed an item back — has to say so before the push reads the
        publishing gate, and `record` is too late for that.

        A no-op by default. What a falsified fix means is the domain's call,
        not the pipeline's: the comments pass owes a reviewer a reply and must
        not send one it cannot stand behind, while `pr ci` and `pr review` owe
        nobody anything mid-pass and hold nothing.
        """
        return None


@dataclass(frozen=True)
class _Batch:
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
    scope: fix_scope.BatchScope = fix_scope.UNKNOWN_SCOPE
    # MAX_TURNS when this invocation hit the cap, even if it ticked boxes.
    # Distinct from `unproductive`, which is the retry decision.
    stop: Diagnosis | None = None


@dataclass(frozen=True)
class _Settled:
    """Every item's final answer, and the worst exit code behind them."""

    outcomes: list[ItemOutcome]
    exit_code: int
    # Which invocation's observation answers for each id. Keyed by id rather
    # than carried on the outcome because an outcome is persisted state and a
    # file list is not: the scope is evidence for a decision taken during the
    # pass, and what survives into the record is the decision.
    scopes: dict[str, fix_scope.BatchScope] = field(default_factory=dict)
    stop: Diagnosis | None = None

    def scope_for(self, outcome: ItemOutcome) -> fix_scope.BatchScope:
        """The observation behind one answer, or the unknown scope.

        Unknown for an id no batch claims — an answer the agent invented, or one
        carried from a state file — which is the right default twice over: there
        is genuinely no observation, and the consumers all treat unknown as
        "draw no conclusion".
        """
        return self.scopes.get(outcome.id, fix_scope.UNKNOWN_SCOPE)


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
        role_block=agent_templates.ROLE_BLOCK,
        max_turns=str(turns),
        **adapter.template_vars(),
    )
    return _RESUME_HINT + text if resume else text


def _invoke(
    adapter: FixAdapter, items: list[FixItem], *,
    label: str, turns: int, budget: float | None, resume: bool = False,
    before: set[str] | None = None,
) -> _Batch:
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
        before = fix_scope.changed_files(adapter.workdir)
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
        scope=_batch_scope(adapter, before),
        stop=result.stop,
    )


def _batch_scope(
    adapter: FixAdapter, before: set[str] | None,
) -> fix_scope.BatchScope:
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
    scope = fix_scope.batch_scope(adapter.workdir, before)
    if not scope.known or not scope.files:
        return scope
    artifacts = {
        _relative_to(adapter.workdir, path)
        for path in (
            [adapter.tracking_path]
            + sorted(adapter.artifacts.glob(VERIFY_TRACKING_GLOB))
        )
    }
    return fix_scope.BatchScope(files=scope.files - {a for a in artifacts if a})


def _relative_to(workdir: Path, path: Path) -> str:
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


def _run_batch(
    adapter: FixAdapter, items: list[FixItem], label: str,
    before: set[str] | None = None,
) -> _Batch:
    """Run one batch at the budget the phase gives work of that size."""
    return _invoke(
        adapter, items, label=label,
        turns=agent_phases.phase_turns(adapter.phase, items=len(items)),
        budget=agent_phases.phase_budget(
            adapter.phase, adapter.effort, items=len(items),
        ),
        before=before,
    )


def _retry(
    adapter: FixAdapter, items: list[FixItem], trail: Trail | None,
) -> list[_Batch]:
    """Run the deferred remainder again, chunked, at the phase's retry budget.

    Sized per chunk rather than against the first pass's largest batch: a
    remainder of three items is not owed the retry of a sixteen-item cap, and
    sending every leftover in one invoke is how a partial-progress retry
    collapsed back to ~2 turns an item against the cap.
    """
    chunk_size = agent_phases.phase_chunk_size(adapter.phase)
    batched = _chunks(items, chunk_size)
    name = f"{PHASES[adapter.phase].label} retry"
    if len(batched) > 1:
        log.info(
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
    adapter: FixAdapter, items: list[FixItem], label: str, *,
    announce: bool,
) -> _Batch:
    """One retry invoke, budgeted for this chunk's size rather than the pass's."""
    original = agent_phases.phase_turns(adapter.phase, items=len(items))
    retry_turns = agent_phases.phase_retry_turns(adapter.phase, original)
    if announce:
        log.info(
            f"Retry pass — {len(items)} deferred item(s) "
            f"(max_turns={retry_turns})..."
        )
    return _invoke(
        adapter, items,
        label=label,
        turns=retry_turns,
        budget=agent_phases.phase_budget(
            adapter.phase, adapter.effort, items=len(items),
        ),
        resume=True,
    )


def _count(outcomes: list[ItemOutcome], outcome: FixOutcome) -> int:
    return sum(1 for o in outcomes if o.outcome is outcome)


def _record_unevidenced(outcomes: list[ItemOutcome], trail: Trail | None) -> None:
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
    """
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
    unreasoned = [
        o.id for o in outcomes
        if o.outcome in _OWES_A_REASON and not o.reason
    ]
    if unreasoned:
        trail.warn(
            "fix_unreasoned",
            f"{len(unreasoned)} finding(s) rejected with no reason given",
            data={"items": unreasoned},
        )


def _settle(
    adapter: FixAdapter, batches: list[_Batch], by_id: dict[str, FixItem],
    trail: Trail | None,
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
    scopes = _scopes(batches)

    again = [by_id[o.id] for o in deferred if o.id in by_id]
    if not again:
        return _Settled(
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
    return _Settled(
        stalled + settled + unknown + [o for b in retried for o in b.outcomes],
        max(worst, max((b.exit_code for b in retried), default=0)),
        _merge_scopes(scopes, _scopes(retried)),
        stop=_first_stop(retried),
    )


def _first_stop(batches: list[_Batch]) -> Diagnosis | None:
    """The first stop reason among `batches`, or None if none of them stopped.

    A chunked pass is truncated when any one of its chunks ran out, not only
    when the last did: the chunks after it were still handed their own budget
    and may have finished cleanly, so reading the final batch would report a
    pass that lost items as complete.
    """
    return next((b.stop for b in batches if b.stop is not None), None)


def _scopes(batches: list[_Batch]) -> dict[str, fix_scope.BatchScope]:
    """Each answered id mapped to the observation of the run that answered it."""
    return {o.id: b.scope for b in batches for o in b.outcomes}


def _merge_scopes(
    first: dict[str, fix_scope.BatchScope],
    retry: dict[str, fix_scope.BatchScope],
) -> dict[str, fix_scope.BatchScope]:
    """One observation per id, accumulated across the runs that answered it.

    The union, not the later reading. A retried item's outcomes supersede — the
    retry re-decided them — but its *observations* must not, because the
    question reconciliation asks spans the whole pass: was this item's file
    changed at any point while the pass was recording that no work was done?

    Superseding loses exactly the case this exists for. An item edited in the
    first batch and deferred there, then deferred again by a retry that touched
    nothing, has an empty retry scope — attribution is by path and the file was
    already dirty when the retry began, so the retry cannot see the edit that
    the first batch made. Keeping only the retry's reading would report that
    item as an honest deferral and commit its edit anyway, which is the original
    defect surviving in the one path most likely to produce it.

    The union cannot manufacture a contradiction against a retry that worked: an
    item the retry actually fixed comes back FIXED, and a fixed item is not a
    claim that no work was done, so no amount of accumulated observation
    contradicts it.

    An unknown reading on either side makes the union unknown. Half an
    observation is not a smaller observation — the missing half is where the
    file would have been.
    """
    merged = dict(first)
    for item_id, scope in retry.items():
        prior = merged.get(item_id)
        if prior is None:
            merged[item_id] = scope
            continue
        merged[item_id] = (
            fix_scope.UNKNOWN_SCOPE
            if not (prior.known and scope.known)
            else fix_scope.BatchScope(files=prior.files | scope.files)
        )
    return merged


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
    if dirty_before:
        # Recorded, not refused. A pass that commits into a tree somebody else
        # is editing moves HEAD under them, which is how a fix pass came to
        # capture work that had just been reverted — but nothing observable here
        # tells that tree from an ordinary one. A dirty worktree is the normal
        # case for a self-review (`review.collect` reviews uncommitted edits on
        # purpose), the pre-push pass exists to repair a dirty tree, and no fix
        # pass on this machine has a terminal to be asked which it is. So this
        # leaves the evidence a diagnosis needs — alongside the `origin` every
        # record carries — rather than guessing and refusing the common case.
        tinfo(trail, "dirty_baseline",
              f"{len(dirty_before)} file(s) already modified before the pass",
              data={"paths": sorted(dirty_before), "workdir": str(adapter.workdir)})

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
            # The pass baseline is the first batch's baseline: it was read just
            # above and nothing has run since. Every later batch reads its own,
            # because the batch before it has been editing.
            before=dirty_before if n == 1 else None,
        )
        for n, chunk in enumerate(batched, start=1)
    ]
    max_turns = max((b.max_turns for b in results), default=0)

    # The items as the domain rendered them, which both the retry and the gate
    # key back into: one asks for the item behind a deferred id, the other for
    # the reviewer's own words behind a fixed one.
    by_id = {item.id: item for item in items}

    settled = _settle(adapter, results, by_id, trail)

    # Before the gate runs, so the record is of what the pass claimed rather
    # than of what survived being checked: a fix the gate later falsifies is a
    # different finding from one that never offered evidence at all.
    _record_unevidenced(settled.outcomes, trail)

    # Before the scope is read and before anything is committed: a fix the gate
    # falsifies must not reach `landing` as a fix, or the commit and the record
    # would disagree about what the pass did.
    fix_gate._verify(settled.outcomes, verify, adapter, by_id, trail, scope_for=settled.scope_for)

    # Between the gate and the push, which is the only window that works: the
    # outcomes are final here, and `land` below reads the publishing gate a
    # domain may want to close on the strength of them.
    adapter.after_verify(settled.outcomes)

    # After the agent and before the commit — the one moment the difference is
    # the agent's work and nothing else's.
    changed = fix_scope.agent_changed(adapter.workdir, dirty_before)
    if changed is None:
        # Reported here rather than by each adapter. Every one of them owes the
        # operator this line — the fixes are loose in the worktree and only
        # this says so — and four copies of it is four chances for the next
        # adapter to be the one that stays quiet.
        fix_scope.report_unattributable(adapter.workdir)
    adapter.stop = settled.stop
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
        stop=settled.stop,
        head_before=head_before,
        batches=len(batched),
        max_turns=max_turns,
        max_budget=max((b.max_budget for b in results), default=0.0),
    )
    adapter.record(finished)
    return finished
