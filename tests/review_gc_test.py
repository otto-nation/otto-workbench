"""Tests for review and target-state garbage collection."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

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
    """Removing the target would unlink the inode that run's flock lives on."""
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


def test_prune_merged_targets_removes_a_target_with_corrupt_state(tmp_path):
    """load_state folds an unparseable file into None; the recovery branch
    that follows must still reclaim the target rather than leaving it stuck."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    (target / pr_state.STATE_FILE).write_bytes(b"{ not json")

    assert review_gc.prune_merged_targets(tmp_path) == 1
    assert not target.exists()


def test_prune_merged_targets_keeps_a_target_with_no_pr_number(tmp_path, monkeypatch):
    """A branch that never opened a PR has no liveness signal to ask GitHub
    about, so the guard must skip it before it ever costs budget or a `gh`
    call — not merely leave it unpruned after asking."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=None,
        head_sha="sha", worktree_root="/wt"))

    def _fail(repo, n):
        raise AssertionError("must not ask GitHub about a target with no PR")

    monkeypatch.setattr(review_gc, "_pr_close_state", _fail)

    assert review_gc.prune_merged_targets(tmp_path) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_counts_a_partial_prune_failure_as_not_pruned(tmp_path, monkeypatch):
    """An entry that will not unlink leaves the directory there; the count and
    the target's continued existence must agree, or a later sweep whose
    state.json alone went would never revisit it."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))
    (target / "leftover").mkdir()
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    assert review_gc.prune_merged_targets(tmp_path) == 0
    assert target.exists()


def test_prune_merged_targets_yields_to_a_run_that_arrives_mid_removal(tmp_path, monkeypatch):
    """The window rmtree left open: a run that takes the target after we unlink
    run.lock holds a flock on a fresh inode while we still hold the old one.
    A non-recursive rmdir fails with ENOTEMPTY instead of deleting its state."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    real_rmdir = Path.rmdir

    def arriving_run(self):
        (self / run_lock.LOCK_FILE).write_text("{}")
        real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", arriving_run)

    assert review_gc.prune_merged_targets(tmp_path) == 0
    assert (target / run_lock.LOCK_FILE).is_file()


def test_prune_one_target_unlinks_the_lock_file_last(tmp_path):
    """Ordering is the whole fix: every other entry goes while the lock we hold
    still pins the inode a contender would have to agree with."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=1,
        head_sha="sha", worktree_root="/wt"))

    unlinked = []
    real_unlink = Path.unlink

    def recording_unlink(self, **kwargs):
        unlinked.append(self.name)
        real_unlink(self, **kwargs)

    with patch.object(Path, "unlink", recording_unlink):
        assert review_gc._prune_one_target(target) is True

    assert unlinked[-1] == run_lock.LOCK_FILE
    assert pr_state.STATE_FILE in unlinked[:-1]


def test_prune_merged_targets_respects_the_budget(tmp_path, monkeypatch):
    for i in range(5):
        d = tmp_path / f"widget-feat-{i}"
        d.mkdir(parents=True)
        pr_state.save_state(d, pr_state.new_state(
            repo="acme/widget", branch=f"feat/{i}", pr_number=i + 1,
            head_sha="sha", worktree_root="/wt"))
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: "MERGED")

    assert review_gc.prune_merged_targets(tmp_path, max_files=2) == 2
