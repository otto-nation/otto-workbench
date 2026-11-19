"""Whether an (item, step) job may be submitted now.

Predicates join the run index (`Item.jobs`) with a live worktree + queue snapshot.
They do not submit, and they do not import `cli`.
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from batch.model import STEP_ORDER, Item, Job, Step, StepStatus
from otto_queue.model import EntryState, LIVE_STATES


@dataclass(frozen=True)
class Snapshot:
    dirty: bool
    rebasing: bool
    branch_ok: bool
    queue_states: dict[int, EntryState]  # entry_id -> state


def _newest_job(item: Item, step: Step) -> Job | None:
    for job in reversed(item.jobs):
        if job.step is step:
            return job
    return None


def live_job(item: Item, step: Step, states: dict[int, EntryState]) -> Job | None:
    """The newest job for *step* whose state is queued or running."""
    for job in reversed(item.jobs):
        if job.step is not step:
            continue
        state = states.get(job.entry_id)
        if state in LIVE_STATES:
            return job
    return None


def succeeded(item: Item, step: Step, states: dict[int, EntryState]) -> bool:
    """True when the newest job for *step* is done (exit recorded as DONE)."""
    job = _newest_job(item, step)
    if job is None:
        return False
    return states.get(job.entry_id) is EntryState.DONE


def ready(item: Item, step: Step, *, wanted: frozenset[Step], snap: Snapshot,
          parent: Item | None) -> bool:
    """Whether (item, step) may be submitted now. See spec §3."""
    states = snap.queue_states
    if step not in wanted:
        return False
    if snap.dirty or snap.rebasing or not snap.branch_ok:
        return False
    if live_job(item, step, states) is not None:
        return False
    if succeeded(item, step, states):
        return False
    for earlier in STEP_ORDER:
        if earlier is step:
            break
        if earlier in wanted and not succeeded(item, earlier, states):
            return False
    if step is Step.REBASE and parent is not None and _parent_blocks_rebase(parent, states):
        return False
    return True


def _parent_blocks_rebase(parent: Item, states: dict[int, EntryState]) -> bool:
    rec = next((r for r in parent.steps if r.step is Step.REBASE), None)
    if rec is None or rec.status is StepStatus.SKIPPED:
        return False
    return not succeeded(parent, Step.REBASE, states)


def submit_order(jobs: list[tuple[Item, Step]], *,
                 conflict_files: dict[str, tuple[str, ...]],
                 generated: Callable[[Item, str], bool]) -> list[tuple[Item, Step]]:
    """Ready jobs in queue FIFO order: rebase easiest-first, then ci, then comments."""

    def rank(job: tuple[Item, Step]) -> tuple[int, int, int, int, int]:
        item, step = job
        paths = conflict_files.get(item.key, ())
        n_files = len(paths)
        handwritten_first = 0 if all(generated(item, p) for p in paths) else 1
        stacked = 1 if item.stacked_on else 0
        return (STEP_ORDER.index(step), handwritten_first, stacked, n_files, item.pr)

    return sorted(jobs, key=rank)
