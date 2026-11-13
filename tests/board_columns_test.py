"""Lane and issue classification for the PR board document."""

from board.columns import (
    MINE_COLUMNS,
    TEAM_COLUMNS,
    columns,
    issues,
    lanes,
    mine_lane,
    team_lane,
)
from board.model import (
    Board,
    CiStatus,
    Column,
    Issue,
    IssueKind,
    Mergeable,
    MineLane,
    PrState,
    ReviewRunRef,
    Row,
    StatusKind,
    TeamLane,
)
import pytest


def _row(**kwargs) -> Row:
    return Row(**kwargs)


# 15 Python team-lane cases: 14 on team_lane, reviewing as team_reviewing.
TEAM_LANE_CASES = [
    ("merged -> landed", dict(state=PrState.MERGED), TeamLane.LANDED),
    ("inMergeQueue -> landed", dict(in_merge_queue=True), TeamLane.LANDED),
    (
        "merged beats isRequestedReviewer",
        dict(state=PrState.MERGED, is_requested_reviewer=True),
        TeamLane.LANDED,
    ),
    (
        "isRequestedReviewer -> needs-my-review",
        dict(is_requested_reviewer=True),
        TeamLane.NEEDS_MY_REVIEW,
    ),
    (
        "hasNewChangesSinceReview -> needs-my-review",
        dict(has_new_changes_since_review=True),
        TeamLane.NEEDS_MY_REVIEW,
    ),
    (
        "needs-my-review beats an unposted run",
        dict(
            is_requested_reviewer=True,
            review_runs=(ReviewRunRef(posted_event=None),),
        ),
        TeamLane.NEEDS_MY_REVIEW,
    ),
    (
        "unposted newer than posted -> review-ready",
        dict(
            review_runs=(
                ReviewRunRef(posted_event=None),
                ReviewRunRef(posted_event="approve"),
            ),
        ),
        TeamLane.REVIEW_READY,
    ),
    (
        "unposted older than newest posted -> reviewed",
        dict(
            review_runs=(
                ReviewRunRef(posted_event="approve"),
                ReviewRunRef(posted_event=None),
            ),
        ),
        TeamLane.REVIEWED,
    ),
    (
        "only unposted run (never posted) -> review-ready",
        dict(review_runs=(ReviewRunRef(posted_event=None),)),
        TeamLane.REVIEW_READY,
    ),
    (
        "pending latest run -> review-ready",
        dict(review_runs=(ReviewRunRef(posted_event="pending"),)),
        TeamLane.REVIEW_READY,
    ),
    (
        "dismissed counts as posted -> reviewed",
        dict(
            review_runs=(
                ReviewRunRef(posted_event="dismissed"),
                ReviewRunRef(posted_event=None),
            ),
        ),
        TeamLane.REVIEWED,
    ),
    (
        "myReviewState APPROVED without runs -> reviewed",
        dict(my_review_state="APPROVED"),
        TeamLane.REVIEWED,
    ),
    (
        "runs all posted -> reviewed",
        dict(
            review_runs=(
                ReviewRunRef(posted_event="approve"),
                ReviewRunRef(posted_event="comment"),
            ),
        ),
        TeamLane.REVIEWED,
    ),
    ("nothing -> open", dict(), TeamLane.OPEN),
]


