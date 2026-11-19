"""`pr batch` cancel. Cancel stops queue jobs.

Moved out of `cli.pr_batch` so the dispatcher stays under the file-size cap.
"""

# doc-group: cli

from __future__ import annotations

from pathlib import Path

import batch.inbox
import batch.report
import batch.store
import cli.pr_batch_common
import core.report
from batch.model import FINISHED_RUN, Item, Run, RunStatus
from batch.store import Role

EXIT_OK = batch.report.EXIT_OK


def ambiguous(given: str | None) -> str | None:
    """The refusal for a mutating no-id call when several unfinished runs cover this repo."""
    if given or not (repo := cli.pr_batch_common.cwd_repo()):
        return None
    candidates = batch.store.unfinished_covering(repo)
    if len(candidates) > 1:
        return ("more than one unfinished run covers this repo "
                f"({', '.join(candidates)}); pass a run id")
    return None


def _cancel_jobs(run: Run) -> str:
    ids = [job.entry_id for item in run.items for job in item.jobs]
    if not ids:
        return ""
    import otto_queue.ledger
    import otto_queue.ticket
    try:
        ledger = otto_queue.ledger.Ledger.open()
    except otto_queue.ledger.LedgerError as exc:
        return str(exc)
    for entry_id in ids:
        otto_queue.ticket.cancel_entry(ledger, entry_id)
    return ""


def cmd_cancel(args, bin_dir: Path) -> int:
    if refusal := ambiguous(args.run_id):
        return cli.pr_batch_common.fail(refusal)
    run_id = cli.pr_batch_common.resolve_run_id(args.run_id, covering_only=not args.run_id)
    if not run_id:
        return cli.pr_batch_common.fail("no batch run covers this repo; pass a run id")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    if run.status in FINISHED_RUN:
        return cli.pr_batch_common.fail(f"run {run_id} is {run.status.value}")
    # --kill is the same as cancel this cut: jobs are not batch children.
    try:
        with batch.store.session(
            run_id, Role.APPLIER,
            inbox=batch.inbox.Inbox(batch.inbox.ApplyContext()),
            command=f"pr batch cancel {run_id}",
        ) as session:
            return _cancel_locked(session)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    except (batch.store.RunBusy, batch.store.RunUnreachable) as exc:
        return cli.pr_batch_common.fail(str(exc))


def _cancel_locked(session) -> int:
    if session.run.status in FINISHED_RUN:
        return cli.pr_batch_common.fail(
            f"run {session.run.id} is {session.run.status.value}")
    err = _cancel_jobs(session.run)
    if err:
        return cli.pr_batch_common.fail(err)
    session.run.status = RunStatus.CANCELLED
    session.commit()
    core.report.emit_json({"cancelled": True, "run": session.run.id})
    return EXIT_OK


def resolve_item(run: Run, given: str) -> Item | str:
    """The item *given* names, or an error to print."""
    if given.isdigit():
        matches = [i for i in run.items if i.pr == int(given)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            return f"run {run.id} has no PR {given}"
        listed = ", ".join(i.key for i in matches)
        return f"PR {given} matches more than one item ({listed}); pass owner/repo#N"
    try:
        return run.item(given)
    except KeyError:
        listed = ", ".join(i.key for i in run.items) or "none"
        return f"run {run.id} has no item {given} (have {listed})"
