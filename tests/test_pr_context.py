"""Tests for pr_context shared resolution module."""

import sys
from pathlib import Path
from unittest.mock import patch

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
