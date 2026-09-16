"""Unit tests for review.recover — importable without executing claude-review."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review import recover as review_recover
from review.worktree import WorktreeResult


def _write_partial_pipeline(review_dir: Path, head_sha: str = "abc1234") -> None:
    (review_dir / "pipeline.json").write_text(json.dumps({
        "head_sha": head_sha, "group_names": ["g1", "g2"],
        "failed": {"synthesis": "crashed"},
        "groups_done": [1], "groups_failed": {},
    }))


# ── read_review_sha ───────────────────────────────────────────────────────────


def test_read_review_sha_extracts_the_html_comment(tmp_path):
    review = tmp_path / "review.md"
    review.write_text("<!-- head_sha: abcdef0123456789 -->\n# Review\n")
    assert review_recover.read_review_sha(review) == "abcdef0123456789"


def test_read_review_sha_missing_file_is_empty(tmp_path):
    assert review_recover.read_review_sha(tmp_path / "nope.md") == ""


def test_read_review_sha_without_comment_is_empty(tmp_path):
    review = tmp_path / "review.md"
    review.write_text("# Review\n")
    assert review_recover.read_review_sha(review) == ""


# ── get_pr_head_sha ───────────────────────────────────────────────────────────


def test_get_pr_head_sha_reads_headRefOid(monkeypatch):
    monkeypatch.setattr(
        review_recover.gh_client, "pr_view",
        lambda *a, **kw: {"headRefOid": "deadbeef"},
    )
    assert review_recover.get_pr_head_sha("owner/repo", "42") == "deadbeef"


def test_get_pr_head_sha_missing_field_is_empty(monkeypatch):
    monkeypatch.setattr(review_recover.gh_client, "pr_view", lambda *a, **kw: {})
    assert review_recover.get_pr_head_sha("owner/repo", "42") == ""


# ── resolve_recover_sha ───────────────────────────────────────────────────────


def test_resolve_recover_sha_returns_recorded_sha(tmp_path):
    _write_partial_pipeline(tmp_path)
    assert review_recover.resolve_recover_sha(tmp_path, "abc1234") == "abc1234"


def test_resolve_recover_sha_pins_when_head_moved(tmp_path):
    """New commits must not abort recovery — the run completes at its own commit."""
    _write_partial_pipeline(tmp_path)
    assert review_recover.resolve_recover_sha(tmp_path, "def5678") == "abc1234"


def test_resolve_recover_sha_without_head(tmp_path):
    """Empty head_sha means HEAD couldn't be determined — still pin to the record."""
    _write_partial_pipeline(tmp_path)
    assert review_recover.resolve_recover_sha(tmp_path, "") == "abc1234"


def test_resolve_recover_sha_untracked_state(tmp_path):
    """State written before SHA tracking has nothing to pin to."""
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "group_names": ["g1"], "failed": {"synthesis": "crashed"},
        "groups_done": [],
    }))
    assert review_recover.resolve_recover_sha(tmp_path, "abc1234") == ""


def test_resolve_recover_sha_without_pipeline_state(tmp_path):
    with pytest.raises(SystemExit) as exc:
        review_recover.resolve_recover_sha(tmp_path, "abc1234")
    assert exc.value.code == 1


def test_resolve_recover_sha_completed_review(tmp_path):
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc1234", "group_names": ["g1"],
        "done": ["synthesis", "disprove"],
        "failed": {}, "groups_done": [1], "groups_failed": {},
    }))
    with pytest.raises(SystemExit) as exc:
        review_recover.resolve_recover_sha(tmp_path, "abc1234")
    assert exc.value.code == 0


def test_resolve_recover_sha_recovers_a_run_killed_in_the_gate(tmp_path):
    """The entry point `--recover` goes through, ahead of the pipeline itself.

    `resolve_recover_sha` exits before `_resolve_recovery` is ever reached, so
    the two have to agree on what finished means or the resume is unreachable.
    """
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc1234", "group_names": ["g1"], "done": ["synthesis"],
        "failed": {}, "groups_done": [1], "groups_failed": {},
    }))

    assert review_recover.resolve_recover_sha(tmp_path, "abc1234") == "abc1234"


