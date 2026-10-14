"""Shared git topology fixtures — a paused rebase in a temp repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import commit_all, git_in, init_repo


def _paused_rebase(
    tmp_path, *rebase_args: str, branch: str | None = "feat", worktree: bool = False,
) -> Path:
    """A repo stopped mid-rebase on a conflict, HEAD detached.

    *branch* None starts the rebase from a detached HEAD instead of a branch.
    *worktree* runs the rebase in a linked worktree at ``tmp_path/feat-wt`` and
    returns that path; the main checkout stays at ``tmp_path/repo`` on main.
    *worktree* implies a branch: it cannot be combined with ``branch=None``.
    """
    assert branch or not worktree, "worktree=True needs a branch; branch=None is unsupported"
    repo = init_repo(tmp_path / "repo")
    (repo / "f").write_text("base\n")
    commit_all(repo, "base")
    git_in(repo, "checkout", "-qb", "feat")
    (repo / "f").write_text("feat\n")
    commit_all(repo, "feat")
    git_in(repo, "checkout", "-q", "main")
    (repo / "f").write_text("main\n")
    commit_all(repo, "main")
    where = repo
    if worktree:
        where = tmp_path / "feat-wt"
        git_in(repo, "worktree", "add", "-q", str(where), "feat")
    else:
        git_in(repo, "checkout", "-q", "feat" if branch else "--detach")
        if branch is None:
            git_in(repo, "reset", "-q", "--hard", "feat")
    result = subprocess.run(
        ["git", "rebase", *rebase_args, "main"],
        cwd=where, capture_output=True, text=True,
    )
    assert result.returncode != 0, "fixture expected the rebase to stop on a conflict"
    return where
