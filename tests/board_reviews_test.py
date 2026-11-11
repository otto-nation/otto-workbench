"""Join of review listing rows onto GitHub PR records."""

from datetime import datetime, timedelta, timezone

from board.model import ReviewHealth
from board.reviews import (
    join,
    keep_landed,
    landed_candidates,
    posted,
    runs_for,
    runs_for_pr,
)


def _row(**over):
    base = {
        "repo": "o/n",
        "mode": "pr",
        "pr_number": 7,
        "host": "github.com",
        "head_sha": "a",
        "post": None,
        "reviewed_at": "2026-01-02",
        "status": "completed",
        "history": [],
    }
    base.update(over)
    return base


def _pr(**over):
    base = {
        "number": 7,
        "head_sha": "a",
        "latest_reviews": [],
        "requested_reviewers": [],
    }
    base.update(over)
    return base


def test_join_matches_repo_exactly():
    prs = [_pr()]
    listing = [_row(repo="o/nn"), _row(repo="p/n"), _row(repo="o/n-x")]
    assert join(prs, listing, "o/n", "github.com", "me")[0].review_runs == ()


def test_join_host_filter_excludes_other_hosts_and_includes_absent_host():
    other = join([_pr()], [_row(host="ghe.example.com")], "o/n", "github.com", "me")
    missing = _row()
    del missing["host"]
    absent = join([_pr()], [missing], "o/n", "github.com", "me")
    assert [len(other[0].review_runs), len(absent[0].review_runs)] == [0, 1]


def test_join_ignores_mode_self_rows():
    got = join([_pr()], [_row(mode="self")], "o/n", "github.com", "me")
    assert len(got[0].review_runs) == 0


def test_history_runs_follow_the_row_sparse_history_entries_pass_through():
    listing = [_row(head_sha="c", history=[{"head_sha": "b", "post": {"event": "comment"}}])]
    runs = join([_pr()], listing, "o/n", "github.com", "me")[0].review_runs
    assert [(r.posted_event, r.head_sha, r.status) for r in runs] == [
        (None, "c", "completed"),
        ("comment", "b", ""),
    ]


def test_posted_event_and_posted_for_null_dismissed_and_empty_event():
    null_event = runs_for(_row(post=None))[0].posted_event
    dismissed = runs_for(_row(post={"event": "dismissed"}))[0].posted_event
    empty = runs_for(_row(post={"event": ""}))[0].posted_event
    assert [
        null_event,
        dismissed,
        empty,
        posted(_row(post=None)),
        posted(_row(post={"event": "dismissed"})),
        posted(_row(post={"event": ""})),
    ] == [None, "dismissed", "", False, True, True]


def test_my_review_state_github_latest_reviews_wins_over_runs():
    prs = [_pr(latest_reviews=[{"login": "me", "state": "APPROVED"}])]
    listing = [_row(post={"event": "comment"})]
    assert join(prs, listing, "o/n", "github.com", "me")[0].my_review_state == "APPROVED"


def test_my_review_state_dismissed_and_pending_fall_back_to_newest_posted_run():
    prs = [_pr(latest_reviews=[
        {"login": "me", "state": "DISMISSED"},
        {"login": "me", "state": "PENDING"},
    ])]
    listing = [_row(post={"event": "approve"})]
    assert join(prs, listing, "o/n", "github.com", "me")[0].my_review_state == "APPROVED"


def test_my_review_state_maps_each_posted_event():
    mapped = [
        join([_pr()], [_row(post={"event": event})], "o/n", "github.com", "me")[0].my_review_state
        for event in ("approve", "requested_changes", "comment")
    ]
    assert mapped == ["APPROVED", "CHANGES_REQUESTED", "COMMENTED"]


def test_my_review_state_is_null_for_a_dismissed_only_run():
    got = join([_pr()], [_row(post={"event": "dismissed"})], "o/n", "github.com", "me")
    assert got[0].my_review_state is None


def test_my_review_state_compares_login_case_insensitively():
    prs = [_pr(latest_reviews=[{"login": "ME", "state": "COMMENTED"}])]
    assert join(prs, [], "o/n", "github.com", "me")[0].my_review_state == "COMMENTED"


def test_is_requested_reviewer_true_with_no_review_and_false_once_a_review_exists():
    none = join(
        [_pr(requested_reviewers=["ME"])], [], "o/n", "github.com", "me",
    )[0].is_requested_reviewer
    reviewed = join(
        [_pr(
            requested_reviewers=["me"],
            latest_reviews=[{"login": "me", "state": "APPROVED"}],
        )],
        [],
        "o/n",
        "github.com",
        "me",
    )[0].is_requested_reviewer
    assert [none, reviewed] == [True, False]


