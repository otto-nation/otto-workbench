"""The pipeline every fix pass runs: batch, invoke, retry, land, record.

`fix.types` says what an item is, `fix.tracking` says how the agent is asked
about it, `agent.invoke` runs the agent and `land` commits what it produced.
This is the order those happen in, written once. Three passes sequenced them
themselves and the sequences disagreed — one batched its checklist and two
inlined it whole, two retried the items left over and disagreed about which
ones, and one of them had no partial-progress retry at all.

A domain supplies a :class:`FixAdapter` and nothing else: which phase sizes the
pass, the items, the prompt substitutions its template needs, the commit it
wants and what to do with the outcomes. Batching, invoke, retry and settle live
in :mod:`fix.batches`; everything else between those is here.

Two rules the passes disagreed on, settled in :mod:`fix.batches`:

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

The gate is not a parameter. Every pass here runs on an operator's behalf, so
the commit is unconditional and the push waits for ``--post``; :mod:`land`'s
module docstring makes that argument, and a pass that wanted the other split would
be a fix pass asserting something outward nobody approved.
"""

# doc-group: pipeline

from __future__ import annotations

import dataclasses
import math
import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import agent.phases
import agent.retry
import fix.baseline
import fix.blame
import fix.containment
import fix.dirty_backup
import fix.head_guard
import fix.leftovers
import fix.regression
import fix.pass_resume
import fix.resume
import fix.scope
import fix.suite
import gh.owner_hooks
import git.land
import core.log
import core.publishing
import core.session_lock
from agent.diagnosis import Diagnosis
from agent.registry import PHASES
from core.phases import Effort, Phase
from fix.batches import (
    TRACKING_FILENAME, Batch, Settled, chunks, record_unevidenced, run_batch,
    settle,
)
from fix.types import FixItem
from pr.fix import ItemOutcome
from pr.fix_checkpoint import CheckpointPhase, EngineProgress
from core.trail import Trail, tinfo, twarn
from config.workbench_config import WorkbenchConfig, load_config_or_default
import fix.gate
from fix.gate import VerifyFn

# Proceed even though an interactive session holds the worktree. For a
# scheduled pass deliberately aimed at a tree whose session is known stale;
# named in the refusal so the operator does not have to find it here.
_LOCK_OVERRIDE_ENV = "WORKBENCH_SESSION_LOCK_OVERRIDE"


