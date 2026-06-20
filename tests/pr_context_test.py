"""Tests for pr_context library."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_context import _parse_pr_input, ResolvedContext


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
