"""The PR board's vocabulary: schema version, lanes, badges, and row facts.

Stdlib only. Enum string values are the wire spellings; renaming a member is
free, changing a value is a schema break. Optional fields do not bump
``schema_version``; a removed or renamed field or a changed type does, and
enforcement comes from ``SCHEMA_VERSIONS`` being allowed to shrink.
"""

# doc-group: board

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


SCHEMA_VERSIONS = (1,)

# What this build's `pr` accepts that a board consumer drives; add one when a
# consumer must tell a newer `pr` from an older one; never remove one without
# a schema bump.
FEATURES = ("review-publish",)


class Phase(StrEnum):
    LISTING = "listing"
    STATUS = "status"
    OWED = "owed"


class RepoPhase(StrEnum):
    QUEUED = "queued"
    LOADING = "loading"
    HYDRATING = "hydrating"
    LOADED = "loaded"
    FAILED = "failed"


class Board(StrEnum):
    MINE = "mine"
    TEAM = "team"


class Mergeable(StrEnum):
    MERGEABLE = "MERGEABLE"
    CONFLICTING = "CONFLICTING"
    UNKNOWN = "UNKNOWN"


class CiStatus(StrEnum):
    PASSING = "passing"
    FAILING = "failing"
    PENDING = "pending"
    NONE = "none"


class PrState(StrEnum):
    OPEN = "OPEN"
    MERGED = "MERGED"
    CLOSED = "CLOSED"


class TeamLane(StrEnum):
    OPEN = "open"
    NEEDS_MY_REVIEW = "needs-my-review"
    IN_QUEUE = "in-queue"
    REVIEW_READY = "review-ready"
    REVIEWED = "reviewed"
    LANDED = "landed"


class MineLane(StrEnum):
    HAS_CONFLICTS = "has-conflicts"
    CI_FAILING = "ci-failing"
    HAS_COMMENTS = "has-comments"
    CHANGES_REQUESTED = "changes-requested"
    IN_REVIEW = "in-review"
    AWAITING_REVIEW = "awaiting-review"
    APPROVED = "approved"
    IN_QUEUE = "in-queue"
    MERGED = "merged"


class IssueKind(StrEnum):
    CONFLICTS = "conflicts"
    CI_FAILING = "ci-failing"
    CI_RUNNING = "ci-running"
    CI_UNKNOWN = "ci-unknown"
    CI_PENDING = "ci-pending"
    HAS_COMMENTS = "has-comments"
    CHANGES_REQUESTED = "changes-requested"


class StatusKind(StrEnum):
    """DESIGN.md's status kinds; ``_lib/status.js`` keys them in this order."""

    PASS = "pass"
    FAIL = "fail"
    RUNNING = "running"
    WARN = "warn"
    NONE = "none"


class ReviewHealth(StrEnum):
    PARTIAL = "Partial"
    ERROR = "Error"
    OK = "ok"


@dataclass(frozen=True)
class Column:
    board: Board
    key: str
    label: str
    order: int


@dataclass(frozen=True)
class Issue:
    kind: IssueKind
    label: str
    status: StatusKind


@dataclass(frozen=True)
class LatestReview:
    login: str
    state: str


@dataclass(frozen=True)
class RunPostRef:
    event: str
    posted_at: str = ""
    submitted: bool | None = None


@dataclass(frozen=True)
class ReviewRunRef:
    posted_event: str | None = None
    head_sha: str = ""
    status: str = ""
    reviewed_at: str = ""
    review_file: str = ""
    verdict: str = ""
    findings: object | None = None
    stamp: str = ""
    structured: bool = True
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    post: RunPostRef | None = None
    models: str = ""


@dataclass(frozen=True)
class Lanes:
    team: TeamLane
    mine: MineLane
    team_reviewing: TeamLane
    mine_reviewing: MineLane


@dataclass(frozen=True)
class RepoError:
    kind: str
    message: str


@dataclass(frozen=True)
class RepoStatus:
    slug: str
    host: str
    checkout: str
    phase: RepoPhase
    error: RepoError | None = None
    partial: bool = False
    fetched_at: str = ""


@dataclass(frozen=True)
class PullFacts:
    """Facts a listing fetch can fill without GraphQL."""

    repo: str
    number: int
    title: str = ""
    author: str = ""
    branch: str = ""
    base_branch: str = ""
    url: str = ""
    head_sha: str = ""
    labels: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    merged_at: str | None = None
    is_draft: bool = False
    state: PrState = PrState.OPEN


@dataclass(frozen=True)
class StatusFacts:
    """Facts a status (GraphQL) fetch fills."""

    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    in_merge_queue: bool = False
    mergeable: Mergeable = Mergeable.UNKNOWN
    ci_status: CiStatus = CiStatus.NONE
    ci_unknown: bool = True
    review_decision: str | None = None
    latest_reviews: tuple[LatestReview, ...] = ()
    requested_reviewers: tuple[str, ...] = ()
    merged_at: str | None = None
    state: PrState | None = None


@dataclass(frozen=True)
class Row:
    """One board row's facts; lanes and issues are computed from this."""

    repo: str = ""
    number: int = 0
    title: str = ""
    author: str = ""
    branch: str = ""
    base_branch: str = ""
    url: str = ""
    head_sha: str = ""
    labels: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    merged_at: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    is_draft: bool = False
    state: PrState = PrState.OPEN
    in_merge_queue: bool = False
    mergeable: Mergeable = Mergeable.MERGEABLE
    ci_status: CiStatus = CiStatus.NONE
    ci_unknown: bool = False
    hydrated: bool = True
    review_decision: str | None = None
    latest_reviews: tuple[LatestReview, ...] = ()
    requested_reviewers: tuple[str, ...] = ()
    review_runs: tuple[ReviewRunRef, ...] = ()
    my_review_state: str | None = None
    is_requested_reviewer: bool = False
    has_new_changes_since_review: bool = False
    review_health: ReviewHealth = ReviewHealth.OK
    busy: bool = False
    is_mine: bool = False
    owed: str | None = None
    owed_known: bool = False
