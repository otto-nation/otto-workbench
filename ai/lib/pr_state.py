"""Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<worktree>/.workbench/state.json``
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from enum import Enum, StrEnum
from functools import cache
from pathlib import Path
from typing import get_type_hints

# get_type_hints(CIDomain) resolves its `runs` annotation at runtime, so
# RunState must be bound in this module's namespace; ci_failures keeps its
# own pr_state import under TYPE_CHECKING, which is what keeps this acyclic.
from ci_failures import RunState
import log
from serde import from_dict as _serde_from_dict, to_dict as _serde_to_dict


STATE_DIR = ".workbench"
STATE_FILE = "state.json"
STATE_VERSION = 1


def now_iso() -> str:
    """UTC ISO timestamp for state updates."""
    return datetime.now(timezone.utc).isoformat()


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class PRIdentity:
    """Resolved once, shared by all subcommands."""
    repo: str
    branch: str
    pr_number: int | None
    head_sha: str
    worktree_root: str


class Domain:
    """A section of PRState that one subcommand owns and writes as a unit.

    Subclassing is the registration. ``_domains`` derives the registry from
    PRState's own annotations, so a new domain is one field there and nothing
    else — no updater, no table entry, no deserializer.
    """

    def merge_into(self, prior: "Domain") -> "Domain":
        """Combine this update with what is already stored.

        A write replaces what came before, which is what every domain wants
        except the ones that accumulate across rounds. Those override this and
        fold ``prior`` in themselves, so the policy lives with the shape it
        applies to rather than in the code that happens to call the write.
        """
        return self


@dataclass
class CIDomain(Domain):
    """Full CI domain — summary fields plus detailed run history.

    Merges the former CISummary (summary snapshot) with run-history tracking
    that previously lived in ci_failures.CIState. All CI state now lives in
    a single domain within PRState.
    """
    # Summary fields (formerly CISummary)
    conclusion: str = ""
    failure_count: int = 0
    failure_kinds: dict[str, int] = field(default_factory=dict)
    last_run_id: int | None = None
    last_run_number: int | None = None
    updated_at: str = ""
    # Detailed run tracking (formerly in CIState)
    # Keyed by run_id. JSON stringifies every key on the way out; serde
    # restores the ints on the way back in.
    runs: dict[int, RunState] = field(default_factory=dict)
    latest_run_id: int | None = None


class ReviewVerdict(Enum):
    APPROVE = "approve"
    CHANGES_REQUESTED = "changes_requested"
    DISAPPROVE = "disapprove"


class PostedAs(Enum):
    REVIEW = "review"
    COMMENT = "comment"


class PostEvent(Enum):
    COMMENT = "comment"
    APPROVE = "approve"
    REQUESTED_CHANGES = "requested_changes"
    DISMISSED = "dismissed"


class ReviewStatus(Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"


class RebaseStatus(Enum):
    COMPLETED = "completed"
    CONFLICTS = "conflicts"
    ABORTED = "aborted"


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


@dataclass
class ReviewSummary(Domain):
    """Snapshot written by ``pr review``."""
    review_file: str = ""
    review_type: str = ""
    head_sha: str = ""
    finding_counts: dict[str, int] = field(default_factory=dict)
    verdict: str = ""
    status: str = ""
    failure_detail: str = ""
    cost_usd: float = 0.0
    total_tokens: int = 0
    updated_at: str = ""


@dataclass
class CommentsSummary(Domain):
    """Snapshot written by ``pr comments``."""
    total_threads: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    blocking_reviewers: list[str] = field(default_factory=list)
    has_approvals: bool = False
    seen_issue_comment_ids: list[int] = field(default_factory=list)
    seen_review_body_comment_ids: list[int] = field(default_factory=list)
    updated_at: str = ""


@dataclass
class TriageSummary(Domain):
    """Snapshot written by ``pr triage``."""
    total: int = 0
    actionable: int = 0
    valid: int = 0
    questions: int = 0
    comment_items_total: int = 0
    comment_items_actionable: int = 0
    updated_at: str = ""


@dataclass
class RebaseSummary(Domain):
    """Snapshot written by ``pr rebase``."""
    status: str = ""
    target_base: str = ""
    commits_replayed: int = 0
    conflicts_resolved: int = 0
    files_resolved: list[str] = field(default_factory=list)
    files_stale: list[str] = field(default_factory=list)
    force_pushed: bool = False
    updated_at: str = ""


@dataclass
class DescribeSummary(Domain):
    """Snapshot written by ``pr describe``.

    ``head_sha`` is what makes the pass commit-aware: a description already
    written for the current HEAD does not need rewriting, so a repeated run is
    a no-op instead of another AI call against an unchanged branch.
    """
    head_sha: str = ""
    template_path: str = ""
    changed: bool = False
    updated_at: str = ""


class ThreadAction(StrEnum):
    FIXED = "fixed"
    DEFERRED = "deferred"
    NEEDS_HUMAN = "needs_human"
    DISMISSED = "dismissed"
    ALREADY_ADDRESSED = "already_addressed"


@dataclass
class ThreadOutcome:
    """Per-thread outcome from a comment processing pass."""
    id: str = ""
    file: str = ""
    line: int = 0
    reviewer: str = ""
    summary: str = ""
    action: ThreadAction = ThreadAction.FIXED
    reason: str = ""
    # The commit that landed this thread's fix. Per-outcome rather than
    # per-pass: FixSummary accumulates outcomes across rounds, so one envelope
    # SHA would relabel every earlier round's work with the latest round's
    # commit — or, when the latest round commits nothing, with none at all.
    commit_sha: str = ""

    @classmethod
    def from_entry(
        cls, entry, action: ThreadAction, reason_key: str = "reason",
    ) -> "ThreadOutcome":
        if hasattr(entry, "id"):
            return cls(
                id=entry.id,
                file=entry.file,
                line=entry.line,
                reviewer=entry.reviewer,
                summary=entry.summary,
                action=action,
                reason=getattr(entry, reason_key, ""),
                commit_sha=getattr(entry, "commit_sha", ""),
            )
        return cls(
            id=entry.get("id", entry.get("thread_id", "")),
            file=entry.get("file", ""),
            line=entry.get("line", 0),
            reviewer=entry.get("reviewer", ""),
            summary=entry.get("summary", ""),
            action=action,
            reason=entry.get(reason_key, ""),
            commit_sha=entry.get("commit_sha", ""),
        )

    @classmethod
    def _from_raw(cls, raw) -> "ThreadOutcome":
        """Rebuild an outcome from an instance or a dict, renaming a legacy key.

        `serde` hands the whole field over here rather than assuming the
        current key names: an outcome written before the field was renamed
        carries `thread_id` where the dataclass now declares `id`. Copying
        rather than popping leaves the caller's dict alone — `apply_state_update`
        is handed a payload it does not expect this function to rewrite.
        """
        if isinstance(raw, cls):
            return raw
        data = dict(raw)
        if "thread_id" in data and "id" not in data:
            data["id"] = data.pop("thread_id")
        return _serde_from_dict(cls, data)


@dataclass
class FixSummary(Domain):
    """Snapshot written by comment fix pass."""
    threads: list[ThreadOutcome] = field(default_factory=list)
    commit_sha: str = ""
    commit_status: str = ""
    # The HEAD this snapshot describes. --finish compares it against current
    # HEAD: outcomes recorded against a commit that is no longer checked out
    # describe work that may since have been done, undone, or superseded by
    # hand, and must be reconciled before anything is published.
    head_sha: str = ""
    replies_posted: int = 0
    # The fix pass produced per-thread replies but did not deliver them — the
    # push failed, or the run was a draft. --resolve drains the queue.
    replies_pending: bool = False
    summary_url: str = ""
    summary_deferred: bool = False
    deferred_issue_id: str = ""
    deferred_issue_url: str = ""
    has_comment_items: bool = False
    updated_at: str = ""

    def merge_into(self, prior: "FixSummary") -> "FixSummary":
        """Merge this fix pass into the accumulated summary.

        Thread outcomes accumulate across rounds, keyed by thread id — a later
        pass supersedes an earlier outcome for the same thread, but never drops
        threads it did not touch.  A review cycle spans several rounds and the
        summary comment must account for all of them, not just the most recent
        pass.

        The deferred tracking issue is likewise cycle-scoped: it is created once
        and updated on later rounds.  A fix pass builds its FixSummary before
        knowing about it, so an empty id/url means "not set this round", not
        "cleared" — dropping it would make the next deferred round open a
        duplicate issue.  Every other field is per-round and comes from this pass.
        """
        merged = {t.id: t for t in prior.threads if t.id}
        no_id: list[ThreadOutcome] = [t for t in prior.threads if not t.id]
        for outcome in self.threads:
            if outcome.id:
                merged[outcome.id] = outcome
            else:
                # Entries without an id cannot be de-duplicated; append rather than
                # colliding every one onto the "" key and losing all but the last.
                # ceiling: this list only grows across rounds. No-id outcomes are
                # rare and a cycle's rounds are bounded, so the growth is bounded in
                # practice — de-dup on content if a cycle ever accumulates enough to
                # bloat the state file or the summary comment.
                no_id.append(outcome)
        return dataclass_replace(
            self,
            threads=list(merged.values()) + no_id,
            deferred_issue_id=self.deferred_issue_id or prior.deferred_issue_id,
            deferred_issue_url=self.deferred_issue_url or prior.deferred_issue_url,
        )


@dataclass
class PendingComment:
    """A PR comment deferred until a blocking condition clears."""
    body: str = ""
    source: str = ""
    updated_at: str = ""


@dataclass
class PRState:
    """Unified PR state — envelope over domain summaries."""
    identity: PRIdentity
    ci: CIDomain = field(default_factory=CIDomain)
    review: ReviewSummary = field(default_factory=ReviewSummary)
    comments: CommentsSummary = field(default_factory=CommentsSummary)
    triage: TriageSummary = field(default_factory=TriageSummary)
    rebase: RebaseSummary = field(default_factory=RebaseSummary)
    describe: DescribeSummary = field(default_factory=DescribeSummary)
    fix: FixSummary = field(default_factory=FixSummary)
    pending_comments: list[PendingComment] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


# ── Serialization ───────────────────────────────────────────────────────────


def state_to_dict(state: PRState) -> dict:
    d = _serde_to_dict(state)
    d["_version"] = STATE_VERSION
    return d


def state_from_dict(d: dict) -> PRState:
    # ceiling: strict reconstruction — a field with no dataclass default must be present in
    # the file or serde raises TypeError. Every writer has always been dataclasses.asdict,
    # which emits every field, so no shape ever written can be missing one. The state file
    # is a regenerable per-worktree cache, so the recovery is `rm -rf .workbench/`. Upgrade
    # to catching and returning None (as PipelineState.load does) if it ever fires in
    # practice, or give the field a default if it becomes genuinely optional.
    return _serde_from_dict(PRState, d)


# ── I/O ─────────────────────────────────────────────────────────────────────


def load_state(worktree_root: Path) -> PRState | None:
    """Load unified PR state. Returns None if file doesn't exist."""
    path = worktree_root / STATE_DIR / STATE_FILE
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return state_from_dict(data)


