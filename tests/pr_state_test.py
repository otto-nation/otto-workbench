"""Tests for pr_state library."""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from pr_state import (
    PRIdentity, CIDomain, ReviewSummary, ReviewVerdict, ReviewStatus,
    CommentsSummary, TriageSummary, RebaseSummary,
    PRState, load_state, save_state, new_state, update_identity, update_ci_domain,
    update_review, update_comments, update_triage, update_rebase,
    state_to_dict, state_from_dict,
    load_or_init, apply_state_update,
    STATE_VERSION,
)
from ci_failures import RunState, FailureGroup, FailureItem, FailureKind, Outcome


# ── Dataclass construction ──────────────────────────────────────────────────


def test_pr_identity_fields():
    ident = PRIdentity(
        repo="owner/repo", branch="isaac/feat/foo",
        pr_number=42, head_sha="abc123", worktree_root="/tmp/wt",
    )
    assert ident.repo == "owner/repo"
    assert ident.pr_number == 42


def test_ci_domain_defaults():
    ci = CIDomain()
    assert ci.conclusion == ""
    assert ci.failure_count == 0
    assert ci.failure_kinds == {}
    assert ci.last_run_id is None
    assert ci.last_run_number is None
    assert ci.updated_at == ""
    assert ci.runs == {}
    assert ci.latest_run_id is None


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
    assert c.seen_issue_comment_ids == []


def test_comments_summary_with_seen_ids():
    c = CommentsSummary(seen_issue_comment_ids=[111, 222, 333])
    assert c.seen_issue_comment_ids == [111, 222, 333]


def test_triage_summary_defaults():
    t = TriageSummary()
    assert t.total == 0
    assert t.actionable == 0
    assert t.valid == 0
    assert t.questions == 0
    assert t.updated_at == ""


def test_pr_state_defaults():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.ci.failure_count == 0
    assert state.review.verdict == ""
    assert state.comments.total_threads == 0
    assert state.triage.total == 0


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


def test_state_to_dict_has_version():
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root="/wt")
    d = state_to_dict(state)
    assert d["_version"] == STATE_VERSION


def test_state_to_dict_and_back_empty():
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root="/wt")
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.identity.repo == "owner/repo"
    assert restored.identity.pr_number == 1
    assert restored.ci.failure_count == 0
    assert restored.review.verdict == ""
    assert restored.comments.total_threads == 0
    assert restored.triage.total == 0


