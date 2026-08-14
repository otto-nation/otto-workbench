"""Tests for review and target-state garbage collection."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_state
import review_gc
import run_lock


def test_prune_merged_targets_removes_a_merged_prs_dir(tmp_path, monkeypatch):
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    assert review_gc.prune_merged_targets(tmp_path) == 1
    assert not target.exists()


def test_prune_merged_targets_keeps_an_open_pr(tmp_path, monkeypatch):
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "")

    assert review_gc.prune_merged_targets(tmp_path) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_leaves_a_live_targets_dir_alone(tmp_path, monkeypatch):
    """rmtree would unlink the inode that run's flock lives on."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    with run_lock.acquire(target, command="pr review", started="t"):
        os.environ.pop(run_lock.LOCK_ENV, None)
        assert review_gc.prune_merged_targets(tmp_path) == 0

    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_skips_our_own_target(tmp_path, monkeypatch):
    """gc runs holding its own target's lock; LOCK_ENV would wave it through."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    assert review_gc.prune_merged_targets(tmp_path, skip=target) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_respects_the_budget(tmp_path, monkeypatch):
    for i in range(5):
        d = tmp_path / f"widget-feat-{i}"
        d.mkdir(parents=True)
        pr_state.save_state(d, pr_state.new_state(
            repo="acme/widget", branch=f"feat/{i}", pr_number=i + 1,
            head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    assert review_gc.prune_merged_targets(tmp_path, max_files=2) == 2
