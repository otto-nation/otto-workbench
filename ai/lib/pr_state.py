"""Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<state_dir()>/pr/<repo-key>-<branch-slug>/state.json``, keyed on the
run's target — see ``pr_target.target_dir``, which owns that path.

The domains this is an envelope over live in ``pr_domains`` — and one of them,
the comment pass's, in ``pr_comments_fix``. This module imports both; neither
imports it.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import cache
from pathlib import Path
from typing import get_type_hints

# _domains() resolves PRState's annotations at runtime, so every domain class it
# names has to be bound in this module's namespace — an unused-looking import
# here is a registry entry.
from pr_comments_fix import FixSummary
from pr_domains import (
    CIDomain,
    CommentsSummary,
    DescribeSummary,
    Domain,
    PushDomain,
    Readiness,
    RebaseSummary,
    ReviewSummary,
    SupersessionDomain,
    TriageSummary,
)

from serde import (
    from_dict as _serde_from_dict,
    load_file as _serde_load_file,
    to_dict as _serde_to_dict,
    write_json as _serde_write_json,
)


STATE_FILE = "state.json"
STATE_VERSION = 1


def now_iso() -> str:
    """UTC ISO timestamp for state updates."""
    return datetime.now(timezone.utc).isoformat()


# ── Identity ────────────────────────────────────────────────────────────────


@dataclass
class PRIdentity:
    """Resolved once, shared by all subcommands."""
    repo: str
    branch: str
    pr_number: int | None
    head_sha: str
    worktree_root: str


# ── Review posting vocabulary ───────────────────────────────────────────────


class PostedAs(Enum):
    REVIEW = "review"
    COMMENT = "comment"


class PostEvent(Enum):
    COMMENT = "comment"
    APPROVE = "approve"
    REQUESTED_CHANGES = "requested_changes"
    DISMISSED = "dismissed"


@dataclass
class PostTracking:
    """Recorded in post.jsonl alongside each review."""
    posted_as: str
    status: str
    review_id: int = 0
    review_ids: list[int] = field(default_factory=list)
    commit_id: str = ""
    posted_at: str = ""
    inline_count: int = 0
    body_count: int = 0
    skipped_count: int = 0
    submitted: bool = False
    chunk_count: int = 1
    review_sha: str = ""
    head_sha_at_post: str = ""
    sha_drifted: bool = False
    verdict: str = ""

    def __post_init__(self):
        if not self.posted_at:
            self.posted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.review_ids and not self.review_id:
            self.review_id = self.review_ids[0]
        if self.review_id and not self.review_ids:
            self.review_ids = [self.review_id]


# ── The envelope ────────────────────────────────────────────────────────────


@dataclass
class PendingComment:
    """A PR comment deferred until a blocking condition clears."""
    body: str = ""
    source: str = ""
    updated_at: str = ""


@dataclass
class PRState:
    """Unified PR state — envelope over domain summaries.

    The domain fields are declared in the order ``pr status`` prints them.
    Nothing enforces that and nothing breaks if it drifts, but the dashboard and
    the merge-readiness fold both iterate the registry derived from these
    annotations, so the declaration order *is* the display order — reordering
    here is how the dashboard is reordered.

    ``describe`` and ``supersession`` render nothing, so they sit after the ones
    that do rather than interrupting them.
    """
    identity: PRIdentity
    ci: CIDomain = field(default_factory=CIDomain)
    review: ReviewSummary = field(default_factory=ReviewSummary)
    comments: CommentsSummary = field(default_factory=CommentsSummary)
    triage: TriageSummary = field(default_factory=TriageSummary)
    fix: FixSummary = field(default_factory=FixSummary)
    rebase: RebaseSummary = field(default_factory=RebaseSummary)
    push: PushDomain = field(default_factory=PushDomain)
    describe: DescribeSummary = field(default_factory=DescribeSummary)
    supersession: SupersessionDomain = field(default_factory=SupersessionDomain)
    pending_comments: list[PendingComment] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


# ── PR outcome ──────────────────────────────────────────────────────────────


class PRCloseState(Enum):
    """A PR's lifecycle state as ``gh`` reports it, with the field that dates it.

    ``ended_at_field`` is the ``gh pr view --json`` key holding when the PR
    reached this state. OPEN has none — an open PR has not ended — and that is
    what ``is_terminal`` reads, so no caller restates which states retire a
    review cycle.

    OPEN is a member rather than an absence because a sweep has to tell "gh
    said the PR is open" from "gh answered with a state this code does not
    know". The first is a real answer; the second is a renamed or added state,
    which would otherwise read as still-open forever and quietly retire the
    prune.
    """

    OPEN = ("OPEN", None)
    MERGED = ("MERGED", "mergedAt")
    CLOSED = ("CLOSED", "closedAt")

    def __new__(cls, value: str, ended_at_field: str | None) -> PRCloseState:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.ended_at_field = ended_at_field
        return obj

    @property
    def is_terminal(self) -> bool:
        """Whether this state ends the PR, and with it the cycle's artifacts."""
        return self.ended_at_field is not None

    @classmethod
    def parse(cls, raw: str | None) -> PRCloseState | None:
        """The state ``gh`` named, or None for one this code does not know."""
        try:
            return cls(raw)
        except ValueError:
            return None