# ── recover_drifted ───────────────────────────────────────────────────────────


def test_recover_drifted_when_head_moved(monkeypatch):
    monkeypatch.setattr(review_recover.pr_context, "head_sha", lambda cwd=None: "def5678")
    assert review_recover.recover_drifted("abc1234", "/wt") is True


def test_recover_drifted_when_head_matches(monkeypatch):
    monkeypatch.setattr(review_recover.pr_context, "head_sha", lambda cwd=None: "abc1234")
    assert review_recover.recover_drifted("abc1234", "/wt") is False


def test_recover_drifted_empty_sha_is_false(monkeypatch):
    monkeypatch.setattr(review_recover.pr_context, "head_sha", lambda cwd=None: "abc1234")
    assert review_recover.recover_drifted("", "/wt") is False


# ── pin_recover_worktree ──────────────────────────────────────────────────────


def test_pin_recover_worktree_noop_when_head_matches(monkeypatch):
    head_sha = MagicMock(return_value="abc1234")
    monkeypatch.setattr(review_recover.pr_context, "head_sha", head_sha)
    detach = MagicMock()
    monkeypatch.setattr(review_recover.review_worktree, "detached_worktree_at", detach)

    assert review_recover.pin_recover_worktree("abc1234", "/wt", "/repo", "l") == ("/wt", None)
    assert detach.call_count == 0
    assert head_sha.call_args.args == ("/wt",)


def test_pin_recover_worktree_checks_out_pinned_commit(monkeypatch):
    monkeypatch.setattr(review_recover.pr_context, "head_sha", lambda cwd=None: "def5678")
    pinned = WorktreeResult(
        path="/repo/.worktrees/l", cleanup_ref="/repo/.worktrees/l", is_fallback=True)
    monkeypatch.setattr(
        review_recover.review_worktree, "detached_worktree_at",
        lambda sha, repo_dir, label: pinned,
    )

    path, result = review_recover.pin_recover_worktree("abc1234", "/wt", "/repo", "l")

    assert path == "/repo/.worktrees/l"
    assert result is pinned


def test_pin_recover_worktree_exits_when_commit_gone(monkeypatch):
    monkeypatch.setattr(review_recover.pr_context, "head_sha", lambda cwd=None: "def5678")
    monkeypatch.setattr(
        review_recover.review_worktree, "detached_worktree_at", lambda *a, **kw: None)

    with pytest.raises(SystemExit) as exc:
        review_recover.pin_recover_worktree("abc1234", "/wt", "/repo", "l")
    assert exc.value.code == 1


# ── should_auto_recover ───────────────────────────────────────────────────────


def test_should_auto_recover_logs_when_head_matches_a_partial(tmp_path, monkeypatch, capsys):
    review = tmp_path / "review.md"
    review.write_text("<!-- head_sha: abc1234 -->\n")
    _write_partial_pipeline(tmp_path)
    monkeypatch.setattr(
        review_recover.gh_client, "pr_view",
        lambda *a, **kw: {"headRefOid": "abc1234"},
    )

    review_recover.should_auto_recover("owner/repo", "1", review)

    err = capsys.readouterr().err
    assert "Recovering failed review agents" in err


def test_should_auto_recover_silent_when_head_moved(tmp_path, monkeypatch, capsys):
    review = tmp_path / "review.md"
    review.write_text("<!-- head_sha: abc1234 -->\n")
    monkeypatch.setattr(
        review_recover.gh_client, "pr_view",
        lambda *a, **kw: {"headRefOid": "def5678"},
    )

    review_recover.should_auto_recover("owner/repo", "1", review)

    assert capsys.readouterr().err == ""
