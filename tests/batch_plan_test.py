import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.plan  # noqa: E402
import pr.domains  # noqa: E402
import pr.state  # noqa: E402
import pr.target  # noqa: E402
from batch.model import Step  # noqa: E402
from review.types import ReviewMeta  # noqa: E402
from review_record_support import record, store_record  # noqa: E402
from pr.comments_fix import CloseoutDebt, FixSummary  # noqa: E402


@pytest.mark.parametrize("state,needed", [
    ("BEHIND", True), ("DIRTY", True), ("CLEAN", False), ("BLOCKED", False),
    ("UNSTABLE", False), ("DRAFT", False), ("HAS_HOOKS", False), ("UNKNOWN", True),
])
def test_rebase_need_follows_merge_state(state, needed):
    n = batch.plan.rebase_need(state)
    assert n.needed is needed and n.reason


def test_rebase_need_unknown_leaves_it_to_the_rebase_step():
    assert "the rebase step decides" in batch.plan.rebase_need("UNKNOWN").reason


def _threads_node(*threads):
    return {"reviewThreads": {"nodes": list(threads)}}


def test_comments_need_counts_unresolved_unsettled_threads(monkeypatch):
    monkeypatch.setattr(batch.plan, "_settled_from", lambda state, threads, viewer: {"T2"})
    node = _threads_node({"id": "T1", "isResolved": False}, {"id": "T2", "isResolved": False},
                         {"id": "T3", "isResolved": True})
    assert batch.plan.comments_need(node, "me", None) == \
        batch.plan.StepNeed(True, "1 unresolved thread")


def test_comments_not_needed_when_every_open_thread_is_settled(monkeypatch):
    monkeypatch.setattr(batch.plan, "_settled_from", lambda state, threads, viewer: {"T1"})
    node = _threads_node({"id": "T1", "isResolved": False})
    assert batch.plan.comments_need(node, "me", None).needed is False


def test_review_needed_when_no_file(tmp_path):
    assert batch.plan.review_need(tmp_path / "missing.md", "abc").needed is True


def test_review_needed_when_head_moved(tmp_path):
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha="1111111"))
    n = batch.plan.review_need(f, "2222222aaaa")
    assert n.needed is True and "1111111" in n.reason


def test_review_not_needed_at_same_head_even_abbreviated(tmp_path):
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha="abcdef0"))
    assert batch.plan.review_need(f, "abcdef0123456").needed is False


def test_search_query_scopes_to_me_open_and_each_repo():
    assert batch.plan.search_query(["o/a", "o/b"]) == \
        "is:pr is:open author:@me archived:false repo:o/a repo:o/b"


def test_rows_from_search_maps_nodes_and_drops_unknown_repos(monkeypatch):
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
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
    monkeypatch.setattr(batch.plan, "fetch_namespace", lambda d, ns, branches=(): False)

    class R:
        ok, stdout, stderr = False, "", "boom"

    monkeypatch.setattr(batch.plan.gh.client, "graphql", lambda *a, **k: R())
    with pytest.raises(batch.plan.PlanError, match="boom"):
        batch.plan.build_plan(["/repos/a"])


def test_build_plan_raises_when_two_checkouts_name_the_same_repo(monkeypatch):
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")
    with pytest.raises(batch.plan.PlanError, match="o/a"):
        batch.plan.build_plan(["/repos/a", "/repos/a-worktree-2"])


def test_build_plan_records_a_relative_checkout_as_absolute(monkeypatch, tmp_path):
    # A run persists repo_dir; a relative one names another directory to any later reader.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch.plan, "_repo_slug", lambda d: "o/a")
    monkeypatch.setattr(batch.plan, "fetch_namespace", lambda d, ns, branches=(): False)
    monkeypatch.setattr(batch.plan, "_local_heads", lambda d: {})
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
    node = {"number": 1, "headRefName": "b", "headRefOid": "h",
            "repository": {"nameWithOwner": "o/a"}}
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {"search": {"nodes": [node]}})
    [row] = batch.plan.build_plan(["."]).rows
    assert row.repo_dir == str(tmp_path)


def test_replan_returns_none_for_a_closed_pr(monkeypatch):
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {
        "repository": {"pullRequest": {"state": "MERGED"}}})
    row = batch.plan.PlanRow("o/a", "/r", 1, "t", "b", "h", False, {})
    assert batch.plan.replan_row(row) is None


def test_rows_from_search_matches_repos_case_insensitively(monkeypatch):
    seen = []
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
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
    monkeypatch.setattr(batch.plan, "fetch_namespace", lambda d, ns, branches=(): False)

    class R:
        ok, stdout, stderr = True, '{"errors":[{"message":"bad"}]}', ""

    monkeypatch.setattr(batch.plan.gh.client, "graphql", lambda *a, **k: R())
    with pytest.raises(batch.plan.PlanError, match="bad"):
        batch.plan.build_plan(["/repos/a"])


def _review_at(tmp_path, sha):
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha=sha))
    return f