# What ``gh pr view --json`` has to fetch to build a PRClosure: the state, plus
# the timestamp field every terminal state dates itself with. Derived from the
# enum so a state added there is asked for without also editing the query.
GH_STATE_JSON_FIELDS = ",".join(
    ["state", *(s.ended_at_field for s in PRCloseState if s.is_terminal)]
)


@dataclass(frozen=True)
class PRClosure:
    """How a PR ended, and when.

    Only ever built for a terminal state: "still open" and "we could not ask"
    are both the absence of a closure rather than a member of one, which is what
    lets a sweep decide on the closure's presence alone.

    ``ended_at`` is GitHub's timestamp, not the sweep's clock. It is empty when
    gh has not filled the field in yet — a missing timestamp, not a PR that has
    not ended — so it never stands in for the state.
    """

    state: PRCloseState
    ended_at: str = ""


# The action on the trail's terminal event. `pr_state` owns it because it owns
# the payload's shape; `review_gc` owns the emit.
TERMINAL_SUMMARY_ACTION = "pr_outcome"


def terminal_summary(state: PRState, closure: PRClosure) -> dict:
    """How a review cycle ended, for the trail's terminal event.

    Read straight off the domains rather than recomputed: this is the last
    reading of state that is about to be deleted, not a fresh measurement.

    The closure's ``ended_at`` comes from GitHub rather than from the clock —
    the scheduled maintenance sweep is what usually runs gc, so the event's own
    ``ts`` says when that sweep noticed, up to a cycle after the merge.
    """
    return {
        "outcome": closure.state.value,
        "ended_at": closure.ended_at,
        "cost_usd": state.review.cost_usd,
        "total_tokens": state.review.total_tokens,
        "verdict": state.review.verdict,
        "finding_counts": dict(state.review.finding_counts),
        "rebase_conflicts": state.rebase.conflicts_resolved,
    }


# ── Serialization ───────────────────────────────────────────────────────────


def state_to_dict(state: PRState) -> dict:
    d = _serde_to_dict(state)
    d["_version"] = STATE_VERSION
    return d


def state_from_dict(d: dict) -> PRState:
    # Strict: a field with no dataclass default must be present or serde raises.
    # Tolerance belongs at the file level, where discarding is a real recovery —
    # see load_state.
    return _serde_from_dict(PRState, d)


# ── I/O ─────────────────────────────────────────────────────────────────────


def load_state(target_dir: Path) -> PRState | None:
    """Load unified PR state, or None if there is no usable file.

    A missing file and an unreadable one both come back as None. Nothing stored
    here is authoritative — every field is rebuilt by the command that wrote it —
    so discarding a file that will not parse is always a correct recovery, and
    the next write replaces it. `pr gc` clears one on demand.

    A path outside a git worktree reads as "no state yet" for the same reason:
    the status line runs wherever the user's shell is, and a directory that
    cannot hold state has none.

    No caller needs to tell corrupt from missing; the warning serde emits is
    what supplies the part "no state yet" leaves out.
    """
    return _serde_load_file(PRState, target_dir / STATE_FILE)


def save_state(target_dir: Path, state: PRState) -> None:
    """Save unified PR state, creating directories as needed.

    ``serde.write_json`` owns the atomicity: the status line reads this file
    from whatever shell the user is in, concurrently with the write.
    """
    state.updated_at = datetime.now(timezone.utc).isoformat()
    _serde_write_json(target_dir / STATE_FILE, state_to_dict(state))


# ── Updaters ────────────────────────────────────────────────────────────────


def new_state(
    repo: str,
    branch: str,
    pr_number: int | None,
    head_sha: str,
    worktree_root: str,
) -> PRState:
    """Create a fresh PRState with identity populated."""
    now = datetime.now(timezone.utc).isoformat()
    return PRState(
        identity=PRIdentity(
            repo=repo,
            branch=branch,
            pr_number=pr_number,
            head_sha=head_sha,
            worktree_root=worktree_root,
        ),
        created_at=now,
        updated_at=now,
    )


