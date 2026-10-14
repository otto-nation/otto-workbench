"""Tests for `cli.locks` — the run locks a command declares, taken for it."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402
from conftest import make_ctx  # noqa: E402

import cli.locks  # noqa: E402
import core.run_lock  # noqa: E402
from run_lock_support import flocked, git_dir, held_elsewhere  # noqa: E402


def _ctx(worktree):
    return make_ctx(worktree_root=worktree, target_dir=worktree / "target")


def test_a_direct_rebase_holds_target_and_checkout(worktree):
    ctx = _ctx(worktree)
    cli.locks.claim_declared("rebase", [], ctx, command="pr-rebase")
    assert core.run_lock.is_held(ctx.target_dir)
    assert flocked(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE)


def test_ci_without_fix_leaves_the_checkout_free(worktree):
    ctx = _ctx(worktree)
    cli.locks.claim_declared("ci", [], ctx, command="ci-check")
    assert core.run_lock.is_held(ctx.target_dir)
    assert not flocked(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE)


def test_ci_with_fix_holds_the_checkout(worktree):
    cli.locks.claim_declared("ci", ["--fix"], _ctx(worktree), command="ci-check --fix")
    assert flocked(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE)


def test_a_reply_leaves_the_checkout_free(worktree):
    ctx = _ctx(worktree)
    cli.locks.claim_declared("comments", ["--reply", "1"], ctx, command="review-threads")
    assert core.run_lock.is_held(ctx.target_dir)
    assert not flocked(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE)


def test_a_command_declaring_nothing_takes_nothing(worktree):
    ctx = _ctx(worktree)
    cli.locks.claim_declared("status", [], ctx, command="pr status")
    assert not (ctx.target_dir / core.run_lock.LOCK_FILE).exists()


def test_a_global_lock_is_never_claimed_for_a_process(worktree):
    with pytest.raises(ValueError):
        cli.locks.claim_declared("batch", ["run"], _ctx(worktree), command="pr batch run")


def test_contention_exits_1_naming_the_holder_and_the_target(worktree, capsys):
    ctx = _ctx(worktree)
    record = {"pid": 4242, "command": "pr batch run R", "started": "t0"}
    with held_elsewhere(ctx.target_dir / core.run_lock.LOCK_FILE, record), \
         pytest.raises(SystemExit) as exc:
        cli.locks.claim_declared("rebase", [], ctx, command="pr-rebase")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "owns this target owner/repo@feat/test: pr batch run R (pid 4242" in err


def test_hold_declared_takes_a_global_with_no_context():
    batch = core.run_lock.Global("batch")
    with cli.locks.hold_declared({batch}, None, command="pr batch run"):
        assert core.run_lock.is_global_held(batch)
    assert not core.run_lock.is_global_held(batch)
