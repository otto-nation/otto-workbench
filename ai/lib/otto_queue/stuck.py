"""When a queued job is stuck: worker alive, could admit, never claimed.

A live worker polls every second and starts the oldest job it can admit.
Tickets wait behind that job while `worker_alive` is true, so a worker that
holds the lock and never claims a job it *could* start leaves every heavy
ticket sitting until something else kills the waiter. That is not a busy
queue, and it is not a job waiting on host pressure: tickets skip a job
`try_admit(host_room=True)` would refuse (`otto_queue.ticket._waiting_ahead`).

Observed 2026-11 through pi's `job_start`: a maintenance job queued 10h with
the worker process up and 0/2 heavy running; `pr review` and `pr create`
waited until a harness timeout cancelled them. A later incident was PSI
blocking admit, misread as this wedge; skipping the un-admittable job is
the fix, not raising `UNCLAIMED_S`. `--no-queue` is not the retry for a
true wedge.

Upgrade trigger: if a healthy worker can leave an *admittable* job unclaimed
longer than `UNCLAIMED_S` with empty slots (a start delay we have measured),
raise it.
"""

# doc-group: queue

from __future__ import annotations

from otto_queue.model import Entry, EntryKind, EntryState
from otto_queue.wait_report import format_duration

# Worker poll is 1s. A few polls is a start; a quarter-minute with empty slots
# is the worker wedged. Not a bound on the ticket's own work.
UNCLAIMED_S = 15.0


def unclaimed_job(*, ahead: Entry | None, now: float, worker_alive: bool,
                  running: int, unclaimed_s: float = UNCLAIMED_S) -> str | None:
    """Diagnosis when *ahead* is a job the live worker should have claimed; else None."""
    if ahead is None or ahead.kind is not EntryKind.JOB:
        return None
    if ahead.state is not EntryState.QUEUED or ahead.started_at is not None:
        return None
    if not worker_alive or running > 0:
        return None
    age = now - ahead.created_at
    if age < unclaimed_s:
        return None
    return (f"stuck: job #{ahead.id} queued {format_duration(age)}, "
            f"worker alive, never claimed")
