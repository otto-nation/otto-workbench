"""Tests for review_worktree library."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_worktree import WorktreeResult, setup_pr_worktree, switch_to_branch, switch_to_pr_branch, cleanup_worktree


# ── WorktreeResult ────────────────────────────────────────────────────────────


def test_worktree_result_is_frozen():
    r = WorktreeResult(path="/tmp/wt", cleanup_ref="feat/auth", is_fallback=False)
    with pytest.raises(AttributeError):
        r.path = "/other"


def test_worktree_result_fields():
    r = WorktreeResult(path="/tmp/wt", cleanup_ref="pr-42-review", is_fallback=True)
    assert r.path == "/tmp/wt"
    assert r.cleanup_ref == "pr-42-review"
    assert r.is_fallback is True


# ── setup_pr_worktree ─────────────────────────────────────────────────────────


@patch("review_worktree.subprocess.run")
def test_setup_pr_worktree_via_wt(mock_run):
    wt_json = json.dumps({"path": "/repos/repo/pr-42", "branch": "pr:42"})

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "reset" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = setup_pr_worktree("owner/repo", 42, "/repos/repo")

    assert result.path == "/repos/repo/pr-42"
    assert result.is_fallback is False
    assert result.cleanup_ref == "pr:42"


@patch("review_worktree.subprocess.run")
def test_setup_pr_worktree_fallback_when_wt_fails(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd and "pull/42/head" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = setup_pr_worktree("owner/repo", 42, "/repos/repo")

    assert result.path == "/repos/repo/.worktrees/pr-42-review"
    assert result.is_fallback is True
    assert result.cleanup_ref == "/repos/repo/.worktrees/pr-42-review"


@patch("review_worktree.subprocess.run")
def test_setup_pr_worktree_unshallows_if_needed(mock_run):
    calls_made = []

    def side_effect(cmd, **kwargs):
        calls_made.append(cmd)
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "true\n"
            return m
        if cmd[0] == "git" and "--unshallow" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            wt_json = json.dumps({"path": "/repos/repo/pr-7", "branch": "pr:7"})
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "reset" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    setup_pr_worktree("owner/repo", 7, "/repos/repo")

    unshallow_calls = [c for c in calls_made if "git" in c[0] and "--unshallow" in c]
    assert len(unshallow_calls) == 1


@patch("review_worktree.subprocess.run")
def test_setup_pr_worktree_fetches_and_resets_on_wt_success(mock_run):
    calls_made = []

    def side_effect(cmd, **kwargs):
        calls_made.append(cmd)
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            wt_json = json.dumps({"path": "/repos/repo/pr-10", "branch": "feat/thing"})
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "reset" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = setup_pr_worktree("owner/repo", 10, "/repos/repo", pr_head="feat/thing")

    fetch_calls = [c for c in calls_made if c[0] == "git" and "fetch" in c and "origin" in c]
    assert len(fetch_calls) >= 1
    reset_calls = [c for c in calls_made if c[0] == "git" and "reset" in c and "--hard" in c]
    assert len(reset_calls) == 1


@patch("review_worktree.subprocess.run")
def test_setup_pr_worktree_raises_on_total_failure(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd and "pull/" in str(cmd):
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 1
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect

    with pytest.raises(RuntimeError, match="Failed to create worktree"):
        setup_pr_worktree("owner/repo", 42, "/repos/repo")


# ── switch_to_branch ──────────────────────────────────────────────────────────


@patch("review_worktree.subprocess.run")
def test_switch_to_branch_via_wt(mock_run):
    wt_json = json.dumps({"path": "/repos/repo/feat-auth"})

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("feat/auth", "/repos/repo")

    assert result is not None
    assert result.path == "/repos/repo/feat-auth"
    assert result.is_fallback is False
    assert result.cleanup_ref == "feat/auth"


@patch("review_worktree.subprocess.run")
def test_switch_to_branch_fallback(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "rev-parse" in cmd:
            m.returncode = 0
            m.stdout = "/repos/repo"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("main", "/repos/repo")

    assert result is not None
    assert result.path == "/repos/repo/self-review-main"
    assert result.is_fallback is True
    assert result.cleanup_ref == "/repos/repo/self-review-main"


@patch("review_worktree.subprocess.run")
def test_switch_to_branch_sanitizes_slashes(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("feat/auth", "/repos/repo")

    assert result is not None
    assert result.path == "/repos/repo/self-review-feat-auth"


@patch("review_worktree.subprocess.run")
def test_switch_to_branch_returns_none_on_total_failure(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 1
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("feat/auth", "/repos/repo")

    assert result is None


# ── switch_to_pr_branch ───────────────────────────────────────────────────────


@patch("review_worktree.switch_to_branch")
@patch("review_worktree.subprocess.run")
def test_switch_to_pr_branch_delegates(mock_run, mock_switch):
    expected_result = WorktreeResult(path="/repos/repo/feat-x", cleanup_ref="feat/x", is_fallback=False)
    mock_switch.return_value = expected_result

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "gh":
            m.returncode = 0
            m.stdout = "feat/x\n"
            return m
        if cmd[0] == "git" and "rev-parse" in cmd:
            m.returncode = 0
            m.stdout = "main\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_pr_branch(42, "owner/repo", "/repos/repo")

    assert result == expected_result
    mock_switch.assert_called_once_with("feat/x", "/repos/repo")


@patch("review_worktree.switch_to_branch")
@patch("review_worktree.subprocess.run")
def test_switch_to_pr_branch_already_on_branch(mock_run, mock_switch):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "gh":
            m.returncode = 0
            m.stdout = "feat/x\n"
            return m
        if cmd[0] == "git" and "rev-parse" in cmd:
            m.returncode = 0
            m.stdout = "feat/x\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_pr_branch(42, "owner/repo", "/repos/repo")

    assert result is None
    mock_switch.assert_not_called()


@patch("review_worktree.switch_to_branch")
@patch("review_worktree.subprocess.run")
def test_switch_to_pr_branch_returns_none_when_pr_head_unknown(mock_run, mock_switch):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "gh":
            m.returncode = 1
            m.stdout = ""
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_pr_branch(42, "owner/repo", "/repos/repo")

    assert result is None
    mock_switch.assert_not_called()


# ── cleanup_worktree ──────────────────────────────────────────────────────────


@patch("review_worktree.subprocess.run")
def test_cleanup_worktree_fallback_uses_git_remove(mock_run):
    result = WorktreeResult(
        path="/repos/repo/.worktrees/pr-42-review",
        cleanup_ref="/repos/repo/.worktrees/pr-42-review",
        is_fallback=True,
    )
    cleanup_worktree(result, "/repos/repo")

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "git" in cmd[0]
    assert "worktree" in cmd
    assert "remove" in cmd
    assert "--force" in cmd
    assert result.path in cmd


@patch("review_worktree.subprocess.run")
def test_cleanup_worktree_wt_uses_wt_remove(mock_run):
    result = WorktreeResult(
        path="/repos/repo/pr-42",
        cleanup_ref="pr:42",
        is_fallback=False,
    )
    cleanup_worktree(result, "/repos/repo")

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "wt"
    assert "remove" in cmd
    assert "pr:42" in cmd
    assert "--force" in cmd
    assert "-y" in cmd


@patch("review_worktree.subprocess.run")
def test_cleanup_worktree_none_is_noop(mock_run):
    cleanup_worktree(None, "/repos/repo")
    mock_run.assert_not_called()


@patch("review_worktree.subprocess.run")
def test_cleanup_worktree_swallows_errors(mock_run):
    mock_run.side_effect = Exception("boom")
    result = WorktreeResult(
        path="/repos/repo/pr-42",
        cleanup_ref="pr:42",
        is_fallback=False,
    )
    cleanup_worktree(result, "/repos/repo")
