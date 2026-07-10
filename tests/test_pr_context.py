"""Tests for pr_context shared resolution module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_context


def test_pr_and_branch_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        pr_context.resolve(pr="123", branch="feat/foo")


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo"))
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_branch_from_pr", return_value="feat/bar")
def test_pr_only_resolves(mock_branch, mock_sha, mock_repo, mock_top):
    ctx = pr_context.resolve(pr="42")
    assert ctx.pr_number == 42
    assert ctx.branch == "feat/bar"
    assert ctx.repo == "owner/repo"


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo"))
@patch.object(pr_context, "_current_branch_quiet", return_value="main")
@patch.object(pr_context, "_find_worktree_by_branch", return_value=None)
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_resolve_branch", return_value="feat/baz")
@patch.object(pr_context, "_pr_from_branch", return_value=99)
def test_branch_only_resolves(mock_pr, mock_resolve, mock_sha, mock_repo,
                              mock_find_wt, mock_current, mock_top):
    ctx = pr_context.resolve(branch="baz")
    assert ctx.pr_number == 99
    assert ctx.branch == "feat/baz"
    assert ctx.repo == "owner/repo"


# ── Bare-repo handling ─────────────────────────────────────────────────────


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "is_bare_repo", return_value=True)
@patch.object(pr_context, "resolve_bare_repo_worktree", return_value=Path("/wt/main"))
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="def456")
@patch.object(pr_context, "_current_branch", return_value="main")
@patch.object(pr_context, "_pr_from_current", return_value=None)
def test_bare_repo_finds_worktree(mock_pr, mock_branch, mock_sha, mock_repo,
                                  mock_resolve_wt, mock_bare, mock_top):
    ctx = pr_context.resolve()
    assert ctx.worktree_root == Path("/wt/main")
    assert ctx.repo == "owner/repo"
    mock_repo.assert_called_once_with("/wt/main")
    mock_resolve_wt.assert_called_once_with(None, None)


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "is_bare_repo", return_value=True)
@patch.object(pr_context, "resolve_bare_repo_worktree", return_value=None)
def test_bare_repo_no_worktree_no_args_exits(mock_resolve_wt, mock_bare, mock_top):
    with pytest.raises(SystemExit):
        pr_context.resolve()


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "is_bare_repo", return_value=True)
@patch.object(pr_context, "resolve_bare_repo_worktree", return_value=None)
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_resolve_branch", return_value="feat/thing")
@patch.object(pr_context, "_pr_from_branch", return_value=42)
def test_bare_repo_with_branch_continues(mock_pr, mock_resolve, mock_repo,
                                         mock_resolve_wt, mock_bare, mock_top):
    ctx = pr_context.resolve(branch="thing")
    assert ctx.worktree_root is None
    assert ctx.branch == "feat/thing"
    assert ctx.head_sha == ""


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "is_bare_repo", return_value=False)
def test_not_git_repo_exits(mock_bare, mock_top):
    with pytest.raises(SystemExit):
        pr_context.resolve()


# ── Bare-repo helpers (unit) ───────────────────────────────────────────────


@patch.object(pr_context, "subprocess")
def test_is_bare_repo_true(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout="true\n")
    assert pr_context.is_bare_repo("/some/path") is True


@patch.object(pr_context, "subprocess")
def test_is_bare_repo_false(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout="false\n")
    assert pr_context.is_bare_repo("/some/path") is False


@patch.object(pr_context, "subprocess")
def test_find_worktree_for_branch_found(mock_sub):
    mock_sub.run.return_value = MagicMock(
        stdout="/home/user/repo/feat-branch  abc1234 [feat/branch]\n"
               "/home/user/repo/main         def5678 [main]\n",
    )
    result = pr_context.find_worktree_for_branch("feat/branch")
    assert result == Path("/home/user/repo/feat-branch")


@patch.object(pr_context, "subprocess")
def test_find_worktree_for_branch_not_found(mock_sub):
    mock_sub.run.return_value = MagicMock(
        stdout="/home/user/repo/main  def5678 [main]\n",
    )
    result = pr_context.find_worktree_for_branch("nonexistent")
    assert result is None


# ── Branch-aware worktree resolution ──────────────────────────────────────


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(pr_context, "_current_branch_quiet", return_value="main")
@patch.object(pr_context, "find_worktree_for_branch", return_value=Path("/repo/feat-branch"))
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_resolve_branch", return_value="feat/branch")
@patch.object(pr_context, "_pr_from_branch", return_value=42)
def test_branch_redirects_to_correct_worktree(
    mock_pr, mock_resolve, mock_sha, mock_repo,
    mock_find_wt, mock_current, mock_top,
):
    """When --branch points to a different worktree, resolve() uses that worktree."""
    ctx = pr_context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/feat-branch")
    mock_find_wt.assert_called_once_with("feat/branch", "/repo/main")


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo/feat-branch"))
@patch.object(pr_context, "_current_branch_quiet", return_value="feat/branch")
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_resolve_branch", return_value="feat/branch")
@patch.object(pr_context, "_pr_from_branch", return_value=42)
def test_branch_matching_cwd_stays_in_place(
    mock_pr, mock_resolve, mock_sha, mock_repo, mock_current, mock_top,
):
    """When --branch matches CWD's branch, stay in the current worktree."""
    ctx = pr_context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/feat-branch")


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(pr_context, "_current_branch_quiet", return_value="main")
@patch.object(pr_context, "find_worktree_for_branch", return_value=None)
@patch.object(pr_context, "_resolve_branch", return_value="feat/branch")
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_pr_from_branch", return_value=None)
def test_branch_no_worktree_falls_back_to_cwd(
    mock_pr, mock_sha_unused, mock_repo, mock_resolve, mock_find_wt,
    mock_current, mock_top,
):
    """When no worktree exists for the branch, fall back to CWD's worktree."""
    ctx = pr_context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/main")


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(pr_context, "_current_branch_quiet", return_value="main")
@patch.object(pr_context, "find_worktree_for_branch", side_effect=[None, Path("/repo/feat")])
@patch.object(pr_context, "_resolve_branch", return_value="feat/resolved")
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_pr_from_branch", return_value=None)
def test_branch_fuzzy_resolved_finds_worktree(
    mock_pr, mock_sha, mock_repo, mock_resolve, mock_find_wt,
    mock_current, mock_top,
):
    """When exact branch hint doesn't match but resolve-branch finds it, use that worktree."""
    ctx = pr_context.resolve(branch="feat-hint")
    assert ctx.worktree_root == Path("/repo/feat")
    assert mock_find_wt.call_count == 2
    mock_find_wt.assert_any_call("feat-hint", "/repo/main")
    mock_find_wt.assert_any_call("feat/resolved", "/repo/main")


