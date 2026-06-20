"""Tests for pr_state library."""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_state import (
    PRIdentity, CISummary, ReviewSummary, CommentsSummary, PRState,
    load_state, save_state, new_state, update_identity, update_ci,
    update_review, update_comments, state_to_dict, state_from_dict,
)


# ── Dataclass construction ──────────────────────────────────────────────────


def test_pr_identity_fields():
    ident = PRIdentity(
        repo="owner/repo", branch="isaac/feat/foo",
        pr_number=42, head_sha="abc123", worktree_root="/tmp/wt",
    )
    assert ident.repo == "owner/repo"
    assert ident.pr_number == 42


def test_ci_summary_defaults():
    ci = CISummary()
    assert ci.last_run_id is None
    assert ci.failure_count == 0
    assert ci.failure_kinds == {}
    assert ci.updated_at == ""


def test_review_summary_defaults():
    rev = ReviewSummary()
    assert rev.review_file == ""
    assert rev.finding_counts == {}
    assert rev.cost_usd == 0.0


def test_comments_summary_defaults():
    c = CommentsSummary()
    assert c.total_threads == 0
    assert c.by_state == {}
    assert c.blocking_reviewers == []
    assert c.has_approvals is False


def test_pr_state_defaults():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.ci.failure_count == 0
    assert state.review.verdict == ""
    assert state.comments.total_threads == 0


# ── new_state ───────────────────────────────────────────────────────────────


def test_new_state_sets_identity():
    state = new_state("owner/repo", "main", pr_number=7, head_sha="aaa", worktree_root="/wt")
    assert state.identity.repo == "owner/repo"
    assert state.identity.pr_number == 7
    assert state.created_at != ""


def test_new_state_no_pr():
    state = new_state("owner/repo", "main", pr_number=None, head_sha="bbb", worktree_root="/wt")
    assert state.identity.pr_number is None


# ── Serialization roundtrip ─────────────────────────────────────────────────


def test_state_to_dict_and_back_empty():
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root="/wt")
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.identity.repo == "owner/repo"
    assert restored.identity.pr_number == 1
    assert restored.ci.failure_count == 0
    assert restored.review.verdict == ""
    assert restored.comments.total_threads == 0


def test_state_roundtrip_with_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    update_ci(state, CISummary(
        last_run_id=999, last_run_number=7,
        conclusion="failure", failure_count=3,
        failure_kinds={"lint": 2, "test": 1},
        updated_at="2026-06-20T00:00:00+00:00",
    ))
    update_review(state, ReviewSummary(
        review_file="/tmp/review.md", review_type="self",
        head_sha="def", finding_counts={"M": 1, "S": 2},
        verdict="changes_requested", cost_usd=1.50,
        updated_at="2026-06-20T00:00:00+00:00",
    ))
    update_comments(state, CommentsSummary(
        total_threads=5, by_state={"new": 2, "addressed": 3},
        blocking_reviewers=["alice"], has_approvals=True,
        updated_at="2026-06-20T00:00:00+00:00",
    ))

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert restored.ci.last_run_id == 999
    assert restored.ci.failure_count == 3
    assert restored.ci.failure_kinds == {"lint": 2, "test": 1}

    assert restored.review.review_file == "/tmp/review.md"
    assert restored.review.finding_counts == {"M": 1, "S": 2}
    assert restored.review.verdict == "changes_requested"
    assert restored.review.cost_usd == 1.50

    assert restored.comments.total_threads == 5
    assert restored.comments.by_state == {"new": 2, "addressed": 3}
    assert restored.comments.blocking_reviewers == ["alice"]
    assert restored.comments.has_approvals is True


# ── File I/O ────────────────────────────────────────────────────────────────


def test_load_state_missing_file():
    result = load_state(Path("/nonexistent/worktree"))
    assert result is None


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root=tmp)
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.identity.repo == "owner/repo"
        assert loaded.identity.pr_number == 1
        assert loaded.updated_at != ""


def test_save_creates_parent_directories():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "nested" / "worktree"
        state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root=str(root))
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None


def test_save_preserves_ci_data():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        update_ci(state, CISummary(
            last_run_id=100, conclusion="failure", failure_count=2,
            failure_kinds={"lint": 2}, updated_at="2026-06-20T00:00:00+00:00",
        ))
        save_state(root, state)

        loaded = load_state(root)
        assert loaded is not None
        assert loaded.ci.last_run_id == 100
        assert loaded.ci.failure_count == 2
        assert loaded.ci.failure_kinds == {"lint": 2}


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


def test_update_ci_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_ci(state, CISummary(conclusion="success", updated_at="t1"))
    assert state.ci.conclusion == "success"
    update_ci(state, CISummary(conclusion="failure", failure_count=1, updated_at="t2"))
    assert state.ci.conclusion == "failure"
    assert state.ci.failure_count == 1


def test_update_review_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_review(state, ReviewSummary(verdict="approve", updated_at="t1"))
    assert state.review.verdict == "approve"


def test_update_comments_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_comments(state, CommentsSummary(total_threads=3, updated_at="t1"))
    assert state.comments.total_threads == 3
