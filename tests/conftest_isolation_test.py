"""The sandboxes prove themselves: no test writes to the real state root, and
no test repo runs the machine's git hooks."""

import os
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths

from conftest import disown_hooks, init_worktree, seed_repo


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
    (repo / "f.txt").write_text("one")
    subprocess.run(["git", "-C", str(repo), "add", "--", "f.txt"],
                   check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "one"],
        capture_output=True, text=True)


def test_disown_hooks_silences_a_hook_that_would_reject_the_commit(tmp_path):
    """The contract, against a hooks dir this test controls.

    `core.hooksPath` is global on a workbench machine, so the real thing being
    disowned here is the developer's own `pre-commit` — which no test may set
    up, and which is free to change under the suite.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q", str(repo)],
                   check=True, capture_output=True)
    hooks = _reject_commits_from(tmp_path / "hooks")
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", str(hooks)],
                   check=True, capture_output=True)
    assert _commit_in(repo).returncode != 0

    disown_hooks(repo)
    assert _commit_in(repo).returncode == 0


def test_a_repo_from_init_worktree_runs_no_hook(tmp_path):
    """The wiring: a test repo is disowned by construction, not on request.

    Planted where git looks by default, so this fails on a machine that has no
    global `core.hooksPath` if `init_worktree` ever stops disowning.
    """
    repo = init_worktree(tmp_path / "repo")
    _reject_commits_from(repo / ".git" / "hooks")

    assert _commit_in(repo).returncode == 0


def test_a_container_worktree_runs_no_hook(tmp_path):
    """The bare clone is its own repo, so it needs disowning of its own —
    `init_worktree` never sees it, and its worktrees read its config."""
    seed = seed_repo(tmp_path / "seed")
    root = tmp_path / "container"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(root / ".git")],
                   check=True, capture_output=True)
    disown_hooks(root / ".git")
    _reject_commits_from(root / ".git" / "hooks")
    subprocess.run(["git", "-C", str(root / ".git"), "worktree", "add", "-q",
                    str(root / "main"), "main"], check=True, capture_output=True)

    assert _commit_in(root / "main").returncode == 0
