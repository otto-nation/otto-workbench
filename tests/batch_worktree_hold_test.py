"""A batch holds each item's worktree with its target, and never runs on the wrong branch."""

import contextlib
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import rebase.inspect  # noqa: E402

import batch.plan  # noqa: E402
import batch.publish  # noqa: E402
import batch.steps  # noqa: E402
import batch.targets  # noqa: E402
import cli.pr_batch  # noqa: E402
import core.run_lock  # noqa: E402
from batch.model import DecisionKind, Item, ItemStatus, Run, Step  # noqa: E402
from batch.publish import Refusal, TreeState  # noqa: E402
# `_quiet_outcomes` and `_close_open_sessions` are autouse fixtures; imported so pytest
# registers them here.
from batch_scheduler_support import (  # noqa: E402,F401
    NEED, NO, Harness, _close_open_sessions, _quiet_outcomes, row,
)
from conftest import commit_all, git_in, seed_repo  # noqa: E402
from core.run_lock import CHECKOUT, TARGET, TREE_LOCK_ENV, TREE_LOCK_FILE  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402
from run_lock_support import repo_with_origin  # noqa: E402

_PROC_TIMEOUT = 60
ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}

# A child that takes one checkout's lock and exits: 0 when it got through,
# 1 (refusal on stderr) when another process holds it.
_CLAIM_TREE = textwrap.dedent("""\
    import pathlib, sys
    sys.path.insert(0, sys.argv[1])
    import core.run_lock
    try:
        with core.run_lock.hold({core.run_lock.CHECKOUT}, worktree=pathlib.Path(sys.argv[2]),
                                command="child", started="t"):
            pass
    except core.run_lock.LockBusy as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
""")

# A child that holds one checkout until its stdin closes.
_HOLD_TREE = textwrap.dedent("""\
    import pathlib, sys
    sys.path.insert(0, sys.argv[1])
    import core.run_lock
    with core.run_lock.hold({core.run_lock.CHECKOUT}, worktree=pathlib.Path(sys.argv[2]),
                            command="pr comments --fix", started="t"):
        print("held", flush=True)
        sys.stdin.read()
""")


@pytest.fixture
def repo(tmp_path):
    return repo_with_origin(tmp_path / "repo")


