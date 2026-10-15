"""Apply one inbound request to a loaded run, and settle a run that is idle.

`apply_one` is the policy: a `Decide` goes through `batch.resolve.apply`
unless the decision already carries that action (an idempotent replay), and a
`Cancel` records `Run.cancel` — `kill` only upgrades, `requested_at` is set
the first time only. A cancel against `DONE` is an error; against `CANCELLED`
it is a no-op success. `settle_idle` turns a recorded cancel into `CANCELLED`,
or every item terminal into `DONE`, and never promotes `CANCELLED`. `Inbox` is
what a caller holds: apply, settle, report.

`deliver` is the one path for `pr batch resolve` and `cancel`: it writes the
request, then either applies it in an `APPLIER` session or returns `queued`
when a live holder that drains will apply it. A request reported `queued` is
applied by that holder or its hand-off, or, if the holder dies, by the next
session to open the run.
"""

# doc-group: batch

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import batch.publish
import batch.resolve
import batch.store
import core.run_lock
from batch.model import FINISHED_RUN, CancelState, Item, Run, RunStatus
from batch.requests import Cancel, Decide, Envelope, Malformed, Outcome
from batch.store import Holder, Role
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
    is success without invoking the runner. ``ResolveError``, ``KeyError``,
    ``TypeError``, ``OSError`` and ``subprocess.SubprocessError`` become
    ``Outcome.error``. ``finished`` names the decision's item when that item
    became terminal during this apply.
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
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome(name=name, request=request,
                       error=f"{type(exc).__name__}: {exc}")


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


@dataclass(frozen=True)
class Delivery:
    """What became of one request handed to `deliver`.

    `queued` means a live holder that drains had the run, and will apply the
    request before it lets go; `holder` names it. Otherwise this process
    opened the run, and its entry drain applied the request. Then `own` is the
    request's outcome, or None when another session had already applied it,
    and `others` are the requests queued before it that the same drain applied.
    """

    name: str
    queued: bool
    own: Outcome | None
    others: tuple[Outcome, ...]
    holder: Holder | None


def _collector(seen: dict[str, Outcome],
               forward: Callable[[Run, Outcome], None]) -> Callable[[Run, Outcome], None]:
    """A report callback that records each outcome in *seen* by request name, then forwards it."""

    def report(run: Run, outcome: Outcome) -> None:
        seen[outcome.name] = outcome
        forward(run, outcome)

    return report


def _attempt(run_id: str, name: str, ctx: ApplyContext, command: str) -> Delivery:
    """Open *run_id* as an applier and say what its drains did.

    A refusal by a live holder that drains means *name* is queued for that holder.
    A GC holder or a RunBusy whose holder is None is re-raised for `deliver` to retry.
    """
    seen: dict[str, Outcome] = {}
    inbox = Inbox(replace(ctx, report=_collector(seen, ctx.report)))
    try:
        with batch.store.session(run_id, Role.APPLIER, inbox=inbox, command=command):
            # The entry drain has applied the request; the normal exit finishes.
            pass
    except batch.store.RunBusy as exc:
        if exc.holder is not None and exc.holder.drains:
            return Delivery(name=name, queued=True, own=None, others=(), holder=exc.holder)
        raise
    own = seen.pop(name, None)
    return Delivery(name=name, queued=False, own=own, others=tuple(seen.values()),
                    holder=None)


def deliver(run_id: str, request: Decide | Cancel, ctx: ApplyContext, *,
            command: str) -> Delivery:
    """Queue *request* in *run_id*'s inbox, then apply it here or leave it to a holder that will.

    *ctx* is the context the entry drain applies with, and *command* is what
    this process's holder record names. Every outcome the drain produces also
    goes to `ctx.report`.

    Raises RunNotFound when the run directory is gone, including when the
    write succeeded and every later open still saw it missing past
    `core.run_lock.retry_delays()`. Raises RunUnreachable when `session()`
    gave up on a vacated holder record, a GC holder kept the run past that
    same budget, or an unreadable holder record did. Either way the request
    is still in the inbox, and it is never reported queued.
    """
    name = batch.store.write_request(run_id, request)
    delays = core.run_lock.retry_delays()
    missing = False
    unreadable = False
    while True:
        try:
            return _attempt(run_id, name, ctx, command)
        except batch.store.RunNotFound:
            missing = True
            unreadable = False
        except batch.store.RunBusy as exc:
            missing = False
            unreadable = exc.holder is None
        delay = next(delays, None)
        if delay is None:
            break
        time.sleep(delay)
    if missing:
        raise batch.store.RunNotFound(run_id)
    if unreadable:
        raise batch.store.RunUnreachable(
            run_id, "an unreadable holder record kept the run past the retry budget")
    raise batch.store.RunUnreachable(
        run_id, "a gc holder kept the run past the retry budget")