def _lock_override() -> bool:
    """Whether the operator has asked to commit past a held worktree."""
    return os.environ.get(_LOCK_OVERRIDE_ENV, "") not in ("", "0")


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

    `exit_code` is non-zero when any batch's backend call was, or when landing
    reports `COMMIT_FAILED`. A held or failed push is not a process failure: the
    commit exists. An agent can still exit non-zero having ticked boxes, and it
    can exit clean having ticked none. A caller that reports process success
    reads this one; a caller reporting what got fixed reads `outcomes`.
    `stop` is MAX_TURNS when the last invocation hit the turn cap, even if it
    ticked boxes. Landing reads it so the commit body can tell a truncated pass
    from a finished one without consulting the trail. A stalled pass (see
    `Batch.unproductive`) can also have `stop` set to MAX_TURNS — a batch the
    guard already gave up retrying is also the one whose last invocation ran
    out of turns, and both signals firing together is expected: a stalled pass
    is a truncated pass too.
    """

    outcomes: list[ItemOutcome] = field(default_factory=list)
    landed: git.land.LandResult | None = None
    exit_code: int = 0
    stop: Diagnosis | None = None
    # HEAD before the agent ran, which is what `LandSpec.recover` compares
    # against and what a domain stamps its pre-pass anchors with.
    head_before: str = ""
    batches: int = 0
    max_turns: int = 0
    max_budget: float = 0.0
    # What the repo's own checks said about the tree this pass committed, for a
    # caller reporting the pass after the fact. The commit body has it already:
    # `landing` reads the same result off the adapter before the message is
    # rendered, which is why it is not carried here for the domain's benefit.
    suite: fix.suite.SuiteResult = field(default_factory=fix.suite.SuiteResult)


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
    `contain_worktrees` is the extra trees those grants made writable — empty
    unless a domain handed the agent a sibling checkout.
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
    # Deliberately not `agent.retry.hint_for`, whose hints are written for a
    # phase that produces a document out of nothing: one tells the agent to
    # write its findings file immediately, the other that the file exists and is
    # empty. Neither is true of a tracking file that arrives populated, so a fix
    # pass is told to fix things rather than to write the file it already has.
    fix_hint: str = agent.retry.FIX_RETRY_HINT
    # Set by the engine before `landing`: MAX_TURNS when the last attempt hit
    # the cap, else None. The commit body is assembled in `landing`, which runs
    # before `FixRun` exists, so this is how a domain names a truncated pass.
    stop: Diagnosis | None = None
    # Set by the engine before `landing`, the same way and for the same reason:
    # what the repo's own checks said about the tree this pass is committing.
    # A domain that renders a commit body reads it to say so there, which is
    # the whole point of running the checks before the landing rather than
    # after — see `fix.suite`. Left at NOT_ATTEMPTED for a pass that never had
    # anything worth checking, which is what keeps those passes silent.
    #
    # A shared instance rather than a `field(default_factory=...)`: this class
    # is a plain ABC and not a dataclass, so a `field()` here is a `Field`
    # object sitting where a result should be, and the first attribute read
    # off it raises. Safe to share because `SuiteResult` is frozen.
    suite: fix.suite.SuiteResult = fix.suite.SuiteResult()
    # Whether this domain wants the repo's checks run over its work. On by
    # default: a pass that holds its push is a pass nothing else checks, which
    # is every domain but one. `rebase.prepush` turns it off and says why
    # there — the checks it would run are the ones about to run on its push.
    verifies_with_suite: bool = True
    # Off by default so CI and review passes behave as they did before
    # checkpoints existed. A domain that can resume an interrupted pass turns
    # this on and implements `checkpoint`.
    keeps_checkpoints: bool = False
    # When set, `run` resumes this progress at `resume_phase` instead of
    # starting a fresh pass. Both stay None on every domain that does not
    # keep checkpoints.
    resume: EngineProgress | None = None
    resume_phase: CheckpointPhase | None = None
    # Whether a claimed fix to behaviour-bearing code must come with a test in
    # the same commit. Off by default: a domain whose oracle is something else
    # (a failing CI check, a rebase) has no test to ask for. A domain turning it
    # on lists the items exempt from the ask in `test_exempt_ids`.
    requires_tests: bool = False
    # Which phase sizes and prompts the verify gate, for a domain that runs one.
    # Separate from `phase` because the gate is a different agent asking a
    # different question: sizing it as the fix pass gives it the fix pass's
    # budget, and prompting it as the fix pass hands it a template that tells it
    # to edit source — which the gate's own rules forbid. A domain that passes
    # no `verify=` never reaches the gate and leaves this alone.
    verify_phase: Phase | None = None
    # Comments resumes leftovers an interrupted pass left uncommitted; review
    # keeps its stash instead (`review.convergence.set_aside_leftovers`).
    resumes_interrupted: bool = False

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
        chunk 1, so gc and batch_scope glob one pattern.
        """
        if chunk < 1:
            raise ValueError(f"verify chunk is 1-based, got {chunk}")
        return self.artifacts / f"verify-tracking-{chunk}.md"

    def verify_session_log(self, chunk: int) -> Path:
        """Where this verify chunk streams its session.

        Indexed for the same reason the tracking file is: one path shared by
        two chunks is one chunk's diagnosis overwriting the other's, and a gate
        that ran two chunks would answer for whichever finished last. A
        single-chunk gate passes 1, which is the whole range rather than a
        special case — the suffix is always written.
        """
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

    def commit_scope(self, changed: set[str] | None) -> set[str] | None:
        """The part of *changed* this domain will commit. All of it by default.

        Asked before the repo's checks run rather than inside `landing`,
        because a file kept out of the commit is still on disk while they run:
        the engine has to know what will be left out to say that a green run
        was not over the commit. A domain that narrows its commit narrows it
        here, and `landing` is handed the result.
        """
        return changed

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
        """The directories the agent may reach: the worktree and the artifacts.

        The artifacts directory is not optional and is not a read. The engine
        writes the empty checklist there and then judges the pass on whether
        the agent ticked it — `produced` is `checked(tracking_path) > 0` — so
        an agent that cannot write it cannot succeed, whatever it does to the
        code. It sits outside the worktree on purpose, to keep it out of the
        commit, which is exactly what puts it outside the default grant.

        Granted here rather than by each adapter because the base class is
        what sites the tracking file: `tracking_path`, `session_log` and the
        verify chunks are all derived from `artifacts` a few lines above. An
        override that forgets it produces an agent looping against a guard,
        which is what the comments pass did for twenty turns.

        An adapter widening this should extend the result rather than replace
        it. Order is preserved and duplicates dropped, so siting artifacts
        inside the worktree grants one entry rather than two.

        `CIFixAdapter` and `PrePushFixAdapter` take neither path — they declare
        no `add_dirs` of their own — and both site `artifacts` outside the
        worktree the same way the comments adapter did, so this default was
        silently carrying the same write-guard fix for them. Noted here rather
        than left for a future reader to rediscover: it is intentional, not
        incidental — the hazard this method exists to close is `produced()`
        checking a file the agent could not reach, and that is exactly as true
        for a CI or pre-push pass as it was for comments.
        """
        return list(dict.fromkeys([self.workdir, self.artifacts]))

    def contain_worktrees(self) -> list[Path]:
        """Extra worktrees to snapshot around the agent; none by default.

        Writes in ``workdir`` are attributed and committed. Writes in a sibling
        the agent was granted — the comments pass's default-branch checkout —
        are not, and nothing else reverts them. Returning ``workdir`` here is
        filtered out so it cannot undo the pass's own edits.
        """
        return []

    def test_exempt_ids(self) -> frozenset[str]:
        """Item ids that owe no test when `requires_tests` is on; none by default."""
        return frozenset()

    def after_verify(self, outcomes: list[ItemOutcome]) -> None:
        """A domain's last word before the commit is landed and pushed.

        Called once both gates have spoken — the per-item verify gate and the
        batch suite — and before `landing`. The window matters: a domain that
        wants to stop the pass asserting anything outward — because a gate
        falsified a fix, the repo's own checks came back red, or the agent
        handed an item back — has to say so before the push reads the
        publishing gate, and `record` is too late for that.

        The suite's verdict is deliberately upstream of this rather than after
        it, so an override reading `outcome.verified` reads its final value.
        Do not build a red-suite hold on top of that, though: a suite demotion
        leaves the item at FIXED and sets only `verified`, so a per-item filter
        cannot distinguish it from a gate that stayed silent. `_verify_suite`
        holds publishing centrally for exactly that reason.

        A no-op by default. What a falsified fix means is the domain's call,
        not the pipeline's: the comments pass owes a reviewer a reply and must
        not send one it cannot stand behind, while `pr ci` and `pr review` owe
        nobody anything mid-pass and hold nothing.
        """
        return None

    def interrupted(self, leftovers: frozenset[str], head_before: str) -> None:
        """Called when a stop unwinds through the pass, before it propagates.

        *leftovers* is what the agent had changed and nothing committed. A
        domain that can resume records it; the default records nothing, and the
        engine's own trail event is what every domain gets.
        """
        return None

    def checkpoint(self, phase: CheckpointPhase, progress: EngineProgress) -> None:
        """Record that the pass reached *phase* with *progress*.

        A no-op unless the domain keeps checkpoints. The engine calls this
        after each batch and after the gate, the suite, and the landing.
        """
        return None


