"""Tests for review.worktree resolution — resolve_wt_path, resolve_branch_input and
find_repo_root."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.worktree import find_repo_root, resolve_branch_input, resolve_wt_path


# ── resolve_wt_path ───────────────────────────────────────────────────────────


def test_resolve_wt_path_returns_git_toplevel(monkeypatch):
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **k: "/repos/widget",
    )
    assert resolve_wt_path("", "feat/x") == "/repos/widget"
    assert resolve_wt_path("/ignored", "feat/x") == "/repos/widget"


def test_resolve_wt_path_bare_repo_uses_topology(monkeypatch):
    monkeypatch.setattr(
        "git.client.out", lambda *a, **k: "")
    monkeypatch.setattr(
        "git.topology.is_bare_repo", lambda cwd=None: True)
    monkeypatch.setattr(
        "git.topology.resolve_bare_repo_worktree",
        lambda cwd, branch: Path("/repos/widget/feat-x"),
    )
    assert resolve_wt_path("", "feat/x") == "/repos/widget/feat-x"


def test_resolve_wt_path_bare_repo_missing_worktree_exits(monkeypatch):
    monkeypatch.setattr(
        "git.client.out", lambda *a, **k: "")
    monkeypatch.setattr(
        "git.topology.is_bare_repo", lambda cwd=None: True)
    monkeypatch.setattr(
        "git.topology.resolve_bare_repo_worktree",
        lambda cwd, branch: None,
    )
    monkeypatch.setattr(
        "git.topology.default_branch", lambda cwd=None: "main")
    with pytest.raises(SystemExit) as exc:
        resolve_wt_path("", "feat/x")
    assert exc.value.code == 1


def test_resolve_wt_path_not_a_repo_exits(monkeypatch):
    monkeypatch.setattr(
        "git.client.out", lambda *a, **k: "")
    monkeypatch.setattr(
        "git.topology.is_bare_repo", lambda cwd=None: False)
    with pytest.raises(SystemExit) as exc:
        resolve_wt_path("/not/git", "")
    assert exc.value.code == 1


# ── resolve_branch_input ──────────────────────────────────────────────────────


def test_resolve_branch_input_uses_resolve_branch(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="feat/real\n"),
    )
    assert resolve_branch_input("feat/fuzzy", "/repo") == "feat/real"


def test_resolve_branch_input_falls_back_on_failure(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=1, stdout=""),
    )
    assert resolve_branch_input("feat/fuzzy", "/repo") == "feat/fuzzy"


def test_resolve_branch_input_falls_back_on_exception(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        MagicMock(side_effect=FileNotFoundError("resolve-branch")),
    )
    assert resolve_branch_input("feat/fuzzy", "") == "feat/fuzzy"


# ── find_repo_root ────────────────────────────────────────────────────────────


def test_find_repo_root_explicit_dir_that_exists(tmp_path, monkeypatch):
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("gh.client.out", gh)
    assert find_repo_root("owner/widget", str(tmp_path)) == str(tmp_path)
    gh.assert_not_called()


def test_find_repo_root_explicit_dir_missing_is_empty(tmp_path, monkeypatch):
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("gh.client.out", gh)
    assert find_repo_root("owner/widget", str(tmp_path / "nope")) == ""


def test_find_repo_root_toplevel_basename_match(monkeypatch):
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/Users/me/git/personal/widget",
    )
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("gh.client.out", gh)
    assert find_repo_root("owner/widget") == "/Users/me/git/personal/widget"


def test_find_repo_root_bare_container_parent_heuristic(monkeypatch):
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/Users/me/git/personal/widget/main",
    )
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("gh.client.out", gh)
    assert find_repo_root("owner/widget") == "/Users/me/git/personal/widget"


def test_find_repo_root_falls_through_to_gh_and_find(monkeypatch):
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/somewhere/else",
    )
    monkeypatch.setattr(
        "gh.client.out",
        lambda *a, **kw: "widget",
    )
    monkeypatch.setattr(
        "review.worktree.os.path.expanduser", lambda p: "/home/me/git")
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(
            stdout="/home/me/git/org/widget\n", returncode=0),
    )
    assert find_repo_root("owner/widget") == "/home/me/git/org/widget"


def test_find_repo_root_without_a_repo_name_returns_empty(monkeypatch):
    """A slug with no name half leaves nothing to search ``~/git`` for.

    This used to be the case where ``gh repo view --json name`` came back
    empty. The name is now taken from the slug the caller already passed, so
    the only way to have none is to pass none.
    """
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/somewhere/else",
    )
    find_run = MagicMock(side_effect=AssertionError("find should not run"))
    monkeypatch.setattr("review.worktree.subprocess.run", find_run)
    assert find_repo_root("") == ""
    find_run.assert_not_called()


def test_find_repo_root_names_the_repo_without_gh(monkeypatch):
    """The name half of the slug is the repo's name — asking GitHub for it was
    a GraphQL call that returned its own argument."""
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/somewhere/else",
    )

    def fail(*a, **kw):
        raise AssertionError("find_repo_root must not call gh")

    monkeypatch.setattr("gh.client.out", fail)
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "/home/dev/git/widget\n", ""),
    )
    assert find_repo_root("owner/widget") == "/home/dev/git/widget"


def test_find_repo_root_unusable_git_falls_through_to_gh(monkeypatch):
    monkeypatch.setattr(
        "git.client.out",
        MagicMock(side_effect=OSError("git not found")),
    )
    monkeypatch.setattr(
        "review.worktree.os.path.expanduser", lambda p: "/home/me/git")
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(stdout="", returncode=0),
    )
    assert find_repo_root("owner/widget") == ""


def test_find_repo_root_find_exception_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/somewhere/else",
    )
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        MagicMock(side_effect=TimeoutError("find hung")),
    )
    assert find_repo_root("owner/widget") == ""


def test_find_repo_root_matches_a_mixed_case_checkout(monkeypatch):
    """The slug is case-folded; the directory on disk is not.

    `detect_repo` returns `pr.target.RepoIdentity.label`, which folds A-Z, so a
    repo cloned as `MyProject` arrives here as `myproject`. Comparing that
    byte-for-byte against the directory name would miss the checkout the caller
    is sitting in and send the review off to walk ~/git for a repo it already
    found.
    """
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/home/dev/git/MyProject",
    )

    def fail(*a, **kw):
        raise AssertionError("a matched toplevel must not reach the ~/git walk")

    monkeypatch.setattr("review.worktree.subprocess.run", fail)
    assert find_repo_root("acme/myproject") == "/home/dev/git/MyProject"


def test_find_repo_root_matches_a_mixed_case_bare_container(monkeypatch):
    """Same fold, one level up: a bare-repo container holds the worktrees."""
    monkeypatch.setattr(
        "git.client.out",
        lambda *a, **kw: "/home/dev/git/MyProject/main",
    )

    def fail(*a, **kw):
        raise AssertionError("a matched container must not reach the ~/git walk")

    monkeypatch.setattr("review.worktree.subprocess.run", fail)
    assert find_repo_root("acme/myproject") == "/home/dev/git/MyProject"


def test_find_repo_root_walk_is_case_insensitive(monkeypatch):
    """The ~/git fallback folds too, or it reintroduces the miss one rung down."""
    seen = {}

    monkeypatch.setattr(
        "git.client.out", lambda *a, **kw: "/somewhere/else")

    def record(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "/home/dev/git/MyProject\n", "")

    monkeypatch.setattr("review.worktree.subprocess.run", record)
    monkeypatch.setattr("review.worktree.os.path.expanduser", lambda p: "/home/dev/git")

    assert find_repo_root("acme/myproject") == "/home/dev/git/MyProject"
    assert "-iname" in seen["cmd"]
