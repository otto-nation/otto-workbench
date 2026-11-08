"""A worktree path already occupied by another branch is a named collision."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.steps  # noqa: E402
import git.topology  # noqa: E402
from batch.model import FindingReason  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from conftest import git_in, init_repo  # noqa: E402

from batch_scheduler_support import (  # noqa: F401
    Harness, _close_open_sessions, _quiet_outcomes, row,
)


def test_a_worktree_on_another_branch_at_the_branch_path_is_a_collision(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "main")
    git_in(repo, "worktree", "add", "-b", "other", str(tmp_path / "feat-x"))
    monkeypatch.setattr(git.topology, "wt_switch", lambda *a, **kw: None)
    res = batch.steps.ensure_worktree(str(repo), "feat/x")
    assert res.collision
    assert "worktree move" in res.error
    assert "other" in res.error
    assert str(tmp_path / "feat-x") in res.error


def test_a_failed_switch_with_no_such_directory_is_not_a_collision(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "main")
    monkeypatch.setattr(git.topology, "wt_switch", lambda *a, **kw: None)
    res = batch.steps.ensure_worktree(str(repo), "feat/x")
    assert not res.collision
    assert res.error == "could not create a worktree for feat/x"


def test_a_collision_fails_the_item_with_its_reason():
    # Revert: skip findings.record in scheduler._decide.
    h = Harness(
        [row(1)],
        worktrees=lambda d, b: WorktreeResult("", False, "x", collision=True),
    )
    h.sched.run_until_blocked()
    failed = h.run.items[0].findings[0]
    assert failed.reason is FindingReason.WORKTREE_COLLISION
    assert failed.step == "worktree"
    assert failed.detail == "x"
