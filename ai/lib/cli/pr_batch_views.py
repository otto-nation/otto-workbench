"""Read-only `pr batch` views: plan, status, next, report, and the report they share.

Moved out of `cli.pr_batch` so the dispatcher stays under the file-size cap.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
from pathlib import Path

import batch.live
import batch.plan
import batch.publish
import batch.report
import batch.report_md
import batch.session_report
import batch.store
import cli.pr_batch_common
import core.report
import core.serde
from batch.model import Item, Run

EXIT_OK = batch.report.EXIT_OK


def offline(p: argparse.ArgumentParser) -> None:
    p.add_argument("--offline", action="store_true",
                   help="Skip the `github` section: report only what this machine saved, "
                        "without asking GitHub for the run's repos' open PRs. Without it, a read "
                        "runs `git fetch` in every repo and a GraphQL search (slower, uses API quota), "
                        "even while a driver holds the run")


def report(run: Run, active: bool, github: dict | None = None,
           sessions: list | None = None) -> dict:
    """The whole report for *run*, with what its inbox still holds and what it rejected."""
    return batch.report.build(run, active=active,
                              pending_requests=batch.store.pending_requests(run.id),
                              failed_requests=batch.store.failed_requests(run.id),
                              github=github, sessions=sessions)


def cmd_status(args) -> int:
    """`status` and `next`: the report, a projection of it, or what it summarises."""
    run_id = cli.pr_batch_common.resolve_run_id(args.run_id)
    if not run_id:
        return cli.pr_batch_common.fail("no batch runs yet")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    active = cli.pr_batch_common.is_active(run_id)
    if args.command == "next":
        core.report.emit_json(batch.report.next_view(report(run, active, _github(run, args.offline))))
        return EXIT_OK
    if args.full:
        core.report.emit_json({"schema_version": run.schema_version, "active": active,
                               # Where the run's state.json and logs live, so a
                               # consumer can watch it without deriving the path.
                               # Optional, so the schema does not bump.
                               "run_dir": str(batch.store.run_dir(run.id)),
                               "run": core.serde.to_dict(run)})
        return EXIT_OK
    if args.decision:
        try:
            core.report.emit_json(batch.report.decision_detail(run, args.decision))
        except KeyError:
            return cli.pr_batch_common.fail(f"run {run_id} has no decision {args.decision}")
        return EXIT_OK
    core.report.emit_json(report(run, active, _github(run, args.offline)))
    return EXIT_OK


def _github(run: Run, offline: bool) -> dict:
    """The report's `github` section: GitHub's open PRs for *run*'s repos, unless *offline*."""
    return batch.live.skipped() if offline else batch.live.view(run, cwd=os.getcwd())


def cmd_plan(args) -> int:
    # A standalone plan is a read: no namespace, nothing to drop. `run --plan`
    # from a saved file reads mergeStateStatus instead of namespaced refs.
    core.report.emit_json(core.serde.to_dict(batch.plan.build_plan(args.checkout)))
    return EXIT_OK


def write_report(run: Run, *, offline: bool) -> tuple[Path, dict]:
    """Write `report.json` and `report.md` under the run dir; return the path and document.

    Offline skips GitHub and `peek_tree`. A missing worktree is never probed.
    """
    github = _github(run, offline)
    entries = batch.session_report.sessions(run, tree=None if offline else _peek)
    doc = report(run, cli.pr_batch_common.is_active(run.id), github,
                 sessions=[e.to_dict() for e in entries])
    directory = batch.store.run_dir(run.id)
    directory.mkdir(parents=True, exist_ok=True)
    core.serde.write_json(directory / "report.json", doc)
    markdown = batch.report_md.render_markdown(doc)
    tmp = directory / "report.md.tmp"
    tmp.write_text(markdown)
    dest = directory / "report.md"
    core.serde.replace_file(tmp, dest)
    return dest, doc


def cmd_report(args) -> int:
    """`report`: write the files, then print stdout lines, JSON, or markdown."""
    run_id = cli.pr_batch_common.resolve_run_id(args.run_id)
    if not run_id:
        return cli.pr_batch_common.fail("no batch runs yet")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    path, doc = write_report(run, offline=args.offline)
    if args.format == "json":
        core.report.emit_json(doc)
        return EXIT_OK
    if args.format == "md":
        print(path.read_text(), end="")
        return EXIT_OK
    lines = batch.report_md.stdout_lines(doc)
    if lines:
        print(lines)
    print(f"report: {path}")
    return EXIT_OK


def _peek(item: Item):
    """`peek_tree` only when the worktree is a directory; never a relative probe."""
    wt = item.worktree
    if not (wt and Path(wt).is_dir()):
        return None
    return batch.publish.peek_tree(item)