def _verify_suite(
    adapter: FixAdapter, outcomes: list[ItemOutcome],
    changed: set[str] | None, trail: Trail | None,
    by_id: dict[str, FixItem] | None = None,
    *, base: str = "", left_out: set[str] | frozenset[str] = frozenset(),
    baseline_ref: str = "", dirty_before: frozenset[str] = frozenset(),
) -> fix.suite.SuiteResult:
    """Run the repo's declared checks over the pass's work and apply the verdict.

    The engine's half of `fix.suite`: resolve the command from the worktree's
    config, run it, and let a red result withdraw the pass's verification
    claims before `landing` renders a body that would otherwise say those
    claims held.

    *base* is the HEAD the pass started from, exported as `fix.suite.FIX_BASE_ENV`.

    *left_out* is what `commit_scope` keeps out of the commit. Those files are
    in the tree the checks run over, so a green run becomes PARTIAL — see
    `fix.suite.scoped_to`.

    Config is re-read from the worktree rather than taken from `adapter.config`
    because a pass launched from another directory carries the config of
    wherever it was launched, and the checks belong to the tree being edited.
    An adapter that was given one still wins: it is the same file when the two
    agree, and the caller's explicit choice when they do not.
    """
    if not adapter.verifies_with_suite or not fix.suite.should_run(outcomes, changed):
        return fix.suite.SuiteResult()
    config = adapter.config or load_config_or_default(adapter.workdir)
    result = _run_checks(
        adapter, config, trail, base=base, baseline_ref=baseline_ref,
        paths=(changed or set()) | dirty_before,
    )
    result = fix.suite.scoped_to(result, set(left_out))
    fix.suite.apply_to(outcomes, result)
    if result.demotes:
        # Only on red, and only as a lead. `fix.blame` reads the pass's own
        # diff against the failure text; it changes no outcome, because one
        # run over sixteen items still cannot say which broke the tree. What
        # it can say is which item's file lost the symbol the failure names,
        # which turns seven hedged rows into one worth reading first.
        result = dataclasses.replace(result, pointers=fix.blame.pointers(
            adapter.workdir, _anchors(outcomes, by_id or {}), result.output_tail,
        ))
        for pointer in result.pointers:
            tinfo(trail, "fix_verify_pointer", pointer.describe(),
                  data={"item": pointer.item_id, "file": pointer.file,
                        "symbols": list(pointer.symbols)})
    _hold_for_suite(result)
    return result


