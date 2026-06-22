"""Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<worktree>/ignore/pr/state.json``
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


STATE_DIR = "ignore/pr"
STATE_FILE = "state.json"


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


@dataclass
class CISummary:
    """Snapshot written by ``pr ci``."""
    last_run_id: int | None = None
    last_run_number: int | None = None
    conclusion: str = ""
    failure_count: int = 0
    failure_kinds: dict[str, int] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class ReviewSummary:
    """Snapshot written by ``pr review``."""
    review_file: str = ""
    review_type: str = ""
    head_sha: str = ""
    finding_counts: dict[str, int] = field(default_factory=dict)
    verdict: str = ""
    cost_usd: float = 0.0
    updated_at: str = ""


@dataclass
class CommentsSummary:
    """Snapshot written by ``pr comments``."""
    total_threads: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    blocking_reviewers: list[str] = field(default_factory=list)
    has_approvals: bool = False
    updated_at: str = ""


@dataclass
class TriageSummary:
    """Snapshot written by ``pr triage``."""
    total: int = 0
    actionable: int = 0
    valid: int = 0
    questions: int = 0
    updated_at: str = ""


@dataclass
class RebaseSummary:
    """Snapshot written by ``pr rebase``."""
    target_base: str = ""
    commits_replayed: int = 0
    conflicts_resolved: int = 0
    files_resolved: list[str] = field(default_factory=list)
    force_pushed: bool = False
    updated_at: str = ""


@dataclass
class PRState:
    """Unified PR state — envelope over domain summaries."""
    identity: PRIdentity
    ci: CISummary = field(default_factory=CISummary)
    review: ReviewSummary = field(default_factory=ReviewSummary)
    comments: CommentsSummary = field(default_factory=CommentsSummary)
    triage: TriageSummary = field(default_factory=TriageSummary)
    rebase: RebaseSummary = field(default_factory=RebaseSummary)
    created_at: str = ""
    updated_at: str = ""


# ── Serialization ───────────────────────────────────────────────────────────


def _identity_to_dict(ident: PRIdentity) -> dict:
    return {
        "repo": ident.repo,
        "branch": ident.branch,
        "pr_number": ident.pr_number,
        "head_sha": ident.head_sha,
        "worktree_root": ident.worktree_root,
    }


def _identity_from_dict(d: dict) -> PRIdentity:
    return PRIdentity(
        repo=d["repo"],
        branch=d["branch"],
        pr_number=d.get("pr_number"),
        head_sha=d.get("head_sha", ""),
        worktree_root=d.get("worktree_root", ""),
    )


def _ci_to_dict(ci: CISummary) -> dict:
    return {
        "last_run_id": ci.last_run_id,
        "last_run_number": ci.last_run_number,
        "conclusion": ci.conclusion,
        "failure_count": ci.failure_count,
        "failure_kinds": ci.failure_kinds,
        "updated_at": ci.updated_at,
    }


def _ci_from_dict(d: dict) -> CISummary:
    return CISummary(
        last_run_id=d.get("last_run_id"),
        last_run_number=d.get("last_run_number"),
        conclusion=d.get("conclusion", ""),
        failure_count=d.get("failure_count", 0),
        failure_kinds=d.get("failure_kinds", {}),
        updated_at=d.get("updated_at", ""),
    )


def _review_to_dict(rev: ReviewSummary) -> dict:
    return {
        "review_file": rev.review_file,
        "review_type": rev.review_type,
        "head_sha": rev.head_sha,
        "finding_counts": rev.finding_counts,
        "verdict": rev.verdict,
        "cost_usd": rev.cost_usd,
        "updated_at": rev.updated_at,
    }


def _review_from_dict(d: dict) -> ReviewSummary:
    return ReviewSummary(
        review_file=d.get("review_file", ""),
        review_type=d.get("review_type", ""),
        head_sha=d.get("head_sha", ""),
        finding_counts=d.get("finding_counts", {}),
        verdict=d.get("verdict", ""),
        cost_usd=d.get("cost_usd", 0.0),
        updated_at=d.get("updated_at", ""),
    )


def _comments_to_dict(c: CommentsSummary) -> dict:
    return {
        "total_threads": c.total_threads,
        "by_state": c.by_state,
        "blocking_reviewers": c.blocking_reviewers,
        "has_approvals": c.has_approvals,
        "updated_at": c.updated_at,
    }


def _comments_from_dict(d: dict) -> CommentsSummary:
    return CommentsSummary(
        total_threads=d.get("total_threads", 0),
        by_state=d.get("by_state", {}),
        blocking_reviewers=d.get("blocking_reviewers", []),
        has_approvals=d.get("has_approvals", False),
        updated_at=d.get("updated_at", ""),
    )


def _triage_to_dict(t: TriageSummary) -> dict:
    return {
        "total": t.total,
        "actionable": t.actionable,
        "valid": t.valid,
        "questions": t.questions,
        "updated_at": t.updated_at,
    }


def _triage_from_dict(d: dict) -> TriageSummary:
    return TriageSummary(
        total=d.get("total", 0),
        actionable=d.get("actionable", 0),
        valid=d.get("valid", 0),
        questions=d.get("questions", 0),
        updated_at=d.get("updated_at", ""),
    )


def _rebase_to_dict(r: RebaseSummary) -> dict:
    return {
        "target_base": r.target_base,
        "commits_replayed": r.commits_replayed,
        "conflicts_resolved": r.conflicts_resolved,
        "files_resolved": r.files_resolved,
        "force_pushed": r.force_pushed,
        "updated_at": r.updated_at,
    }


def _rebase_from_dict(d: dict) -> RebaseSummary:
    return RebaseSummary(
        target_base=d.get("target_base", ""),
        commits_replayed=d.get("commits_replayed", 0),
        conflicts_resolved=d.get("conflicts_resolved", 0),
        files_resolved=d.get("files_resolved", []),
        force_pushed=d.get("force_pushed", False),
        updated_at=d.get("updated_at", ""),
    )


def state_to_dict(state: PRState) -> dict:
    return {
        "identity": _identity_to_dict(state.identity),
        "ci": _ci_to_dict(state.ci),
        "review": _review_to_dict(state.review),
        "comments": _comments_to_dict(state.comments),
        "triage": _triage_to_dict(state.triage),
        "rebase": _rebase_to_dict(state.rebase),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def state_from_dict(d: dict) -> PRState:
    return PRState(
        identity=_identity_from_dict(d["identity"]),
        ci=_ci_from_dict(d.get("ci", {})),
        review=_review_from_dict(d.get("review", {})),
        comments=_comments_from_dict(d.get("comments", {})),
        triage=_triage_from_dict(d.get("triage", {})),
        rebase=_rebase_from_dict(d.get("rebase", {})),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
    )


# ── I/O ─────────────────────────────────────────────────────────────────────


def load_state(worktree_root: Path) -> PRState | None:
    """Load unified PR state. Returns None if file doesn't exist."""
    path = worktree_root / STATE_DIR / STATE_FILE
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return state_from_dict(data)


