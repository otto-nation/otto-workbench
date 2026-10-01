"""Enums, dataclasses, and report payloads for the rebase subsystem.

All wire-format types live here so that every ``rebase/`` module reaches them
through the same import and the binary can re-export them with one alias block.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from core import report as core_report
from gh import landed as branch_landed
from git import regenerate as regen
from pr import context as pr_context
from pr import domains as pr_domains
from pr import state as pr_state
from rebase import inspect as rebase_inspect

RebaseStatus = pr_domains.RebaseStatus
Regenerator = regen.Regenerator


# ── Enums ───────────────────────────────────────────────────────────────────


class ConflictStrategy(StrEnum):
    """How a single conflicted file should be resolved."""
    REGENERATE = "regenerate"
    ACCEPT_THEIRS = "accept_theirs"
    DELETE = "delete"
    BINARY_ERROR = "binary_error"
    AI_MERGE = "ai_merge"


class DeleteSide(StrEnum):
    """Which side of a modify/delete conflict removed the file."""
    OURS_DELETED = "ours_deleted"
    THEIRS_DELETED = "theirs_deleted"


class GeneratedSignal(StrEnum):
    """Which signal identified a file as generated."""
    GITATTRIBUTES = "gitattributes"
    HEADER = "header"


class RefusalSignal(StrEnum):
    """Which check refused the rebase.

    The first three found the branch's work already present in the target ref;
    ``NO_MERGE_BASE``, ``PARTIALLY_LANDED`` and the two budget signals found
    the rebase itself unsafe to run against that ref. ``TRACKER_REFUSED``
    found neither — it refuses because whether the branch landed could not be
    determined at all, the one signal that refuses on an absence rather than
    on evidence.

    ``PARTIALLY_LANDED`` is the only one whose remedy the tool can carry out
    itself: it names the ref to pass to ``--fork-point``, which turns the
    replay into ``git rebase --onto <base> <ref>`` and skips the prefix that
    already landed.

    The landed three take their wire values from ``branch_landed``, which owns
    both the checks and their names — ``push_intent`` reports on the same three
    signals, and a rename that reached only one of the two would leave the pair
    describing the same evidence in different words.
    """
    PR_MERGED = branch_landed.LandedSignal.PR_MERGED.value
    EMPTY_DIFF = branch_landed.LandedSignal.EMPTY_DIFF.value
    COMMITS_UPSTREAM = branch_landed.LandedSignal.COMMITS_UPSTREAM.value
    NO_MERGE_BASE = "no_merge_base"
    PARTIALLY_LANDED = "partially_landed"
    CONFLICTS_OVER_BUDGET = "conflicts_over_budget"
    RESOLUTIONS_OVER_BUDGET = "resolutions_over_budget"
    TRACKER_REFUSED = "tracker_refused"


class ParseFailure(StrEnum):
    """Why AI output could not be parsed into resolved content."""
    MISSING_BOTH_MARKERS = "missing_both_markers"
    MISSING_BEGIN_MARKER = "missing_begin_marker"
    MISSING_END_MARKER = "missing_end_marker"
    END_BEFORE_BEGIN = "end_before_begin"
    SURVIVING_CONFLICT_MARKER = "surviving_conflict_marker"
    MISSING_BLOCK_MARKERS = "missing_markers_for_block"
    ECHOED_CONTEXT = "echoed_context"
    # An echo that trimming cannot repair: every line the model returned came
    # from the context, so removing the echo removes the resolution with it.
    WHOLLY_ECHOED = "wholly_echoed_context"


class RunMode(StrEnum):
    """Mode selected by the CLI flags.

    Threaded through the rebase drivers in place of a bare ``fix`` boolean, so
    that resolving conflicts and pushing the result stay separable:
    ``--fix --no-push`` is a real combination, and inferring the push from the
    fix flag is what made it force-push anyway.
    """
    FIX = "fix"
    FIX_ONLY = "fix-no-push"
    PUSH = "push"
    REBASE_ONLY = "rebase-only"

    @property
    def resolves_conflicts(self) -> bool:
        """Whether the AI is allowed to resolve conflicts during the rebase."""
        return self in (RunMode.FIX, RunMode.FIX_ONLY)

    @property
    def reaches_remote(self) -> bool:
        """Whether the run pushes at all, wherever the push is issued from.

        Deliberately not the same question as "does this function push": FIX
        pushes from the rebase-completion path and PUSH pushes from main() via
        cmd_push, so a predicate for the latter reads False under PUSH even
        though the run force-pushes seconds later.  Conflating the two is what
        printed a manual-push hint ahead of an automatic push.
        """
        return self in (RunMode.FIX, RunMode.PUSH)


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConflictBlock:
    """One conflict region within a file, with surrounding context."""
    index: int
    start: int
    end: int
    conflict: str
    context_before: str
    context_after: str

    @property
    def line_count(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class ConflictPlan:
    """Resolution strategy for a conflicted file, with strategy-specific detail."""
    strategy: ConflictStrategy
    regenerator: Regenerator | None = None
    delete_side: DeleteSide | None = None
    signal: GeneratedSignal | None = None


@dataclass(frozen=True)
class EchoSide:
    """One boundary's overlap between a resolution and the context beside it.

    Two numbers rather than one because they answer different questions.
    ``run`` is how many lines the overlap spans, which is what a repair has to
    remove — the whole run is duplicated in the spliced file, so a partial trim
    leaves half a duplicate behind. ``substantive`` is how much of that run is
    *evidence* of an echo rather than filler both sides produce independently,
    which is what decides whether to repair at all.

    Keeping them apart is what lets a one-line coincidence be tolerated while a
    three-line run containing one real echoed line is removed entire.
    """

    run: int = 0
    substantive: int = 0

    @property
    def echoed(self) -> bool:
        """Whether this side's overlap is read as an echo rather than a coincidence."""
        return self.substantive > MAX_ECHOED_CONTEXT_LINES