def test_review_needed_when_local_head_is_ahead_of_github(tmp_path):
    f = _review_at(tmp_path, "aaaaaaa")
    n = batch.plan.review_need(f, "aaaaaaa1111", local_head="bbbbbbb2222")
    assert n.needed is True
    assert "aaaaaaa" in n.reason and "bbbbbbb" in n.reason and "local" in n.reason


def test_review_reason_names_both_local_and_github_heads(tmp_path):
    f = _review_at(tmp_path, "ccccccc")
    n = batch.plan.review_need(f, "aaaaaaa1111", local_head="bbbbbbb2222")
    assert "local HEAD bbbbbbb" in n.reason and "GitHub's aaaaaaa" in n.reason


def test_review_current_when_it_matches_local_head_not_github(tmp_path):
    f = _review_at(tmp_path, "bbbbbbb")
    assert batch.plan.review_need(f, "aaaaaaa1111", local_head="bbbbbbb2222").needed is False


def test_review_falls_back_to_github_head_without_a_worktree(tmp_path):
    f = _review_at(tmp_path, "aaaaaaa")
    assert batch.plan.review_need(f, "aaaaaaa1111", local_head="").needed is False


def _node(branch="b", head="remote1"):
    return {"number": 7, "title": "t", "isDraft": False, "headRefName": branch,
            "headRefOid": head, "mergeStateStatus": "CLEAN",
            "repository": {"nameWithOwner": "o/a"}, "reviewThreads": {"nodes": []}}


def test_rows_from_search_judges_review_against_the_branch_worktree_head(monkeypatch, tmp_path):
    review = _review_at(tmp_path, "remote1")
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: review)
    monkeypatch.setattr(batch.plan, "_local_heads", lambda repo_dir: {"b": "local22"})
    rows = batch.plan.rows_from_search({"search": {"nodes": [_node()]}}, {"o/a": "/repos/a"})
    assert rows[0].local_head == "local22"
    assert rows[0].needs[Step.REVIEW].needed is True


def test_local_heads_maps_each_checked_out_branch_to_its_head(monkeypatch):
    Entry = batch.plan.git.topology.WorktreeEntry
    monkeypatch.setattr(batch.plan.git.topology, "worktree_entries", lambda cwd: [
        Entry(Path("/wt/main"), "main"), Entry(Path("/wt/b"), "b"), Entry(Path("/wt/d"), None)])
    monkeypatch.setattr(batch.plan.git.client, "head_sha", lambda cwd: f"sha-of-{cwd}")
    assert batch.plan._local_heads("/repos/a") == {
        "main": "sha-of-/wt/main", "b": "sha-of-/wt/b"}


def test_replan_judges_review_against_the_rows_local_head(monkeypatch, tmp_path):
    review = _review_at(tmp_path, "remote1")
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: review)
    node = dict(_node(), state="OPEN")
    monkeypatch.setattr(batch.plan, "_graphql", lambda q, v: {"repository": {"pullRequest": node}})
    row = batch.plan.PlanRow("o/a", "/r", 7, "t", "b", "remote1", False, {}, local_head="local22")
    fresh = batch.plan.replan_row(row)
    assert fresh.local_head == "local22"
    assert fresh.needs[Step.REVIEW].needed is True


def test_ci_step_need_carries_pr_ci_need_answer():
    assert batch.plan.ci_step_need("FAILURE") == batch.plan.StepNeed(True, "checks failure")
    assert batch.plan.ci_step_need("") == batch.plan.StepNeed(False, "no checks reported")


def test_rollup_state_reads_the_last_commit():
    node = {"commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]}}
    assert batch.plan.rollup_state(node) == "FAILURE"
    assert batch.plan.rollup_state({"commits": {"nodes": [{"commit": {}}]}}) == ""


def test_a_fork_row_skips_rebase_and_ci(monkeypatch):
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: None)
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: Path("/nonexistent"))
    node = dict(_node(), mergeStateStatus="BEHIND", isCrossRepository=True,
                commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]})
    row = batch.plan._row(node, "/repos/a", "o/a")
    assert row.needs[Step.REBASE] == batch.plan.FORK_NEED
    assert row.needs[Step.CI] == batch.plan.FORK_NEED


def _saved_fix(monkeypatch, tmp_path, fix):
    """Save *fix* as the pr state for o/a branch b, reachable from tmp_path as the checkout."""
    monkeypatch.setattr(batch.plan.pr.target, "repo_key_from_origin", lambda d: "o/a")
    state = pr.state.new_state("o/a", "b", None, "h", str(tmp_path))
    state.fix = fix
    pr.state.save_state(pr.target.target_dir("o/a", "b"), state)
    return str(tmp_path)


def test_closeout_debt_reads_what_the_fix_pass_left_undelivered(monkeypatch, tmp_path):
    checkout = _saved_fix(monkeypatch, tmp_path,
                          FixSummary(summary_deferred=True, replies_pending=True))
    debt = batch.plan.closeout_debt(checkout, "b")
    assert (debt.owed, debt.summary, debt.replies) == (True, True, True)