@patch.object(pr_context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(pr_context, "_current_branch_quiet", return_value=None)
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_resolve_branch", return_value="feat/branch")
@patch.object(pr_context, "_pr_from_branch", return_value=None)
def test_detached_head_skips_worktree_redirect(
    mock_pr, mock_resolve, mock_sha, mock_repo, mock_current, mock_top,
):
    """When CWD is in detached HEAD, skip worktree redirect (can't compare branches)."""
    ctx = pr_context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/main")


# ── is_pr_ref / classify_target ──────────────────────────────────────────


def test_is_pr_ref_number():
    assert pr_context.is_pr_ref("42") is True


def test_is_pr_ref_pr_url():
    assert pr_context.is_pr_ref("https://github.com/owner/repo/pull/123") is True


def test_is_pr_ref_pr_url_trailing_slash():
    assert pr_context.is_pr_ref("https://github.com/owner/repo/pull/456/") is True


def test_is_pr_ref_branch_with_slashes():
    assert pr_context.is_pr_ref("ibarsi/ENG-2239/migration-stream-jsonl") is False


def test_is_pr_ref_simple_branch():
    assert pr_context.is_pr_ref("feat-auth") is False


def test_is_pr_ref_branch_with_numbers():
    assert pr_context.is_pr_ref("isaac/ENG-1234/fix-thing") is False


def test_classify_target_number():
    pr, branch = pr_context.classify_target("42")
    assert pr == "42"
    assert branch is None


def test_classify_target_url():
    pr, branch = pr_context.classify_target("https://github.com/o/r/pull/99")
    assert pr == "https://github.com/o/r/pull/99"
    assert branch is None


def test_classify_target_branch():
    pr, branch = pr_context.classify_target("ibarsi/ENG-2239/migration-stream-jsonl")
    assert pr is None
    assert branch == "ibarsi/ENG-2239/migration-stream-jsonl"


def test_classify_target_simple_branch():
    pr, branch = pr_context.classify_target("feat-auth")
    assert pr is None
    assert branch == "feat-auth"
