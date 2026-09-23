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
from fix import reconcile as fix_reconcile
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

# The checklist's name inside a pass's artifact directory. Published because a
# directory that sweeps a pass's leavings has to name the file, and one spelling
# of it is what keeps the sweep and the write from drifting apart.
TRACKING_FILENAME = "fix-tracking.md"

# The gate's own checklist, published for the same reason: a review's sweep
# removes both files by name, and a gate running inside a review directory
# would otherwise leave its answers beside the deliverable.
VERIFY_TRACKING_FILENAME = "verify-tracking.md"

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
        return self.artifacts / VERIFY_TRACKING_FILENAME

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
        `tracking_file`, `answer_format`, `worktree_block`, `generated_block`,
        `role_block` and `max_turns`.
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
        for path in (adapter.tracking_path, adapter.verify_tracking_path)
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
    scopes = _scopes(batches)

    again = [by_id[o.id] for o in deferred if o.id in by_id]
    if not again:
        return _Settled(stalled + settled + deferred, worst, scopes)
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
        _merge_scopes(scopes, _scopes([retried])),
    )


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


# A decline asks the gate a different question from a fix. A fix says "I changed
# this" and is checked by running it; a decline says "this finding is wrong about
# the code" and is checked by reading the tree the decline describes. Both are
# claims the pass made about work nobody else watched, which is why they share a
# gate — but a gate handed a decline under the fix wording looks for a change
# that was never made and reports every one of them broken.
_DECLINE_HEADING = (
    "**The fix pass rejected this {noun} rather than acting on it, saying:**"
)

_DECLINE_ASK = (
    "Check that reason against the tree, and judge only whether it holds.\n\n"
    "Two ways it commonly does not, both of which read as sound prose:\n\n"
    "1. **It describes the tree after the pass's own edits.** A pass that fixed "
    "the {noun} and then declined it reports the fix as a non-defect, and the "
    "reason is true when you read the file precisely because the pass made it "
    "true. The uncommitted edits are in the worktree — `git diff` is exactly "
    "what this pass changed. If the reason is true only with that diff applied, "
    "the {noun} was fixed, not declined: answer **broken** and say so.\n"
    "2. **It cites a commit that does not contain what it claims.** A reason "
    "naming a SHA is checkable: `git show <sha>` it. A pass cannot cite its own "
    "commit here, because it has not committed yet — so a SHA that does not "
    "carry the change described is a reason with nothing behind it.\n\n"
    "A decline resting on scope, house convention, a documented tradeoff, or the "
    "{noun}'s own text is not any of the above. Judge it as written and answer "
    "**verified** when it holds."
)


def _claim_block(reason: str) -> str:
    """What the fix pass said holds this change, framed as a claim to check.

    The empty case says so in words rather than rendering nothing: a gate shown
    no claim block cannot tell "the pass was never asked" from "the pass was
    asked and declined to answer", and only the second is worth reporting.
    """
    return f"{_CLAIM_HEADING} {reason}" if reason else _NO_CLAIM


def _decline_block(reason: str, noun: str) -> str:
    """A decline as the gate is asked to check it, worded for the domain's own noun."""
    heading = _DECLINE_HEADING.format(noun=noun)
    ask = _DECLINE_ASK.format(noun=noun)
    return f"{heading} {reason}\n\n{ask}"


# A contradicted deferral asks the gate the inverse of every other item here.
# The rest say "I changed this, check it works"; this one says "I changed
# nothing" while its own file moved in the same run. Handing it over under the
# fix wording would ask the gate to verify a fix the pass never claimed, and the
# honest answer to that is always "broken" — which is the wrong answer, since
# the question is whether the work is there at all.
_UNDONE_HEADING = (
    "**The fix pass recorded this {noun} as work it did not do**, but this "
    "{noun}'s own file changed while that answer was being written. One of the "
    "two is wrong."
)