def _hold_for_suite(result: fix.suite.SuiteResult) -> None:
    """Re-place the publishing holds a suite result implies.

    Holds are process-local, so a resumed pass that skips the suite still
    owes the hold the original run would have placed. Extracted from
    `_verify_suite` so both paths share one owner.
    """
    if result.status is fix.suite.SuiteStatus.PREEXISTING:
        # Not demoting — the pass did not make the tree red — but held all the
        # same: nothing should push or reply off a red tree, whoever made it so.
        core.publishing.hold("the repo's checks were already red before this pass")
    if result.demotes:
        # Held here rather than left to a domain's `after_verify`, because a
        # red suite is a fact about the pass and not about any item in it.
        # `hold_after_verify` cannot reach it by construction: it selects on
        # `outcome.outcome in NEEDS_A_PERSON` before it reads `.verified`,
        # and `apply_to` deliberately leaves a demoted item at FIXED — so a
        # suite-only demotion is invisible to every per-item filter, in this
        # domain and any future one. Holding centrally is also wider than the
        # one domain that overrides the hook: nothing should reply, resolve,
        # or push off a tree whose own checks are failing.
        core.publishing.hold("the repo's checks are red with this pass's changes")


def _run_checks(
    adapter: FixAdapter, config: WorkbenchConfig, trail: Trail | None, *,
    base: str, baseline_ref: str, paths: set[str] | frozenset[str],
) -> fix.suite.SuiteResult:
    """The declared command over the pass's tree, and a red verdict re-asked at its base.

    *paths* is everything the tree differs from HEAD in — the agent's edits and
    whatever was dirty before them — written to a file both runs are handed, so
    the baseline selects the tests the pass run selected.
    """
    with tempfile.TemporaryDirectory(prefix="fix-paths-") as scratch:
        listed = Path(scratch) / "paths"
        listed.write_text("".join(f"{p}\n" for p in sorted(paths)))
        command, timeout_s = config.fix.verify_command, config.fix.verify_timeout
        interval = config.fix.verify_progress_interval
        result = fix.suite.run(adapter.workdir, command, timeout_s, trail,
                               base=base, paths_file=str(listed),
                               progress_s=interval)
        remaining = timeout_s - math.ceil(result.duration_s)
        return fix.baseline.label_preexisting(
            adapter.workdir, result, command, remaining, trail,
            ref=baseline_ref, paths_file=str(listed), progress_s=interval)


def _anchors(
    outcomes: list[ItemOutcome], by_id: dict[str, FixItem],
) -> dict[str, str]:
    """The anchor file behind each item this pass claimed to have fixed.

    Claimed fixes only. An item the pass deferred or declined asserted nothing
    for the suite to contradict, and pointing a red run at one would send a
    reader to the one place the pass says it did not touch.

    The outcome carries a `file` of its own and the item carries the one the
    domain rendered; the outcome's wins where it has one, because a
    reconciliation can move it and the item is fixed at the moment the pass
    was assembled.
    """
    anchors: dict[str, str] = {}
    for outcome in outcomes:
        if not outcome.outcome.counts_as_fixed:
            continue
        item = by_id.get(outcome.id)
        anchor = outcome.file or (item.file if item else "")
        if anchor:
            anchors[outcome.id] = anchor
    return anchors


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


@dataclass(frozen=True)
class _PassStart:
    """The baselines a pass captured before the agent ran, reused on resume."""

    items: tuple[FixItem, ...]
    trail: Trail | None
    verify: VerifyFn | None
    head_before: str
    dirty_before: frozenset[str]
    baseline_ref: str
    pre_pass: fix.containment.Snapshot
    head_state: fix.head_guard.HeadState
    backup_dir: Path | None
    backed_up: tuple[str, ...] = ()
    resume_phase: CheckpointPhase | None = None
    prior: EngineProgress | None = None


@dataclass(frozen=True)
class _AgentRun:
    start: _PassStart
    results: tuple[Batch, ...]
    local_batches: int


def _records(adapter: FixAdapter) -> bool:
    """Whether this pass should write checkpoints."""
    return adapter.keeps_checkpoints or adapter.resume is not None


def _agent_blobs(
    adapter: FixAdapter, start: _PassStart, dirty_before: set[str],
) -> dict[str, str] | None:
    if not _records(adapter):
        return None
    return fix.pass_resume.agent_files(
        adapter.workdir, dirty_before, base=start.head_before,
    )


