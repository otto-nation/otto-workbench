"""A worktree path already occupied by another branch is a named collision."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.steps  # noqa: E402
import git.topology  # noqa: E402
from conftest import git_in, init_repo, seed_repo  # noqa: E402


def test_a_worktree_on_another_branch_at_the_branch_path_is_a_collision(tmp_path, monkeypatch):
    repo = seed_repo(tmp_path / "main")
    git_in(repo, "worktree", "add", "-b", "other", str(tmp_path / "feat-x"))
    monkeypatch.setattr(git.topology, "wt_switch", lambda *a, **kw: None)
    res = batch.steps.ensure_worktree(str(repo), "feat/x")
    assert res.collision
    assert "worktree move" in res.error
    assert "other" in res.error
    assert str(tmp_path / "feat-x") in res.error


def test_a_named_worktree_already_on_the_branch_is_not_a_collision(tmp_path, monkeypatch):
    """Index miss + failed switch still reuses a dir that is already on the branch."""
    repo = seed_repo(tmp_path / "main")
    wt = tmp_path / "feat-x"
    git_in(repo, "worktree", "add", "-b", "feat/x", str(wt))
    monkeypatch.setattr(git.topology, "find_worktree_for_branch", lambda *a, **kw: None)
    monkeypatch.setattr(git.topology, "wt_switch", lambda *a, **kw: None)
    res = batch.steps.ensure_worktree(str(repo), "feat/x")
    assert res.ok
    assert not res.collision
    assert Path(res.path).resolve() == wt.resolve()


def test_a_failed_switch_with_no_such_directory_is_not_a_collision(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "main")
    monkeypatch.setattr(git.topology, "wt_switch", lambda *a, **kw: None)
    res = batch.steps.ensure_worktree(str(repo), "feat/x")
    assert not res.collision
    assert res.error == "could not create a worktree for feat/x"
