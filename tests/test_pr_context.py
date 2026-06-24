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
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="abc123")
@patch.object(pr_context, "_resolve_branch", return_value="feat/baz")
@patch.object(pr_context, "_pr_from_branch", return_value=99)
def test_branch_only_resolves(mock_pr, mock_resolve, mock_sha, mock_repo, mock_top):
    ctx = pr_context.resolve(branch="baz")
    assert ctx.pr_number == 99
    assert ctx.branch == "feat/baz"
    assert ctx.repo == "owner/repo"


# ── Bare-repo handling ─────────────────────────────────────────────────────


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "_is_bare_repo", return_value=True)
@patch.object(pr_context, "_resolve_bare_repo_worktree", return_value=Path("/wt/main"))
@patch.object(pr_context, "_detect_repo", return_value="owner/repo")
@patch.object(pr_context, "_head_sha", return_value="def456")
@patch.object(pr_context, "_current_branch", return_value="main")
@patch.object(pr_context, "_pr_from_current", return_value=None)
def test_bare_repo_finds_worktree(mock_pr, mock_branch, mock_sha, mock_repo,
                                  mock_resolve_wt, mock_bare, mock_top):
    ctx = pr_context.resolve()
    assert ctx.worktree_root == Path("/wt/main")
    assert ctx.repo == "owner/repo"
    mock_detect_cwd = mock_repo.call_args[1].get("cwd") or mock_repo.call_args[0]
    mock_resolve_wt.assert_called_once_with(None, None)


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "_is_bare_repo", return_value=True)
@patch.object(pr_context, "_resolve_bare_repo_worktree", return_value=None)
def test_bare_repo_no_worktree_no_args_exits(mock_resolve_wt, mock_bare, mock_top):
    with pytest.raises(SystemExit):
        pr_context.resolve()


@patch.object(pr_context, "_git_toplevel", return_value=None)
@patch.object(pr_context, "_is_bare_repo", return_value=True)
@patch.object(pr_context, "_resolve_bare_repo_worktree", return_value=None)
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
@patch.object(pr_context, "_is_bare_repo", return_value=False)
def test_not_git_repo_exits(mock_bare, mock_top):
    with pytest.raises(SystemExit):
        pr_context.resolve()


# ── Bare-repo helpers (unit) ───────────────────────────────────────────────


@patch.object(pr_context, "subprocess")
def test_is_bare_repo_true(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout="true\n")
    assert pr_context._is_bare_repo("/some/path") is True


@patch.object(pr_context, "subprocess")
def test_is_bare_repo_false(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout="false\n")
    assert pr_context._is_bare_repo("/some/path") is False


@patch.object(pr_context, "subprocess")
def test_find_worktree_for_branch_found(mock_sub):
    mock_sub.run.return_value = MagicMock(
        stdout="/home/user/repo/feat-branch  abc1234 [feat/branch]\n"
               "/home/user/repo/main         def5678 [main]\n",
    )
    result = pr_context._find_worktree_for_branch("feat/branch")
    assert result == Path("/home/user/repo/feat-branch")


@patch.object(pr_context, "subprocess")
def test_find_worktree_for_branch_not_found(mock_sub):
    mock_sub.run.return_value = MagicMock(
        stdout="/home/user/repo/main  def5678 [main]\n",
    )
    result = pr_context._find_worktree_for_branch("nonexistent")
    assert result is None
