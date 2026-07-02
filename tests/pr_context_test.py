"""Tests for pr_context library."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_context import (
    _parse_pr_input, _resolve_branch, resolve_bare_repo_worktree,
    find_worktree_for_branch, ResolvedContext, update_to_remote,
    fetch_and_reset,
)


# ── PR input parsing ────────────────────────────────────────────────────────


def test_parse_pr_input_number():
    assert _parse_pr_input("42") == 42


def test_parse_pr_input_url():
    assert _parse_pr_input("https://github.com/owner/repo/pull/123") == 123


def test_parse_pr_input_url_trailing_slash():
    assert _parse_pr_input("https://github.com/owner/repo/pull/456/") == 456


# ── ResolvedContext ─────────────────────────────────────────────────────────


def test_resolved_context_is_frozen():
    import pytest
    ctx = ResolvedContext(
        repo="owner/repo", branch="main", pr_number=1,
        worktree_root=Path("/tmp"), head_sha="abc",
    )
    with pytest.raises(AttributeError):
        ctx.repo = "other"


def test_resolved_context_fields():
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/auth",
        pr_number=None, worktree_root=Path("/wt"), head_sha="def",
    )
    assert ctx.repo == "owner/repo"
    assert ctx.branch == "feat/auth"
    assert ctx.pr_number is None
    assert ctx.head_sha == "def"


# ── fetch_and_reset ────────────────────────────────────────────────────────


@patch("pr_context.subprocess.run")
def test_fetch_and_reset_runs_fetch_then_reset(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    fetch_and_reset("/wt", "feat/x")
    assert mock_run.call_count == 2
    fetch_call = mock_run.call_args_list[0].args[0]
    assert "fetch" in fetch_call
    assert "origin" in fetch_call
    assert "feat/x" in fetch_call
    reset_call = mock_run.call_args_list[1].args[0]
    assert "reset" in reset_call
    assert "--hard" in reset_call
    assert "origin/feat/x" in reset_call


@patch("pr_context.subprocess.run", side_effect=Exception("network error"))
def test_fetch_and_reset_survives_fetch_exception(mock_run):
    fetch_and_reset("/wt", "feat/x")


# ── update_to_remote ───────────────────────────────────────────────────────


def test_update_to_remote_noop_without_worktree():
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/x",
        pr_number=1, worktree_root=None, head_sha="aaa",
    )
    result = update_to_remote(ctx)
    assert result is ctx


def test_update_to_remote_noop_without_branch():
    ctx = ResolvedContext(
        repo="owner/repo", branch="",
        pr_number=None, worktree_root=Path("/wt"), head_sha="aaa",
    )
    result = update_to_remote(ctx)
    assert result is ctx


@patch("pr_context.fetch_and_reset")
@patch("pr_context._head_sha", return_value="aaa111")
def test_update_to_remote_skips_when_already_current(mock_sha, mock_far):
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/x",
        pr_number=1, worktree_root=Path("/wt"), head_sha="aaa111",
    )
    result = update_to_remote(ctx)
    assert result is ctx
    mock_far.assert_called_once_with("/wt", "feat/x")


@patch("pr_context.log")
@patch("pr_context.fetch_and_reset")
@patch("pr_context._head_sha", side_effect=["old111", "new222"])
def test_update_to_remote_returns_new_context_when_sha_changes(mock_sha, mock_far, mock_log):
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/x",
        pr_number=1, worktree_root=Path("/wt"), head_sha="old111",
    )
    result = update_to_remote(ctx)
    assert result.head_sha == "new222"
    assert result.branch == "feat/x"
    assert result.repo == "owner/repo"
    mock_far.assert_called_once_with("/wt", "feat/x")


# ── Branch resolution ──────────────────────────────────────────────────────


@patch("pr_context.subprocess.run", side_effect=FileNotFoundError)
@patch("pr_context._current_branch", return_value="fallback-branch")
def test_resolve_branch_uses_hint_on_missing_script(mock_current, mock_run):
    assert _resolve_branch("some-hint") == "some-hint"
    mock_current.assert_not_called()


@patch("pr_context.subprocess.run", side_effect=FileNotFoundError)
@patch("pr_context._current_branch", return_value="fallback-branch")
def test_resolve_branch_falls_back_on_missing_script_no_hint(mock_current, mock_run):
    assert _resolve_branch("") == "fallback-branch"
    mock_current.assert_called_once_with(None)


@patch("pr_context.subprocess.run")
@patch("pr_context._current_branch", return_value="fallback-branch")
def test_resolve_branch_returns_hint_on_failure(mock_current, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert _resolve_branch("bad-hint") == "bad-hint"
    mock_current.assert_not_called()


@patch("pr_context.subprocess.run")
def test_resolve_branch_returns_stdout(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="isaac/feat/resolved_branch\n")
    assert _resolve_branch("resolved") == "isaac/feat/resolved_branch"


# ── Bare-repo worktree resolution ─────────────────────────────────────────


@patch("pr_context.find_worktree_for_branch")
def testresolve_bare_repo_worktree_prefers_branch(mock_find):
    mock_find.return_value = Path("/wt/feat-branch")
    result = resolve_bare_repo_worktree(None, "feat/branch")
    assert result == Path("/wt/feat-branch")
    mock_find.assert_called_once_with("feat/branch", None)


@patch("pr_context.find_worktree_for_branch")
@patch("pr_context.subprocess.run")
def testresolve_bare_repo_worktree_falls_back_to_default(mock_run, mock_find):
    mock_find.side_effect = [None, Path("/wt/main")]
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/main\n",
    )
    result = resolve_bare_repo_worktree(None, "nonexistent")
    assert result == Path("/wt/main")
    assert mock_find.call_count == 2


@patch("pr_context.find_worktree_for_branch", return_value=None)
@patch("pr_context.subprocess.run")
def testresolve_bare_repo_worktree_returns_none(mock_run, mock_find):
    mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
    result = resolve_bare_repo_worktree(None, None)
    assert result is None
