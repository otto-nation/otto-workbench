"""pr.state updaters: identity, routing a domain to its field, merge readiness and the
dashboard."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from pr.comments_fix import CLOSEOUT_COMMAND, FixSummary
from pr.domains import (
    CIDomain,
    CommentsSummary,
    TriageSummary,
    RebaseSummary,
    PushDomain,
    ReviewSummary,
    ReviewVerdict,
    ReviewStatus,
)
from pr.fix import FixOutcome, FixRecord, ItemOutcome
from pr.state import (
    PRIdentity,
    PendingComment,
    PRState,
    load_state,
    save_state,
    new_state,
    update_identity,
    apply,
    _domains,
    domains_of,
    merge_readiness,
    render_dashboard,
    render_merge_readiness,
    state_to_dict,
    state_from_dict,
)


# ── Updaters ────────────────────────────────────────────────────────────────


def test_update_identity_refreshes_sha():
    state = new_state("repo", "branch", pr_number=None, head_sha="old", worktree_root="/wt")
    update_identity(state, head_sha="new", pr_number=42)
    assert state.identity.head_sha == "new"
    assert state.identity.pr_number == 42


def test_update_identity_preserves_pr_when_none():
    state = new_state("repo", "branch", pr_number=7, head_sha="old", worktree_root="/wt")
    update_identity(state, head_sha="new")
    assert state.identity.pr_number == 7


@pytest.mark.parametrize("name,cls", sorted(_domains().items()))
def test_apply_routes_every_domain_to_its_own_field(name, cls):
    """A domain update reaches the PRState field annotated with its type, and no other.

    Parametrized off the derived registry, so a new domain is covered here the
    day its field lands on PRState. Routing by type is the whole job of the
    registry, and misrouting is silent — the write succeeds, just into the
    wrong field.
    """
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")

    apply(state, cls(updated_at="marker"))

    assert getattr(state, name).updated_at == "marker"
    assert [n for n in _domains() if getattr(state, n).updated_at] == [name]


def test_domains_of_yields_every_registered_domain_in_declaration_order():
    """Declaration order is display order — `pr status` prints this sequence."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")

    assert [type(d) for d in domains_of(state)] == list(_domains().values())


def test_merge_readiness_gathers_blockers_and_unchecked_from_every_domain():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CIDomain(conclusion="failure", updated_at="t"))
    apply(state, CommentsSummary(blocking_reviewers=["alice"], updated_at="t"))

    answer = merge_readiness(state)

    assert answer.blockers == ("CI failing", "blocking reviewers")
    # Review never ran, and no other domain reports on whether it may merge.
    assert answer.unchecked == ("review",)


# A stamp recent enough that the staleness fold vouches for it. A bare "t"
# parses as no time at all, which now reads as a domain nobody can date — fine
# for a test that only needs "this domain was written", wrong for one whose
# subject is whether the PR reads as ready.
_JUST_NOW = datetime.now(timezone.utc).isoformat()


def test_merge_readiness_is_empty_when_every_domain_is_clean():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", updated_at=_JUST_NOW))
    apply(state, ReviewSummary(finding_counts={"S": 1}, updated_at=_JUST_NOW))
    apply(state, CommentsSummary(updated_at=_JUST_NOW))
    apply(state, PushDomain(ahead=0, updated_at=_JUST_NOW))

    assert merge_readiness(state).blockers == ()
    assert merge_readiness(state).unchecked == ()


def test_render_merge_readiness_delegates_to_readiness_render():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CIDomain(conclusion="failure", updated_at="t"))

    assert render_merge_readiness(state) == merge_readiness(state).render()
    assert "CI failing" in render_merge_readiness(state)


def _green_state():
    """Everything checked, clean and current — anything blocked is the closeout."""
    state = new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", updated_at=_JUST_NOW))
    apply(state, ReviewSummary(
        finding_counts={"S": 1}, verdict=ReviewVerdict.APPROVE.value,
        updated_at=_JUST_NOW,
    ))
    apply(state, CommentsSummary(blocking_reviewers=[], updated_at=_JUST_NOW))
    return state


