"""Remove finished `pr batch` runs once they are old enough; `pr gc` calls it.

Only `done` and `cancelled` runs are candidates. `running` and `waiting` runs
are live, and an `interrupted` run is one `pr batch resume` can still pick up.
A run is kept for `review.gc.GC_STALE_DAYS` after its last save, or for
`review.gc.GC_FAILED_STALE_DAYS` when it kept work a person may come back for —
see `retention_days`. Removal goes through `batch.store.reap`, which takes the
run's resource as `GC` so a run some process holds is skipped rather than
deleted under it, and a run whose refs would not all delete is kept for the
next sweep. That retry is unbounded: a ref that can never delete (a
permissions problem, say) keeps the run, and warns, on every sweep. A leftover
`<id>.reaping` tombstone from a crash mid-reap is warned about and left in
place: deleting it from outside the lock would race a live reap that still
holds the flock on that directory's inode.
"""

# doc-group: batch

from __future__ import annotations

import functools
import time

import batch.store
import core.log
from batch.model import FINISHED_RUN, DecisionKind, Item, ItemStatus, Run, StepStatus
from review.gc import GC_FAILED_STALE_DAYS, GC_STALE_DAYS

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


def _read_run(run_id: str) -> Run | None:
    """*run_id*'s run, or None when the state file cannot be read.

    An unreadable state file is warned about and kept: gc deletes on what the
    state says, and a run it cannot read says nothing.
    """
    try:
        return batch.store.load(run_id)
    except (OSError, ValueError, TypeError, batch.store.RunNotFound) as exc:
        core.log.warn(f"GC: could not read batch run {run_id} ({exc}) — leaving it in place")
        return None


def _expired_run(run: Run, now: float) -> bool:
    """True when *run* is finished and past its window."""
    try:
        age = now - batch.store.state_mtime(run.id)
    except OSError as exc:
        core.log.warn(f"GC: could not read batch run {run.id} ({exc}) — leaving it in place")
        return False
    if run.status not in FINISHED_RUN:
        return False
    return age >= retention_days(run) * DAY_SECONDS


def _reap(run: Run, now: float) -> bool:
    """Run ``store.reap`` on *run*; True when the directory went.

    The locked load inside reap may disagree with this unlocked snapshot; a
    FRESH/PENDING/BUSY/REFS_KEPT/MISSING result keeps the run. Log lines match
    the messages this module printed before reap existed.
    """
    try:
        result = batch.store.reap(
            run.id,
            expired=functools.partial(_expired_run, now=now),
            command="gc",
        )
    except OSError as exc:
        core.log.warn(f"GC: could not remove batch run {run.id} ({exc})")
        return False
    if result is batch.store.ReapResult.REMOVED:
        core.log.info(f"Pruned batch run {run.id} ({run.status.value})")
        return True
    if result is batch.store.ReapResult.REFS_KEPT:
        core.log.warn(f"GC: kept batch run {run.id} — some of its refs would not delete")
    return False


def _warn_leftover_tombstones() -> None:
    """Name leftover ``<id>.reaping`` dirs; do not delete them."""
    # ceiling: leftover tombstones are warned about, never rmtree'd.
    # A live reap holds the flock on an inode inside the tombstone; deleting
    # the tree from outside that lock would race it.
    # Upgrade trigger: if an operator reports a leftover `<id>.reaping` that
    # outlived the process that created it, then gc_runs may rmtree
    # tombstones older than one day whose flock is not held.
    root = batch.store.batch_root()
    if not root.is_dir():
        return
    names = sorted(p.name for p in root.iterdir() if p.name.endswith(".reaping"))
    if not names:
        return
    core.log.warn(
        f"GC: leftover reap tombstone(s) {', '.join(names)} — leaving in place")


def gc_runs(*, now: float | None = None) -> int:
    """Remove every finished batch run past its window; returns how many went.

    *now* is the instant every run's age is measured against, the clock when
    omitted. The unlocked expiry check still runs first so a live or young run
    is never locked just to be told FRESH.
    """
    instant = time.time() if now is None else now
    removed = 0
    for run_id in batch.store.run_ids():
        run = _read_run(run_id)
        if run is None or not _expired_run(run, instant):
            continue
        if _reap(run, instant):
            removed += 1
    _warn_leftover_tombstones()
    return removed
