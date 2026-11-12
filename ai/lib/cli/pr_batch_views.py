"""Read-only `pr batch` views: plan, status and report.

Moved out of `cli.pr_batch` so the dispatcher stays under the file-size cap.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
import tempfile
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


def run_list() -> dict:
    """Every saved run on this machine, newest first: what a picker needs, not the runs themselves."""
    runs = []
    for run_id in reversed(batch.store.run_ids()):
        entry = {"id": run_id, "run_dir": str(batch.store.run_dir(run_id))}
        try:
            run = batch.store.load(run_id)
        except (batch.store.RunNotFound, ValueError, TypeError) as err:
            # A state file that vanished, no longer parses, or no longer fits the Run shape is still a
            # run directory; say so rather than dropping it from the list.
            runs.append({**entry, "error": str(err) or type(err).__name__})
            continue
        runs.append({**entry, "started_at": run.started_at, "status": run.status.value,
                     "active": cli.pr_batch_common.is_active(run_id),
                     "items": len(run.items), "repos": sorted({i.repo for i in run.items})})
    return {"schema_version": 2, "runs": runs}


def cmd_status(args) -> int:
    """`status`: the run report, or the raw state with `--full`."""
    if args.list:
        if args.run_id or args.full or args.offline:
            return cli.pr_batch_common.fail("--list takes no run id, no --full and no --offline")
        core.report.emit_json(run_list())
        return EXIT_OK
    run_id = cli.pr_batch_common.resolve_run_id(args.run_id)
    if not run_id:
        return cli.pr_batch_common.fail("no batch runs yet")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    active = cli.pr_batch_common.is_active(run_id)
    if args.full:
        core.report.emit_json({"schema_version": run.schema_version, "active": active,
                               # Where the run's state.json and logs live, so a
                               # consumer can watch it without deriving the path.
                               # Optional, so the schema does not bump.
                               "run_dir": str(batch.store.run_dir(run.id)),
                               "run": core.serde.to_dict(run)})
        return EXIT_OK
    core.report.emit_json(report(run, active, _github(run, args.offline)))
    return EXIT_OK


def _github(run: Run, offline: bool) -> dict:
    """The report's `github` section: GitHub's open PRs for *run*'s repos, unless *offline*."""
    return batch.live.skipped() if offline else batch.live.view(run, cwd=os.getcwd())


def cmd_plan(args) -> int:
    # A standalone plan is a read: no namespace, nothing to drop. `run --plan`
    # from a saved file reads mergeStateStatus instead of namespaced refs.
    steps = frozenset(args.needs) if args.needs else None
    core.report.emit_json(core.serde.to_dict(batch.plan.build_plan(args.checkout, steps)))
    return EXIT_OK


def write_report(run: Run, *, offline: bool) -> tuple[Path | None, dict]:
    """Write `report.json` and `report.md` under the run dir; return the path and document.

    Offline skips GitHub and `peek_tree`; the tree is read from local objects.
    A missing worktree is never probed. An active run returns the document
    without writing files, so a live driver is not racing a report rewrite.
    """
    github = _github(run, offline)
    reader = batch.publish.local_tree if offline else batch.publish.peek_tree
    entries = batch.session_report.sessions(run, tree=lambda item: _tree(item, reader))
    active = cli.pr_batch_common.is_active(run.id)
    doc = report(run, active, github, sessions=[e.to_dict() for e in entries])
    if active:
        return None, doc
    directory = batch.store.run_dir(run.id)
    directory.mkdir(parents=True, exist_ok=True)
    core.serde.write_json(directory / "report.json", doc)
    markdown = batch.report_md.render_markdown(doc)
    dest = directory / "report.md"
    fd, tmp = tempfile.mkstemp(dir=directory, prefix="report.md.", suffix=".tmp")
    try:
        try:
            handle = os.fdopen(fd, "w")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(markdown)
        core.serde.replace_file(Path(tmp), dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
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
        print(batch.report_md.render_markdown(doc), end="")
        return EXIT_OK
    lines = batch.report_md.stdout_lines(doc)
    if lines:
        print(lines)
    if path is not None:
        print(f"report: {path}")
    return EXIT_OK


def _tree(item: Item, reader):
    """*reader* only when the worktree is a directory; never a relative probe."""
    wt = item.worktree
    if not (wt and Path(wt).is_dir()):
        return None
    return reader(item)
