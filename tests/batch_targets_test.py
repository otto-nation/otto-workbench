"""Each batch child is handed only its own item's target lock."""

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import batch.resolve  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.targets  # noqa: E402
import core.run_lock  # noqa: E402
import pr.target  # noqa: E402
from batch.model import Decision, DecisionKind, Item, ItemStatus, Run, Step  # noqa: E402
from batch.plan import PlanRow  # noqa: E402
from batch.requests import Decide  # noqa: E402
# `_quiet_outcomes` is an autouse fixture; imported only so pytest registers it here.
from batch_scheduler_support import (  # noqa: E402,F401
    NEED, Harness, _close_open_sessions, _quiet_outcomes, attach_scheduler, ff_tree, row,
)
from config.workbench_config import BatchConfig  # noqa: E402
from conftest import seed_repo  # noqa: E402
from core.run_lock import GLOBAL_LOCK_ENV, LOCK_ENV, TARGET, TREE_LOCK_ENV  # noqa: E402
from run_lock_support import repo_with_origin  # noqa: E402

_PROC_TIMEOUT = 60

# A child that claims one target for its lifetime and exits: 0 when it got
# through, 1 (with the refusal on stderr) when another process holds it.
_CLAIM = textwrap.dedent("""\
    import pathlib, sys
    sys.path.insert(0, sys.argv[1])
    import core.run_lock
    core.run_lock.claim_for_process(pathlib.Path(sys.argv[2]), command="child", started="t")
""")


@pytest.fixture
def repo(tmp_path):
    return repo_with_origin(tmp_path / "repo")


def _item(repo_dir, branch, n):
    return Item(key=f"acme/widget#{n}", repo="acme/widget", repo_dir=str(repo_dir), pr=n,
                branch=branch, head_sha="h")


def _claim_in_child(target, env):
    return subprocess.run([sys.executable, "-c", _CLAIM, str(LIB_DIR), str(target)],
                          env=env, capture_output=True, text=True, timeout=_PROC_TIMEOUT)


def test_an_items_target_is_the_one_its_child_resolves(repo):
    expected = pr.target.target_dir(pr.target.repo_key_from_origin(str(repo)), "feat/a")
    assert batch.targets.item_target_dir(_item(repo, "feat/a", 1)) == expected


def test_an_item_with_no_origin_has_no_target(tmp_path):
    bare = seed_repo(tmp_path / "no-origin")
    assert batch.targets.item_target_dir(_item(bare, "feat/a", 1)) is None


def test_a_vanished_checkout_has_no_target():
    assert batch.targets.item_target_dir(_item("/r", "feat/a", 1)) is None


def test_item_repo_key_prefers_the_recorded_key():
    item = _item("/r", "feat/a", 1)
    item.repo_key = "recorded-key-aaaaaaaa"
    assert batch.targets.item_repo_key(item) == "recorded-key-aaaaaaaa"
    assert batch.targets.item_target_dir(item) == pr.target.target_dir(
        "recorded-key-aaaaaaaa", "feat/a")


def test_item_repo_key_reads_origin_when_unset(repo):
    assert batch.targets.item_repo_key(_item(repo, "feat/a", 1)) == (
        pr.target.repo_key_from_origin(str(repo)))


def test_item_repo_key_for_a_relative_repo_dir_uses_the_slug_not_cwd(tmp_path, monkeypatch):
    cwd_repo = repo_with_origin(tmp_path / "cwd")
    monkeypatch.chdir(cwd_repo)
    item = _item(".", "feat/a", 1)
    item.repo = "Other/Repo"
    item.key = "Other/Repo#1"
    got = batch.targets.item_repo_key(item)
    assert got == pr.target.repo_key_for_slug("Other/Repo")
    assert got != pr.target.repo_key_from_origin(str(cwd_repo))


def test_run_repo_keys_are_sorted_and_deduplicated(repo):
    key = pr.target.repo_key_from_origin(str(repo))
    a = _item(repo, "feat/a", 1)
    a.repo_key = key
    b = _item(repo, "feat/b", 2)
    b.repo_key = key
    c = _item("/missing", "feat/c", 3)
    run = Run(id="r", started_at="t", steps=[], pool=1, auto_publish=[], items=[a, b, c])
    assert batch.targets.run_repo_keys(run) == [key]