def test_state_roundtrip_with_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    update_ci_domain(state, CIDomain(
        last_run_id=999, last_run_number=7,
        conclusion="failure", failure_count=3,
        failure_kinds={"lint": 2, "test": 1},
        updated_at="2026-06-20T00:00:00+00:00",
    ))
    update_review(state, ReviewSummary(
        review_file="/tmp/review.md", review_type="self",
        head_sha="def", finding_counts={"M": 1, "S": 2},
        verdict=ReviewVerdict.CHANGES_REQUESTED.value, cost_usd=1.50,
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
    assert restored.review.verdict == ReviewVerdict.CHANGES_REQUESTED.value
    assert restored.review.cost_usd == 1.50

    assert restored.comments.total_threads == 5
    assert restored.comments.by_state == {"new": 2, "addressed": 3}
    assert restored.comments.blocking_reviewers == ["alice"]
    assert restored.comments.has_approvals is True
    assert restored.comments.seen_issue_comment_ids == []


def test_state_roundtrip_with_seen_issue_comment_ids():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    update_comments(state, CommentsSummary(
        total_threads=3, by_state={"new": 1, "addressed": 2},
        seen_issue_comment_ids=[111, 222, 333],
        updated_at="2026-07-02T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.comments.seen_issue_comment_ids == [111, 222, 333]


def test_state_roundtrip_with_triage_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    update_triage(state, TriageSummary(
        total=10, actionable=4, valid=3, questions=2,
        updated_at="2026-06-20T00:00:00+00:00",
    ))

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert restored.triage.total == 10
    assert restored.triage.actionable == 4
    assert restored.triage.valid == 3
    assert restored.triage.questions == 2
    assert restored.triage.updated_at == "2026-06-20T00:00:00+00:00"


def test_state_roundtrip_with_ci_runs():
    """CIDomain with nested RunState objects survives round-trip."""
    item = FailureItem(
        id="sc2086-bin-foo-42", annotation="SC2086: Double quote",
        file="bin/foo.sh", line=42, diagnosis="Unquoted var",
        fix_sha="abc123", outcome=Outcome.FIXED,
        headline="SC2086: Double quote to prevent globbing",
    )
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    run = RunState(
        run_id=999, run_number=7, head_sha="def456",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00",
        failures={"shellcheck": group},
    )
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="def456", worktree_root="/wt")
    state.ci.runs["999"] = run
    state.ci.latest_run_id = 999
    state.ci.conclusion = "failure"
    state.ci.failure_count = 1

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert restored.ci.latest_run_id == 999
    assert "999" in restored.ci.runs
    restored_run = restored.ci.runs["999"]
    assert restored_run.head_sha == "def456"
    assert "shellcheck" in restored_run.failures
    restored_group = restored_run.failures["shellcheck"]
    assert restored_group.kind == FailureKind.LINT
    assert len(restored_group.items) == 1
    assert restored_group.items[0].outcome == Outcome.FIXED
    assert restored_group.items[0].fix_sha == "abc123"
    assert restored_group.items[0].headline == "SC2086: Double quote to prevent globbing"


def test_state_roundtrip_ci_runs_without_headline():
    """Old state files without headline field should deserialize with None."""
    item = FailureItem(
        id="x", annotation="err", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
    )
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=1, head_sha="aaa",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    state = new_state("owner/repo", "feat", pr_number=1, head_sha="aaa", worktree_root="/wt")
    state.ci.runs["100"] = run
    state.ci.latest_run_id = 100

    d = state_to_dict(state)
    del d["ci"]["runs"]["100"]["failures"]["build"]["items"][0]["headline"]

    restored = state_from_dict(d)
    restored_item = restored.ci.runs["100"].failures["build"].items[0]
    assert restored_item.headline is None


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
        update_ci_domain(state, CIDomain(
            last_run_id=100, conclusion="failure", failure_count=2,
            failure_kinds={"lint": 2}, updated_at="2026-06-20T00:00:00+00:00",
        ))
        save_state(root, state)

        loaded = load_state(root)
        assert loaded is not None
        assert loaded.ci.last_run_id == 100
        assert loaded.ci.failure_count == 2
        assert loaded.ci.failure_kinds == {"lint": 2}


def test_save_preserves_ci_runs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        run = RunState(
            run_id=200, run_number=3, head_sha="ghi",
            status="completed", conclusion="failure",
            fetched_at="2026-06-18T14:30:00+00:00", failures={},
        )
        state.ci.runs["200"] = run
        state.ci.latest_run_id = 200
        save_state(root, state)

        loaded = load_state(root)
        assert loaded is not None
        assert loaded.ci.latest_run_id == 200
        assert "200" in loaded.ci.runs
        assert loaded.ci.runs["200"].run_number == 3


def test_save_preserves_triage_data():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        update_triage(state, TriageSummary(
            total=8, actionable=3, valid=2, questions=1,
            updated_at="2026-06-20T00:00:00+00:00",
        ))
        save_state(root, state)

        loaded = load_state(root)
        assert loaded is not None
        assert loaded.triage.total == 8
        assert loaded.triage.actionable == 3
        assert loaded.triage.valid == 2
        assert loaded.triage.questions == 1


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


def test_update_ci_domain_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_ci_domain(state, CIDomain(conclusion="success", updated_at="t1"))
    assert state.ci.conclusion == "success"
    update_ci_domain(state, CIDomain(conclusion="failure", failure_count=1, updated_at="t2"))
    assert state.ci.conclusion == "failure"
    assert state.ci.failure_count == 1


def test_update_review_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_review(state, ReviewSummary(verdict=ReviewVerdict.APPROVE.value, updated_at="t1"))
    assert state.review.verdict == ReviewVerdict.APPROVE.value


def test_review_summary_status_default():
    rev = ReviewSummary()
    assert rev.status == ""


def test_review_summary_status_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    update_review(state, ReviewSummary(
        verdict=ReviewVerdict.APPROVE.value, status=ReviewStatus.ERROR.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.status == ReviewStatus.ERROR.value


def test_review_summary_status_completed_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    update_review(state, ReviewSummary(
        verdict=ReviewVerdict.APPROVE.value, status=ReviewStatus.COMPLETED.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.status == ReviewStatus.COMPLETED.value


def test_review_summary_verdict_disapprove_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    update_review(state, ReviewSummary(
        verdict=ReviewVerdict.DISAPPROVE.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.verdict == ReviewVerdict.DISAPPROVE.value


def test_update_comments_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_comments(state, CommentsSummary(total_threads=3, updated_at="t1"))
    assert state.comments.total_threads == 3


def test_update_comments_with_seen_ids():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_comments(state, CommentsSummary(
        total_threads=2, seen_issue_comment_ids=[100, 200], updated_at="t1",
    ))
    assert state.comments.seen_issue_comment_ids == [100, 200]
    update_comments(state, CommentsSummary(
        total_threads=3, seen_issue_comment_ids=[100, 200, 300], updated_at="t2",
    ))
    assert state.comments.seen_issue_comment_ids == [100, 200, 300]


def test_update_triage_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_triage(state, TriageSummary(total=5, actionable=2, updated_at="t1"))
    assert state.triage.total == 5
    update_triage(state, TriageSummary(total=10, actionable=4, valid=3, updated_at="t2"))
    assert state.triage.total == 10
    assert state.triage.actionable == 4
    assert state.triage.valid == 3


def test_rebase_summary_defaults():
    rb = RebaseSummary()
    assert rb.target_base == ""
    assert rb.commits_replayed == 0
    assert rb.conflicts_resolved == 0
    assert rb.files_resolved == []
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


def test_update_rebase_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_rebase(state, RebaseSummary(
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
    update_rebase(state, RebaseSummary(
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


def test_save_preserves_rebase_data():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        update_rebase(state, RebaseSummary(
            target_base="origin/main", commits_replayed=3,
            conflicts_resolved=1, files_resolved=["f.py"],
            force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
        ))
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.rebase.target_base == "origin/main"
        assert loaded.rebase.commits_replayed == 3
        assert loaded.rebase.files_resolved == ["f.py"]


def test_save_preserves_seen_issue_comment_ids():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        update_comments(state, CommentsSummary(
            total_threads=2, seen_issue_comment_ids=[111, 222],
            updated_at="2026-07-02T00:00:00+00:00",
        ))
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.comments.seen_issue_comment_ids == [111, 222]


def test_load_state_without_seen_ids_defaults_empty():
    """Old state files without seen_issue_comment_ids should deserialize with []."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        save_state(root, state)
        path = root / ".workbench" / "state.json"
        import json
        data = json.loads(path.read_text())
        del data["comments"]["seen_issue_comment_ids"]
        path.write_text(json.dumps(data))
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.comments.seen_issue_comment_ids == []


# ── load_or_init ───────────────────────────────────────────────────────────


def test_load_or_init_creates_new_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = load_or_init(
            worktree_root=root, repo="owner/repo", branch="feat",
            pr_number=42, head_sha="abc123",
        )
        assert state.identity.repo == "owner/repo"
        assert state.identity.pr_number == 42
        assert state.identity.head_sha == "abc123"


def test_load_or_init_loads_existing_and_updates_identity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=1, head_sha="old", worktree_root=tmp)
        update_ci_domain(state, CIDomain(conclusion="failure", failure_count=3, updated_at="t"))
        save_state(root, state)

        loaded = load_or_init(
            worktree_root=root, repo="owner/repo", branch="feat",
            pr_number=2, head_sha="new",
        )
        assert loaded.identity.head_sha == "new"
        assert loaded.identity.pr_number == 2
        assert loaded.ci.failure_count == 3


# ── apply_state_update ─────────────────────────────────────────────────────


def test_apply_state_update_ci():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        apply_state_update(
            worktree_root=root, repo="owner/repo", branch="feat",
            pr_number=1, head_sha="abc", domain="ci",
            data={"conclusion": "failure", "failure_count": 2, "failure_kinds": {"lint": 2}, "updated_at": "t"},
        )
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.ci.conclusion == "failure"
        assert loaded.ci.failure_count == 2


def test_apply_state_update_review():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        apply_state_update(
            worktree_root=root, repo="owner/repo", branch="feat",
            pr_number=1, head_sha="abc", domain="review",
            data={"verdict": ReviewVerdict.APPROVE.value, "finding_counts": {"S": 1}, "cost_usd": 0.5, "updated_at": "t"},
        )
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.review.verdict == ReviewVerdict.APPROVE.value
        assert loaded.review.cost_usd == 0.5


def test_apply_state_update_triage():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        apply_state_update(
            worktree_root=root, repo="owner/repo", branch="feat",
            pr_number=1, head_sha="abc", domain="triage",
            data={"total": 5, "actionable": 2, "valid": 1, "questions": 1, "updated_at": "t"},
        )
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.triage.total == 5
        assert loaded.triage.actionable == 2


def test_apply_state_update_unknown_domain():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(ValueError, match="Unknown state domain"):
            apply_state_update(
                worktree_root=root, repo="r", branch="b",
                head_sha="a", domain="bogus", data={},
            )
