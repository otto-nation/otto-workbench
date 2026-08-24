"""The sandboxes prove themselves: no test writes to the real state root, and
no git command a test runs fires the machine's hooks."""

import os
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths

from conftest import git_in, init_worktree


def test_state_root_is_sandboxed_per_test(tmp_path):
    """Every test gets its own state root, so nothing lands in ~/.local/state."""
    assert os.environ["WORKBENCH_STATE_DIR"] == str(tmp_path / "state")


def test_state_dir_resolves_through_the_sandbox(tmp_path):
    """The env var is the whole mechanism — it reaches subprocesses too."""
    assert workbench_paths.state_dir() == tmp_path / "state"


# ── git hooks ───────────────────────────────────────────────────────────────


def _reject_commits_from(hooks: Path) -> Path:
    """A hooks directory whose `pre-commit` refuses every commit."""
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/usr/bin/env bash\necho rejected-by-hook >&2\nexit 1\n")
    hook.chmod(0o755)
    return hooks


def _commit_in(repo: Path) -> subprocess.CompletedProcess:
    """Commit a file, reporting the outcome rather than raising on rejection.

    The commit is spelled out rather than run through `git_in` because `git_in`
    raises on a non-zero exit, and a rejected commit is the result half these
    tests are asserting on. The staging step above has no such reason.
    """
    (repo / "f.txt").write_text("one")
    git_in(repo, "add", "--", "f.txt")
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "one"],
        capture_output=True, text=True)


def test_a_test_repo_runs_no_hook(tmp_path):
    """A hook planted where git looks by default does not fire."""
    repo = init_worktree(tmp_path / "repo")
    _reject_commits_from(repo / ".git" / "hooks")

    assert _commit_in(repo).returncode == 0


def test_the_sandbox_is_what_stops_the_hook(tmp_path, live_git_hooks):
    """The same repo with the sandbox lifted, so the test above is known to be
    reporting the fixture rather than passing for some other reason.

    Worth pinning because on a machine that has a global `core.hooksPath` the
    planted hook is bypassed either way, and the test above would keep passing
    long after the fixture stopped working.
    """
    repo = init_worktree(tmp_path / "repo")
    _reject_commits_from(repo / ".git" / "hooks")

    assert _commit_in(repo).returncode != 0


def test_a_container_worktree_runs_no_hook(container):
    """The bare clone is a repo of its own that `init_worktree` never sees, and
    its worktrees read its config — so it is the case a per-repo write missed."""
    _reject_commits_from(container / ".git" / "hooks")

    assert _commit_in(container / "main").returncode == 0
