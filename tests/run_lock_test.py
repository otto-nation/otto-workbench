"""Tests for the run lock."""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest
from conftest import init_worktree

from core import run_lock
from core.run_lock import (
    LOCK_ENV, LOCK_FILE, TREE_LOCK_ENV, TREE_LOCK_FILE, LockBusy, acquire,
)


@pytest.fixture(autouse=True)
def _clear_lock_env():
    """Never inherit a real run's lock marker into a test.

    claim_for_process holds its handle until the process exits, which for a
    test process means the rest of the session — so drop those here too.
    """
    saved = os.environ.pop(LOCK_ENV, None)
    saved_tree = os.environ.pop(TREE_LOCK_ENV, None)
    yield
    for handle in run_lock._HELD:
        handle.close()
    run_lock._HELD.clear()
    os.environ.pop(LOCK_ENV, None)
    os.environ.pop(TREE_LOCK_ENV, None)
    if saved is not None:
        os.environ[LOCK_ENV] = saved
    if saved_tree is not None:
        os.environ[TREE_LOCK_ENV] = saved_tree


def _contend(root, command="pr review --fix"):
    """Enter the lock the way a fresh, unrelated run would.

    Clearing LOCK_ENV drops the same-process-tree pass-through, so the flock
    itself decides — it conflicts across open file descriptions, which makes
    real contention reproducible without a second process.
    """
    os.environ.pop(LOCK_ENV, None)
    with acquire(root, command=command, started="t"):
        pytest.fail("second acquire should not have entered the block")


def test_acquire_creates_the_target_dir(tmp_path):
    """The directory is ours to create — nothing conjures a caller's worktree."""
    target = tmp_path / "pr" / "widget-feat-a"
    with acquire(target, command="pr review", started="t"):
        assert (target / LOCK_FILE).is_file()


def test_two_targets_lock_independently(tmp_path):
    """Two reviews of different PRs must not exclude each other."""
    first = tmp_path / "pr" / "widget-feat-a"
    second = tmp_path / "pr" / "widget-feat-b"
    with acquire(first, command="pr review 1", started="t"):
        os.environ.pop(LOCK_ENV, None)
        with acquire(second, command="pr review 2", started="t"):
            assert (first / LOCK_FILE).is_file()
            assert (second / LOCK_FILE).is_file()


def test_acquire_creates_lock_file_and_marks_env(worktree):
    with acquire(worktree, command="pr review", started="2026-08-12T00:00:00+00:00"):
        assert os.environ[LOCK_ENV] == str(worktree)
        record = json.loads((worktree / LOCK_FILE).read_text())
        assert record["pid"] == os.getpid()
        assert record["command"] == "pr review"
        assert record["started"] == "2026-08-12T00:00:00+00:00"


def test_env_marker_cleared_after_release(worktree):
    with acquire(worktree, command="pr review", started="t"):
        pass
    assert LOCK_ENV not in os.environ


def test_lock_is_released_for_the_next_run(worktree):
    with acquire(worktree, command="pr review", started="t"):
        pass
    # A sequential second run must not see the first as a live holder.
    with acquire(worktree, command="pr comments", started="t"):
        record = json.loads((worktree / LOCK_FILE).read_text())
        assert record["command"] == "pr comments"


def test_concurrent_acquire_raises_lock_busy(worktree):
    """flock conflicts across open file descriptions, so a second acquire
    fails even from this same process once the env marker is cleared."""
    with acquire(worktree, command="pr review --fix", started="2026-08-12T07:21:19+00:00"):
        with pytest.raises(LockBusy) as excinfo:
            _contend(worktree)

    exc = excinfo.value
    assert exc.holder["pid"] == os.getpid()
    assert exc.holder["command"] == "pr review --fix"
    assert "pr review --fix" in str(exc)
    assert "2026-08-12T07:21:19+00:00" in str(exc)


