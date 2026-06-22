"""Tests for pr CLI helper functions."""

import importlib.util
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless pr script via importlib
_pr_path = str(BIN_DIR / "pr")
_loader = importlib.machinery.SourceFileLoader("pr_cli", _pr_path)
_spec = importlib.util.spec_from_loader("pr_cli", _loader, origin=_pr_path)
pr_cli = importlib.util.module_from_spec(_spec)
pr_cli.__file__ = _pr_path
_spec.loader.exec_module(pr_cli)


# ── _parse_ci_json ──────────────────────────────────────────────────────────


def test_parse_ci_json_valid():
    stdout = '{"conclusion": "failure", "failures": []}'
    result = pr_cli._parse_ci_json(stdout)
    assert result == {"conclusion": "failure", "failures": []}


def test_parse_ci_json_with_dashboard_prefix():
    stdout = 'Dashboard text\nMore text\n{"conclusion": "success"}'
    result = pr_cli._parse_ci_json(stdout)
    assert result["conclusion"] == "success"


def test_parse_ci_json_no_json():
    assert pr_cli._parse_ci_json("no json here") is None


def test_parse_ci_json_empty():
    assert pr_cli._parse_ci_json("") is None


# ── _parse_review_summary ──────────────────────────────────────────────────


def test_parse_review_summary_valid():
    output = 'REVIEW_SUMMARY:{"repo":"owner/repo","verdict":"approve","findings":{"M":0,"S":1,"total":1}}'
    result = pr_cli._parse_review_summary(output)
    assert result["verdict"] == "approve"
    assert result["findings"]["S"] == 1


def test_parse_review_summary_multiline():
    output = "Some output\nMore output\nREVIEW_SUMMARY:{\"verdict\":\"changes_requested\"}\nTrailing"
    result = pr_cli._parse_review_summary(output)
    assert result["verdict"] == "changes_requested"


def test_parse_review_summary_missing():
    assert pr_cli._parse_review_summary("no summary here") is None


def test_parse_review_summary_invalid_json():
    assert pr_cli._parse_review_summary("REVIEW_SUMMARY:{invalid}") is None


# ── _is_pr_target ──────────────────────────────────────────────────────────


def test_is_pr_target_number():
    assert pr_cli._is_pr_target("42") is True


def test_is_pr_target_url():
    assert pr_cli._is_pr_target("https://github.com/owner/repo/pull/123") is True


def test_is_pr_target_branch():
    assert pr_cli._is_pr_target("isaac/feat/foo") is False


def test_is_pr_target_none():
    assert pr_cli._is_pr_target(None) is False


def test_is_pr_target_empty():
    assert pr_cli._is_pr_target("") is False


# ── _merge_readiness ────────────────────────────────────────────────────────


def test_merge_readiness_all_green():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci(state, pr_state.CISummary(conclusion="success", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={"S": 1}, verdict="approve", updated_at="t",
    ))
    pr_state.update_comments(state, pr_state.CommentsSummary(
        blocking_reviewers=[], updated_at="t",
    ))
    result = pr_cli._merge_readiness(state)
    assert "ready" in result.lower()


def test_merge_readiness_ci_failing():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci(state, pr_state.CISummary(conclusion="failure", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(updated_at="t"))
    pr_state.update_comments(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "CI failing" in result


def test_merge_readiness_must_fix():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci(state, pr_state.CISummary(conclusion="success", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={"M": 2}, updated_at="t",
    ))
    pr_state.update_comments(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "must-fix" in result


def test_merge_readiness_not_checked():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    result = pr_cli._merge_readiness(state)
    assert "not checked" in result


# ── _build_triage_summary ──────────────────────────────────────────────────


def test_build_triage_summary():
    report = {"stats": {"total": 5, "actionable": 2, "valid": 1, "questions": 1}}
    result = pr_cli._build_triage_summary(report)
    assert result.total == 5
    assert result.actionable == 2
    assert result.valid == 1
    assert result.questions == 1
    assert result.updated_at  # should be set


def test_build_triage_summary_empty():
    result = pr_cli._build_triage_summary({})
    assert result.total == 0
    assert result.actionable == 0
    assert result.valid == 0
    assert result.questions == 0
    assert result.updated_at


# ── _render_triage_section ─────────────────────────────────────────────────


def test_render_triage_section_not_run():
    import pr_state
    t = pr_state.TriageSummary()
    result = pr_cli._render_triage_section(t)
    assert result == ["**Triage**: not run yet"]


def test_render_triage_section_with_data():
    import pr_state
    t = pr_state.TriageSummary(
        total=5, actionable=2, valid=1, questions=1, updated_at="2024-01-01T00:00:00Z",
    )
    result = pr_cli._render_triage_section(t)
    assert len(result) == 1
    assert "5 threads" in result[0]
    assert "2 actionable" in result[0]
    assert "1 valid" in result[0]
    assert "1 questions" in result[0]


# ── _render_rebase_section ────────────────────────────────────────────────


def test_render_rebase_section_not_run():
    import pr_state
    r = pr_state.RebaseSummary()
    result = pr_cli._render_rebase_section(r)
    assert result == ["**Rebase**: not run yet"]


def test_render_rebase_section_with_data():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert len(result) >= 1
    assert "2 file(s)" in result[0] or "2" in result[0]
    assert "3 commit(s)" in result[0] or "3" in result[0]
    assert "force-pushed" in result[0]


def test_render_rebase_section_no_conflicts():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=5,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert len(result) >= 1
    assert "clean" in result[0].lower() or "0" in result[0]


def test_render_rebase_section_not_pushed():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["a.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert "force-pushed" not in result[0]