@dataclass(frozen=True)
class ContextEcho:
    """How much of a block's context a resolution repeated back, on each side."""

    head: EchoSide = field(default_factory=EchoSide)
    tail: EchoSide = field(default_factory=EchoSide)

    @property
    def found(self) -> bool:
        return self.head.echoed or self.tail.echoed

    @property
    def lines(self) -> int:
        """The larger side's substantive count — what a failure reason reports.

        The larger rather than the sum: with the budget at zero either side
        alone already fails, so the two can never combine into a verdict
        neither reaches on its own.
        """
        return max(self.head.substantive, self.tail.substantive)


@dataclass(frozen=True)
class Trim:
    """A resolution with any echoed context removed, and what came off.

    ``ok`` is False for the one echo trimming cannot repair: a resolution that
    was *entirely* context, where removing the echo leaves nothing to splice.
    That is not a resolution with a flaw to fix, it is an answer that never
    resolved anything, and it goes back to the model.
    """

    text: str = ""
    head: int = 0
    tail: int = 0
    ok: bool = True

    @property
    def trimmed(self) -> int:
        """How many echoed lines were removed."""
        return self.head + self.tail


@dataclass(frozen=True)
class ChunkedResolutions:
    """Per-block resolutions parsed out of one chunked answer.

    A type rather than the pair this returned, because the parser now reports a
    third thing: how many blocks it repaired on the way through. A caller wants
    the resolutions, the failure reason and the repair count, and a tuple that
    grew to three would have broken every call site to say so.
    """

    resolutions: list[str] | None = None
    reason: str = ""
    repaired: int = 0

    @property
    def ok(self) -> bool:
        return self.resolutions is not None


@dataclass(frozen=True)
class Resolution:
    """Files resolved in one rebase step, and which of those went wrong.

    A file is stale when it was staged from the incoming side but its
    regeneration command failed, so it never got merged with the target
    branch's changes.

    A file is *failed* when nothing resolved it at all — the AI's answer would
    not parse twice over, the file is binary, or a git command refused. The
    step carries on past one rather than returning nothing: every other file in
    the step is resolvable, and abandoning them throws away work the run has
    already paid for. What the caller does with a non-empty ``failed`` is stop
    *without* aborting, so the resolutions already staged survive for the
    resume — see `lifecycle.step_conflicts`.
    """
    files: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """No conflicted file in the step was an outright failure.

        A file in ``stale`` still makes this True: it was staged, just with a
        regenerate command that failed, so it is resolved-with-a-caveat rather
        than resolved clean.
        """
        return not self.failed


@dataclass
class ResolutionTally:
    """Files resolved and commits that conflicted across a whole rebase."""
    files: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    commits: int = 0

    def absorb(self, resolution: Resolution) -> None:
        """Fold one step's resolution into the running totals."""
        self.files.extend(resolution.files)
        self.stale.extend(resolution.stale)
        self.failed.extend(resolution.failed)

    @property
    def spread(self) -> int:
        """Distinct files this rebase has conflicted in so far."""
        return len(set(self.files))

    @property
    def depth(self) -> int:
        """Resolution calls this rebase has spent — one per file per commit."""
        return len(self.files)