def test_reentrant_acquire_in_same_process_tree(worktree):
    """Delegates inherit LOCK_ENV and must pass straight through."""
    with acquire(worktree, command="pr review", started="t"):
        with acquire(worktree, command="claude-review", started="t"):
            # The child must not overwrite the parent's ownership record.
            record = json.loads((worktree / LOCK_FILE).read_text())
            assert record["command"] == "pr review"
        # Leaving the inner block must not release the parent's lock.
        assert os.environ[LOCK_ENV] == str(worktree)


def test_reentrancy_is_keyed_on_the_target(worktree):
    """A different target is a different lock, not a free pass."""
    other = worktree / "other"
    other.mkdir()
    with acquire(worktree, command="pr review", started="t"):
        with acquire(other, command="pr review", started="t"):
            assert (other / LOCK_FILE).exists()
        # Releasing the inner lock must hand the marker back to the outer one,
        # not leave it pointing at a target we no longer hold.
        assert os.environ[LOCK_ENV] == str(worktree)


def test_lock_released_when_body_raises(worktree):
    with pytest.raises(RuntimeError):
        with acquire(worktree, command="pr review", started="t"):
            raise RuntimeError("boom")
    assert LOCK_ENV not in os.environ
    with acquire(worktree, command="pr review", started="t"):
        pass


def test_report_busy_names_the_holder(tmp_path, capsys):
    exc = LockBusy({"pid": 4242, "command": "pr review", "started": "t"}, tmp_path)
    run_lock.report_busy(exc)
    err = capsys.readouterr().err
    assert "pr review" in err
    assert "4242" in err


def test_lock_busy_tolerates_an_unreadable_holder_record(worktree):
    """The flock enforces exclusion; a garbled record must still report."""
    lock_path = worktree / LOCK_FILE
    lock_path.write_text("not json")
    with acquire(worktree, command="pr review", started="t"):
        lock_path.write_text("not json")
        with pytest.raises(LockBusy) as excinfo:
            _contend(worktree)
    assert "unknown command" in str(excinfo.value)


# ── claim_for_process ────────────────────────────────────────────────────────


def test_claim_for_process_holds_without_a_context_manager(worktree):
    """Delegates lock for their whole run; the kernel releases it at exit."""
    run_lock.claim_for_process(worktree, command="ci-check --fix", started="t")
    assert os.environ[LOCK_ENV] == str(worktree)
    record = json.loads((worktree / LOCK_FILE).read_text())
    assert record["command"] == "ci-check --fix"
    with pytest.raises(LockBusy):
        _contend(worktree)


def test_claim_for_process_passes_through_when_pr_already_holds_it(worktree):
    """Launched by pr, a delegate inherits LOCK_ENV and must not deadlock."""
    with acquire(worktree, command="pr review --fix", started="t"):
        run_lock.claim_for_process(worktree, command="claude-review", started="t")
        # The parent's ownership record has to survive the delegate.
        record = json.loads((worktree / LOCK_FILE).read_text())
        assert record["command"] == "pr review --fix"


def test_a_rewriting_delegate_is_guarded_on_its_own(worktree):
    """pr-rebase rewrites history and force-pushes, so a direct run must lock.

    It was reachable unguarded: only `pr rebase` took a lock, and invoking the
    backing script itself serialized against nothing.
    """
    run_lock.claim_for_process(
        worktree, command="pr-rebase --fix", started="t",
    )
    with pytest.raises(LockBusy):
        _contend(worktree)


def test_a_delegate_passes_through_the_lock_its_parent_holds(worktree):
    """`pr rebase` locks, then launches pr-rebase, which must not deadlock.

    The two agree because the parent forwards a target flag and the child
    resolves the same key. This pins the arrangement from the lock's side; the
    forwarding itself is pinned in pr_cli_test.
    """
    with acquire(worktree, command="pr rebase --fix", started="t"):
        run_lock.claim_for_process(
            worktree, command="pr-rebase --fix", started="t",
        )
        record = json.loads((worktree / LOCK_FILE).read_text())
        assert record["command"] == "pr rebase --fix"


def test_claim_for_process_exits_when_another_run_owns_the_target(worktree, capsys):
    with acquire(worktree, command="pr review --fix", started="t"):
        os.environ.pop(LOCK_ENV, None)
        with pytest.raises(SystemExit) as excinfo:
            run_lock.claim_for_process(worktree, command="ci-check", started="t")
    assert excinfo.value.code == 1
    assert "pr review --fix" in capsys.readouterr().err


