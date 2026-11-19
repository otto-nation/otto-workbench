"""Build a run from plan rows; emit request outcomes."""

# doc-group: batch

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

import batch.events
import batch.store
from batch.model import (STEP_ORDER, Item, RebaseWhen, Run, Step, StepRecord,
                         StepStatus)
from batch.plan import PlanRow
from batch.requests import Cancel, Outcome, Release


def new_run(rows: list[PlanRow], *, steps: list[Step], selected: dict[str, list[Step]] | None,
            pool: int, now: datetime | None = None,
            ref_namespace: str = "", ref_dirs: list[str] | None = None,
            no_verify: bool = False, focus: Sequence[str] = (),
            with_ai: list[Step] | None = None,
            auto_publish_ff: bool = False,
            rebase_when: RebaseWhen = RebaseWhen.CONFLICTS,
            checkouts: Sequence[str] = ()) -> Run:
    now = now or datetime.now(timezone.utc)
    items = []
    explicit = selected is not None
    for r in rows:
        chosen = selected.get(r.key, []) if selected is not None else \
            [s for s in steps if (n := r.need_for(s, rebase_when)) and n.needed]
        # A row whose only work is the closeout pr recorded as owed is kept with
        # every step skipped. Only when comments is a step of this run, and never
        # past an explicit selection that left the row out.
        closeout_only = (not chosen and not explicit and bool(r.closeout)
                         and Step.COMMENTS in steps)
        if not chosen and not closeout_only:
            continue
        recs = [StepRecord(s, StepStatus.PENDING if s in chosen else StepStatus.SKIPPED,
                           explicit=explicit and s in chosen)
                for s in STEP_ORDER if s in steps]
        items.append(Item(key=r.key, repo=r.repo, repo_dir=r.repo_dir, pr=r.pr, branch=r.branch,
                          head_sha=r.head_sha, steps=recs, base_ref=r.base_ref,
                          repo_key=r.repo_key, is_draft=r.is_draft))
    # A fork's head branch names a branch in another repo — often `main` —
    # so keying it under the base repo would stack every PR based on `main`.
    by_branch = {(r.repo, r.branch): r.key for r in rows if not r.is_fork}
    queued = {it.key for it in items}
    bases = {r.key: by_branch.get((r.repo, r.base_ref), "") for r in rows if r.base_ref}
    for it in items:
        base = bases.get(it.key, "")
        if base in queued and base != it.key:
            it.stacked_on = base
    return Run(id=batch.store.new_run_id(now), started_at=now.isoformat(timespec="seconds"),
               steps=list(steps), pool=pool, auto_publish=[], items=items,
               ref_namespace=ref_namespace, ref_dirs=list(ref_dirs or []),
               no_verify=no_verify, focus=list(focus), with_ai=with_ai,
               schema_version=3, auto_publish_ff=auto_publish_ff,
               rebase_when=rebase_when, checkouts=list(checkouts))


def row_for(item: Item, local_head: str = "", ref_namespace: str = "") -> PlanRow:
    # The remote sha, not head_sha: a replan that echoes its input must never
    # read as GitHub reporting the plan-time head again.
    return PlanRow(item.repo, item.repo_dir, item.pr, "", item.branch, item.remote_sha, False,
                   {}, local_head=local_head, ref_namespace=ref_namespace,
                   repo_key=item.repo_key)


def emit_outcome(run: Run, outcome: Outcome, *,
                 emit: Callable[..., None] = batch.events.emit) -> None:
    """Emit the events one applied request produces.

    A `Cancel` emits `cancel_requested`. A `Release` emits `item_finished` for
    each item it made terminal. A malformed request emits nothing: stderr and
    `failed_requests` already carry it. A failed outcome carries `error`.
    """
    request = outcome.request
    failed = {"error": outcome.error} if outcome.error else {}
    if isinstance(request, Cancel):
        emit("cancel_requested", run=run.id, kill=request.kill, **failed)
        return
    if isinstance(request, Release):
        for key in outcome.finished:
            emit("item_finished", run=run.id, item=key, status=run.item(key).status.value,
                 **failed)
