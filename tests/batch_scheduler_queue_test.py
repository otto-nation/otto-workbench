"""Scheduler adoption of otto-queue tickets: pass_fds, LedgerBusy, spawn close."""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import otto_queue.model  # noqa: E402
from batch.plan import StepNeed  # noqa: E402
from batch_scheduler_support import (  # noqa: E402,F401
    FakeQueueEntries, Harness, _close_open_sessions, _quiet_outcomes, row,
)

NEED = StepNeed(True, "x")
NO = StepNeed(False, "y")
_ONLY_REVIEW = {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO,
                batch.model.Step.REVIEW: NEED}
_ONLY_REBASE = {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NO,
                batch.model.Step.REVIEW: NO}


def test_an_ai_step_is_spawned_with_the_accepted_fd_then_the_scheduler_closes_it():
    q = FakeQueueEntries()
    h = Harness([row(1, _ONLY_REVIEW)], queue_entries=q)
    h.sched._once()
    assert len(q.accepted) == 1
    adopted = q.accepted[0]
    assert h.pass_fds == [(adopted.fd,)]
    env = h.envs[0]
    assert env[otto_queue.model.ADOPT_ENV] == str(adopted.entry_id)
    assert env[otto_queue.model.LEASE_FD_ENV] == str(adopted.fd)
    with pytest.raises(OSError):
        os.fstat(adopted.fd)


def test_a_non_ai_step_does_not_accept_a_ticket():
    q = FakeQueueEntries()
    h = Harness([row(1, _ONLY_REBASE)], with_ai=[], queue_entries=q)
    h.sched._once()
    assert h.spawned
    assert q.accept_calls == []
    assert q.accepted == []
    assert otto_queue.model.ADOPT_ENV not in h.envs[0]
    assert otto_queue.model.LEASE_FD_ENV not in h.envs[0]
    assert h.pass_fds == [()]


def test_ledger_busy_refuses_the_tick_then_succeeds_on_the_next():
    q = FakeQueueEntries()
    q.busy = True
    h = Harness([row(1, _ONLY_REVIEW)], queue_entries=q)
    h.sched._once()
    assert h.spawned == []
    item = h.run.items[0]
    assert item.wait_reason == "otto-queue ledger busy"
    assert item.status is batch.model.ItemStatus.WAITING_ADMISSION
    q.busy = False
    h.sched._once()
    assert len(h.spawned) == 1
    assert q.accepted


def test_accept_returning_none_still_spawns_without_adopt_env():
    q = FakeQueueEntries()
    q.unavailable = True
    h = Harness([row(1, _ONLY_REVIEW)], queue_entries=q)
    h.sched._once()
    assert len(h.spawned) == 1
    assert q.accepted == []
    assert otto_queue.model.ADOPT_ENV not in h.envs[0]
    assert otto_queue.model.LEASE_FD_ENV not in h.envs[0]
    assert h.pass_fds == [()]


def test_a_spawn_that_raises_still_closes_the_fd():
    q = FakeQueueEntries()
    h = Harness([row(1, _ONLY_REVIEW)], queue_entries=q)

    def boom(*_a, **_k):
        raise RuntimeError("spawn failed")

    h.sched._spawn = boom
    with pytest.raises(RuntimeError, match="spawn failed"):
        h.sched._once()
    assert len(q.accepted) == 1
    with pytest.raises(OSError):
        os.fstat(q.accepted[0].fd)
