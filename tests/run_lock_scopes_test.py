"""Tests for the run lock's scopes: a global lock, and the one order every holder takes them in."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import core.run_lock  # noqa: E402
import core.workbench_paths  # noqa: E402
from core.run_lock import CHECKOUT, NO_LOCK, TARGET, Global, LockBusy  # noqa: E402
from run_lock_support import flocked, git_dir, held_elsewhere  # noqa: E402

BATCH = Global("batch")


def test_a_global_lock_lives_under_the_state_root():
    assert core.run_lock.global_lock_path("batch") == (
        core.workbench_paths.state_dir() / "locks" / "batch.lock")


@pytest.mark.parametrize("name", ["", "a/b", ".hidden"])
def test_a_global_lock_is_named_by_one_plain_component(name):
    with pytest.raises(ValueError):
        Global(name)


def test_scopes_are_ordered_globals_then_target_then_checkout():
    """One order for every holder is what rules out two `pr` processes deadlocking."""
    assert core.run_lock.ordered({CHECKOUT, TARGET, Global("z"), Global("a")}) == [
        Global("a"), Global("z"), TARGET, CHECKOUT]


def test_hold_takes_every_scope_in_that_order(worktree, tmp_path, monkeypatch):
    taken = []
    real = core.run_lock._take

    def spy(spec, command, started):
        taken.append((spec.var, spec.path.name))
        return real(spec, command, started)

    monkeypatch.setattr(core.run_lock, "_take", spy)
    with core.run_lock.hold({CHECKOUT, Global("b"), TARGET, Global("a")},
                            command="pr x", started="t",
                            target_dir=tmp_path / "t", worktree=worktree):
        pass
    assert taken == [
        (core.run_lock.GLOBAL_LOCK_ENV, "a.lock"),
        (core.run_lock.GLOBAL_LOCK_ENV, "b.lock"),
        (core.run_lock.LOCK_ENV, core.run_lock.LOCK_FILE),
        (core.run_lock.TREE_LOCK_ENV, core.run_lock.TREE_LOCK_FILE),
    ]


def test_no_lock_takes_nothing(tmp_path):
    with core.run_lock.hold(NO_LOCK, command="pr status", started="t",
                            target_dir=tmp_path / "t"):
        pass
    assert not (tmp_path / "t").exists()
    assert not core.run_lock.global_lock_path("batch").parent.exists()


def test_a_target_lock_needs_its_target_dir():
    """Refused before anything is taken, so the global beside it is never held."""
    with pytest.raises(ValueError):
        with core.run_lock.hold({BATCH, TARGET}, command="pr x", started="t"):
            pytest.fail("a TARGET with no target_dir must not enter the block")
    assert not core.run_lock.global_lock_path("batch").exists()


def test_the_global_lock_is_held_for_the_block_and_released_after():
    with core.run_lock.hold({BATCH}, command="pr batch run", started="t"):
        assert core.run_lock.is_global_held(BATCH)
    assert not core.run_lock.is_global_held(BATCH)


def test_a_refused_global_says_which_and_names_the_holder():
    record = {"pid": 4242, "command": "pr batch run --checkout /r", "started": "t0"}
    with held_elsewhere(core.run_lock.global_lock_path("batch"), record), \
         pytest.raises(LockBusy) as exc:
        with core.run_lock.hold({BATCH}, command="pr batch run", started="t"):
            pytest.fail("a held global lock must refuse the block")
    assert exc.value.kind == "global"
    assert ("owns this global batch: pr batch run --checkout /r (pid 4242, started t0)"
            in str(exc.value))


def test_a_refusal_later_in_the_order_releases_what_was_taken(tmp_path):
    target = tmp_path / "t"
    with held_elsewhere(target / core.run_lock.LOCK_FILE, {"pid": 1}), \
         pytest.raises(LockBusy):
        with core.run_lock.hold({BATCH, TARGET}, command="pr x", started="t",
                                target_dir=target):
            pytest.fail("a held target must refuse the block")
    assert not core.run_lock.is_global_held(BATCH)


def test_a_noted_run_reaches_a_contenders_message():
    with core.run_lock.hold({BATCH}, command="pr batch run", started="t"):
        core.run_lock.note_holder(BATCH, run="20261013T000000-abcd")
        record = json.loads(core.run_lock.global_lock_path("batch").read_text())
    assert record["run"] == "20261013T000000-abcd"
    assert "run 20261013T000000-abcd" in str(LockBusy(record, Path("x"), "global batch"))


def test_noting_a_lock_this_process_does_not_hold_changes_nothing():
    core.run_lock.note_holder(BATCH, run="x")
    assert not core.run_lock.global_lock_path("batch").exists()


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
    with held_elsewhere(target / core.run_lock.LOCK_FILE, {"pid": 7, "command": "pr fix"}), \
         pytest.raises(LockBusy) as exc:
        with core.run_lock.hold({TARGET}, target_dir=target, target_label="acme/widget@feat/x",
                                command="pr rebase", started="t"):
            pytest.fail("a held target must refuse the block")
    assert exc.value.kind == "target"
    assert "owns this target acme/widget@feat/x: pr fix" in str(exc.value)


def test_a_refused_checkout_names_the_tree(worktree, tmp_path):
    tree_lock = git_dir(worktree) / core.run_lock.TREE_LOCK_FILE
    with held_elsewhere(tree_lock, {"pid": 7, "command": "pr rebase"}), \
         pytest.raises(LockBusy) as exc:
        with core.run_lock.hold({TARGET, CHECKOUT}, target_dir=tmp_path / "t",
                                worktree=worktree, command="pr comments", started="t"):
            pytest.fail("a held checkout must refuse the block")
    assert exc.value.kind == "checkout"
    assert f"owns this checkout {worktree}: pr rebase" in str(exc.value)