@dataclass(frozen=True)
class GeneratedFix:
    """Generated files held back from the AI fixer, and how they were repaired."""
    excluded: list[str] = field(default_factory=list)
    rebuilt: bool = False
    stale: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RefDivergence:
    """How a local branch ref stands against origin's copy of it."""
    ahead: int = 0
    behind: int = 0
    comparable: bool = False

    @property
    def diverged(self) -> bool:
        """Each ref holds commits the other does not, so neither can be dropped."""
        return self.comparable and self.ahead > 0 and self.behind > 0

    @property
    def local_only_work(self) -> bool:
        """The local ref carries commits that resetting it to origin would drop."""
        return self.comparable and self.ahead > 0


# ── Constants ───────────────────────────────────────────────────────────────


REFUSAL_EXIT = 4
CONFLICTS_EXIT = 3
REFUSAL_OVERRIDE_FLAG = "--force"
CONFLICT_FILE_BUDGET = 20

# The second half of the breaker, and the one that measures the cost. The file
# budget above bounds how *wide* a rebase conflicts; this bounds how *much* it
# spends, because the two come apart badly on a long branch: nine files
# conflicting in each of seven replayed commits reads as a spread of nine
# forever — well inside the file budget — while the run makes sixty-three AI
# calls against a branch whose work had already landed in another shape.
#
# Twice the file budget, derived rather than picked: a rebase may meet every
# file in its permitted spread twice over before this fires. A branch that
# genuinely conflicts more than that in the same files is replaying its own
# history against a base that already holds it, which is the case the refusal
# exists to stop.
CONFLICT_RESOLUTION_BUDGET = 2 * CONFLICT_FILE_BUDGET

MAX_REBASE_STEPS = 500
REGEN_MESSAGE = "chore: regenerate after rebase"
UNPUSHED_SUBJECT_LIMIT = 10

# How many *substantive* context lines a resolution may repeat before it is read
# as having echoed the context back rather than resolved the conflict.
#
# Zero, because the count this is measured against already discounts everything
# that matches by coincidence: lines the conflict region itself held, and the
# blank lines and bare block-closers `conflicts._STRUCTURAL_LINES` names. What
# is left is a line that says something, sitting outside the region being
# replaced and reproduced anyway — for which there is no innocent explanation,
# so one is enough.
#
# The budget belongs on substantive lines rather than raw matched ones. Against
# raw lines it has to be loose enough for a resolution that ends where the
# context begins, and a threshold loose enough for two coincidental blanks is
# also loose enough for a real one-line echo to pass.
#
# ceiling: an exact-match line filter, which cannot see a resolution that echoes
# its context with the indentation changed or a comment reflowed. Upgrade to a
# similarity ratio if a rejected-then-retried resolution is ever traced to an
# echo this missed on the first pass.
MAX_ECHOED_CONTEXT_LINES = 0

# rerere is held off for the whole of an AI-resolved rebase, and the `false` is
# load bearing — omitting the key does not do this.
#
# git enables rerere on its own whenever `$GIT_DIR/rr-cache` exists, so once any
# run has created that directory the feature is on for good, with no config
# entry anywhere naming it. Only an explicit `false` overrides the auto-detect.
#
# What that cost when it was on: every conflict the AI resolved was recorded
# into the cache as the postimage for that hunk, unreviewed. `rr-cache` lives in
# the *common* directory, so one cache is shared by every worktree of the repo,
# and a later plain `git rebase` — no AI, no prompt, rerere unset in the
# operator's own config — silently replays it. That is how a resolution that
# duplicated a shell function came back after being fixed by hand.
#
# It lives here, in the module every other one in `rebase/` already imports,
# because a merge git performs on this run's behalf is not only a `git rebase`:
# `git stash pop` replays the worktree onto the rewritten branch and is as able
# to reuse a cached resolution as any rebase step. Holding it off in `lifecycle`
# alone left that one uncovered, which is the same regression in a second
# doorway — so the constant sits where both doors can reach it rather than
# being re-derived beside each.
#
# The reuse this gives up was measured before it was removed rather than assumed
# away: at the time, the trail held 371 conflict resolutions and no replay at
# all. The standing reason behind that number is structural — rerere keys on the
# exact hunk and a rebase meets each distinct conflict once, so the cache can
# only pay off across runs, which is the same cross-run reach that made it
# unsafe. Re-measure with `otto-log` before reviving this; do not trust the
# count above to have stayed true.
RERERE_CONFIG = {"rerere.enabled": "false"}


