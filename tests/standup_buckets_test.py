"""Standup bucket classification — no GitHub."""

from cli.standup import (
    APPROVED,
    BLOCKED,
    DRAFT,
    WAITING,
    bucket_open,
    ci_fail_names,
    ci_pending,
    item_from_merged,
    item_from_open,
)


def _pr(**overlay):
    body = {
        "number": 1,
        "title": "t",
        "url": "https://github.com/o/r/pull/1",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "reviewDecision": "REVIEW_REQUIRED",
        "additions": 10,
        "deletions": 2,
        "statusCheckRollup": [{"name": "Unit", "conclusion": "SUCCESS"}],
    }
    body.update(overlay)
    return body


def test_waiting_when_mergeable_green_not_approved():
    assert bucket_open(_pr()) == WAITING


def test_draft_wins_over_conflicts():
    assert bucket_open(_pr(isDraft=True, mergeable="CONFLICTING")) == DRAFT


def test_approved_even_if_ci_red():
    assert bucket_open(_pr(
        reviewDecision="APPROVED",
        statusCheckRollup=[{"name": "Unit", "conclusion": "FAILURE"}],
    )) == APPROVED


def test_conflicts_are_blocked():
    assert bucket_open(_pr(mergeable="CONFLICTING")) == BLOCKED


def test_unknown_mergeable_is_blocked():
    assert bucket_open(_pr(mergeable="UNKNOWN")) == BLOCKED


def test_failing_check_is_blocked():
    assert bucket_open(_pr(
        statusCheckRollup=[{"name": "Lint", "conclusion": "FAILURE"}],
    )) == BLOCKED


def test_pending_check_is_blocked():
    assert bucket_open(_pr(
        statusCheckRollup=[{"name": "Unit", "state": "IN_PROGRESS"}],
    )) == BLOCKED


def test_ci_fail_names_skips_success():
    assert ci_fail_names([
        {"name": "Unit", "conclusion": "SUCCESS"},
        {"name": "Lint", "conclusion": "FAILURE"},
    ]) == ["Lint"]


def test_ci_pending_reads_state_when_conclusion_missing():
    assert ci_pending([{"name": "Unit", "state": "PENDING"}])


def test_item_from_open_carries_bucket_and_fail():
    row = item_from_open(_pr(
        statusCheckRollup=[{"name": "Lint", "conclusion": "FAILURE"}],
        mergeable="MERGEABLE",
    ), "o/r")
    assert row["bucket"] == BLOCKED
    assert row["fail"] == ["Lint"]
    assert row["repo"] == "o/r"


def test_item_from_merged_reads_search_repo():
    row = item_from_merged({
        "number": 9,
        "title": "landed",
        "url": "https://github.com/o/r/pull/9",
        "additions": 4,
        "closedAt": "2026-11-20T00:00:00Z",
        "repository": {"nameWithOwner": "o/r"},
    })
    assert row["bucket"] == "landed"
    assert row["repo"] == "o/r"
    assert row["deletions"] is None