def save_state(worktree_root: Path, state: PRState) -> None:
    """Save unified PR state, creating directories as needed."""
    path = worktree_root / STATE_DIR / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(state_to_dict(state), f, indent=2)
        f.write("\n")


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


def update_ci(state: PRState, summary: CISummary) -> None:
    """Replace CI summary."""
    state.ci = summary


def update_review(state: PRState, summary: ReviewSummary) -> None:
    """Replace review summary."""
    state.review = summary


def update_comments(state: PRState, summary: CommentsSummary) -> None:
    """Replace comments summary."""
    state.comments = summary


def update_triage(state: PRState, summary: TriageSummary) -> None:
    """Replace triage summary."""
    state.triage = summary


def update_rebase(state: PRState, summary: RebaseSummary) -> None:
    """Replace rebase summary."""
    state.rebase = summary


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


_DOMAIN_DESERIALIZERS: dict[str, tuple[Callable, Callable]] = {
    "ci": (_ci_from_dict, update_ci),
    "review": (_review_from_dict, update_review),
    "comments": (_comments_from_dict, update_comments),
    "triage": (_triage_from_dict, update_triage),
    "rebase": (_rebase_from_dict, update_rebase),
}


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
    if domain not in _DOMAIN_DESERIALIZERS:
        raise ValueError(f"Unknown state domain: {domain!r}")
    from_dict, updater = _DOMAIN_DESERIALIZERS[domain]
    state = load_or_init(
        worktree_root=worktree_root,
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
    )
    updater(state, from_dict(data))
    save_state(worktree_root, state)