_UNDONE_ASK = (
    "Establish which. The pass may have done the work and mis-recorded it; it "
    "may have edited the file for a different {noun} answered in the same run; "
    "or it may have left something half-applied.\n\n"
    "Answer for *this* {noun} only:\n\n"
    "- **verified** — the work this {noun} asks for is present in the tree and "
    "does what was asked. The recorded answer was wrong and will be corrected "
    "to fixed.\n"
    "- **broken** — something for this {noun} is present but does not do what "
    "was asked. It goes to a person rather than back to the pass.\n"
    "- **not verified** — the change belongs to something else, or you cannot "
    "tell. The recorded answer stands untouched, which is the right outcome "
    "whenever the file moved for a reason other than this {noun}.\n\n"
    "`git diff` is the pass's uncommitted work. Nothing here is committed yet, "
    "so a reason citing a SHA cannot be citing this pass's own work."
)


def _undone_block(reason: str, noun: str) -> str:
    """A contradicted "not done" as the gate is asked to settle it.

    The agent's own reason is included when it gave one — a pass that said *why*
    it could not do the work is offering the gate the most direct thing to check
    the tree against.
    """
    heading = _UNDONE_HEADING.format(noun=noun)
    said = f" It said: {reason}" if reason else ""
    return f"{heading}{said}\n\n{_UNDONE_ASK.format(noun=noun)}"


def _verify_item(
    outcome: ItemOutcome, source: FixItem | None, noun: str,
    scope: fix_scope.BatchScope = fix_scope.UNKNOWN_SCOPE,
) -> FixItem:
    """One claimed fix as the gate is asked about it.

    The body is three things joined: the domain's own rendering of what the
    reviewer said, carried over verbatim; the fix pass's claim about what now
    holds the change; and what the worktree showed while that claim was being
    made. The gate judges the fix against the ask, and the ask is not
    recoverable from the outcome; it also checks the claim, and the claim is not
    recoverable from the source item.

    The observation is the half that was missing, and it is why a reason
    describing work the pass did not do used to survive this gate. The claim is
    prose written by the agent being checked, so a gate holding it up against
    nothing could only ask whether it *sounded* like a fix — and a fluent
    sentence about an unrelated change reads exactly like a fluent sentence
    about a real one. Naming the files that actually moved gives the gate
    something the agent's prose has to agree with.

    It is evidence and not a verdict: the list is per batch, the block says so,
    and a fix landing in a caller rather than at the reviewer's line is normal
    enough that concluding from a path miss alone would be wrong more often than
    right. The gate is the reader equipped to weigh that; this only makes sure
    it is not weighing it blind.

    The claim goes last so the ask is read first, and so it sits immediately
    above the verdict boxes answering it. Falling back to the outcome alone
    keeps a gate that is merely under-informed rather than one that crashes, for
    an id the pass answered but never handed out — the claim still reaches it,
    since that half comes from the outcome.
    """
    claim = (
        _decline_block(outcome.reason, noun)
        if outcome.outcome is FixOutcome.DECLINED
        else _undone_block(outcome.reason, noun)
        if outcome.outcome in fix_reconcile.CLAIMS_NO_WORK
        else _claim_block(outcome.reason)
    )
    seen = fix_reconcile.observed(outcome, scope)
    if seen:
        claim = f"{claim}\n\n{seen}"
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


def _gated_decline(outcome: ItemOutcome) -> bool:
    """Whether this decline is one the gate can check.

    Every decline with a reason is, and the predicate is deliberately no
    cleverer than that. The tempting alternative is to gate only the ones whose
    wording asserts something about the tree — "already", "not present",
    "current HEAD" — but a decline is prose, the phrasings are unbounded, and a
    keyword list quietly exempts the rewording it does not know. The failure it
    would miss is the one worth catching, so the cost of gating the honest
    declines too is accepted.

    A decline with no reason is skipped because there is no claim to check.
    `_record_unevidenced` reports that one instead.
    """
    return outcome.outcome is FixOutcome.DECLINED and bool(outcome.reason)


