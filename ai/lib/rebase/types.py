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
    the last two found the rebase itself unsafe to run against that ref.

    The landed three take their wire values from ``branch_landed``, which owns
    both the checks and their names — ``push_intent`` reports on the same three
    signals, and a rename that reached only one of the two would leave the pair
    describing the same evidence in different words.
    """
    PR_MERGED = branch_landed.LandedSignal.PR_MERGED.value
    EMPTY_DIFF = branch_landed.LandedSignal.EMPTY_DIFF.value
    COMMITS_UPSTREAM = branch_landed.LandedSignal.COMMITS_UPSTREAM.value
    NO_MERGE_BASE = "no_merge_base"
    CONFLICTS_OVER_BUDGET = "conflicts_over_budget"


class ParseFailure(StrEnum):
    """Why AI output could not be parsed into resolved content."""
    MISSING_BOTH_MARKERS = "missing_both_markers"
    MISSING_BEGIN_MARKER = "missing_begin_marker"
    MISSING_END_MARKER = "missing_end_marker"
    END_BEFORE_BEGIN = "end_before_begin"
    SURVIVING_CONFLICT_MARKER = "surviving_conflict_marker"
    MISSING_BLOCK_MARKERS = "missing_markers_for_block"


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
class Resolution:
    """Files resolved in one rebase step, and which of those have stale content.

    A file is stale when it was staged from the incoming side but its
    regeneration command failed, so it never got merged with the target
    branch's changes.
    """
    files: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


@dataclass
class ResolutionTally:
    """Files resolved and commits that conflicted across a whole rebase."""
    files: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    commits: int = 0

    def absorb(self, resolution: Resolution) -> None:
        """Fold one step's resolution into the running totals."""
        self.files.extend(resolution.files)
        self.stale.extend(resolution.stale)


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
REFUSAL_OVERRIDE_FLAG = "--force"
CONFLICT_FILE_BUDGET = 20
MAX_REBASE_STEPS = 500
FORCE_PUSH_ARGS = ("--force-with-lease",)
REGEN_MESSAGE = "chore: regenerate after rebase"
UNPUSHED_SUBJECT_LIMIT = 10


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