def test_render_merge_readiness_blocked_by_a_deferred_summary():
    state = _green_state()
    apply(state, FixSummary(summary_deferred=True, updated_at="t"))
    result = render_merge_readiness(state)
    assert "blocked" in result
    assert "closeout not delivered" in result
    assert CLOSEOUT_COMMAND in result


def test_render_merge_readiness_blocked_by_a_pending_reply_queue():
    state = _green_state()
    apply(state, FixSummary(replies_pending=True, updated_at="t"))
    assert "closeout not delivered" in render_merge_readiness(state)


def test_render_merge_readiness_blocked_by_an_unfiled_tracking_issue():
    """Deferred comments with nowhere to live are not a mergeable state."""
    state = _green_state()
    apply(state, FixSummary(deferred_issue_pending=True, updated_at="t"))
    result = render_merge_readiness(state)
    assert "ready" not in result.lower()
    assert "closeout not delivered" in result


def test_render_merge_readiness_ignores_a_drained_closeout():
    state = _green_state()
    apply(state, FixSummary(
        fix=FixRecord(
            items=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED)],
        ),
        summary_url="https://example.test/c/1", replies_posted=1,
        updated_at=_JUST_NOW,
    ))
    result = render_merge_readiness(state)
    assert "closeout" not in result
    assert "ready" in result.lower()


def test_render_dashboard_without_state_is_the_header_and_live_push():
    push = PushDomain(ahead=0, updated_at="t")
    lines = render_dashboard(None, push, repo="acme/widget", branch="feat/x")
    assert lines[0] == "## PR Status — acme/widget (no PR) (feat/x)"
    assert "No status data yet. Run: pr ci, pr review, or pr comments" in lines
    assert "**Push**: up to date" in lines


def test_render_dashboard_push_refresh_is_visible_in_state_to_dict():
    """The stdout dump reads the same object this mutates.

    Rendering from a copy would leave the caller's push stale, and
    ``json.dump(state_to_dict(state))`` would silently change.
    """
    state = new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                      worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", updated_at="t"))
    push = PushDomain(ahead=2, updated_at="now")

    render_dashboard(state, push, repo="acme/widget", branch="feat/x")

    dumped = state_to_dict(state)
    assert dumped["push"]["ahead"] == 2
    assert dumped["push"]["updated_at"] == "now"
    assert state.push is push


def test_apply_rejects_a_type_no_field_holds():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    with pytest.raises(ValueError, match="not a PRState domain"):
        apply(state, PendingComment())


def test_apply_replaces_ci_domain():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", updated_at="t1"))
    assert state.ci.conclusion == "success"
    apply(state, CIDomain(conclusion="failure", failure_count=1, updated_at="t2"))
    assert state.ci.conclusion == "failure"
    assert state.ci.failure_count == 1


def test_apply_replaces_review():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, ReviewSummary(verdict=ReviewVerdict.APPROVE.value, updated_at="t1"))
    assert state.review.verdict == ReviewVerdict.APPROVE.value


def test_review_summary_status_default():
    rev = ReviewSummary()
    assert rev.status == ""