# ── Report payloads ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConflictReport:
    """Unresolved-conflict snapshot — the exit-code-3 payload on stdout."""
    status: str
    files: list[str]
    rebase_head: str
    rebase_head_subject: str
    remaining_commits: int

    @classmethod
    def from_repo(
        cls, cwd: str, status: str = RebaseStatus.CONFLICTS.value,
    ) -> "ConflictReport":
        sha, subject = rebase_inspect.rebase_head_info(cwd)
        return cls(
            status=status,
            files=rebase_inspect.detect_conflicts(cwd),
            rebase_head=sha,
            rebase_head_subject=subject,
            remaining_commits=rebase_inspect.remaining_rebase_commits(cwd),
        )

    def emit(self) -> None:
        core_report.emit_json(asdict(self))


@dataclass
class RebaseOutcome:
    """Result of a rebase — owns state persistence and JSON reporting."""
    status: RebaseStatus = RebaseStatus.COMPLETED
    commits_replayed: int = 0
    conflicts_resolved: int = 0
    files_resolved: list[str] = field(default_factory=list)
    files_stale: list[str] = field(default_factory=list)
    force_pushed: bool | None = None
    # The remote tip the replay was based on, remembered from before the fetch.
    # Saved because `pr rebase --no-push` finishes the rebase in one run and
    # pushes in the next, and by then the only readings still available are the
    # rewritten local tip and a tracking ref the fetch has moved — the two
    # values a lease must not be built from.
    lease_expect: str = ""
    # Keyword-only and required: the recorded base is what a caller reading
    # state.json uses to tell which branch a run actually replayed onto, so a
    # default here would let an outcome report a base the rebase never used.
    target_base: str = field(kw_only=True)

    def save(self, ctx: pr_context.ResolvedContext) -> None:
        state = load_or_init(ctx)
        pr_state.apply(state, pr_domains.RebaseSummary(
            status=self.status.value,
            target_base=self.target_base,
            commits_replayed=self.commits_replayed,
            conflicts_resolved=self.conflicts_resolved,
            files_resolved=self.files_resolved,
            files_stale=self.files_stale,
            force_pushed=self.force_pushed is True,
            lease_expect=self.lease_expect,
            updated_at=pr_state.now_iso(),
        ))
        pr_state.save_state(ctx.target_dir, state)

    def emit(self) -> None:
        report: dict = {
            "status": self.status.value,
            "commits_replayed": self.commits_replayed,
            "conflicts_resolved": self.conflicts_resolved,
            "files_resolved": self.files_resolved,
            "files_stale": self.files_stale,
        }
        if self.force_pushed is not None:
            report["force_pushed"] = self.force_pushed
        core_report.emit_json(report)


@dataclass(frozen=True)
class RefusalReport:
    """A refusal to rebase — the exit-code-4 payload on stdout.

    One shape for every refusal, whatever refused: a caller reads ``signal`` to
    learn which check fired and ``status`` to learn what it concluded, rather
    than telling payloads apart by which keys they happen to carry.
    """
    branch: str
    signal: str
    detail: str
    # None on the tracker path, which runs before the branch is checked out:
    # HEAD is someone else's there, so any count would describe the wrong
    # branch.  Null says "not measured" rather than reporting a wrong number.
    commits_ahead: int | None = None
    pr_number: int | None = None
    status: str = RebaseStatus.ALREADY_LANDED.value
    override: str = REFUSAL_OVERRIDE_FLAG
    # The command that resolves this refusal without waiving it, for the one
    # refusal that has one. Empty everywhere else, so a caller reads "there is
    # a narrower way to do what you asked" from its presence rather than from
    # knowing which signal fired. `override` remains the blunt way out of any
    # of them.
    remedy: str = ""

    def emit(self) -> None:
        core_report.emit_json(asdict(self))


# ── State helpers ───────────────────────────────────────────────────────────


def load_or_init(ctx: pr_context.ResolvedContext) -> pr_state.PRState:
    """Load existing state or create a fresh one from resolved context."""
    return pr_state.load_or_init(
        target_dir=ctx.target_dir,
        repo=ctx.repo,
        branch=ctx.branch,
        pr_number=ctx.pr_number,
        head_sha=ctx.head_sha,
        worktree_root=str(ctx.require_worktree()),
    )


def recorded_target_base(ctx: pr_context.ResolvedContext) -> str | None:
    """The ``target_base`` a prior run of this command recorded, if any.

    Read-only — ``load_or_init`` never writes, so calling this to peek at state
    has no side effect on a run that ends up resolving its own target ref.
    """
    return load_or_init(ctx).rebase.target_base or None
