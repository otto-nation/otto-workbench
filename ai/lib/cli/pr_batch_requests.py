"""Inbox-writing `pr batch` commands: cancel and release.

Moved out of `cli.pr_batch` so the dispatcher stays under the file-size cap.
"""

# doc-group: cli

from __future__ import annotations

import sys
from pathlib import Path

import batch.inbox
import batch.plan
import batch.report
import batch.store
import cli.pr_batch_common
import core.report
from batch.model import FINISHED_RUN, Item, Run
from batch.requests import Cancel, Release

EXIT_OK = batch.report.EXIT_OK


def _unreachable(run_id: str) -> str:
    return (f"run {run_id} is unreachable; the request is in its inbox and "
            f"will be applied by the next session to open it")


def _deliver(run_id: str, request: Cancel | Release, bin_dir: Path,
             verb: str) -> batch.inbox.Delivery:
    """`batch.inbox.deliver`, naming on stderr each earlier request the same drain failed."""
    delivery = batch.inbox.deliver(run_id, request, batch.inbox.ApplyContext(),
                                   command=f"pr batch {verb} {run_id}")
    for other in delivery.others:
        if not other.ok:
            print(f"pr batch: request {other.name}: {other.error}", file=sys.stderr)
    return delivery


def _own_error(run_id: str, delivery: batch.inbox.Delivery) -> str:
    """This request's error, or "" if it applied.

    When another session applied it, the error is read from `rejected/`.
    """
    if delivery.own is not None:
        return delivery.own.error
    for failed in batch.store.failed_requests(run_id):
        if failed.name == delivery.name:
            return failed.error
    return ""


def ambiguous(given: str | None) -> str | None:
    """The refusal for a mutating no-id call when several unfinished runs cover this repo."""
    if given or not (repo := cli.pr_batch_common.cwd_repo()):
        return None
    candidates = batch.store.unfinished_covering(repo)
    if len(candidates) > 1:
        return ("more than one unfinished run covers this repo "
                f"({', '.join(candidates)}); pass a run id")
    return None


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
    try:
        delivery = _deliver(run_id, Cancel(kill=args.kill), bin_dir, "cancel")
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    except batch.store.RunUnreachable:
        return cli.pr_batch_common.fail(_unreachable(run_id))
    error = "" if delivery.queued else _own_error(run_id, delivery)
    result = {"cancelled": not error, "run": run_id, "queued": delivery.queued}
    if error:
        # The run finished before the cancel reached it; say what it ended as.
        result["status"] = batch.store.load(run_id).status.value
        print(f"pr batch: {error}", file=sys.stderr)
    core.report.emit_json(result)
    return 1 if error else EXIT_OK


def _resolve_item(run: Run, given: str) -> Item | str:
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


def _release_view(item: Item, *, run_id: str, queued: bool, released: bool) -> dict:
    drafted = [{"step": r.step.value, "commits": list(r.commits)}
               for r in item.steps if r.drafted]
    closeout = ""
    if item.repo_dir and Path(item.repo_dir).is_dir():
        try:
            closeout = batch.plan.closeout_debt(item.repo_dir, item.branch).describe()
        except (OSError, TypeError, ValueError):
            closeout = ""
    return {"released": released, "run": run_id, "item": item.key, "queued": queued,
            "worktree": item.worktree, "drafted": drafted, "closeout": closeout}


def cmd_release(args, bin_dir: Path) -> int:
    run_id = args.run_id
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    item = _resolve_item(run, args.pr)
    if isinstance(item, str):
        return cli.pr_batch_common.fail(item)
    try:
        delivery = _deliver(run_id, Release(item=item.key, kill=args.kill), bin_dir, "release")
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    except batch.store.RunUnreachable:
        return cli.pr_batch_common.fail(_unreachable(run_id))
    error = "" if delivery.queued else _own_error(run_id, delivery)
    if error:
        print(f"pr batch: {error}", file=sys.stderr)
    try:
        current = batch.store.load(run_id).item(item.key)
    except (batch.store.RunNotFound, KeyError):
        current = item
    core.report.emit_json(_release_view(current, run_id=run_id, queued=delivery.queued,
                                        released=not error))
    return 1 if error else EXIT_OK