def test_child_env_names_only_the_items_target_when_this_process_holds_it(repo, monkeypatch):
    monkeypatch.setenv(LOCK_ENV, "/x:/y")
    monkeypatch.setenv(TREE_LOCK_ENV, "/tree")
    monkeypatch.setenv(GLOBAL_LOCK_ENV, "/global")
    item = _item(repo, "feat/a", 1)
    target = batch.targets.item_target_dir(item)
    with core.run_lock.hold({TARGET}, target_dir=target, command="pr batch run", started="t"):
        env = batch.targets.child_env(item)
    assert env[LOCK_ENV] == str(target)
    assert TREE_LOCK_ENV not in env
    assert GLOBAL_LOCK_ENV not in env


def test_child_env_strips_markers_when_this_process_does_not_hold_the_target(repo, monkeypatch):
    monkeypatch.setenv(LOCK_ENV, "/x:/y")
    monkeypatch.setenv(TREE_LOCK_ENV, "/tree")
    monkeypatch.setenv(GLOBAL_LOCK_ENV, "/global")
    env = batch.targets.child_env(_item(repo, "feat/a", 1))
    assert LOCK_ENV not in env
    assert TREE_LOCK_ENV not in env
    assert GLOBAL_LOCK_ENV not in env


def test_child_env_without_a_target_carries_no_marker(tmp_path, monkeypatch):
    monkeypatch.setenv(LOCK_ENV, "/x")
    env = batch.targets.child_env(_item(seed_repo(tmp_path / "no-origin"), "feat/a", 1))
    assert LOCK_ENV not in env


def test_a_child_gets_through_its_own_item_and_is_refused_on_another(repo):
    first, second = _item(repo, "feat/a", 1), _item(repo, "feat/b", 2)
    a = batch.targets.item_target_dir(first)
    b = batch.targets.item_target_dir(second)
    with core.run_lock.hold({TARGET}, target_dir=a, command="pr batch run R", started="t"), \
         core.run_lock.hold({TARGET}, target_dir=b, command="pr batch run R", started="t"):
        env = batch.targets.child_env(first)
        own = _claim_in_child(a, env)
        other = _claim_in_child(b, env)
    assert own.returncode == 0, own.stderr
    assert other.returncode == 1
    assert "pr batch run R" in other.stderr


def test_every_step_is_spawned_with_only_its_items_marker(monkeypatch):
    monkeypatch.setattr(batch.targets, "item_target_dir",
                        lambda item: Path(f"/targets/{item.pr}"))
    monkeypatch.setattr(core.run_lock, "holds", lambda lock_file: True)
    h = Harness([row(1), row(10)])
    seen = []
    real = h._spawn

    def spawn(argv, **kw):
        seen.append((argv[-1], kw["env"][LOCK_ENV]))
        return real(argv, **kw)

    h.sched._spawn = spawn
    h.sched.run_until_blocked()
    assert seen
    # argv[-1] is --repo-dir's worktree (`/wt/b{pr}` from the harness). Match
    # that exact path, not a last-character substring, so PR 10 is not "0".
    assert all(marker == f"/targets/{Path(worktree).name[1:]}" for worktree, marker in seen)


