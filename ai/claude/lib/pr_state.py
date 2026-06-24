"""Unified PR state framework.

Provides a summary envelope over per-domain state files (CI failures,
PR comments, review artifacts). Each ``pr`` subcommand updates its own
section; ``pr status`` reads the whole thing without network calls.

State file: ``<worktree>/.workbench/state.json``
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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


@dataclass
class CIDomain:
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
    runs: dict = field(default_factory=dict)
    latest_run_id: int | None = None


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
    ci: CIDomain = field(default_factory=CIDomain)
    review: ReviewSummary = field(default_factory=ReviewSummary)
    comments: CommentsSummary = field(default_factory=CommentsSummary)
    triage: TriageSummary = field(default_factory=TriageSummary)
    rebase: RebaseSummary = field(default_factory=RebaseSummary)
    created_at: str = ""
    updated_at: str = ""


# ── Serialization ───────────────────────────────────────────────────────────


def _identity_to_dict(ident: PRIdentity) -> dict:
    return _serde_to_dict(ident)


def _identity_from_dict(d: dict) -> PRIdentity:
    return _serde_from_dict(PRIdentity, d)


def _run_state_from_dict(d: dict):
    """Deserialize a RunState including nested FailureGroup/FailureItem objects."""
    from ci_failures import FailureGroup, FailureItem, FailureKind, Outcome, RunState
    failures = {}
    for group_key, group_data in d.get("failures", {}).items():
        items = []
        for item_data in group_data.get("items", []):
            outcome_val = item_data.get("outcome")
            items.append(FailureItem(
                id=item_data["id"],
                annotation=item_data.get("annotation", ""),
                file=item_data.get("file"),
                line=item_data.get("line"),
                diagnosis=item_data.get("diagnosis"),
                fix_sha=item_data.get("fix_sha"),
                outcome=Outcome(outcome_val) if outcome_val else None,
            ))
        failures[group_key] = FailureGroup(
            job=group_data.get("job", group_key),
            kind=FailureKind(group_data["kind"]),
            items=tuple(items),
        )
    return RunState(
        run_id=d["run_id"],
        run_number=d.get("run_number", 0),
        head_sha=d.get("head_sha", ""),
        status=d.get("status", ""),
        conclusion=d.get("conclusion", ""),
        fetched_at=d.get("fetched_at", ""),
        failures=failures,
    )


def _ci_from_dict(d: dict) -> CIDomain:
    """Deserialize CIDomain, handling nested RunState objects in runs dict."""
    d = dict(d)
    runs_raw = d.pop("runs", {})
    domain = _serde_from_dict(CIDomain, d)
    if runs_raw:
        domain.runs = {str(k): _run_state_from_dict(v) for k, v in runs_raw.items()}
    return domain


def _review_from_dict(d: dict) -> ReviewSummary:
    return _serde_from_dict(ReviewSummary, d)


def _comments_from_dict(d: dict) -> CommentsSummary:
    return _serde_from_dict(CommentsSummary, d)


def _triage_from_dict(d: dict) -> TriageSummary:
    return _serde_from_dict(TriageSummary, d)


def _rebase_from_dict(d: dict) -> RebaseSummary:
    return _serde_from_dict(RebaseSummary, d)


def _ci_to_dict(ci: CIDomain) -> dict:
    """Serialize CIDomain, handling nested RunState objects in runs dict."""
    d = _serde_to_dict(ci)
    # runs values are RunState dataclasses; serde.to_dict already handles them
    # but keys must be strings for JSON compatibility
    if ci.runs:
        d["runs"] = {str(k): _serde_to_dict(v) for k, v in ci.runs.items()}
    return d


def state_to_dict(state: PRState) -> dict:
    d = _serde_to_dict(state)
    d["_version"] = STATE_VERSION
    # identity is a required positional arg — serialize separately
    d["identity"] = _identity_to_dict(state.identity)
    # ci.runs needs special handling for RunState nested objects
    if state.ci.runs:
        d["ci"]["runs"] = {str(k): _serde_to_dict(v) for k, v in state.ci.runs.items()}
    return d


def state_from_dict(d: dict) -> PRState:
    ci_data = dict(d.get("ci", {}))
    runs_raw = ci_data.pop("runs", {})
    ci_domain = _serde_from_dict(CIDomain, ci_data)
    if runs_raw:
        ci_domain.runs = {str(k): _run_state_from_dict(v) for k, v in runs_raw.items()}

    return PRState(
        identity=_identity_from_dict(d["identity"]),
        ci=ci_domain,
        review=_serde_from_dict(ReviewSummary, d.get("review", {})),
        comments=_serde_from_dict(CommentsSummary, d.get("comments", {})),
        triage=_serde_from_dict(TriageSummary, d.get("triage", {})),
        rebase=_serde_from_dict(RebaseSummary, d.get("rebase", {})),
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


def update_ci_domain(state: PRState, domain: CIDomain) -> None:
    """Replace CI domain (summary + run history)."""
    state.ci = domain


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
    "ci": (_ci_from_dict, update_ci_domain),
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
    domain_from_dict, updater = _DOMAIN_DESERIALIZERS[domain]
    state = load_or_init(
        worktree_root=worktree_root,
        repo=repo,
        branch=branch,
        pr_number=pr_number,
        head_sha=head_sha,
    )
    updater(state, domain_from_dict(data))
    save_state(worktree_root, state)