# 17 Python mine-lane cases minus the reviewing overlay (tested separately).
MINE_LANE_CASES = [
    ("merged", dict(state=PrState.MERGED), MineLane.MERGED),
    ("inMergeQueue -> in-queue", dict(in_merge_queue=True), MineLane.IN_QUEUE),
    (
        "merged beats inMergeQueue",
        dict(state=PrState.MERGED, in_merge_queue=True),
        MineLane.MERGED,
    ),
    (
        "mergeable CONFLICTING -> has-conflicts",
        dict(mergeable=Mergeable.CONFLICTING),
        MineLane.HAS_CONFLICTS,
    ),
    (
        "CONFLICTING beats ciStatus failing",
        dict(mergeable=Mergeable.CONFLICTING, ci_status=CiStatus.FAILING),
        MineLane.HAS_CONFLICTS,
    ),
    ("ci failing -> ci-failing", dict(ci_status=CiStatus.FAILING), MineLane.CI_FAILING),
    (
        "ci-failing beats CHANGES_REQUESTED",
        dict(ci_status=CiStatus.FAILING, review_decision="CHANGES_REQUESTED"),
        MineLane.CI_FAILING,
    ),
    (
        "owed feedback -> has-comments, ahead of changes requested and draft",
        dict(
            owed="2 unresolved threads",
            review_decision="CHANGES_REQUESTED",
            is_draft=True,
        ),
        MineLane.HAS_COMMENTS,
    ),
    (
        "conflicts come before has-comments",
        dict(owed="x", mergeable=Mergeable.CONFLICTING),
        MineLane.HAS_CONFLICTS,
    ),
    (
        "failing CI comes before has-comments",
        dict(owed="x", ci_status=CiStatus.FAILING),
        MineLane.CI_FAILING,
    ),
    (
        "CHANGES_REQUESTED -> changes-requested",
        dict(review_decision="CHANGES_REQUESTED"),
        MineLane.CHANGES_REQUESTED,
    ),
    (
        "changes-requested beats isDraft",
        dict(review_decision="CHANGES_REQUESTED", is_draft=True),
        MineLane.CHANGES_REQUESTED,
    ),
    ("a draft is awaiting-review", dict(is_draft=True), MineLane.AWAITING_REVIEW),
    (
        "a draft with conflicts is has-conflicts",
        dict(is_draft=True, mergeable=Mergeable.CONFLICTING),
        MineLane.HAS_CONFLICTS,
    ),
    (
        "a draft with failing CI is ci-failing",
        dict(is_draft=True, ci_status=CiStatus.FAILING),
        MineLane.CI_FAILING,
    ),
    (
        "APPROVED + ciStatus passing -> approved",
        dict(review_decision="APPROVED", ci_status=CiStatus.PASSING),
        MineLane.APPROVED,
    ),
    (
        "APPROVED + none -> approved",
        dict(review_decision="APPROVED", ci_status=CiStatus.NONE),
        MineLane.APPROVED,
    ),
    (
        "APPROVED + pending -> awaiting-review",
        dict(review_decision="APPROVED", ci_status=CiStatus.PENDING),
        MineLane.AWAITING_REVIEW,
    ),
    ("default -> awaiting-review", dict(), MineLane.AWAITING_REVIEW),
]


def _issue(kind: IssueKind, status: StatusKind, label: str) -> Issue:
    return Issue(kind=kind, label=label, status=status)


ISSUES_CASES = [
    ("clean PR has none", dict(), ()),
    (
        "conflicts, failing CI, owed feedback, changes requested in order",
        dict(
            mergeable=Mergeable.CONFLICTING,
            ci_status=CiStatus.FAILING,
            owed="2 unresolved threads",
            review_decision="CHANGES_REQUESTED",
        ),
        (
            _issue(IssueKind.CONFLICTS, StatusKind.FAIL, "Conflicts"),
            _issue(IssueKind.CI_FAILING, StatusKind.FAIL, "CI failing"),
            _issue(IssueKind.HAS_COMMENTS, StatusKind.WARN, "2 unresolved threads"),
            _issue(IssueKind.CHANGES_REQUESTED, StatusKind.WARN, "Changes requested"),
        ),
    ),
    (
        "pending CI is ci-running",
        dict(ci_status=CiStatus.PENDING),
        (_issue(IssueKind.CI_RUNNING, StatusKind.RUNNING, "CI running"),),
    ),
    (
        "ciUnknown with none is ci-unknown",
        dict(ci_unknown=True, ci_status=CiStatus.NONE),
        (_issue(IssueKind.CI_UNKNOWN, StatusKind.NONE, "CI unknown"),),
    ),
    (
        "failing CI takes the one CI slot",
        dict(ci_unknown=True, ci_status=CiStatus.FAILING),
        (_issue(IssueKind.CI_FAILING, StatusKind.FAIL, "CI failing"),),
    ),
    (
        "owed feedback label is the first clause",
        dict(owed="2 unresolved threads, 1 new comment, 3 drafts"),
        (_issue(IssueKind.HAS_COMMENTS, StatusKind.WARN, "2 unresolved threads"),),
    ),
    (
        "MERGED with conflicts is empty",
        dict(state=PrState.MERGED, mergeable=Mergeable.CONFLICTING),
        (),
    ),
    (
        "conflicting reviewing PR still lists conflicts",
        dict(mergeable=Mergeable.CONFLICTING),
        (_issue(IssueKind.CONFLICTS, StatusKind.FAIL, "Conflicts"),),
    ),
]


@pytest.mark.parametrize(
    "name,kwargs,expected", TEAM_LANE_CASES, ids=[c[0] for c in TEAM_LANE_CASES],
)
def test_team_lane(name, kwargs, expected):
    assert team_lane(_row(**kwargs)) is expected


@pytest.mark.parametrize(
    "name,kwargs,expected", MINE_LANE_CASES, ids=[c[0] for c in MINE_LANE_CASES],
)
def test_mine_lane(name, kwargs, expected):
    assert mine_lane(_row(**kwargs)) is expected


