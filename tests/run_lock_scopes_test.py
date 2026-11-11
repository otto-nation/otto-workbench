"""Tests for the run lock's scopes and the one order every holder takes them in."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import core.run_lock  # noqa: E402
from core.run_lock import CHECKOUT, NO_LOCK, TARGET, LockBusy  # noqa: E402
from run_lock_support import flocked, git_dir, held_elsewhere, live_pid  # noqa: E402


def test_scopes_are_ordered_target_then_checkout():
    """One order for every holder is what rules out two `pr` processes deadlocking."""
    assert core.run_lock.ordered({CHECKOUT, TARGET}) == [TARGET, CHECKOUT]


def test_hold_takes_every_scope_in_that_order(worktree, tmp_path, monkeypatch):
    taken = []
    real = core.run_lock._take

    def spy(spec, command, started):
        taken.append((spec.var, spec.path.name))
        return real(spec, command, started)

    monkeypatch.setattr(core.run_lock, "_take", spy)
    with core.run_lock.hold({CHECKOUT, TARGET}, command="pr x", started="t",
                            target_dir=tmp_path / "t", worktree=worktree):
        pass
    assert taken == [
        (core.run_lock.LOCK_ENV, core.run_lock.LOCK_FILE),
        (core.run_lock.TREE_LOCK_ENV, core.run_lock.TREE_LOCK_FILE),
    ]


def test_no_lock_takes_nothing(tmp_path):
    with core.run_lock.hold(NO_LOCK, command="pr status", started="t",
                            target_dir=tmp_path / "t"):
        pass
    assert not (tmp_path / "t").exists()


def test_a_target_lock_needs_its_target_dir():
    with pytest.raises(ValueError):
        with core.run_lock.hold({TARGET}, command="pr x", started="t"):
            pytest.fail("a TARGET with no target_dir must not enter the block")


def test_a_refusal_later_in_the_order_releases_what_was_taken(worktree, tmp_path):
    target = tmp_path / "t"
    with live_pid() as pid, \
         held_elsewhere(git_dir(worktree) / core.run_lock.TREE_LOCK_FILE, {"pid": pid}), \
         pytest.raises(LockBusy):
        with core.run_lock.hold({TARGET, CHECKOUT}, command="pr x", started="t",
                                target_dir=target, worktree=worktree):
            pytest.fail("a held checkout must refuse the block")
    assert not core.run_lock.is_held(target)


# ── the late checkout lock ────────────────────────────────────────────────────


def test_lock_checkout_refuses_a_checkout_before_its_target(worktree, tmp_path):
    """Checkout after target is the order every holder keeps; this keeps the late one to it."""
    with pytest.raises(core.run_lock.LockOrderError):
        core.run_lock.lock_checkout(tmp_path / "t", worktree, command="review 1", started="t")
    assert not (git_dir(worktree) / core.run_lock.TREE_LOCK_FILE).exists()


def test_lock_checkout_extends_a_target_this_process_holds(worktree, tmp_path):
    target = tmp_path / "t"
    tree_lock = git_dir(worktree) / core.run_lock.TREE_LOCK_FILE
    with core.run_lock.hold({TARGET}, target_dir=target, command="pr review 1", started="t"):
        core.run_lock.lock_checkout(target, worktree, command="review 1", started="t")
        assert flocked(tree_lock)
    # Held for the process, not the block: a review keeps writing after dispatch's block.
    assert flocked(tree_lock)


def test_lock_checkout_accepts_a_target_its_parent_holds(worktree, tmp_path, monkeypatch):
    """A batch child or a `pr`-spawned delegate knows its target only from the marker."""
    target = tmp_path / "t"
    monkeypatch.setenv(core.run_lock.LOCK_ENV, str(target))
    core.run_lock.lock_checkout(target, worktree, command="review 1", started="t")
    record = json.loads((git_dir(worktree) / core.run_lock.TREE_LOCK_FILE).read_text())
    assert record["command"] == "review 1"


# ── what a refusal calls the scope ────────────────────────────────────────────


def test_a_refused_target_names_the_repo_and_branch(tmp_path):
    target = tmp_path / "t"
    with live_pid() as pid, \
         held_elsewhere(target / core.run_lock.LOCK_FILE,
                        {"pid": pid, "command": "pr fix"}), \
         pytest.raises(LockBusy) as exc:
        with core.run_lock.hold({TARGET}, target_dir=target, target_label="acme/widget@feat/x",
                                command="pr rebase", started="t"):
            pytest.fail("a held target must refuse the block")
    assert exc.value.kind == "target"
    assert "owns this target acme/widget@feat/x: pr fix" in str(exc.value)


def test_a_refused_checkout_names_the_tree(worktree, tmp_path):
    tree_lock = git_dir(worktree) / core.run_lock.TREE_LOCK_FILE
    with live_pid() as pid, \
         held_elsewhere(tree_lock, {"pid": pid, "command": "pr rebase"}), \
         pytest.raises(LockBusy) as exc:
        with core.run_lock.hold({TARGET, CHECKOUT}, target_dir=tmp_path / "t",
                                worktree=worktree, command="pr comments", started="t"):
            pytest.fail("a held checkout must refuse the block")
    assert exc.value.kind == "checkout"
    assert f"owns this checkout {worktree}: pr rebase" in str(exc.value)