def _ensure_gitignored(worktree_root: Path) -> None:
    """Append .workbench/ to the repo's .gitignore if not already ignored.

    Skips silently when worktree_root is not inside a git repository.
    """
    try:
        toplevel = subprocess.run(
            ["git", "-C", str(worktree_root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if toplevel.returncode != 0:
            return
    except FileNotFoundError:
        return

    result = subprocess.run(
        ["git", "-C", str(worktree_root), "check-ignore", "-q", STATE_DIR],
        capture_output=True,
    )
    if result.returncode == 0:
        return

    repo_root = Path(toplevel.stdout.strip())
    gitignore = repo_root / ".gitignore"
    needs_newline = False
    if gitignore.exists():
        content = gitignore.read_text()
        needs_newline = content and not content.endswith("\n")
    prefix = "\n" if needs_newline else ""
    with open(gitignore, "a") as f:
        f.write(f"{prefix}\n# Worktree-local state (pr CLI)\n{STATE_DIR}/\n")
    log.info(f"pr_state: added {STATE_DIR}/ to .gitignore")


def save_state(worktree_root: Path, state: PRState) -> None:
    """Save unified PR state, creating directories as needed.

    Writes to a per-process temp file and renames it over the target.
    ``open(path, "w")`` truncates in place, so a concurrent reader can
    observe a zero-byte file and fail with a JSONDecodeError; os.replace
    is atomic, so readers see either the old state or the new one.
    """
    path = worktree_root / STATE_DIR / STATE_FILE
    created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if created:
        _ensure_gitignored(worktree_root)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    # Per-process, not per-call: two threads in one process saving at once
    # would share this name. Nothing here saves off the main thread, and the
    # worktree lock already keeps other processes out.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(state_to_dict(state), f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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


def update_identity(state: PRState, head_sha: str, pr_number: int | None = None) -> None:
    """Refresh identity fields that change across invocations."""
    state.identity.head_sha = head_sha
    if pr_number is not None:
        state.identity.pr_number = pr_number


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
    worktree_root: Path,
    repo: str,
    branch: str,
    pr_number: int | None = None,
    head_sha: str,
) -> PRState:
    """Load existing state or create a fresh one, updating identity."""
    state = load_state(worktree_root)
    if state is not None:
        update_identity(state, head_sha, pr_number)
        return state
    return new_state(
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
        worktree_root=str(worktree_root),
    )


def apply_state_update(
    *,
    worktree_root: Path,
    repo: str,
    branch: str,
    pr_number: int | None = None,
    head_sha: str,
    domain: str,
    data: dict,
) -> None:
    """Load-or-init state, apply a domain update from a dict, and save."""
    domain_cls = _domains().get(domain)
    if domain_cls is None:
        raise ValueError(f"Unknown state domain: {domain!r}")
    state = load_or_init(
        worktree_root=worktree_root,
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
    )
    apply(state, _serde_from_dict(domain_cls, data))
    save_state(worktree_root, state)