def update_identity(
    state: PRState,
    head_sha: str,
    pr_number: int | None = None,
    worktree_root: str = "",
) -> None:
    """Refresh identity fields that change across invocations.

    ``worktree_root`` is one of them now that the file is keyed on the target
    rather than stored inside the checkout: consecutive runs against one target
    can come from different worktrees, and consumers read this field to find the
    checkout a fix was committed in.

    Both it and ``head_sha`` are overwritten only when the incoming value is
    non-empty. A run from a bare repo has neither a worktree to name nor a
    checked-out HEAD to read, and a run with nothing to say must not erase what
    an earlier run knew: the fix summary falls back to ``identity.head_sha`` when
    it has no commit of its own, and would otherwise post an empty SHA.
    """
    if head_sha:
        state.identity.head_sha = head_sha
    if pr_number is not None:
        state.identity.pr_number = pr_number
    if worktree_root:
        state.identity.worktree_root = worktree_root


@cache
def _domains() -> dict[str, type[Domain]]:
    """Domain field name → type, derived from PRState's own annotations.

    Cached because the answer cannot change after import: PRState's fields are
    fixed at class creation.
    """
    return {
        name: hint
        for name, hint in get_type_hints(PRState).items()
        if isinstance(hint, type) and issubclass(hint, Domain)
    }


@cache
def _domain_names() -> dict[type[Domain], str]:
    """The inverse of `_domains`, for routing an update by its type.

    A domain class held by two PRState fields has no single home to write to,
    so it fails here at first use rather than silently writing to whichever
    field happened to be annotated last.
    """
    names: dict[type[Domain], str] = {}
    for name, cls in _domains().items():
        if cls in names:
            raise TypeError(
                f"{cls.__name__} is the type of both PRState.{names[cls]} and "
                f"PRState.{name}; a domain needs exactly one field to own it"
            )
        names[cls] = name
    return names


def domains_of(state: PRState) -> list[Domain]:
    """Every domain this state holds, in the order PRState declares them.

    The one way to walk a state's domains. Every consumer that used to hand-list
    them — the dashboard, the merge-readiness fold — reads this instead, so a
    domain added to PRState is picked up by all of them and by none of them
    selectively.
    """
    return [getattr(state, name) for name in _domains()]


def merge_readiness(state: PRState) -> Readiness:
    """Every domain's answer to whether the PR may merge, folded into one.

    Order follows the registry, so blockers arrive grouped by the domain that
    raised them and in the order the dashboard printed those domains above.

    Refresh ``push`` before calling this. Nothing here can tell an unobserved
    push domain from a branch that is up to date — both say nothing — so a
    caller that skips the refresh reports a branch with unpushed commits as
    ready to merge.
    """
    blockers: list[str] = []
    unchecked: list[str] = []
    for domain in domains_of(state):
        answer = domain.readiness()
        blockers.extend(answer.blockers)
        unchecked.extend(answer.unchecked)
    return Readiness(blockers=tuple(blockers), unchecked=tuple(unchecked))


def apply(state: PRState, domain: Domain) -> None:
    """Write a domain update into the field that owns it, honoring its merge policy."""
    name = _domain_names().get(type(domain))
    if name is None:
        raise ValueError(f"{type(domain).__name__} is not a PRState domain")
    setattr(state, name, domain.merge_into(getattr(state, name)))


def add_pending_comment(state: PRState, comment: PendingComment) -> None:
    """Add a deferred comment, replacing any existing entry with the same source."""
    state.pending_comments = [c for c in state.pending_comments if c.source != comment.source]
    state.pending_comments.append(comment)


def pop_pending_comments(
    state: PRState, source: str | None = None,
) -> list[PendingComment]:
    """Remove and return pending comments, optionally filtered by source."""
    if source is None:
        comments = state.pending_comments
        state.pending_comments = []
        return comments
    kept, popped = [], []
    for c in state.pending_comments:
        (popped if c.source == source else kept).append(c)
    state.pending_comments = kept
    return popped


# ── Convenience ────────────────────────────────────────────────────────────


def load_or_init(
    *,
    target_dir: Path,
    repo: str,
    branch: str,
    pr_number: int | None = None,
    head_sha: str,
    worktree_root: str = "",
) -> PRState:
    """Load existing state or create a fresh one, updating identity.

    ``target_dir`` is where the file lives; ``worktree_root`` is the checkout the
    write ran from, recorded in identity for consumers that want to find it.
    """
    state = load_state(target_dir)
    if state is not None:
        update_identity(state, head_sha, pr_number, worktree_root)
        return state
    return new_state(
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
        worktree_root=worktree_root,
    )


def apply_state_update(
    *,
    target_dir: Path,
    repo: str,
    branch: str,
    pr_number: int | None = None,
    head_sha: str,
    worktree_root: str = "",
    domain: str,
    data: dict,
) -> None:
    """Load-or-init state, apply a domain update from a dict, and save."""
    domain_cls = _domains().get(domain)
    if domain_cls is None:
        raise ValueError(f"Unknown state domain: {domain!r}")
    state = load_or_init(
        target_dir=target_dir,
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
        worktree_root=worktree_root,
    )
    apply(state, _serde_from_dict(domain_cls, data))
    save_state(target_dir, state)