@pytest.fixture
def held_elsewhere(repo):
    """Another process holding *repo*'s checkout, for the length of the test."""
    proc = subprocess.Popen([sys.executable, "-c", _HOLD_TREE, str(LIB_DIR), str(repo)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    yield proc
    proc.stdin.close()
    proc.wait(timeout=_PROC_TIMEOUT)


def _item(repo_dir, branch="feat/a", n=1, worktree=""):
    return Item(key=f"acme/widget#{n}", repo="acme/widget", repo_dir=str(repo_dir), pr=n,
                branch=branch, head_sha="h", worktree=str(worktree))


def _checkout(worktree):
    return core.run_lock.hold({CHECKOUT}, worktree=Path(worktree), command="pr batch run r",
                              started="t")


def _tree_lock(worktree) -> Path:
    return batch.targets.checkout_git_dir(str(worktree)) / TREE_LOCK_FILE


def _holds_with_target(holds, item):
    target = batch.targets.item_target_dir(item)
    holds.take(target, [item.key], core.run_lock.hold(
        {TARGET}, target_dir=target, command="pr batch run r", started="t"))
    return target


# ── ItemHolds ───────────────────────────────────────────────────────────────


def test_a_worktree_is_held_with_its_items_target_and_released_with_it(repo):
    item = _item(repo, worktree=repo)
    run = Run(id="r", started_at="t", steps=[Step.REVIEW], pool=1, auto_publish=[],
              items=[item])
    with batch.targets.ItemHolds(_checkout) as holds:
        _holds_with_target(holds, item)
        holds.hold_checkout(item.key, item.worktree)
        assert core.run_lock.holds(_tree_lock(repo))
        item.status = ItemStatus.DONE
        holds.release_finished(run)
        assert not core.run_lock.holds(_tree_lock(repo))


def test_no_checkout_is_held_for_an_item_whose_target_is_not(repo):
    with batch.targets.ItemHolds(_checkout) as holds:
        holds.hold_checkout("acme/widget#9", str(repo))
    assert not core.run_lock.holds(_tree_lock(repo))


def test_a_busy_worktree_raises_and_records_nothing(repo, held_elsewhere):
    item = _item(repo, worktree=repo)
    with batch.targets.ItemHolds(_checkout) as holds:
        _holds_with_target(holds, item)
        with pytest.raises(core.run_lock.LockBusy):
            holds.hold_checkout(item.key, item.worktree)
        assert not core.run_lock.holds(_tree_lock(repo))


# ── what a child is handed ──────────────────────────────────────────────────


def test_a_child_passes_through_the_worktree_the_batch_holds(repo):
    item = _item(repo, worktree=repo)
    with batch.targets.ItemHolds(_checkout) as holds:
        _holds_with_target(holds, item)
        holds.hold_checkout(item.key, item.worktree)
        env = batch.targets.child_env(item)
        assert env[TREE_LOCK_ENV] == str(batch.targets.checkout_git_dir(str(repo)))
        child = subprocess.run([sys.executable, "-c", _CLAIM_TREE, str(LIB_DIR), str(repo)],
                               env=env, capture_output=True, text=True, timeout=_PROC_TIMEOUT)
        stripped = {k: v for k, v in env.items() if k != TREE_LOCK_ENV}
        refused = subprocess.run([sys.executable, "-c", _CLAIM_TREE, str(LIB_DIR), str(repo)],
                                 env=stripped, capture_output=True, text=True,
                                 timeout=_PROC_TIMEOUT)
    assert child.returncode == 0, child.stderr
    assert refused.returncode == 1
    assert "pr batch run r" in refused.stderr


def test_a_child_gets_no_checkout_marker_for_a_worktree_the_batch_does_not_hold(repo):
    assert TREE_LOCK_ENV not in batch.targets.child_env(_item(repo, worktree=repo))


# ── the driver: resume and assignment ───────────────────────────────────────


def test_resume_is_refused_while_another_pr_command_holds_a_recorded_worktree(
        repo, held_elsewhere):
    item = _item(repo, worktree=repo)
    run = Run(id="r", started_at="t", steps=[Step.REVIEW], pool=1, auto_publish=[],
              items=[item])
    with contextlib.ExitStack() as held:
        with pytest.raises(cli.pr_batch._Refused, match="pr comments --fix"):
            cli.pr_batch._hold_items(held, run, "resume")


def test_resume_holds_a_recorded_worktree(repo):
    item = _item(repo, worktree=repo)
    run = Run(id="r", started_at="t", steps=[Step.REVIEW], pool=1, auto_publish=[],
              items=[item])
    with contextlib.ExitStack() as held:
        cli.pr_batch._hold_items(held, run, "resume")
        assert core.run_lock.holds(_tree_lock(repo))
    assert not core.run_lock.holds(_tree_lock(repo))


def test_a_new_worktree_another_command_holds_is_a_decision_not_a_step():
    h = Harness([row(1, ONLY_REVIEW)], hold_tree=lambda item: f"{item.worktree} is held by x")
    h.sched._once()
    item = h.run.items[0]
    assert h.spawned == []
    assert item.worktree == ""
    (d,) = h.run.open_decisions()
    assert (d.kind, d.step, d.payload["reason"]) == (DecisionKind.FAILED, "worktree", "lock_busy")
    assert d.payload["detail"] == "/wt/b1 is held by x"


def test_a_new_worktree_is_held_before_any_step_starts():
    seen = []
    h = Harness([row(1, ONLY_REVIEW)],
                hold_tree=lambda item: seen.append((item.worktree, len(h.spawned))) or "")
    h.sched._once()
    assert seen == [("/wt/b1", 0)]
    assert len(h.spawned) == 1


# ── the branch check ────────────────────────────────────────────────────────


def test_a_step_never_starts_on_a_worktree_switched_to_another_branch():
    h = Harness([row(1, ONLY_REVIEW)], branch=lambda wt: "feat/other")
    h.sched._once()
    assert h.spawned == []
    (d,) = h.run.open_decisions()
    assert (d.kind, d.step, d.payload["reason"]) == (DecisionKind.FAILED, "review",
                                                     "wrong_branch")
    assert d.payload["detail"] == "/wt/b1 is on feat/other, not b1"


def test_a_branch_git_cannot_name_is_not_the_items_branch():
    h = Harness([row(1, ONLY_REVIEW)], branch=lambda wt: "")
    h.sched._once()
    assert h.spawned == []
    assert "no branch git could name" in h.run.open_decisions()[0].payload["detail"]


def test_publish_refuses_a_worktree_on_another_branch():
    item = _item("/r", branch="b1", worktree="/wt")
    tree = TreeState(local="l", remote="h", checked_out="feat/other",
                     divergence=RefDivergence(ahead=1, behind=0, comparable=True))
    got = batch.publish.plan(item, "pr", tree)
    assert got.refusal is Refusal.WRONG_BRANCH
    assert got.detail == "/wt is on feat/other, not b1"


def test_the_branch_being_rebased_counts_as_checked_out(tmp_path):
    repo = seed_repo(tmp_path / "r")
    for key, value in (("user.email", "t@t"), ("user.name", "T")):
        git_in(repo, "config", key, value)
    git_in(repo, "checkout", "-q", "-b", "feat")
    (repo / "f.txt").write_text("feat\n")
    commit_all(repo, "feat")
    git_in(repo, "checkout", "-q", "-")
    (repo / "f.txt").write_text("main\n")
    commit_all(repo, "main")
    git_in(repo, "checkout", "-q", "feat")
    paused = subprocess.run(["git", "-C", str(repo), "rebase", "-q", "-"],
                            capture_output=True, timeout=_PROC_TIMEOUT)
    assert paused.returncode != 0
    assert rebase.inspect.rebase_in_progress(str(repo))
    assert batch.steps.checked_out_branch(str(repo)) == "feat"


def test_read_tree_records_the_branch_the_worktree_stands_on(repo):
    branch = batch.steps.checked_out_branch(str(repo))
    item = _item(repo, branch=branch, worktree=repo)
    assert batch.publish.read_tree(item).checked_out == branch


# ── clear refusals ──────────────────────────────────────────────────────────


def test_a_checkout_that_does_not_exist_is_a_plan_error(tmp_path):
    with pytest.raises(batch.plan.PlanError, match="does not exist"):
        batch.plan._repo_slug(str(tmp_path / "missing"))


def test_a_checkout_with_no_origin_is_a_plan_error(tmp_path):
    with pytest.raises(batch.plan.PlanError, match="no origin remote"):
        batch.plan._repo_slug(str(seed_repo(tmp_path / "no-origin")))


def test_a_vanished_checkout_cannot_take_a_worktree(tmp_path):
    res = batch.steps.ensure_worktree(str(tmp_path / "gone"), "feat")
    assert not res.ok
    assert "no longer exists" in res.error