def test_closeout_debt_is_nothing_without_saved_state(monkeypatch, tmp_path):
    monkeypatch.setattr(batch.plan.pr.target, "repo_key_from_origin", lambda d: "o/a")
    assert batch.plan.closeout_debt(str(tmp_path), "b") == CloseoutDebt()


def test_a_checkout_that_is_gone_has_no_debt(tmp_path):
    gone = str(tmp_path / "removed")
    assert batch.plan.closeout_debt(gone, "b") == CloseoutDebt()


def test_a_row_carries_the_closeout_pr_says_is_owed(monkeypatch):
    loads = []
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: loads.append(1))
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: Path("/nonexistent"))
    monkeypatch.setattr(batch.plan, "_closeout_from",
                        lambda state: CloseoutDebt(replies=True, reply_count=2))
    data = {"viewer": {"login": "me"}, "search": {"nodes": [
        {"number": 7, "title": "t", "isDraft": False, "headRefName": "b", "headRefOid": "sha",
         "mergeStateStatus": "CLEAN", "repository": {"nameWithOwner": "o/a"},
         "reviewThreads": {"nodes": []}}]}}
    [r] = batch.plan.rows_from_search(data, {"o/a": "/repos/a"})
    assert r.closeout == "2 replies"
    # One state load (one git call) per row.
    assert len(loads) == 1


def test_review_short_prefix_is_not_current(tmp_path):
    """A header shorter than MIN_SHA_PREFIX is not evidence of the same commit."""
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha="abc"))
    assert batch.plan.review_need(f, "abcdef0123456").needed is True


# ── The comments need is `pr comments`' definition of owed feedback ─────────
#
# A PR whose only feedback is a review body or a plain PR comment used to plan
# no comments step, because only review threads were counted.


def _review_body(rid=11, *, edited=None, login="rev", body="M1: rename the helper"):
    return {"databaseId": rid, "state": "COMMENTED", "body": body, "minimizedReason": None,
            "lastEditedAt": edited, "author": {"login": login}}


def _comments_need(monkeypatch, *, reviews=(), comments=(), threads=(), seen_bodies=None):
    """The planned comments need for one PR, its seen record saved as *seen_bodies*."""
    state = None
    if seen_bodies is not None:
        state = pr.state.new_state("o/a", "b", None, "h", "/repos/a")
        state.comments = pr.domains.CommentsSummary(
            seen_review_body_comments=seen_bodies, updated_at="2026-01-01T00:00:00Z")
    monkeypatch.setattr(batch.plan, "_pr_state", lambda repo_dir, branch: state)
    monkeypatch.setattr(batch.plan, "_review_file", lambda repo, branch: Path("/nonexistent"))
    node = dict(_node(), reviewThreads={"nodes": list(threads)},
                reviews={"nodes": list(reviews)}, comments={"nodes": list(comments)})
    data = {"viewer": {"login": "me"}, "search": {"nodes": [node]}}
    [row] = batch.plan.rows_from_search(data, {"o/a": "/repos/a"})
    return row.needs[Step.COMMENTS]


def test_a_pr_whose_only_feedback_is_a_review_body_plans_comments(monkeypatch):
    need = _comments_need(monkeypatch, reviews=[_review_body()])
    assert need == batch.plan.StepNeed(True, "1 unanswered review body")


def test_a_review_body_in_the_seen_record_is_not_owed(monkeypatch):
    # 11 was read by an earlier triage; 12 was not. Only 12 is owed.
    need = _comments_need(monkeypatch, reviews=[_review_body(11), _review_body(12)],
                          seen_bodies={11: ""})
    assert need == batch.plan.StepNeed(True, "1 unanswered review body")


# passes-at-base: the old planner never planned review bodies, seen or not
def test_every_review_body_seen_plans_no_comments(monkeypatch):
    need = _comments_need(monkeypatch, reviews=[_review_body(11)], seen_bodies={11: ""})
    assert need.needed is False


def test_a_seen_review_body_edited_since_is_owed_again(monkeypatch):
    need = _comments_need(monkeypatch, reviews=[_review_body(11, edited="2026-02-01T00:00:00Z")],
                          seen_bodies={11: ""})
    assert need == batch.plan.StepNeed(True, "1 unanswered review body")


def test_own_bodies_count_for_nothing_and_a_plain_pr_comment_is_owed(monkeypatch):
    comment = {"databaseId": 31, "lastEditedAt": None, "body": "why?",
               "author": {"login": "rev", "__typename": "User"}}
    bot = {"databaseId": 32, "lastEditedAt": None, "body": "deployed",
           "author": {"login": "ci", "__typename": "Bot"}}
    need = _comments_need(monkeypatch, reviews=[_review_body(login="me")],
                          comments=[comment, bot])
    assert need == batch.plan.StepNeed(True, "1 unanswered PR comment")


def test_the_reason_names_every_kind_of_owed_feedback(monkeypatch):
    need = _comments_need(monkeypatch, reviews=[_review_body(11), _review_body(12)],
                          threads=[{"id": "T1", "isResolved": False}])
    assert need == batch.plan.StepNeed(True, "1 unresolved thread, 2 unanswered review bodies")
