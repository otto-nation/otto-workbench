"""Scheduler: admission counts every driver on the machine, and the floor slot is shared."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.machine  # noqa: E402
from batch.model import ItemStatus, Step  # noqa: E402
# `_close_open_sessions` and `_quiet_outcomes` are autouse fixtures, imported only so
# pytest registers them (the `noqa: F401` covers them); the other names are used directly.
from batch_scheduler_support import (  # noqa: F401
    NEED, NO, Harness, _close_open_sessions, _quiet_outcomes, row,
)

GiB = 1024 ** 3
ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}
# Room for one 1.5 GiB review step above the 2G reserve, and no more.
TIGHT = batch.admission.HostSample(4 * GiB, 1.0, 0.0)
NO_METRICS = batch.admission.HostSample(None, None, None)


class FakeMachine:
    """What the scheduler asks of `batch.machine`, answered from fixed values."""

    def __init__(self, peers=batch.machine.Peers(), floor=False):
        self.peers, self.floor = peers, floor
        self.synced, self.settled, self.asked = [], [], 0

    def sync(self, live):
        self.synced.append(list(live))
        return self.peers

    def take_floor(self):
        self.asked += 1
        return self.floor

    def settle(self, live, *, waiting):
        self.settled.append((list(live), waiting))

    def close(self):
        pass


def _first_tick(h):
    h.sched._once()
    return h.run.items[0]


def test_a_driver_with_nothing_running_waits_without_the_floor_slot():
    """Regression: every driver started its first step whatever memory said, so N
    concurrent batches started N steps on a host with room for one."""
    machine = FakeMachine(peers=batch.machine.Peers(pending=3 * GiB), floor=False)
    h = Harness([row(1, ONLY_REVIEW)], host=TIGHT, machine=machine)
    item = _first_tick(h)
    assert h.spawned == []
    assert item.status is ItemStatus.WAITING_ADMISSION
    assert "waiting for memory" in item.wait_reason
    assert machine.asked == 1
    assert machine.settled[-1] == ([], True)


def test_the_floor_slot_starts_one_step_on_a_starved_host():
    machine = FakeMachine(peers=batch.machine.Peers(pending=3 * GiB), floor=True)
    h = Harness([row(1, ONLY_REVIEW), row(2, ONLY_REVIEW)], host=TIGHT, machine=machine)
    h.sched._once()
    assert len(h.spawned) == 1
    live, waiting = machine.settled[-1]
    assert [s.item for s in live] == ["o/r#1"] and waiting is False


def test_other_drivers_pending_memory_comes_off_the_host():
    h = Harness([row(1, ONLY_REVIEW)], host=TIGHT,
                machine=FakeMachine(peers=batch.machine.Peers(pending=0)))
    _first_tick(h)
    assert len(h.spawned) == 1
    h2 = Harness([row(1, ONLY_REVIEW)], host=TIGHT,
                 machine=FakeMachine(peers=batch.machine.Peers(pending=GiB, live=1)))
    _first_tick(h2)
    assert h2.spawned == []


def test_the_floor_is_not_asked_for_while_a_step_of_ours_runs():
    machine = FakeMachine(peers=batch.machine.Peers(pending=3 * GiB), floor=True)
    h = Harness([row(1, ONLY_REVIEW), row(2, ONLY_REVIEW)], host=TIGHT, machine=machine)
    h.sched._once()
    assert machine.asked == 1
    assert len(h.spawned) == 1


def test_without_host_metrics_steps_elsewhere_count_against_the_pool_default():
    machine = FakeMachine(peers=batch.machine.Peers(live=1), floor=False)
    h = Harness([row(1, ONLY_REVIEW)], host=NO_METRICS, machine=machine)
    item = _first_tick(h)
    assert h.spawned == []
    assert "running machine-wide" in item.wait_reason


def test_a_scheduler_given_no_machine_keeps_the_per_run_floor():
    h = Harness([row(1, ONLY_REVIEW)], host=batch.admission.HostSample(1 * GiB, 99.0, 50.0))
    _first_tick(h)
    assert len(h.spawned) == 1


def test_two_real_drivers_on_a_starved_host_start_one_step_between_them(tmp_path):
    """The bug end to end: before, each driver admitted its first step on its own."""
    starved = batch.admission.HostSample(1 * GiB, 1.0, 0.0)
    with batch.machine.register("run-a", tmp_path) as ma, \
            batch.machine.register("run-b", tmp_path) as mb:
        a = Harness([row(1, ONLY_REVIEW)], host=starved, machine=ma)
        b = Harness([row(2, ONLY_REVIEW)], host=starved, machine=mb)
        a.sched._once()
        b.sched._once()
        assert (len(a.spawned), len(b.spawned)) == (1, 0)
        assert "waiting for memory" in b.run.items[0].wait_reason


def test_floor_refused_while_a_peer_has_a_step_live(tmp_path):
    starved = batch.admission.HostSample(1 * GiB, 1.0, 0.0)
    healthy = batch.admission.HostSample(64 * GiB, 1.0, 0.0)
    with batch.machine.register("run-a", tmp_path) as ma, \
            batch.machine.register("run-b", tmp_path) as mb:
        a = Harness([row(1, ONLY_REVIEW)], host=healthy, machine=ma)
        b = Harness([row(2, ONLY_REVIEW)], host=starved, machine=mb)
        a.sched._once()
        b.sched._once()
        assert (len(a.spawned), len(b.spawned)) == (1, 0)


def test_registry_write_failure_does_not_raise(tmp_path):
    with batch.machine.register("run-w", tmp_path) as m:
        m._root = tmp_path / "gone"
        m.settle([], waiting=False)
        assert m.take_floor() in (True, False)
