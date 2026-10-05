"""Tests for rebase.need and git.client.commits_behind — rebase need read from refs."""

import sys
from pathlib import Path

from conftest import commit_all, git_in, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import rebase.need  # noqa: E402
from rebase.need import RebaseNeed  # noqa: E402


def _repo_with_feat_one_behind(tmp_path) -> Path:
    repo = init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("a\n")
    commit_all(repo, "base")
    git_in(repo, "branch", "feat")
    (repo / "b.txt").write_text("b\n")
    commit_all(repo, "main moves")
    return repo


def test_a_branch_behind_its_base_needs_a_rebase(tmp_path):
    repo = _repo_with_feat_one_behind(tmp_path)
    got = rebase.need.need(str(repo), "refs/heads/feat", "refs/heads/main", base_label="main")
    assert got == RebaseNeed(True, "1 behind main", 1)


def test_a_branch_at_its_base_needs_none(tmp_path):
    repo = _repo_with_feat_one_behind(tmp_path)
    got = rebase.need.need(str(repo), "refs/heads/main", "refs/heads/main", base_label="main")
    assert got == RebaseNeed(False, "up to date with main", 0)


def test_an_unresolvable_base_is_unresolved_rather_than_current(tmp_path):
    """Raw `commits_ahead` answers 0 for a missing ref — the bug this exists to stop."""
    repo = _repo_with_feat_one_behind(tmp_path)
    got = rebase.need.need(str(repo), "refs/heads/feat", "refs/heads/nope")
    assert got.resolved is False and got.needed is False


def test_commits_behind_is_none_for_a_missing_head(tmp_path):
    repo = _repo_with_feat_one_behind(tmp_path)
    assert git.client.commits_behind(str(repo), head_ref="refs/heads/gone",
                                     base_ref="refs/heads/main") is None
    assert git.client.commits_behind(str(repo), head_ref="refs/heads/feat",
                                     base_ref="refs/heads/main") == 1