def _reached(phase: CheckpointPhase | None, target: CheckpointPhase) -> bool:
    """Whether a resume has already finished *target*. Fresh passes never have."""
    return phase is not None and phase.reached(target)


def _progress(
    adapter: FixAdapter, start: _PassStart, *,
    answers: list[ItemOutcome] | None = None,
    blobs: dict[str, str] | None = None,
    suite: dict | None = None,
    landed: git.land.LandResult | None = None,
    batches: int | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    exit_code: int | None = None,
    stop: Diagnosis | None = None,
) -> EngineProgress:
    """EngineProgress for *start*, overlaying whatever this call just learned."""
    prior = start.prior or EngineProgress()
    if blobs is None:
        files, shas = list(prior.agent_files), dict(prior.file_shas)
    else:
        files, shas = sorted(blobs), dict(blobs)
    return EngineProgress(
        answers=list(answers) if answers is not None else list(prior.answers),
        agent_files=files,
        file_shas=shas,
        suite=dict(suite) if suite is not None else dict(prior.suite),
        diagnosis=stop if stop is not None else prior.diagnosis,
        commit_sha=landed.sha if landed is not None else prior.commit_sha,
        commit_status=(
            landed.status if landed is not None else prior.commit_status
        ),
        land_error=landed.error if landed is not None else prior.land_error,
        head_before=start.head_before,
        branch=start.head_state.branch,
        dirty_before=sorted(start.dirty_before),
        baseline_ref=start.baseline_ref,
        backup_dir="" if start.backup_dir is None else str(start.backup_dir),
        backed_up=list(start.backed_up),
        batches=prior.batches if batches is None else batches,
        max_turns=prior.max_turns if max_turns is None else max_turns,
        max_budget=prior.max_budget if max_budget is None else max_budget,
        exit_code=prior.exit_code if exit_code is None else exit_code,
        pre_pass_tree=start.pre_pass.tree if start.pre_pass.ok else prior.pre_pass_tree,
    )


def _note(
    adapter: FixAdapter, start: _PassStart, phase: CheckpointPhase, **kwargs,
) -> _PassStart:
    progress = _progress(adapter, start, **kwargs)
    if _records(adapter):
        adapter.checkpoint(phase, progress)
    return dataclasses.replace(start, prior=progress)


def _refused_by_lock(
    adapter: FixAdapter, trail: Trail | None,
) -> FixRun | None:
    """An empty FixRun when another session holds the worktree, else None."""
    foreign = core.session_lock.held_by_others(adapter.workdir)
    if not foreign or _lock_override():
        return None
    holder = foreign[0]
    core.log.error(
        f"refusing to commit into {adapter.workdir}: {holder.describe()} "
        "is editing this worktree"
    )
    core.log.info(
        "wait for that session to finish, run this from the branch's own "
        f"worktree, or set {_LOCK_OVERRIDE_ENV}=1 to proceed anyway"
    )
    tinfo(trail, "session_lock_refused",
          f"{len(foreign)} interactive session(s) editing the worktree",
          data={"holders": [h.describe() for h in foreign],
                "workdir": str(adapter.workdir)})
    return FixRun()


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
    if adapter.resume is not None:
        return _resume(adapter, items, trail, verify)

    head_state = fix.head_guard.read(adapter.workdir)
    head_before = head_state.sha
    # The pre-agent half of the commit scope, taken at the same moment as the
    # HEAD it is the counterpart of: everything dirty here is somebody else's,
    # and what appears after the agent runs is the pass's own.
    dirty_before = fix.scope.changed_files(adapter.workdir)
    if dirty_before is None:
        # Refused before the agent runs, so nothing is lost by refusing. With no
        # baseline the pass could not tell its own work from what was already
        # here, so every outcome is either committing the worktree wholesale or
        # committing none of it — and the agent's turns would be spent either
        # way. Better to spend nothing and say so.
        core.log.error(
            f"could not read the state of {adapter.workdir} — skipping fix pass"
        )
        return FixRun()
    # Fresh pass only. A checkpoint resume returned above and owns dirty_before;
    # leftover-adoption would rewrite that baseline. See `_resume`.
    if adapter.resumes_interrupted:
        dirty_before = fix.resume.adopt(
            adapter.artifacts, adapter.workdir, dirty_before,
            head_now=head_before, trail=trail)
    refused = _refused_by_lock(adapter, trail)
    if refused is not None:
        return refused

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

    # Content before the agent runs, so what the commit leaves behind can be
    # named afterwards: `fix.leftovers` compares it with the tree after landing.
    # Already-dirty files are copied under artifacts/dirty-backup first, so an
    # overwrite is restorable from disk. Failure here must not abort the pass.
    pre_pass = fix.containment.take(adapter.workdir)
    backup = fix.dirty_backup.save(
        adapter.workdir, adapter.artifacts, dirty_before, trail=trail,
        exclude=fix.leftovers.artifacts_prefix(
            adapter.workdir, adapter.artifacts,
        ),
    )

    # Taken before the agent runs, for the one question asked of a red verify
    # run: was the tree already red? Skipped for a domain whose checks never
    # run, and when the repo names no command — a snapshot nobody will compare.
    config = adapter.config or load_config_or_default(adapter.workdir)
    baseline_ref = (
        fix.baseline.snapshot(adapter.workdir)
        if adapter.verifies_with_suite and (config.fix.verify_command or "").strip()
        else ""
    )
    start = _PassStart(
        items=tuple(items), trail=trail, verify=verify,
        head_before=head_before, dirty_before=frozenset(dirty_before),
        baseline_ref=baseline_ref, pre_pass=pre_pass, head_state=head_state,
        backup_dir=backup.dest, backed_up=backup.paths,
    )
    if adapter.keeps_checkpoints:
        adapter.tracking_path.unlink(missing_ok=True)
        start = _note(adapter, start, CheckpointPhase.AGENT_RUNNING)

    try:
        return _run_pass(adapter, start)
    except (KeyboardInterrupt, SystemExit):
        _interrupted(adapter, dirty_before, head_before, trail)
        raise