@pytest.mark.parametrize(
    "name,kwargs,expected", ISSUES_CASES, ids=[c[0] for c in ISSUES_CASES],
)
def test_issues(name, kwargs, expected):
    assert issues(_row(**kwargs)) == expected


def test_reviewing_lanes():
    merged = lanes(_row(state=PrState.MERGED))
    assert merged.team is TeamLane.LANDED
    assert merged.team_reviewing is TeamLane.IN_QUEUE
    assert merged.mine is MineLane.MERGED
    assert merged.mine_reviewing is MineLane.MERGED

    queued = lanes(_row(in_merge_queue=True))
    assert queued.team is TeamLane.LANDED
    assert queued.team_reviewing is TeamLane.IN_QUEUE
    assert queued.mine is MineLane.IN_QUEUE
    assert queued.mine_reviewing is MineLane.IN_QUEUE

    resting = lanes(_row())
    assert resting.team is TeamLane.OPEN
    assert resting.team_reviewing is TeamLane.IN_QUEUE
    assert resting.mine is MineLane.AWAITING_REVIEW
    assert resting.mine_reviewing is MineLane.IN_REVIEW

    blocked = lanes(_row(
        mergeable=Mergeable.CONFLICTING,
        ci_status=CiStatus.FAILING,
    ))
    assert blocked.mine is MineLane.HAS_CONFLICTS
    assert blocked.mine_reviewing is MineLane.IN_REVIEW
    assert blocked.team_reviewing is TeamLane.IN_QUEUE


def test_draft_has_no_special_case():
    draft = dict(is_draft=True)
    open_draft = _row(**draft)
    assert team_lane(open_draft) is team_lane(_row())
    assert mine_lane(open_draft) is mine_lane(_row())
    assert issues(open_draft) == issues(_row())

    conflicted = dict(mergeable=Mergeable.CONFLICTING)
    assert mine_lane(_row(is_draft=True, **conflicted)) is mine_lane(_row(**conflicted))
    failing = dict(ci_status=CiStatus.FAILING)
    assert mine_lane(_row(is_draft=True, **failing)) is mine_lane(_row(**failing))
    requested = dict(review_decision="CHANGES_REQUESTED")
    assert mine_lane(_row(is_draft=True, **requested)) is mine_lane(_row(**requested))

    keys = [col.key for col in columns()]
    assert "draft" not in keys
    assert MineLane.IN_REVIEW in {col.key for col in MINE_COLUMNS}


def test_columns_golden():
    assert TEAM_COLUMNS == (
        Column(Board.TEAM, "open", "Open", 0),
        Column(Board.TEAM, "needs-my-review", "Needs My Review", 1),
        Column(Board.TEAM, "in-queue", "In Queue", 2),
        Column(Board.TEAM, "review-ready", "Review Ready", 3),
        Column(Board.TEAM, "reviewed", "Reviewed", 4),
        Column(Board.TEAM, "landed", "Landed", 5),
    )
    assert MINE_COLUMNS == (
        Column(Board.MINE, "has-conflicts", "Has Conflicts", 0),
        Column(Board.MINE, "ci-failing", "CI Failing", 1),
        Column(Board.MINE, "has-comments", "Has Comments", 2),
        Column(Board.MINE, "changes-requested", "Changes Requested", 3),
        Column(Board.MINE, "in-review", "In Review", 4),
        Column(Board.MINE, "awaiting-review", "Awaiting Review", 5),
        Column(Board.MINE, "approved", "Approved", 6),
        Column(Board.MINE, "in-queue", "In Queue", 7),
        Column(Board.MINE, "merged", "Merged", 8),
    )
    assert columns() == TEAM_COLUMNS + MINE_COLUMNS


def test_unhydrated_issues_are_ci_pending_plus_owed():
    assert issues(_row(hydrated=False)) == (
        _issue(IssueKind.CI_PENDING, StatusKind.NONE, "CI loading"),
    )
    assert issues(_row(hydrated=False, owed="2 unresolved threads, extra")) == (
        _issue(IssueKind.CI_PENDING, StatusKind.NONE, "CI loading"),
        _issue(IssueKind.HAS_COMMENTS, StatusKind.WARN, "2 unresolved threads"),
    )
    assert issues(_row(
        hydrated=False,
        mergeable=Mergeable.CONFLICTING,
        ci_status=CiStatus.FAILING,
        review_decision="CHANGES_REQUESTED",
    )) == (
        _issue(IssueKind.CI_PENDING, StatusKind.NONE, "CI loading"),
    )
