"""Tests for batch.machine: the registry every batch driver on a host admits against.

Several drivers in one process stand in for several processes: `flock` is per
open file description, so two `register` calls exclude each other exactly as
two drivers would.
"""

import contextlib
import errno
import fcntl
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.machine  # noqa: E402
from batch.machine import LiveStep, Machine, PerRun  # noqa: E402

GiB = 1024 ** 3


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def drivers(tmp_path):
    """open(run_id) registers a driver under one shared root; all close at teardown."""
    clock = Clock()
    with contextlib.ExitStack() as stack:
        def open_(run_id):
            return stack.enter_context(batch.machine.register(run_id, tmp_path, clock))
        open_.clock = clock
        open_.root = tmp_path
        yield open_


def _step(item="o/r#1", estimate=2 * GiB, peak=0):
    return LiveStep(item, "review", "o/r", estimate, peak)


# ── what peers see ──────────────────────────────────────────────────────────


def test_a_driver_sees_every_other_drivers_pending_memory_and_steps(drivers):
    a, b, c = drivers("a"), drivers("b"), drivers("c")
    b.settle([_step(estimate=2 * GiB, peak=GiB // 2)], waiting=False)
    c.settle([_step("o/r#2", estimate=GiB), _step("o/r#3", estimate=GiB, peak=2 * GiB)],
             waiting=False)
    peers = a.sync([])
    assert peers == batch.machine.Peers(pending=int(1.5 * GiB) + GiB, live=3)


def test_a_driver_does_not_count_itself(drivers):
    a = drivers("a")
    a.settle([_step()], waiting=False)
    assert a.sync([_step()]) == batch.machine.Peers()


def test_a_dead_driver_is_ignored_and_its_entry_removed(drivers):
    """A driver killed with SIGKILL keeps no lock; what it wrote must not hold memory."""
    a = drivers("a")
    dead = drivers.root / "dead.json"
    dead.write_text(json.dumps({"run": "dead", "pid": 1, "updated_at": 1000.0,
                                "live": [vars(_step())]}))
    (drivers.root / "dead.lock").touch()
    assert a.sync([]) == batch.machine.Peers()
    assert not dead.exists()


def test_closing_leaves_the_registry(drivers, tmp_path):
    with batch.machine.register("gone", tmp_path, Clock()) as gone:
        gone.settle([_step()], waiting=False)
    assert not (tmp_path / "gone.json").exists()
    assert not (tmp_path / "gone.lock").exists()
    assert drivers("a").sync([]) == batch.machine.Peers()


# ── the floor slot ──────────────────────────────────────────────────────────


def test_only_the_longest_waiting_driver_may_take_the_floor(drivers):
    a, b = drivers("a"), drivers("b")
    assert a.take_floor()
    drivers.clock.now += 1
    b.sync([])
    assert not b.take_floor()


def test_the_floor_waits_for_the_head_of_the_queue_even_when_free(drivers):
    """A free slot is not enough: whoever asks first after a release would win it
    every time, which is the starvation the queue exists to stop."""
    a, b = drivers("a"), drivers("b")
    a.sync([])
    assert a.take_floor()
    a.sync([_step()])
    drivers.clock.now += 1
    b.sync([])
    assert not b.take_floor()
    a.sync([])
    a.settle([], waiting=True)
    drivers.clock.now += 1
    c = drivers("c")
    c.sync([])
    # b has waited since before a gave the slot back; c arrived after.
    assert not c.take_floor()
    b.sync([])
    assert b.take_floor()


def test_a_driver_that_gave_the_floor_back_rejoins_at_the_back(drivers):
    a, b = drivers("a"), drivers("b")
    assert a.take_floor()
    a.settle([_step()], waiting=False)
    drivers.clock.now += 1
    b.sync([])
    assert not b.take_floor()
    a.sync([])
    drivers.clock.now += 1
    assert not a.take_floor()
    b.sync([])
    assert b.take_floor()


def test_a_driver_blocked_in_a_publish_does_not_hold_up_the_queue(drivers):
    """b queued ahead of a, then stopped rewriting its entry — an inline publish
    holds its tick — while keeping its lock. a must not wait on it for ever."""
    holder, b, a = drivers("holder"), drivers("b"), drivers("a")
    assert holder.take_floor()
    holder.settle([_step()], waiting=False)
    drivers.clock.now += 1
    b.sync([])
    assert not b.take_floor()
    drivers.clock.now += 1
    a.sync([])
    assert not a.take_floor()
    holder.sync([])
    holder.settle([], waiting=False)
    a.sync([])
    assert not a.take_floor()  # b is ahead and still fresh
    drivers.clock.now += batch.machine.FRESH_SECONDS
    a.sync([])
    assert a.take_floor()


def test_a_fresh_earlier_waiter_keeps_its_place(drivers):
    a, b = drivers("a"), drivers("b")
    a.sync([])
    assert a.take_floor()
    a.sync([_step()])
    drivers.clock.now += 1
    b.sync([])
    assert not b.take_floor()
    a.sync([])
    a.settle([], waiting=True)
    drivers.clock.now += batch.machine.FRESH_SECONDS - 1
    b.settle([], waiting=True)
    c = drivers("c")
    c.sync([])
    assert not c.take_floor()


def test_a_dead_floor_holder_frees_the_slot(drivers, tmp_path):
    with batch.machine.register("holder", tmp_path, drivers.clock) as holder:
        assert holder.take_floor()
    drivers.clock.now += 1
    b = drivers("b")
    b.sync([])
    assert b.take_floor()


# ── fallback ────────────────────────────────────────────────────────────────


def test_no_flock_falls_back_to_per_run_admission(tmp_path, monkeypatch, capsys):
    def refuse(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(batch.machine.fcntl, "flock", refuse)
    with batch.machine.register("a", tmp_path, Clock()) as machine:
        assert isinstance(machine, PerRun)
        assert machine.take_floor()
        assert machine.sync([_step()]) == batch.machine.Peers()
    assert "per-run" in capsys.readouterr().err


def test_the_drivers_own_lock_is_held_for_its_life(drivers):
    a = drivers("a")
    assert isinstance(a, Machine)
    fd = os.open(drivers.root / "a.lock", os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
