"""One-shot submit of ready batch jobs. Does not wait, sleep, or spawn."""

# doc-group: batch

from __future__ import annotations

import sys
from collections.abc import Callable

from batch.model import Item, Job, Run, Step
from batch.ready import Snapshot, live_job, ready, submit_order, succeeded
from batch.steps import step_argv
from otto_queue.model import EntryClass, EntryState
from otto_queue.submit import SubmitMode, SubmitOutcome, SubmitRequest, SubmitResult


def submit_ready(
    run: Run, *,
    pr_bin: str,
    now: str,
    submit: Callable[[SubmitRequest], SubmitResult],
    states: Callable[[list[int]], dict[int, EntryState]],
    dirty: Callable[[str], bool],
    rebasing: Callable[[str], bool],
    branch_ok: Callable[[Item], bool],
    wanted: Callable[[Item, Step], bool],
    conflict_files: Callable[[Item], tuple[str, ...]],
    generated: Callable[[Item, str], bool],
    ensure_worktree: Callable[[Item], str],
) -> int:
    """Submit every ready job. 0 if every still-needed step was submitted or already succeeded;
    1 if something needed remains unsubmitted."""
    skipped: set[str] = set()
    for item in run.items:
        err = ensure_worktree(item)
        if err:
            print(f"pr batch: {item.key}: {err}", file=sys.stderr)
            skipped.add(item.key)

    queue_states = states([job.entry_id for item in run.items for job in item.jobs])
    candidates = _ready_jobs(
        run, skipped=skipped, wanted=wanted, queue_states=queue_states,
        dirty=dirty, rebasing=rebasing, branch_ok=branch_ok)

    ordered = submit_order(
        candidates,
        conflict_files={item.key: conflict_files(item)
                        for item, step in candidates if step is Step.REBASE},
        generated=generated,
    )
    submitted: set[tuple[str, Step]] = set()
    for item, step in ordered:
        argv = step_argv(step, pr_bin, item.worktree, remote_sha=item.remote_sha,
                         focus=run.focus, ai=run.uses_ai(step))
        result = submit(SubmitRequest(
            argv=tuple(argv),
            cwd=item.worktree,
            entry_class=EntryClass.HEAVY if run.uses_ai(step) else EntryClass.LIGHT,
            force=True,
            idem_key=None,
            mode=SubmitMode.ADD,
            label=f"pr batch · {step.value} · {item.key}",
        ))
        if result.outcome is SubmitOutcome.BUSY or result.entry_id is None:
            break
        item.jobs.append(Job(step=step, entry_id=result.entry_id, argv=argv,
                             submitted_at=now))
        submitted.add((item.key, step))

    if _unsubmitted_need(run, wanted, submitted, queue_states):
        return 1
    return 0


def _ready_jobs(
    run: Run, *, skipped: set[str], wanted: Callable[[Item, Step], bool],
    queue_states: dict, dirty: Callable[[str], bool], rebasing: Callable[[str], bool],
    branch_ok: Callable[[Item], bool],
) -> list[tuple[Item, Step]]:
    candidates: list[tuple[Item, Step]] = []
    for item in run.items:
        if item.key in skipped:
            continue
        candidates.extend(_item_ready(
            run, item, wanted=wanted, queue_states=queue_states,
            dirty=dirty, rebasing=rebasing, branch_ok=branch_ok))
    return candidates


def _item_ready(
    run: Run, item: Item, *, wanted: Callable[[Item, Step], bool],
    queue_states: dict, dirty: Callable[[str], bool], rebasing: Callable[[str], bool],
    branch_ok: Callable[[Item], bool],
) -> list[tuple[Item, Step]]:
    wanted_steps = _wanted_steps(run, item, wanted)
    snap = Snapshot(
        dirty=dirty(item.worktree),
        rebasing=rebasing(item.worktree),
        branch_ok=branch_ok(item),
        queue_states=queue_states,
    )
    parent = run.item(item.stacked_on) if item.stacked_on else None
    return [(item, step) for step in wanted_steps
            if ready(item, step, wanted=wanted_steps, snap=snap, parent=parent)]


def _unsubmitted_need(
    run: Run, wanted: Callable[[Item, Step], bool],
    submitted: set[tuple[str, Step]], queue_states: dict,
) -> bool:
    for item in run.items:
        pending = _wanted_steps(run, item, wanted) - {
            step for key, step in submitted if key == item.key}
        if any(not (live_job(item, step, queue_states)
                    or succeeded(item, step, queue_states)) for step in pending):
            return True
    return False


def _wanted_steps(run: Run, item: Item, wanted: Callable[[Item, Step], bool]) -> frozenset[Step]:
    return frozenset(
        step for step in run.steps
        if wanted(item, step)
        and (step not in (Step.REVIEW, Step.COMMENTS) or run.uses_ai(step)))
