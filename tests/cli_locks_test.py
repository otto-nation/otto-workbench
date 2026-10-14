"""Tests for `cli.locks` — the run locks a command declares, taken for it."""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402
from conftest import make_ctx  # noqa: E402

import cli.locks  # noqa: E402
import cli.registry  # noqa: E402
import cli.review_threads  # noqa: E402
import core.run_lock  # noqa: E402
import pr.context  # noqa: E402
import pr.thread_replies  # noqa: E402
import review.review_body_reply  # noqa: E402
from run_lock_support import flocked, git_dir, held_elsewhere, live_pid  # noqa: E402


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
    with live_pid() as pid, \
         held_elsewhere(ctx.target_dir / core.run_lock.LOCK_FILE, {
             "pid": pid, "command": "pr batch run R", "started": "t0",
         }):
        seen = pid
        with pytest.raises(SystemExit) as exc:
            cli.locks.claim_declared("rebase", [], ctx, command="pr-rebase")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"owns this target owner/repo@feat/test: pr batch run R (pid {seen}" in err


def test_hold_declared_takes_a_global_with_no_context():
    batch = core.run_lock.Global("batch")
    with cli.locks.hold_declared({batch}, None, command="pr batch run"):
        assert core.run_lock.is_global_held(batch)
    assert not core.run_lock.is_global_held(batch)


# ── the direct entry points ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", sorted(n for n, s in cli.registry.COMMANDS.items() if s.script))
def test_every_direct_entry_point_takes_its_declared_locks(name):
    """A command with an `ai/bin` script can be run without `pr`, so its handler
    must take what the registry declares — through `claim_declared`, named for
    itself, and never by choosing locks of its own."""
    module = cli.registry.COMMANDS[name].handler.split(":")[0]
    path = (LIB_DIR / Path(*module.split("."))).with_suffix(".py")
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "cli.locks.claim_declared"]
    assert calls, f"{module} takes no declared lock"
    assert all(isinstance(c.args[0], ast.Constant) and c.args[0].value == name
               for c in calls)


def _reply_argv(tmp_path):
    body_file = tmp_path / "reply.md"
    body_file.write_text("Done.")
    return ["--reply", "123", "--body-file", str(body_file)]


def test_a_direct_reply_holds_the_target_and_leaves_the_checkout_free(
    worktree, tmp_path, monkeypatch,
):
    """`--reply` returns before the threads flow, and used to skip the lock with it."""
    ctx = _ctx(worktree)
    monkeypatch.setattr(pr.context, "resolve", lambda **kw: ctx)
    monkeypatch.setattr(review.review_body_reply, "route_reply",
                        lambda c, reply: review.review_body_reply.ReplyRoute(
                            pr_data=None, thread=True))
    seen = {}

    def run_reply(c, reply, body, **kw):
        seen["target"] = core.run_lock.is_held(ctx.target_dir)
        seen["checkout"] = flocked(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE)
        return 0

    monkeypatch.setattr(pr.thread_replies, "run_reply", run_reply)
    assert cli.review_threads.main(_reply_argv(tmp_path)) == 0
    assert seen == {"target": True, "checkout": False}


def test_a_review_body_reply_holds_the_target_and_leaves_the_checkout_free(
    worktree, tmp_path, monkeypatch,
):
    """It falls through to the locked path, where `--reply` still declares the target only."""
    ctx = _ctx(worktree)
    monkeypatch.setattr(pr.context, "resolve", lambda **kw: ctx)
    monkeypatch.setattr(review.review_body_reply, "route_reply",
                        lambda c, reply: review.review_body_reply.ReplyRoute(
                            pr_data=None, source="rb-123"))
    seen = {}

    def run_review_body_reply(c, route, body, trail=None):
        seen["target"] = core.run_lock.is_held(ctx.target_dir)
        seen["checkout"] = flocked(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE)
        return 0

    monkeypatch.setattr(review.review_body_reply, "run_review_body_reply",
                        run_review_body_reply)
    assert cli.review_threads.main(_reply_argv(tmp_path)) == 0
    assert seen == {"target": True, "checkout": False}