def _resume(
    adapter: FixAdapter, items: list[FixItem], trail: Trail | None,
    verify: VerifyFn | None,
) -> FixRun:
    """Continue an interrupted pass from `adapter.resume`, reusing its baselines.

    Does not re-run `dirty_backup.save`: the original copies are still under
    `progress.backup_dir`. A session lock still refuses, because that is about
    the live worktree, not the recorded baseline.

    Checkpoint resume owns this pass: it reuses the recorded dirty_before /
    head_before and must not call ``fix.resume.adopt``, which would rewrite
    that baseline from interrupted-pass.json. That file is cleared so a later
    fresh pass cannot leftover-adopt this interruption.
    """
    progress = adapter.resume
    assert progress is not None
    refused = _refused_by_lock(adapter, trail)
    if refused is not None:
        return refused
    fix.resume.clear(adapter.artifacts)
    dirty_before = set(progress.dirty_before)
    head_before = progress.head_before
    backup_dir = Path(progress.backup_dir) if progress.backup_dir else None
    head_state = fix.head_guard.HeadState(
        sha=head_before, branch=progress.branch,
    )
    if progress.pre_pass_tree:
        pre_pass = fix.containment.read_tree(
            adapter.workdir, progress.pre_pass_tree,
        )
    else:
        pre_pass = fix.containment.take(adapter.workdir)
    start = _PassStart(
        items=tuple(items), trail=trail, verify=verify,
        head_before=head_before, dirty_before=frozenset(dirty_before),
        baseline_ref=progress.baseline_ref,
        pre_pass=pre_pass,
        head_state=head_state, backup_dir=backup_dir,
        backed_up=tuple(progress.backed_up),
        resume_phase=adapter.resume_phase, prior=progress,
    )
    # ceiling: KeyboardInterrupt/SystemExit take `_interrupted`. SIGKILL,
    # double-signal (GNU timeout without --foreground), and standalone
    # `ai/bin/review-threads` (no handler) skip it. Upgrade trigger: once a
    # comments --fix pass dies to SIGKILL or a double TERM with uncommitted
    # edits, persist the checkpoint from a signal-safe atexit write.
    try:
        return _run_pass(adapter, start)
    except (KeyboardInterrupt, SystemExit):
        _interrupted(adapter, dirty_before, head_before, trail)
        raise


def _run_pass(adapter: FixAdapter, start: _PassStart) -> FixRun:
    """`run`'s body once the pass is cleared to start: batch, settle, gate, check, land."""
    with fix.containment.around_each(
        adapter.contain_worktrees(), adapter.artifacts, start.trail,
        exclude=adapter.workdir,
    ):
        return _run_contained_pass(adapter, start)


def _prior_batch(
    outcomes: list[ItemOutcome], progress: EngineProgress | None,
) -> Batch:
    """A reconstructed batch for answers already given. Unknown scope."""
    return Batch(
        outcomes=outcomes,
        unproductive=None,
        exit_code=progress.exit_code if progress else 0,
        max_turns=progress.max_turns if progress else 0,
        max_budget=progress.max_budget if progress else 0.0,
        scope=fix.scope.UNKNOWN_SCOPE,
        stop=progress.diagnosis if progress else None,
    )