def test_review_summary_status_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    apply(state, ReviewSummary(
        verdict=ReviewVerdict.APPROVE.value, status=ReviewStatus.ERROR.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.status == ReviewStatus.ERROR.value


def test_review_summary_status_completed_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    apply(state, ReviewSummary(
        verdict=ReviewVerdict.APPROVE.value, status=ReviewStatus.COMPLETED.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.status == ReviewStatus.COMPLETED.value


def test_review_summary_verdict_disapprove_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    apply(state, ReviewSummary(
        verdict=ReviewVerdict.DISAPPROVE.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.verdict == ReviewVerdict.DISAPPROVE.value


def test_apply_replaces_comments():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CommentsSummary(total_threads=3, updated_at="t1"))
    assert state.comments.total_threads == 3


def test_apply_comments_with_seen_ids():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CommentsSummary(
        total_threads=2, seen_issue_comments={100: "", 200: ""}, updated_at="t1",
    ))
    assert state.comments.seen_issue_comments == {100: "", 200: ""}
    apply(state, CommentsSummary(
        total_threads=3, seen_issue_comments={100: "", 200: "", 300: ""},
        updated_at="t2",
    ))
    assert state.comments.seen_issue_comments == {100: "", 200: "", 300: ""}


def test_apply_replaces_triage():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, TriageSummary(total=5, actionable=2, updated_at="t1"))
    assert state.triage.total == 5
    apply(state, TriageSummary(total=10, actionable=4, valid=3, updated_at="t2"))
    assert state.triage.total == 10
    assert state.triage.actionable == 4
    assert state.triage.valid == 3


def test_rebase_summary_defaults():
    rb = RebaseSummary()
    assert rb.target_base == ""
    assert rb.commits_replayed == 0
    assert rb.conflicts_resolved == 0
    assert rb.files_resolved == []
    assert rb.files_stale == []
    assert rb.force_pushed is False
    assert rb.updated_at == ""


def test_pr_state_has_rebase_field():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.rebase.target_base == ""
    assert state.rebase.force_pushed is False


def test_apply_replaces_rebase():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="t1",
    ))
    assert state.rebase.target_base == "origin/main"
    assert state.rebase.commits_replayed == 3
    assert state.rebase.conflicts_resolved == 2
    assert state.rebase.files_resolved == ["a.py", "b.py"]
    assert state.rebase.force_pushed is True


def test_state_roundtrip_with_rebase_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=5,
        conflicts_resolved=2, files_resolved=["x.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.rebase.target_base == "origin/main"
    assert restored.rebase.commits_replayed == 5
    assert restored.rebase.conflicts_resolved == 2
    assert restored.rebase.files_resolved == ["x.py"]
    assert restored.rebase.force_pushed is True


def test_state_roundtrip_with_stale_files():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=1,
        conflicts_resolved=1, files_resolved=["pnpm-lock.yaml"],
        files_stale=["pnpm-lock.yaml"],
        force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
    ))
    restored = state_from_dict(state_to_dict(state))
    assert restored.rebase.files_stale == ["pnpm-lock.yaml"]


def test_state_from_dict_without_files_stale():
    """State files written before files_stale existed still load."""
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=1,
        conflicts_resolved=1, files_resolved=["x.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    del d["rebase"]["files_stale"]
    assert state_from_dict(d).rebase.files_stale == []


def test_save_preserves_rebase_data(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["f.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.rebase.target_base == "origin/main"
    assert loaded.rebase.commits_replayed == 3
    assert loaded.rebase.files_resolved == ["f.py"]


def test_save_preserves_seen_issue_comments(worktree):
    """Through a real file, where the mapping's int keys go out as strings."""
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, CommentsSummary(
        total_threads=2, seen_issue_comments={111: "", 222: "2026-07-02T00:00:00Z"},
        updated_at="2026-07-02T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.comments.seen_issue_comments == {
        111: "", 222: "2026-07-02T00:00:00Z",
    }


@pytest.mark.parametrize("field_name", [
    "seen_issue_comments", "seen_review_body_comments",
])
def test_load_state_without_seen_stamps_defaults_empty(worktree, field_name):
    """A file missing the mapping loads with an empty one, not a failure."""
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    save_state(worktree, state)
    path = worktree / "state.json"
    data = json.loads(path.read_text())
    del data["comments"][field_name]
    path.write_text(json.dumps(data))
    loaded = load_state(worktree)
    assert loaded is not None
    assert getattr(loaded.comments, field_name) == {}
