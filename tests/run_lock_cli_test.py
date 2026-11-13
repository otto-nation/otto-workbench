"""Tests for `lib/run_lock_cli.py held-tree`: how bash asks whether a live run holds a checkout."""

import subprocess
import sys
from pathlib import Path

from conftest import _load_lib

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.run_lock  # noqa: E402
from run_lock_support import git_dir, held_elsewhere  # noqa: E402

run_lock_cli = _load_lib("run_lock_cli")
CLI = REPO_ROOT / "lib" / "run_lock_cli.py"


def _held_tree(path):
    return subprocess.run([sys.executable, str(CLI), "held-tree", str(path)],
                          capture_output=True, text=True, timeout=120)


def test_a_free_checkout_exits_0(worktree):
    assert _held_tree(worktree).returncode == 0


def test_a_held_checkout_exits_the_busy_code(worktree):
    lock = git_dir(worktree) / core.run_lock.TREE_LOCK_FILE
    with held_elsewhere(lock, {"pid": 1, "command": "pr review 7", "released": None}):
        assert _held_tree(worktree).returncode == core.run_lock.EXIT_BUSY


def test_a_path_git_names_no_checkout_exits_0(tmp_path):
    assert _held_tree(tmp_path / "gone").returncode == 0


def test_an_existing_directory_that_is_not_a_git_repo_exits_2(tmp_path):
    assert _held_tree(tmp_path).returncode == 2


def test_a_usage_error_is_not_an_answer():
    result = subprocess.run([sys.executable, str(CLI), "held-tree"], capture_output=True, timeout=120)
    assert result.returncode == 2


def test_main_answers_in_process(worktree):
    assert run_lock_cli.main(["held-tree", str(worktree)]) == 0
