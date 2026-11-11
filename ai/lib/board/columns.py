"""Classify a PR row into team / mine lanes and issue badges.

Python emits both the resting lanes and the reviewing overrides; a client
picks ``lanes.*_reviewing`` when its terminals show a review running. A draft
is not a lane.
"""

# doc-group: board

from __future__ import annotations

from board.model import (
    Board,
    CiStatus,
    Column,
    Issue,
    IssueKind,
    IssueStatus,
    Lanes,
    Mergeable,
    MineLane,
    PrState,
    Row,
    TeamLane,
)


def _col(board: Board, key: str, label: str, order: int) -> Column:
    return Column(board=board, key=key, label=label, order=order)


TEAM_COLUMNS: tuple[Column, ...] = (
    _col(Board.TEAM, TeamLane.OPEN, "Open", 0),
    _col(Board.TEAM, TeamLane.NEEDS_MY_REVIEW, "Needs My Review", 1),
    _col(Board.TEAM, TeamLane.IN_QUEUE, "In Queue", 2),
    _col(Board.TEAM, TeamLane.REVIEW_READY, "Review Ready", 3),
    _col(Board.TEAM, TeamLane.REVIEWED, "Reviewed", 4),
    _col(Board.TEAM, TeamLane.LANDED, "Landed", 5),
)

MINE_COLUMNS: tuple[Column, ...] = (
    _col(Board.MINE, MineLane.HAS_CONFLICTS, "Has Conflicts", 0),
    _col(Board.MINE, MineLane.CI_FAILING, "CI Failing", 1),
    _col(Board.MINE, MineLane.HAS_COMMENTS, "Has Comments", 2),
    _col(Board.MINE, MineLane.CHANGES_REQUESTED, "Changes Requested", 3),
    _col(Board.MINE, MineLane.IN_REVIEW, "In Review", 4),
    _col(Board.MINE, MineLane.AWAITING_REVIEW, "Awaiting Review", 5),
    _col(Board.MINE, MineLane.APPROVED, "Approved", 6),
    _col(Board.MINE, MineLane.IN_QUEUE, "In Queue", 7),
    _col(Board.MINE, MineLane.MERGED, "Merged", 8),
)


def team_lane(row: Row) -> TeamLane:
    if row.state is PrState.MERGED or row.in_merge_queue:
        return TeamLane.LANDED
    if row.is_requested_reviewer or row.has_new_changes_since_review:
        return TeamLane.NEEDS_MY_REVIEW
    runs = row.review_runs
    if runs:
        latest_posted_idx = next(
            (i for i, run in enumerate(runs) if run.posted_event is not None),
            -1,
        )
        if latest_posted_idx == -1:
            has_newer_unposted = any(run.posted_event is None for run in runs)
        else:
            has_newer_unposted = any(
                run.posted_event is None for run in runs[:latest_posted_idx]
            )
        if has_newer_unposted:
            return TeamLane.REVIEW_READY
    if row.my_review_state:
        return TeamLane.REVIEWED
    if runs:
        return TeamLane.REVIEWED
    return TeamLane.OPEN


def mine_lane(row: Row) -> MineLane:
    if row.state is PrState.MERGED:
        return MineLane.MERGED
    if row.in_merge_queue:
        return MineLane.IN_QUEUE
    if row.mergeable is Mergeable.CONFLICTING:
        return MineLane.HAS_CONFLICTS
    if row.ci_status is CiStatus.FAILING:
        return MineLane.CI_FAILING
    if row.owed:
        return MineLane.HAS_COMMENTS
    if row.review_decision == "CHANGES_REQUESTED":
        return MineLane.CHANGES_REQUESTED
    if row.review_decision == "APPROVED" and row.ci_status in {
        CiStatus.PASSING,
        CiStatus.NONE,
    }:
        return MineLane.APPROVED
    return MineLane.AWAITING_REVIEW


def lanes(row: Row) -> Lanes:
    team = team_lane(row)
    mine = mine_lane(row)
    mine_reviewing = (
        mine if mine in {MineLane.MERGED, MineLane.IN_QUEUE} else MineLane.IN_REVIEW
    )
    return Lanes(
        team=team,
        mine=mine,
        team_reviewing=TeamLane.IN_QUEUE,
        mine_reviewing=mine_reviewing,
    )


def issues(row: Row) -> tuple[Issue, ...]:
    if row.state is PrState.MERGED:
        return ()
    if not row.hydrated:
        pending = Issue(
            kind=IssueKind.CI_PENDING,
            label="CI pending",
            status=IssueStatus.NONE,
        )
        if not row.owed:
            return (pending,)
        return (pending, _owed_issue(row.owed))
    out: list[Issue] = []
    if row.mergeable is Mergeable.CONFLICTING:
        out.append(Issue(
            kind=IssueKind.CONFLICTS,
            label="Conflicts",
            status=IssueStatus.FAIL,
        ))
    if row.ci_status is CiStatus.FAILING:
        out.append(Issue(
            kind=IssueKind.CI_FAILING,
            label="CI failing",
            status=IssueStatus.FAIL,
        ))
    elif row.ci_status is CiStatus.PENDING:
        out.append(Issue(
            kind=IssueKind.CI_RUNNING,
            label="CI running",
            status=IssueStatus.RUNNING,
        ))
    elif row.ci_unknown:
        out.append(Issue(
            kind=IssueKind.CI_UNKNOWN,
            label="CI unknown",
            status=IssueStatus.NONE,
        ))
    if row.owed:
        out.append(_owed_issue(row.owed))
    if row.review_decision == "CHANGES_REQUESTED":
        out.append(Issue(
            kind=IssueKind.CHANGES_REQUESTED,
            label="Changes requested",
            status=IssueStatus.WARN,
        ))
    return tuple(out)


def columns() -> tuple[Column, ...]:
    return TEAM_COLUMNS + MINE_COLUMNS


def _owed_issue(owed: str) -> Issue:
    return Issue(
        kind=IssueKind.HAS_COMMENTS,
        label=owed.split(",")[0],
        status=IssueStatus.WARN,
    )