def test_has_new_changes_since_review_true_when_head_differs_false_when_same_or_no_runs():
    differs = join([_pr(head_sha="b")], [_row()], "o/n", "github.com", "me")
    same = join([_pr()], [_row()], "o/n", "github.com", "me")
    none = join([_pr(head_sha="b")], [], "o/n", "github.com", "me")
    assert [
        differs[0].has_new_changes_since_review,
        same[0].has_new_changes_since_review,
        none[0].has_new_changes_since_review,
    ] == [True, False, False]


def test_health_partial_error_and_ok():
    partial = join([_pr()], [_row(status="partial")], "o/n", "github.com", "me")
    error = join([_pr()], [_row(status="error")], "o/n", "github.com", "me")
    ok = join([_pr()], [_row()], "o/n", "github.com", "me")
    empty = join([_pr()], [], "o/n", "github.com", "me")
    assert [
        partial[0].review_health,
        error[0].review_health,
        ok[0].review_health,
        empty[0].review_health,
    ] == [ReviewHealth.PARTIAL, ReviewHealth.ERROR, ReviewHealth.OK, ReviewHealth.OK]


def test_join_does_not_mutate_inputs():
    prs = [_pr(latest_reviews=[{"login": "me", "state": "APPROVED"}])]
    listing = [_row(post={"event": "comment"}, history=[{"head_sha": "z"}])]
    before_prs = repr(prs)
    before_listing = repr(listing)
    join(prs, listing, "o/n", "github.com", "me")
    assert repr(prs) == before_prs
    assert repr(listing) == before_listing


def test_malformed_listing_yields_empty_review_runs_for_every_pr():
    runs = [
        join([_pr()], listing, "o/n", "github.com", "me")[0].review_runs
        for listing in (None, {}, "x")
    ]
    assert runs == [(), (), ()]


def test_landed_candidates_excludes_open_prs_newest_first_unique_capped_at_100():
    rows = [_row(pr_number=i, reviewed_at=str(i).zfill(3)) for i in range(1, 121)]
    capped = landed_candidates([], rows, "o/n", "github.com")
    excl = landed_candidates(
        [{"number": 7}],
        [
            _row(pr_number=7),
            _row(pr_number=8, reviewed_at="2026-01-03"),
            _row(pr_number=8, reviewed_at="2026-01-01"),
            _row(pr_number=9, reviewed_at="2026-01-02"),
        ],
        "o/n",
        "github.com",
    )
    assert {"len": len(capped), "first": capped[0], "last": capped[99], "excl": excl} == {
        "len": 100,
        "first": 120,
        "last": 21,
        "excl": [8, 9],
    }


def test_keep_landed_keeps_29_days_drops_31_days_missing_merged_at_and_closed_unmerged():
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    day = timedelta(days=1)

    def iso(stamp: datetime) -> str:
        return stamp.isoformat().replace("+00:00", "Z")

    kept = keep_landed(
        [
            {"n": 29, "merged_at": iso(now - 29 * day)},
            {"n": 31, "merged_at": iso(now - 31 * day)},
            {"n": "missing"},
            {"n": "unmerged", "merged_at": None},
            {"n": "empty", "merged_at": ""},
        ],
        now,
    )
    assert [entry["n"] for entry in kept] == [29]


def test_runs_for_pr_returns_empty_when_no_row_matches():
    assert runs_for_pr([_row(pr_number=8), _row(mode="self")], "o/n", "github.com", 7) == ()


def test_runs_for_pr_returns_the_matching_row_then_history():
    listing = [_row(head_sha="b", history=[{"head_sha": "a", "reviewed_at": "2026-01-01"}])]
    runs = runs_for_pr(listing, "o/n", "github.com", 7)
    assert [run.head_sha for run in runs] == ["b", "a"]
    assert runs[0].reviewed_at == "2026-01-02"
    assert runs[1].reviewed_at == "2026-01-01"


def test_runs_for_pr_ignores_other_prs_and_self_later_reviewed_at_wins():
    listing = [
        _row(pr_number=8, head_sha="x"),
        _row(mode="self", head_sha="y"),
        _row(reviewed_at="2026-01-01", head_sha="a"),
        _row(reviewed_at="2026-01-03", head_sha="c", history=[{"head_sha": "h"}]),
    ]
    assert [run.head_sha for run in runs_for_pr(listing, "o/n", "github.com", 7)] == ["c", "h"]