def _run_agent(adapter: FixAdapter, start: _PassStart) -> _AgentRun:
    """Run the agent for items without a prior answer."""
    items = list(start.items)
    chunk_size = agent.phases.phase_chunk_size(adapter.phase)
    prior = {}
    if start.resume_phase is CheckpointPhase.AGENT_RUNNING:
        prior = fix.pass_resume.prior_answers(
            start.prior, adapter.tracking_path, overlay=True,
        )
    remaining = [item for item in items if item.id not in prior]
    results: list[Batch] = []
    if prior:
        results.append(_prior_batch(list(prior.values()), start.prior))
    batched = chunks(remaining, chunk_size)
    if batched == [[]]:
        batched = []
    name = PHASES[adapter.phase].label
    if len(batched) > 1:
        core.log.info(
            f"{name} — {len(remaining)} items in {len(batched)} batches "
            f"of up to {chunk_size}..."
        )
    dirty_before = set(start.dirty_before)
    collected = list(prior.values())
    origin_batches = start.prior.batches if start.prior else 0
    for n, chunk in enumerate(batched, start=1):
        batch = run_batch(
            adapter, chunk,
            name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})",
            before=(
                None if start.resume_phase is not None or n > 1 or prior
                else dirty_before
            ),
        )
        results.append(batch)
        collected.extend(fix.pass_resume.answered(batch.outcomes))
        blobs = _agent_blobs(adapter, start, dirty_before)
        start = _note(
            adapter, start, CheckpointPhase.AGENT_RUNNING,
            answers=collected, blobs=blobs,
            batches=origin_batches + n,
            max_turns=max((b.max_turns for b in results), default=0),
            max_budget=max((b.max_budget for b in results), default=0.0),
            exit_code=max((b.exit_code for b in results), default=0),
            stop=next((b.stop for b in results if b.stop is not None), None),
        )
    return _AgentRun(start, tuple(results), len(batched))


def _run_contained_pass(adapter: FixAdapter, start: _PassStart) -> FixRun:
    """The pass after extra-worktree snapshots are in place."""
    items = list(start.items)
    trail, verify = start.trail, start.verify
    phase = start.resume_phase
    dirty_before = set(start.dirty_before)
    by_id = {item.id: item for item in items}

    if _reached(phase, CheckpointPhase.AGENT_DONE):
        prior = start.prior or EngineProgress()
        settled = Settled(
            outcomes=list(prior.answers),
            exit_code=prior.exit_code,
            stop=prior.diagnosis,
        )
    else:
        agent_run = _run_agent(adapter, start)
        start = agent_run.start
        settled = settle(adapter, list(agent_run.results), by_id, trail)
        record_unevidenced(settled.outcomes, trail)
        blobs = _agent_blobs(adapter, start, dirty_before)
        prior_now = start.prior or EngineProgress()
        start = _note(
            adapter, start, CheckpointPhase.AGENT_DONE,
            answers=list(settled.outcomes), blobs=blobs,
            batches=prior_now.batches or agent_run.local_batches,
            max_turns=max(
                (b.max_turns for b in agent_run.results), default=prior_now.max_turns,
            ),
            max_budget=max(
                (b.max_budget for b in agent_run.results), default=prior_now.max_budget,
            ),
            exit_code=max(settled.exit_code, prior_now.exit_code),
            stop=settled.stop,
        )

    if not _reached(phase, CheckpointPhase.VERIFIED):
        with fix.containment.around(
            adapter.workdir, adapter.artifacts, "verify", trail,
        ):
            fix.gate.verify_claims(
                settled.outcomes, verify, adapter, by_id, trail,
                scope_for=settled.scope_for,
            )
        start = _note(
            adapter, start, CheckpointPhase.VERIFIED,
            answers=list(settled.outcomes),
        )

    changed = fix.scope.agent_changed(adapter.workdir, dirty_before)
    committed = adapter.commit_scope(changed)
    left_out = (changed or set()) - (committed or set())
    if left_out:
        twarn(trail, "fix_commit_left_out",
              f"{len(left_out)} file(s) left out of the fix commit",
              data={"paths": sorted(left_out), "workdir": str(adapter.workdir)})

    if not _reached(phase, CheckpointPhase.CHECKED):
        with fix.containment.around(
            adapter.workdir, adapter.artifacts, "suite", trail,
        ) as writes:
            adapter.suite = _verify_suite(
                adapter, settled.outcomes, changed, trail, by_id,
                base=start.head_before, left_out=left_out,
                baseline_ref=start.baseline_ref,
                dirty_before=frozenset(dirty_before))
        adapter.suite = fix.suite.with_rewrites(
            adapter.suite, writes.paths, adapter.workdir, trail)
        start = _note(
            adapter, start, CheckpointPhase.CHECKED,
            answers=list(settled.outcomes),
            suite=fix.pass_resume.suite_dict(adapter.suite),
        )
    else:
        adapter.suite = fix.pass_resume.suite_of(
            start.prior.suite if start.prior else {},
        )
        _hold_for_suite(adapter.suite)
        fix.pass_resume.apply_suite_if_missing(settled.outcomes, adapter.suite)

    already_landed = _reached(phase, CheckpointPhase.LANDED)
    if not already_landed:
        fix.regression.check(
            adapter, settled.outcomes, committed,
            _anchors(settled.outcomes, by_id), trail)
        if changed is None:
            fix.scope.report_unattributable(adapter.workdir)
    adapter.after_verify(settled.outcomes)
    adapter.stop = settled.stop
    if not already_landed:
        if (
            _reached(phase, CheckpointPhase.CHECKED)
            and fix.pass_resume.head_is_one_past(adapter.workdir, start.head_before)
        ):
            spec = dataclasses.replace(
                adapter.landing(settled.outcomes, set()),
                recover=True, paths=set(),
            )
        else:
            spec = adapter.landing(settled.outcomes, committed)
        landed = _land(adapter, spec, start.head_state, trail)
        start = _note(
            adapter, start, CheckpointPhase.LANDED,
            answers=list(settled.outcomes),
            suite=fix.pass_resume.suite_dict(adapter.suite), landed=landed,
        )
    else:
        landed = fix.pass_resume.landed_of(start.prior)
    _stamp(settled.outcomes, start.head_before, landed.sha)
    fix.leftovers.report(adapter.workdir, fix.leftovers.find(
        adapter.workdir, start.pre_pass, dirty_before,
        exclude=fix.leftovers.artifacts_prefix(adapter.workdir, adapter.artifacts),
    ), landed, trail, backup_dir=start.backup_dir,
                        backed_up=start.backed_up, dirty_before=dirty_before)

    exit_code = settled.exit_code
    if landed.status is git.land.CommitStatus.COMMIT_FAILED:
        exit_code = exit_code or 1
    prior = start.prior
    finished = FixRun(
        outcomes=settled.outcomes,
        landed=landed,
        exit_code=exit_code,
        stop=settled.stop,
        head_before=start.head_before,
        batches=prior.batches if prior else 0,
        suite=adapter.suite,
        max_turns=prior.max_turns if prior else 0,
        max_budget=prior.max_budget if prior else 0.0,
    )
    adapter.record(finished)
    return finished


