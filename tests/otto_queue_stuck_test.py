"""Tests for otto_queue.stuck: an unclaimed job with a live idle worker is stuck."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from otto_queue.model import Entry, EntryClass, EntryKind, EntryState  # noqa: E402
from otto_queue.stuck import UNCLAIMED_S, unclaimed_job  # noqa: E402


def _job(**kw) -> Entry:
    fields = dict(
        id=436, kind=EntryKind.JOB, entry_class=EntryClass.HEAVY, idem_key=None,
        argv=("true",), cwd="/", env_allow=(), state=EntryState.QUEUED, attempts=0,
        exit_status=None, log_path=None, result=None, pid=None,
        created_at=0.0, started_at=None, finished_at=None, not_before=None,
        label="maintenance",
    )
    fields.update(kw)
    return Entry(**fields)


def test_an_old_unclaimed_job_with_a_live_idle_worker_is_stuck():
    now = UNCLAIMED_S + 45
    reason = unclaimed_job(ahead=_job(), now=now, worker_alive=True, running=0)
    assert reason is not None
    assert reason.startswith("stuck: job #436 queued ")
    assert "worker alive, never claimed" in reason


def test_a_fresh_unclaimed_job_is_not_stuck_yet():
    now = UNCLAIMED_S - 1
    assert unclaimed_job(ahead=_job(), now=now, worker_alive=True, running=0) is None


def test_a_job_is_not_stuck_when_the_class_is_actually_running():
    now = UNCLAIMED_S + 45
    assert unclaimed_job(ahead=_job(), now=now, worker_alive=True, running=1) is None


def test_a_job_is_not_stuck_when_no_worker_is_alive():
    now = UNCLAIMED_S + 45
    assert unclaimed_job(ahead=_job(), now=now, worker_alive=False, running=0) is None


def test_an_older_ticket_is_not_a_stuck_job():
    now = UNCLAIMED_S + 45
    ahead = _job(kind=EntryKind.TICKET)
    assert unclaimed_job(ahead=ahead, now=now, worker_alive=True, running=0) is None


@pytest.mark.parametrize("started_at,state", [(1.0, EntryState.QUEUED), (None, EntryState.RUNNING)])
def test_a_started_or_running_job_is_not_stuck(started_at, state):
    now = UNCLAIMED_S + 45
    ahead = _job(started_at=started_at, state=state)
    assert unclaimed_job(ahead=ahead, now=now, worker_alive=True, running=0) is None


def test_nothing_ahead_is_not_stuck():
    assert unclaimed_job(ahead=None, now=UNCLAIMED_S + 45, worker_alive=True, running=0) is None
