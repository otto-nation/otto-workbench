"""Remove finished `pr batch` runs once they are old enough; `pr gc` calls it.

Only `done` and `cancelled` runs are candidates. `running` and `waiting` runs
are live, and an `interrupted` run is one `pr batch resume` can still pick up.
A run is kept for `review.gc.GC_STALE_DAYS` after its last save, or for
`review.gc.GC_FAILED_STALE_DAYS` when it kept work a person may come back for —
see `retention_days`. Its lock is taken, non-blocking, around the removal, so a
run some process holds is skipped rather than deleted under it.
"""

# doc-group: batch

from __future__ import annotations

import time

import batch.plan
import batch.store
import core.log
import core.run_lock
from batch.model import DecisionKind, Item, ItemStatus, Run, RunStatus, StepStatus
from review.gc import GC_FAILED_STALE_DAYS, GC_STALE_DAYS

FINISHED = frozenset({RunStatus.DONE, RunStatus.CANCELLED})
DAY_SECONDS = 86400


def _kept_work(run: Run, item: Item) -> bool:
    """Whether *item* ended with work nobody published or settled.

    A failed step or a still-open `failed` decision, a dropped item, or drafted
    steps that were never pushed: the item never reached `done`, or its publish
    decision was answered with `discard`.
    """
    if item.status is ItemStatus.DROPPED:
        return True
    if any(r.status is StepStatus.FAILED for r in item.steps):
        return True
    mine = [d for d in run.decisions if d.item == item.key]
    if any(d.kind is DecisionKind.FAILED and d.open for d in mine):
        return True
    if not any(r.drafted for r in item.steps):
        return False
    return item.status is not ItemStatus.DONE or any(
        d.kind is DecisionKind.PUBLISH and d.resolution == "discard" for d in mine)


def retention_days(run: Run) -> int:
    """How many days after its last save *run* is kept."""
    if any(_kept_work(run, item) for item in run.items):
        return GC_FAILED_STALE_DAYS
    return GC_STALE_DAYS


def _expired(run_id: str, now: float) -> Run | None:
    """*run_id*'s run when it is finished and past its window, else None.

    An unreadable state file is warned about and kept: gc deletes on what the
    state says, and a run it cannot read says nothing.
    """
    try:
        run = batch.store.load(run_id)
        age = now - batch.store.state_mtime(run_id)
    except (OSError, ValueError, TypeError) as exc:
        core.log.warn(f"GC: could not read batch run {run_id} ({exc}) — leaving it in place")
        return None
    if run.status not in FINISHED or age < retention_days(run) * DAY_SECONDS:
        return None
    return run


def _remove_locked(run: Run, now: float) -> Run | None:
    """Re-read *run* under its held lock and delete it; None when it was kept."""
    fresh = _expired(run.id, now)
    if fresh is None:
        return None
    batch.plan.drop_refs(fresh.ref_dirs, fresh.ref_namespace)
    batch.store.remove(fresh.id)
    return fresh


def _remove(run: Run, now: float) -> bool:
    """Drop *run*'s refs and delete it under its lock; False when it was kept.

    The run is re-read once the lock is held: it may have changed since the
    unlocked check, and only the locked state is safe to delete on.
    """
    try:
        with core.run_lock.acquire(batch.store.run_dir(run.id), "gc", batch.store.now_iso()):
            removed = _remove_locked(run, now)
    except core.run_lock.LockBusy:
        return False
    except OSError as exc:
        core.log.warn(f"GC: could not remove batch run {run.id} ({exc})")
        return False
    if removed is None:
        return False
    core.log.info(f"Pruned batch run {removed.id} ({removed.status.value})")
    return True


def gc_runs(*, now: float | None = None) -> int:
    """Remove every finished batch run past its window; returns how many went.

    *now* is the instant every run's age is measured against, the clock when
    omitted.
    """
    instant = time.time() if now is None else now
    removed = 0
    for run_id in batch.store.run_ids():
        run = _expired(run_id, instant)
        if run is not None and _remove(run, instant):
            removed += 1
    return removed