def test_a_resolve_command_runs_with_its_items_marker(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(batch.resolve, "default_runner",
                        lambda argv, log_path=None, *, env=None: calls.append(env) or 0)
    item = _item(repo, "feat/a", 1)
    target = batch.targets.item_target_dir(item)
    with core.run_lock.hold({TARGET}, target_dir=target, command="pr batch run", started="t"):
        run_cmd = batch.resolve.command_runner(item, None)
        assert run_cmd(["pr", "push"]) == 0
    assert calls[0][LOCK_ENV] == str(target)


def test_command_runner_without_a_runner_uses_child_env(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(batch.targets, "child_env",
                        lambda item: seen.append(item) or {"X": "1"})
    calls = []
    monkeypatch.setattr(batch.resolve, "default_runner",
                        lambda argv, log_path=None, *, env=None: calls.append(env) or 0)
    item = _item(repo, "feat/a", 1)
    batch.resolve.command_runner(item, None)(["pr", "push"])
    assert seen == [item]
    assert calls == [{"X": "1"}]


def test_a_live_run_publish_child_gets_only_its_items_marker(repo, monkeypatch):
    monkeypatch.setenv(LOCK_ENV, "/x:/y")
    monkeypatch.setenv(TREE_LOCK_ENV, "/tree")
    monkeypatch.setenv(GLOBAL_LOCK_ENV, "/global")
    calls = []
    monkeypatch.setattr(batch.resolve, "default_runner",
                        lambda argv, log_path=None, *, env=None: calls.append(env) or 0)
    planned = PlanRow("acme/widget", str(repo), 1, "t", "feat/a", "h", False,
                      {Step.REVIEW: NEED})
    run = batch.scheduler.new_run([planned], steps=[Step.REVIEW], selected=None, pool=1,
                                  auto_publish=[])
    item = run.items[0]
    item.worktree = str(repo)
    run.decisions.append(Decision(id="d1", item=item.key, step="publish",
                                  kind=DecisionKind.PUBLISH))
    target = batch.targets.item_target_dir(item)
    sched = attach_scheduler(
        run, pr_bin="pr", cfg=BatchConfig(pool_max=1),
        emit=lambda *a, **k: None, runner=None, tree=ff_tree)
    with core.run_lock.hold({TARGET}, target_dir=target, command="pr batch run", started="t"):
        sched.session.apply(Decide("d1", "publish"))
    assert calls
    env = calls[0]
    assert env[LOCK_ENV] == str(target)
    assert TREE_LOCK_ENV not in env
    assert GLOBAL_LOCK_ENV not in env


def _holding(holds, *keys_by_target):
    for target, keys in keys_by_target:
        holds.take(target, keys, core.run_lock.hold(
            {TARGET}, target_dir=target, command="pr batch run r", started="t"))


def test_item_holds_release_only_targets_whose_items_are_all_terminal(repo):
    a, b, c = (_item(repo, "feat/a", 1), _item(repo, "feat/b", 2), _item(repo, "feat/b", 3))
    run = Run(id="r", started_at="t", steps=[Step.REVIEW], pool=1, auto_publish=[],
              items=[a, b, c])
    ta, tb = batch.targets.item_target_dir(a), batch.targets.item_target_dir(b)
    with batch.targets.ItemHolds() as holds:
        _holding(holds, (ta, [a.key]), (tb, [b.key, c.key]))
        a.status = ItemStatus.DROPPED
        b.status = ItemStatus.DONE
        assert holds.release_finished(run) == [ta]
        assert not core.run_lock.is_held(ta)
        assert not core.run_lock.holds(ta / core.run_lock.LOCK_FILE)
        assert str(ta) not in core.run_lock._marker_values(LOCK_ENV)
        # feat/b is shared, and #3 is still live.
        assert core.run_lock.is_held(tb)
        assert holds.held() == [tb]
        c.status = ItemStatus.SKIPPED_CLOSED
        assert holds.release_finished(run) == [tb]
        assert holds.held() == []
    assert not core.run_lock.is_held(tb)
    assert core.run_lock.read_holder(ta / core.run_lock.LOCK_FILE)["released"] is not None


def test_item_holds_release_every_target_left_when_the_block_ends(repo):
    a = _item(repo, "feat/a", 1)
    run = Run(id="r", started_at="t", steps=[Step.REVIEW], pool=1, auto_publish=[], items=[a])
    ta = batch.targets.item_target_dir(a)
    with batch.targets.ItemHolds() as holds:
        _holding(holds, (ta, [a.key]))
        assert holds.release_finished(run) == []
        assert core.run_lock.is_held(ta)
    assert not core.run_lock.is_held(ta)


def test_item_holds_keep_a_terminal_items_target_while_its_step_is_live(repo):
    """A released item whose killed step has not exited yet keeps its lock."""
    a = _item(repo, "feat/a", 1)
    run = Run(id="r", started_at="t", steps=[Step.REVIEW], pool=1, auto_publish=[], items=[a])
    ta = batch.targets.item_target_dir(a)
    with batch.targets.ItemHolds() as holds:
        _holding(holds, (ta, [a.key]))
        a.status = ItemStatus.DROPPED
        assert holds.release_finished(run, live={a.key}) == []
        assert core.run_lock.is_held(ta)
        assert holds.release_finished(run, live=set()) == [ta]