# ── The checkout lock ────────────────────────────────────────────────────────
#
# The target key is (origin repo key, branch), which says nothing about where a
# run writes. These pin the second lock: what it stops, and — just as load
# bearing — the concurrency it must leave alone.


def _git_dir(worktree):
    """Where the checkout's lock lives, per git rather than per convention."""
    return run_lock._git_dir(Path(worktree))


def _enter(target, command, worktree):
    """Enter a lock that is expected to be refused, and fail loudly if it is not.

    A helper rather than a nested `with`, so the caller reads as one statement
    and the "this should not have been reached" case still fails the test.
    """
    with acquire(target, command=command, started="t", worktree=worktree):
        pytest.fail(f"{command!r} should not have entered the block")


def test_two_targets_in_one_checkout_now_contend(worktree, tmp_path):
    """The hole this closes: different branches, one working tree.

    A `--pr` run keys on the branch GitHub reports while the worktree stands on
    another, so the two took different target locks and both edited one tree.
    """
    first, second = tmp_path / "t-one", tmp_path / "t-two"

    with acquire(first, command="pr review 1", started="t", worktree=worktree):
        os.environ.pop(LOCK_ENV, None)
        os.environ.pop(TREE_LOCK_ENV, None)
        with pytest.raises(LockBusy):
            _enter(second, "pr rebase 2", worktree)


def test_one_target_from_two_checkouts_still_takes_one_lock(worktree, tmp_path):
    """The property the target lock exists for, unchanged.

    `pr review 2973` from a repo root and `pr review --self` from inside the
    PR's worktree are the same work and must still exclude each other.
    """
    target = tmp_path / "one-target"

    with acquire(target, command="pr review 2973", started="t",
                 worktree=worktree):
        os.environ.pop(LOCK_ENV, None)
        os.environ.pop(TREE_LOCK_ENV, None)
        with pytest.raises(LockBusy):
            _enter(target, "pr review --self", None)


def test_two_checkouts_do_not_contend(worktree, tmp_path):
    """Two checkouts are two trees, and must still run concurrently.

    The lock is keyed on --absolute-git-dir, which is per linked worktree
    rather than per repo, so working on two branches at once stays possible.
    """
    second = init_worktree(tmp_path / "second-checkout")

    with acquire(tmp_path / "t-one", command="pr rebase 1", started="t",
                 worktree=worktree):
        os.environ.pop(LOCK_ENV, None)
        os.environ.pop(TREE_LOCK_ENV, None)
        # A different tree: this must simply enter the block.
        with acquire(tmp_path / "t-two", command="pr rebase 2", started="t",
                     worktree=second):
            assert (run_lock._git_dir(second) / TREE_LOCK_FILE).exists()


def test_the_checkout_lock_lives_in_the_git_dir(worktree, tmp_path):
    """Not in the worktree, and not beside the target's state."""
    with acquire(tmp_path / "t", command="pr rebase", started="t",
                 worktree=worktree):
        assert (_git_dir(worktree) / TREE_LOCK_FILE).exists()
        assert not (worktree / TREE_LOCK_FILE).exists()
        assert not (tmp_path / "t" / TREE_LOCK_FILE).exists()


def test_a_run_that_names_no_checkout_locks_only_its_target(worktree, tmp_path):
    """pr gc prunes a target whose checkout may be long gone."""
    target = tmp_path / "t"
    with acquire(target, command="pr gc", started="t"):
        assert (target / LOCK_FILE).exists()
        assert not (_git_dir(worktree) / TREE_LOCK_FILE).exists()