def _land(
    adapter: FixAdapter, spec: LandSpec, head_state: fix.head_guard.HeadState,
    trail: Trail | None,
) -> git.land.LandResult:
    """Commit the pre-suite scope and push it.

    ``spec.paths`` is the commit scope computed before the suite ran.
    Containment restored any suite writes, so land stages those names at
    pre-suite content. land itself is unchanged: it still adds by pathspec.

    A branch switch or history rewrite since `head_state` was read refuses the
    landing (`fix.head_guard`), unless the domain recovers its own commits.
    """
    found = fix.head_guard.drift(adapter.workdir, head_state)
    fix.head_guard.report(found, trail)
    if found.refuses_landing and not spec.recover:
        return git.land.LandResult(
            git.land.CommitStatus.COMMIT_FAILED, error=found.message(),
        )
    return git.land.land(
        adapter.workdir,
        message=fix.suite.with_trailer(spec.message, adapter.suite),
        gated=True,
        owner_check=gh.owner_hooks.refuse_outgoing,
        trail=trail,
        regen=spec.regen,
        recover_from=head_state.sha if spec.recover else None,
        paths=spec.paths,
        args=spec.args,
    )


def _interrupted(
    adapter: FixAdapter, dirty_before: set[str], head_before: str, trail: Trail | None,
) -> None:
    """Say what a stopped pass left uncommitted, and let the domain record it.

    Nothing here may mask the stop: a failure to record is reported and the
    stop still propagates, which is the one outcome the operator asked for.
    """
    try:
        changed = fix.scope.agent_changed(adapter.workdir, dirty_before)
        leftovers = frozenset(changed or ())
        if changed is None:
            detail = "fix pass stopped with scope unknown"
            paths = None
        else:
            detail = (
                f"fix pass stopped with {len(leftovers)} edited file(s) uncommitted"
            )
            paths = sorted(leftovers)
        twarn(trail, "fix_interrupted", detail,
              data={"paths": paths, "head_before": head_before,
                    "workdir": str(adapter.workdir)})
        adapter.interrupted(leftovers, head_before)
        if adapter.resumes_interrupted:
            fix.resume.record(
                adapter.artifacts, adapter.workdir, head_before, leftovers)
    except Exception as exc:  # noqa: BLE001 - recording must not replace the stop
        core.log.warn(f"could not record the interrupted fix pass: {exc}")