def _verify(
    outcomes: list[ItemOutcome], verify: VerifyFn | None, adapter: FixAdapter,
    by_id: dict[str, FixItem], trail: Trail | None,
    settled: _Settled | None = None,
) -> None:
    """Hold each claim against what actually runs, before anything lands.

    A ticked `fixed` box is the agent saying it applied an edit. That is not the
    same claim as the edit working, and the two are indistinguishable in a fix
    pass's output: both produce a ticked box, a commit, and a summary row. This
    is where they stop being indistinguishable.

    A decline with a reason comes here too, and for the same argument. It is the
    pass closing a reviewer's finding on its own say-so, with no diff anyone can
    read to check it — the weakest-evidence outcome the vocabulary has, and
    until this it was the only one nothing checked. The failure that motivated
    it: a pass fixed a finding, then ticked `declined` describing the tree its
    own edit had just produced, and cited a commit that did not contain the
    change. The edit was committed anyway, because staging reads the worktree
    diff and not the boxes, so the fix shipped recorded as "not a defect".

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
    scope_for = settled.scope_for if settled else _no_scope
    contradicted = {
        o.id for o in outcomes
        if fix_reconcile.contradiction(o, scope_for(o)) is not None
    }
    if verify is None:
        # A domain that runs no gate still gets the observation reported. The
        # contradiction is a fact about the tree and is established without
        # asking anything — it is only the *resolution* that needs an agent —
        # so staying silent here would hide a known discrepancy from the two
        # passes (CI, pre-push) that never opted into a gate. Nothing is
        # demoted or promoted on it: with no verdict available the recorded
        # answer stands, which is the same rule the gated path follows when the
        # gate reaches no verdict.
        _report_ungated(contradicted, trail)
        return
    claimed = [
        o for o in outcomes
        if o.outcome.counts_as_fixed or _gated_decline(o) or o.id in contradicted
    ]
    if not claimed:
        return
    if contradicted:
        log.warn(
            f"{len(contradicted)} item(s) recorded as work not done, in a batch "
            "that changed the item's own file — sent to the verify gate"
        )
        if trail:
            trail.warn(
                "fix_contradicted",
                f"{len(contradicted)} item(s) contradicted by the observed tree",
                data={"items": sorted(contradicted)},
            )

    items = [
        _verify_item(o, by_id.get(o.id), adapter.item_noun, scope_for(o))
        for o in claimed
    ]
    # The gate's own phase where the domain declared one. Falling back to the
    # fix pass's phase keeps a domain that has not declared one working, but it
    # prompts the gate with the fix pass's template — so a domain running a gate
    # is expected to set `verify_phase`.
    verdicts = verify(
        adapter.verify_phase or adapter.phase, "", items=items, adapter=adapter,
    ) or {}

    falsified = 0
    promoted = 0
    for outcome in claimed:
        verdict = verdicts.get(outcome.id)
        if outcome.id in contradicted:
            promoted += _resolve_contradiction(outcome, verdict)
            continue
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
        was_declined = outcome.outcome is FixOutcome.DECLINED
        outcome.outcome = FixOutcome.NEEDS_HUMAN
        outcome.reason = verdict.detail or (
            "the reason given for declining this did not hold up"
            if was_declined else "the fix did not hold up when run"
        )
        outcome.verified = False
        falsified += 1

    if falsified:
        log.warn(
            f"Verify gate: {falsified} of {len(claimed)} claimed "
            f"item{'s' if len(claimed) != 1 else ''} did not hold up — "
            "demoted, not recorded as the pass claimed them"
        )
    if promoted:
        log.warn(
            f"Verify gate: {promoted} item(s) recorded as not done were "
            "confirmed fixed against the tree — recorded as fixed, not as the "
            "pass claimed them"
        )
    if trail:
        trail.info(
            "fix_verify", "verify gate complete",
            data={
                "claimed": len(claimed),
                "falsified": falsified,
                "promoted": promoted,
                "verified": sum(1 for o in claimed if o.verified),
            },
        )


def _report_ungated(contradicted: set[str], trail: Trail | None) -> None:
    """Say that the tree disagrees with the record, for a pass with no gate.

    A warning rather than a change of outcome. The observation is too coarse to
    settle an item on its own — that argument is `fix.reconcile`'s and does not
    weaken because no gate is configured — so what an operator gets here is the
    discrepancy and the ids behind it, which is strictly more than the silence
    that preceded this.
    """
    if not contradicted:
        return
    log.warn(
        f"{len(contradicted)} item(s) recorded as work not done, in a batch "
        "that changed the item's own file. No verify gate is configured for "
        "this pass, so the recorded answer stands — check these by hand: "
        + ", ".join(sorted(contradicted))
    )
    if trail:
        trail.warn(
            "fix_contradicted_ungated",
            f"{len(contradicted)} item(s) contradicted by the observed tree, "
            "with no gate to settle them",
            data={"items": sorted(contradicted)},
        )


def _no_scope(_outcome: ItemOutcome) -> fix_scope.BatchScope:
    """The scope lookup for a caller that supplied no observations.

    Keeps `_verify` callable without a `_Settled` — which the tests do, and
    which a domain calling the gate directly would — by answering the way an
    unobserved pass genuinely should: nothing is known, so nothing is
    contradicted and no observation block is rendered.
    """
    return fix_scope.UNKNOWN_SCOPE


# What a contradicted item says when the gate reached no verdict on it. Not a
# claim either way: the observation is a fact about the tree, and the absence of
# a verdict is a fact about the run. Both beat the summary's "no auto-fix"
# fallback, which is the one reading of an empty reason that is certainly wrong
# here.
_UNSETTLED_CONTRADICTION = (
    "recorded as not done, but this item's file was changed in the same run "
    "and the gate reached no verdict on it — read the diff"
)


def _resolve_contradiction(
    outcome: ItemOutcome, verdict: Verdict | None,
) -> int:
    """Settle an item whose "nothing was done" the tree disagrees with.

    Returns 1 when the item was promoted to FIXED, so the caller can report how
    many answers the tree overturned.

    The gate is asked the inverse of its usual question here, and the three
    answers mean correspondingly inverted things. **verified** is the gate
    confirming the work is present and correct, which makes the recorded
    deferral simply wrong: the item becomes FIXED, and it is the one path in the
    engine that promotes rather than demotes. **broken** is the gate finding the
    edit present but not doing what was asked — not a deferral either, and not
    something a further identical retry fixes, so it goes to NEEDS_HUMAN on the
    same argument the falsified branch makes. **not verified**, and a gate that
    never answered, leave the deferral exactly as the agent recorded it.

    That last case is why this cannot demote on the observation alone. A file
    moving in a batch is evidence that *something* happened, never that this
    item is what happened — several items share a batch and a fix routinely
    touches a neighbour's file — so with no verdict behind it the honest record
    is the one the pass wrote, and the contradiction survives in the trail
    rather than in an outcome nobody established.

    `verified` is left alone throughout. It means "something ran against this
    fix and passed", and the surfaces read `False` as a hedge to print beside a
    *claimed fix* — writing it onto a row that claims no fix at all would
    caveat a deferral for failing to prove work it never said it did.

    What the unsettled cases do get is a `reason`, because the alternative is
    worse than saying nothing: an outcome with no reason renders as the summary's
    bare fallback, "no auto-fix", which asserts to whoever reads the commit that
    nothing happened here — over a commit that carries the edit. The outcome
    stands as recorded; only the sentence published about it stops overclaiming.
    """
    if verdict is None:
        outcome.reason = outcome.reason or _UNSETTLED_CONTRADICTION
        return 0
    outcome.verify_detail = verdict.detail
    if verdict.ok is None:
        outcome.reason = outcome.reason or verdict.detail or _UNSETTLED_CONTRADICTION
        return 0
    if verdict.ok is True:
        outcome.outcome = FixOutcome.FIXED
        outcome.verified = True
        outcome.reason = verdict.detail or (
            "recorded as not done, but the change is present in the tree and "
            "the gate confirmed it does what was asked"
        )
        return 1
    outcome.outcome = FixOutcome.NEEDS_HUMAN
    outcome.reason = verdict.detail or (
        "recorded as not done, but this item's file was changed in the same "
        "run and the change does not do what was asked"
    )
    return 0


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

    settled = _settle(adapter, results, by_id, max_turns, trail)

    # Before the gate runs, so the record is of what the pass claimed rather
    # than of what survived being checked: a fix the gate later falsifies is a
    # different finding from one that never offered evidence at all.
    _record_unevidenced(settled.outcomes, trail)

    # Before the scope is read and before anything is committed: a fix the gate
    # falsifies must not reach `landing` as a fix, or the commit and the record
    # would disagree about what the pass did.
    _verify(settled.outcomes, verify, adapter, by_id, trail, settled)

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