def test_a_path_git_cannot_answer_for_degrades_to_the_target_lock(tmp_path):
    """No repo, no checkout lock — and no crash.

    lock_path is None for more than "not a repo": dubious ownership and a moved
    bare repo answer the same way, and none of them should stop a run.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with acquire(tmp_path / "t", command="pr review", started="t",
                 worktree=plain):
        assert not (plain / TREE_LOCK_FILE).exists()


def test_the_checkout_lock_passes_through_to_a_child(worktree, tmp_path):
    """A delegate re-resolving the same checkout must not deadlock on it."""
    target = tmp_path / "t"
    with acquire(target, command="pr rebase --fix", started="t",
                 worktree=worktree):
        # The child clears neither marker: it inherits both through the env.
        run_lock.claim_for_process(
            target, command="pr-rebase --fix", started="t", worktree=worktree,
        )
        record = json.loads((_git_dir(worktree) / TREE_LOCK_FILE).read_text())
        assert record["command"] == "pr rebase --fix"


def test_the_checkout_marker_is_restored_after_release(worktree, tmp_path):
    with acquire(tmp_path / "t", command="pr rebase", started="t",
                 worktree=worktree):
        assert os.environ[TREE_LOCK_ENV] == str(_git_dir(worktree))
    assert TREE_LOCK_ENV not in os.environ


def test_the_checkout_lock_is_released_when_the_body_raises(worktree, tmp_path):
    with pytest.raises(RuntimeError):
        with acquire(tmp_path / "t", command="pr rebase", started="t",
                     worktree=worktree):
            raise RuntimeError("boom")

    os.environ.pop(LOCK_ENV, None)
    os.environ.pop(TREE_LOCK_ENV, None)
    # Both locks free: a crashed run must not wedge the tree.
    with acquire(tmp_path / "t", command="pr rebase", started="t",
                 worktree=worktree):
        pass


def test_a_contended_checkout_says_so_and_suggests_another_worktree(
    worktree, tmp_path, capsys,
):
    """The two locks fail for different reasons, so they must not read alike.

    "another pr run already owns this target" is wrong for a checkout clash:
    the targets differ, and waiting is not the only remedy.
    """
    with acquire(tmp_path / "t-one", command="pr rebase 1", started="t",
                 worktree=worktree):
        os.environ.pop(LOCK_ENV, None)
        os.environ.pop(TREE_LOCK_ENV, None)
        with pytest.raises(SystemExit) as excinfo:
            run_lock.claim_for_process(
                tmp_path / "t-two", command="pr review 2", started="t",
                worktree=worktree,
            )

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "owns this checkout" in err
    assert "worktree" in err


# ── Telling a released record from a held one ────────────────────────────────
#
# The file is never deleted, so almost every run.lock on a machine names a dead
# pid. These pin the difference between that and a lock someone holds.


def test_a_held_record_says_it_has_not_been_released(worktree):
    with acquire(worktree, command="pr review --fix", started="t"):
        record = json.loads((worktree / LOCK_FILE).read_text())
        assert record["released"] is None


def test_a_released_record_is_stamped_with_when(worktree):
    with acquire(worktree, command="pr review --fix", started="t"):
        pass
    record = json.loads((worktree / LOCK_FILE).read_text())
    assert record["released"]
    # Still names the command: the record's whole job is the next error message.
    assert record["command"] == "pr review --fix"


def test_the_release_stamp_does_not_overwrite_the_next_holder(worktree):
    """Written under the flock, so it cannot land on a successor's record."""
    with acquire(worktree, command="first run", started="t"):
        pass
    os.environ.pop(LOCK_ENV, None)
    with acquire(worktree, command="second run", started="t"):
        record = json.loads((worktree / LOCK_FILE).read_text())
        assert record["command"] == "second run"
        assert record["released"] is None


def test_is_held_is_true_only_while_someone_holds_it(worktree):
    """Both directions: a guard that answers "free" to everything is useless."""
    assert not run_lock.is_held(worktree)
    with acquire(worktree, command="pr review --fix", started="t"):
        assert run_lock.is_held(worktree)
    assert not run_lock.is_held(worktree)


def test_is_held_is_false_for_a_target_never_run(tmp_path):
    assert not run_lock.is_held(tmp_path / "never-used")


def test_is_held_ignores_our_own_env_marker(worktree):
    """It asks the kernel, not our ancestry — a stray marker must not lie."""
    os.environ[LOCK_ENV] = str(worktree)
    assert not run_lock.is_held(worktree)
