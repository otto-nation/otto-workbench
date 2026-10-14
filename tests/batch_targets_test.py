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
import batch.targets  # noqa: E402
import core.run_lock  # noqa: E402
import pr.target  # noqa: E402
from batch.model import Item  # noqa: E402
from batch_scheduler_support import Harness, row, _quiet_outcomes  # noqa: E402,F401
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


def test_child_env_names_only_the_items_target(repo, monkeypatch):
    monkeypatch.setenv(LOCK_ENV, "/x:/y")
    monkeypatch.setenv(TREE_LOCK_ENV, "/tree")
    monkeypatch.setenv(GLOBAL_LOCK_ENV, "/global")
    item = _item(repo, "feat/a", 1)
    env = batch.targets.child_env(item)
    assert env[LOCK_ENV] == str(batch.targets.item_target_dir(item))
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
    run_cmd = batch.resolve.command_runner(item, None, batch.targets.child_env)
    assert run_cmd(["pr", "push"]) == 0
    assert calls[0][LOCK_ENV] == str(batch.targets.item_target_dir(item))


def test_a_resolve_with_no_env_builder_keeps_this_processes_environment(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(batch.resolve, "default_runner",
                        lambda argv, log_path=None, *, env=None: calls.append(env) or 0)
    batch.resolve.command_runner(_item(repo, "feat/a", 1), None, None)(["pr", "push"])
    assert calls == [None]
