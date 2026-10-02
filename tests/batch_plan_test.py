import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.plan  # noqa: E402
from batch.model import Step  # noqa: E402


@pytest.mark.parametrize("state,needed", [
    ("BEHIND", True), ("DIRTY", True), ("CLEAN", False), ("BLOCKED", False),
    ("UNSTABLE", False), ("DRAFT", False), ("HAS_HOOKS", False), ("UNKNOWN", False),
])
def test_rebase_need_follows_merge_state(state, needed):
    n = batch.plan.rebase_need(state)
    assert n.needed is needed and n.reason


def test_rebase_need_unknown_says_github_is_computing():
    assert "computing" in batch.plan.rebase_need("UNKNOWN").reason


def test_comments_need_counts_unresolved_unsettled_threads():
    threads = [{"id": "T1", "isResolved": False}, {"id": "T2", "isResolved": False},
               {"id": "T3", "isResolved": True}]
    assert batch.plan.comments_need(threads, settled={"T2"}) == batch.plan.StepNeed(True, "1 unresolved thread")


def test_comments_not_needed_when_every_open_thread_is_settled():
    assert batch.plan.comments_need([{"id": "T1", "isResolved": False}], {"T1"}).needed is False


def test_review_needed_when_no_file(tmp_path):
    assert batch.plan.review_need(tmp_path / "missing.md", "abc").needed is True


def test_review_needed_when_head_moved(tmp_path):
    f = tmp_path / "review.md"
    f.write_text("<!-- head_sha: 1111111 -->\n# Review\n")
    n = batch.plan.review_need(f, "2222222aaaa")
    assert n.needed is True and "1111111" in n.reason


def test_review_not_needed_at_same_head_even_abbreviated(tmp_path):
    f = tmp_path / "review.md"
    f.write_text("<!-- head_sha: abcdef0 -->\n# Review\n")
    assert batch.plan.review_need(f, "abcdef0123456").needed is False


def test_search_query_scopes_to_me_open_and_each_repo():
    assert batch.plan.search_query(["o/a", "o/b"]) == \
        "is:pr is:open author:@me archived:false repo:o/a repo:o/b"


def test_rows_from_search_maps_nodes_and_drops_unknown_repos(monkeypatch):
    monkeypatch.setattr(batch.plan, "settled_ids", lambda repo_dir, branch: set())
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: Path("/nonexistent"))
    data = {"viewer": {"login": "me"}, "search": {"nodes": [
        {"number": 7, "title": "t", "isDraft": True, "headRefName": "b", "headRefOid": "sha",
         "mergeStateStatus": "BEHIND", "repository": {"nameWithOwner": "o/a"},
         "reviewThreads": {"nodes": [{"id": "T1", "isResolved": False}]}},
        {"number": 8, "title": "x", "isDraft": False, "headRefName": "c", "headRefOid": "s2",
         "mergeStateStatus": "CLEAN", "repository": {"nameWithOwner": "o/zzz"},
         "reviewThreads": {"nodes": []}},
    ]}}
    rows = batch.plan.rows_from_search(data, {"o/a": "/repos/a"})
    assert [r.key for r in rows] == ["o/a#7"]
    r = rows[0]
    assert r.repo_dir == "/repos/a" and r.is_draft
    assert all(r.needs[s].needed for s in (Step.REBASE, Step.COMMENTS, Step.REVIEW))


def test_build_plan_raises_on_graphql_failure(monkeypatch):
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")

    class R:
        ok, stdout, stderr = False, "", "boom"

    monkeypatch.setattr(batch.plan.gh.client, "graphql", lambda *a, **k: R())
    with pytest.raises(batch.plan.PlanError, match="boom"):
        batch.plan.build_plan(["/repos/a"])


def test_build_plan_raises_when_two_checkouts_name_the_same_repo(monkeypatch):
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")
    with pytest.raises(batch.plan.PlanError, match="o/a"):
        batch.plan.build_plan(["/repos/a", "/repos/a-worktree-2"])


def test_replan_returns_none_for_a_closed_pr(monkeypatch):
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {
        "repository": {"pullRequest": {"state": "MERGED"}}})
    row = batch.plan.PlanRow("o/a", "/r", 1, "t", "b", "h", False, {})
    assert batch.plan.replan_row(row) is None


def test_rows_from_search_matches_repos_case_insensitively(monkeypatch):
    seen = []
    monkeypatch.setattr(batch.plan, "settled_ids", lambda repo_dir, branch: set())
    monkeypatch.setattr(
        batch.plan, "_review_file",
        lambda repo, branch: seen.append(repo) or Path("/nonexistent"),
    )
    data = {"search": {"nodes": [
        {"number": 7, "title": "t", "isDraft": True, "headRefName": "b",
         "headRefOid": "sha", "mergeStateStatus": "BEHIND",
         "repository": {"nameWithOwner": "O/A"},
         "reviewThreads": {"nodes": []}},
    ]}}
    rows = batch.plan.rows_from_search(data, {"o/a": "/repos/a"})
    assert [r.key for r in rows] == ["o/a#7"]
    assert rows[0].repo == "o/a"
    assert seen == ["o/a"]


def test_graphql_wraps_ok_payload_with_errors(monkeypatch):
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")

    class R:
        ok, stdout, stderr = True, '{"errors":[{"message":"bad"}]}', ""

    monkeypatch.setattr(batch.plan.gh.client, "graphql", lambda *a, **k: R())
    with pytest.raises(batch.plan.PlanError, match="bad"):
        batch.plan.build_plan(["/repos/a"])
