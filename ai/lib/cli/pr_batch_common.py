"""Shared helpers for the `pr batch` CLI modules.

A leaf: imported by `cli.pr_batch`, `cli.pr_batch_requests`, and
`cli.pr_batch_views`. Imports none of them.
"""

# doc-group: cli

from __future__ import annotations

import os
import sys

import batch.store
import pr.target
from batch.store import Role


def fail(message: str) -> int:
    print(f"pr batch: {message}", file=sys.stderr)
    return 1


def cwd_repo() -> batch.store.RepoRef | None:
    identity = pr.target.repo_identity_from_origin(os.getcwd())
    if identity is None:
        return None
    return batch.store.RepoRef(identity.key, identity.label)


def warn_other_active(chosen: str) -> None:
    for run_id in batch.store.run_ids():
        if run_id != chosen and is_active(run_id):
            print(f"pr batch: other active run {run_id} has a running job", file=sys.stderr)


def warn_foreign_run(cwd: batch.store.RepoRef | None, chosen: str) -> None:
    if cwd is None:
        print(f"pr batch: showing {chosen}, the newest run on this machine",
              file=sys.stderr)
        return
    print(f"pr batch: no run covers {cwd.label}; showing {chosen}, "
          f"the newest run on this machine", file=sys.stderr)


def resolve_run_id(given: str | None, *, covering_only: bool = False) -> str | None:
    """*given*, or the covering run for this checkout; names other live drivers on stderr."""
    if given:
        return given
    cwd = cwd_repo()
    if covering_only:
        chosen = batch.store.covering_run_id(cwd)
    else:
        chosen = batch.store.default_run_id(cwd)
        if chosen and batch.store.covering_run_id(cwd) != chosen:
            warn_foreign_run(cwd, chosen)
    if chosen:
        warn_other_active(chosen)
    return chosen


def continue_run(run_id: str, pr_bin: str) -> int:
    """Load *run_id*, submit what is now ready, and return submit_ready's mapped code."""
    import batch.inbox
    try:
        with batch.store.session(
            run_id, Role.DRIVER,
            inbox=batch.inbox.Inbox(batch.inbox.ApplyContext()),
            command=f"pr batch run --continue {run_id}",
        ) as session:
            return _continue_locked(session, pr_bin)
    except batch.store.RunNotFound:
        return fail(f"no run {run_id}")
    except (batch.store.RunBusy, batch.store.RunUnreachable) as exc:
        return fail(str(exc))


def _continue_locked(session, pr_bin: str) -> int:
    from batch.model import FINISHED_RUN
    if session.run.status in FINISHED_RUN:
        return fail(f"run {session.run.id} is {session.run.status.value}")
    code = submit_jobs(session.run, pr_bin)
    session.commit()
    return 0 if code == 0 else 1


def submit_jobs(run, pr_bin: str) -> int:
    """Submit every ready job on *run* via otto-queue and return submit_ready's code."""
    import batch.orchestrate
    import batch.queue_entries
    import batch.steps
    import cli.otto_queue
    import git.client
    import otto_queue.submit
    import rebase.conflicts
    import rebase.inspect
    import rebase.need
    from batch.model import StepStatus
    from pathlib import Path

    def submit(request):
        return otto_queue.submit.submit(request, cli.otto_queue.machine_policy())

    def ensure_worktree(item):
        res = batch.steps.ensure_worktree(item.repo_dir, item.branch)
        if not res.ok:
            return res.error
        item.worktree = res.path
        return ""

    def wanted(item, step):
        return item.has(step) and item.step(step).status is not StepStatus.SKIPPED

    def conflict_files(item):
        if not item.worktree or not item.base_ref:
            return ()
        return rebase.need.conflict_paths(item.worktree, item.branch, item.base_ref)

    def generated(item, path: str) -> bool:
        wt = item.worktree or os.getcwd()
        return rebase.conflicts.is_generated_file(path, Path(wt) / path, wt) is not None

    return batch.orchestrate.submit_ready(
        run,
        pr_bin=pr_bin,
        now=batch.store.now_iso(),
        submit=submit,
        states=batch.queue_entries.QueueEntries().states,
        dirty=lambda wt: git.client.is_dirty(cwd=wt),
        rebasing=rebase.inspect.rebase_in_progress,
        branch_ok=lambda item: batch.steps.checked_out_branch(item.worktree) == item.branch,
        wanted=wanted,
        conflict_files=conflict_files,
        generated=generated,
        ensure_worktree=ensure_worktree,
    )


def is_active(run_id: str) -> bool:
    """True when any indexed job is still live in otto-queue (queued or running)."""
    import batch.queue_entries
    from otto_queue.model import LIVE_STATES

    try:
        run = batch.store.load(run_id)
    except (batch.store.RunNotFound, ValueError, TypeError):
        return False
    ids = [job.entry_id for item in run.items for job in item.jobs]
    if not ids:
        return False
    states = batch.queue_entries.QueueEntries().states(ids)
    return any(states.get(entry_id) in LIVE_STATES for entry_id in ids)
