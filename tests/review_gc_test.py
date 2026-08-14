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


class _RecordingTrail:
    """Stands in for a Trail, recording what the prune would have written."""

    def __init__(self):
        self.summaries = []

    def summary(self, action, detail, *, data=None, context=None):
        self.summaries.append({
            "action": action, "detail": detail, "data": data, "context": context,
        })


def _seed_target(base, name="widget-feat-a", **overrides):
    """Create a target dir with a state.json, defaulting to a single open PR."""
    target = base / name
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo=overrides.pop("repo", "acme/widget"),
        branch=overrides.pop("branch", "feat/a"),
        pr_number=overrides.pop("pr_number", 1),
        head_sha=overrides.pop("head_sha", "sha"),
        worktree_root=overrides.pop("worktree_root", "/wt"),
    ))
    return target


def test_prune_merged_targets_removes_a_merged_prs_dir(tmp_path, monkeypatch):
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 1
    assert not target.exists()


def test_prune_merged_targets_keeps_an_open_pr(tmp_path, monkeypatch):
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("", ""))

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_leaves_a_live_targets_dir_alone(tmp_path, monkeypatch):
    """Removing the target would unlink the inode that run's flock lives on."""
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))

    with run_lock.acquire(target, command="pr review", started="t"):
        os.environ.pop(run_lock.LOCK_ENV, None)
        assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0

    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_skips_our_own_target(tmp_path, monkeypatch):
    """gc runs holding its own target's lock; LOCK_ENV would wave it through."""
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))

    assert review_gc.prune_merged_targets(tmp_path, skip=target, trail=_RecordingTrail()) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_removes_a_target_with_corrupt_state(tmp_path):
    """load_state folds an unparseable file into None; the recovery branch
    that follows must still reclaim the target rather than leaving it stuck."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    (target / pr_state.STATE_FILE).write_bytes(b"{ not json")

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 1
    assert not target.exists()


def test_prune_merged_targets_keeps_a_target_with_no_pr_number(tmp_path, monkeypatch):
    """A branch that never opened a PR has no liveness signal to ask GitHub
    about, so the guard must skip it before it ever costs budget or a `gh`
    call — not merely leave it unpruned after asking."""
    target = _seed_target(tmp_path, pr_number=None)

    def _fail(repo, n):
        raise AssertionError("must not ask GitHub about a target with no PR")

    monkeypatch.setattr(review_gc, "_pr_close_state", _fail)

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_counts_a_partial_prune_failure_as_not_pruned(tmp_path, monkeypatch):
    """An entry that will not unlink leaves the directory there; the count and
    the target's continued existence must agree, or a later sweep whose
    state.json alone went would never revisit it."""
    target = _seed_target(tmp_path)
    (target / "leftover").mkdir()
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert target.exists()
    # The next sweep's glob only finds targets that still have a state.json,
    # so surviving the failure means surviving with that file.
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_yields_to_a_run_that_arrives_mid_removal(tmp_path, monkeypatch):
    """The window rmtree left open: a run that takes the target after we unlink
    run.lock holds a flock on a fresh inode while we still hold the old one.
    A non-recursive rmdir fails with ENOTEMPTY instead of deleting its state."""
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))

    real_rmdir = Path.rmdir

    def arriving_run(self):
        (self / run_lock.LOCK_FILE).write_text("{}")
        real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", arriving_run)

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert (target / run_lock.LOCK_FILE).is_file()


def test_prune_one_target_unlinks_the_lock_file_last(tmp_path):
    """Ordering is the whole fix: every other entry goes while the lock we hold
    still pins the inode a contender would have to agree with."""
    target = _seed_target(tmp_path)

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
        _seed_target(tmp_path, name=f"widget-feat-{i}",
                     branch=f"feat/{i}", pr_number=i + 1)
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))

    assert review_gc.prune_merged_targets(tmp_path, max_files=2, trail=_RecordingTrail()) == 2


def test_merged_target_emits_one_terminal_summary(tmp_path, monkeypatch):
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(
        review_gc, "_pr_close_state", lambda repo, n: ("MERGED", "2026-08-13T09:00:00Z"))
    trail = _RecordingTrail()

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 1

    assert len(trail.summaries) == 1
    event = trail.summaries[0]
    assert event["action"] == pr_state.TERMINAL_SUMMARY_ACTION
    assert event["data"]["outcome"] == "MERGED"
    assert event["data"]["ended_at"] == "2026-08-13T09:00:00Z"


def test_terminal_summary_names_the_pruned_pr_not_the_gc_run(tmp_path, monkeypatch):
    """`otto-log query --pr N` has to find the record that says how N ended."""
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("CLOSED", ""))
    trail = _RecordingTrail()

    review_gc.prune_merged_targets(tmp_path, trail=trail)

    ctx = trail.summaries[0]["context"]
    assert set(ctx) == {"repo", "pr", "branch"}
    assert ctx["pr"]


def test_open_target_emits_nothing(tmp_path, monkeypatch):
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("", ""))
    trail = _RecordingTrail()

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 0
    assert trail.summaries == []


def test_a_target_that_fails_to_prune_emits_nothing(tmp_path, monkeypatch):
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(review_gc, "_pr_close_state", lambda repo, n: ("MERGED", ""))
    monkeypatch.setattr(review_gc, "_prune_one_target", lambda target: False)
    trail = _RecordingTrail()

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 0
    assert trail.summaries == []
