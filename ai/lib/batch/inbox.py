"""Apply one inbound request to a loaded run, and settle a run that is idle.

``apply_one`` is the policy: a ``Decide`` goes through ``batch.resolve.apply``
unless the decision already carries that action (an idempotent replay), and a
``Cancel`` records ``Run.cancel`` — ``kill`` only upgrades, ``requested_at``
is set the first time only. A cancel against ``DONE`` is an error; against
``CANCELLED`` it is a no-op success. ``settle_idle`` turns a recorded cancel
into ``CANCELLED``, or every item terminal into ``DONE``, and never promotes
``CANCELLED``. ``Inbox`` is what a caller holds: apply, settle, report.
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import batch.publish
import batch.resolve
import batch.store
from batch.model import FINISHED_RUN, CancelState, Item, Run, RunStatus
from batch.requests import Cancel, Decide, Envelope, Malformed, Outcome
from pr.comments_fix import CloseoutDebt


def _ignore(run: Run, outcome: Outcome) -> None:
    """The default report: an inbox built without a reporter reports nowhere."""
    return None


@dataclass(frozen=True)
class ApplyContext:
    pr_bin: str
    runner: Callable[..., int] | None = None
    tree: Callable[[Item], batch.publish.TreeState] | None = None
    closeout: Callable[[str, str], CloseoutDebt] | None = None
    report: Callable[[Run, Outcome], None] = _ignore


def apply_one(run: Run, request: Decide | Cancel, ctx: ApplyContext, *,
              name: str = "") -> Outcome:
    """Apply *request* to *run* and return the outcome.

    Replay of a ``Decide`` whose decision already carries ``request.action``
    is success without invoking the runner. ``ResolveError``, ``KeyError`` and
    ``TypeError`` become ``Outcome.error``. ``finished`` names the decision's
    item when that item became terminal during this apply.
    """
    if isinstance(request, Cancel):
        return _apply_cancel(run, request, name)
    return _apply_decide(run, request, ctx, name)


def _apply_decide(run: Run, request: Decide, ctx: ApplyContext,
                  name: str) -> Outcome:
    try:
        decision = run.decision(request.decision)
        item = run.item(decision.item)
        if decision.resolution == request.action:
            return Outcome(name=name, request=request)
        was_terminal = item.terminal
        created = batch.resolve.apply(
            run, request, pr_bin=ctx.pr_bin, runner=ctx.runner,
            tree=ctx.tree, closeout=ctx.closeout)
        finished = (item.key,) if item.terminal and not was_terminal else ()
        return Outcome(name=name, request=request, created=tuple(created),
                       finished=finished)
    except (batch.resolve.ResolveError, KeyError, TypeError) as exc:
        return Outcome(name=name, request=request, error=str(exc))


def _apply_cancel(run: Run, request: Cancel, name: str) -> Outcome:
    if run.status is RunStatus.DONE:
        return Outcome(name=name, request=request,
                       error=f"run {run.id} is done")
    if run.status is RunStatus.CANCELLED:
        return Outcome(name=name, request=request)
    existing = run.cancel
    if existing is None:
        run.cancel = CancelState(
            kill=request.kill, requested_at=batch.store.now_iso())
        return Outcome(name=name, request=request)
    if request.kill and not existing.kill:
        run.cancel = CancelState(kill=True, requested_at=existing.requested_at)
    return Outcome(name=name, request=request)


def settle_idle(run: Run) -> bool:
    """Promote *run* to CANCELLED or DONE when nothing live remains.

    A recorded cancel wins over every item being terminal. CANCELLED is never
    promoted to DONE. Returns True when ``run.status`` changed.
    """
    if run.cancel is not None and run.status not in FINISHED_RUN:
        run.status = RunStatus.CANCELLED
        return True
    if run.status in (RunStatus.WAITING, RunStatus.RUNNING) and all(
            item.terminal for item in run.items):
        run.status = RunStatus.DONE
        return True
    return False


class Inbox:
    """Apply / settle / report, for a store session to call."""

    def __init__(self, ctx: ApplyContext):
        self._ctx = ctx

    def apply(self, run: Run, envelope: Envelope) -> Outcome:
        request = envelope.request
        if isinstance(request, Malformed):
            return Outcome(name=envelope.name, request=request,
                           error=request.reason)
        return apply_one(run, request, self._ctx, name=envelope.name)

    def settle(self, run: Run) -> bool:
        return settle_idle(run)

    def report(self, run: Run, outcome: Outcome) -> None:
        self._ctx.report(run, outcome)
