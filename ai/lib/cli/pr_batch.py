"""`pr batch` — rebase, fix CI, address comments and self-review across my open PRs.

    pr batch plan   --checkout DIR …           which PRs need which steps (JSON)
    pr batch run    --checkout DIR … [opts]    start a run; NDJSON events on stdout
    pr batch resume [RUN_ID]                   continue a waiting or interrupted run
    pr batch resolve RUN_ID DECISION_ID --action A [--reason/--body-file/--commit]
    pr batch cancel [RUN_ID] [--kill]
    pr batch status [RUN_ID] [--full | --decision ID]   the run report (JSON)
    pr batch next   [RUN_ID]                   only what needs action (JSON)

Every step runs drafted; the batch alone publishes (see batch.publish). --auto-publish answers an item's publish decision when it closes clean; --watch-ci re-checks CI once after a publish.

Exit 0 when a run is done or cancelled, 10 when it is waiting on decisions — waiting, not failed.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import batch.admission
import batch.events
import batch.plan
import batch.report
import batch.resolve
import batch.scheduler
import batch.store
import config.workbench_config
import core.report
import core.run_lock
import core.serde
from batch.model import STEP_ORDER, RunStatus, Step
from core.trail import TRAIL_ROOT_ENV, Trail

EXIT_OK = batch.report.EXIT_OK


def _steps(text: str) -> list[Step]:
    try:
        return [Step(s.strip()) for s in text.split(",") if s.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


@dataclass(frozen=True)
class Selection:
    key: str
    steps: list[Step]


def _selection(text: str) -> Selection:
    key, _, steps = text.partition("=")
    if not key or not steps:
        raise argparse.ArgumentTypeError(f"--select wants KEY=STEPS, got {text!r}")
    return Selection(key, _steps(steps))


_RUN_EPILOG = """\
recipes:
  pr batch run --checkout DIR --steps rebase,ci   rebase and fix CI where needed
  pr batch run --checkout DIR                     every step, where needed

`run` plans for itself — no `pr batch plan` first. It is long-running and
streams NDJSON events; start it as a background job. Step output stays in the
run's logs/ (--verbose streams it as step_log too), and the last line is
always `run_summary`: the run, its counts, and what needs action next.

Nothing is pushed until a publish decision is answered. Exit 10 means the
run is waiting on you, not that it failed: each `next` entry (also printed
by `pr batch next`) says why, names any prep, and lists the
`pr batch resolve RUN_ID DECISION_ID --action …` commands that answer it;
`pr batch resume` continues. --auto-publish STEPS answers clean publishes
without asking.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr batch", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Show which PRs need which steps")
    p.add_argument("--checkout", action="append", required=True, metavar="DIR",
                   help="A checkout whose open PRs to plan for; repeatable")

    r = sub.add_parser("run", help="Start a run", epilog=_RUN_EPILOG,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--checkout", action="append", metavar="DIR",
                     help="A checkout whose open PRs to run on; repeatable")
    src.add_argument("--plan", metavar="FILE", help="A saved `pr batch plan` document")
    r.add_argument("--steps", type=_steps, default=list(STEP_ORDER), metavar="STEPS",
                   help=f"Comma-separated steps, any of {','.join(STEP_ORDER)} (default: all). "
                        "Each runs only on PRs the plan marks as needing it")
    r.add_argument("--pool", type=int, default=None, help="Concurrency ceiling")
    r.add_argument("--auto-publish", type=_steps, default=[], metavar="STEPS",
                   help="Publish an item without asking when it closes with no open decision "
                        "and every drafted step is in STEPS and finished done. Steps never "
                        "push while they run")
    r.add_argument("--prs", default="", help="Comma-separated repo#number keys to include")
    r.add_argument("--select", type=_selection, action="append", default=[],
                   metavar="KEY=STEPS", help="Run exactly these steps for one PR")
    r.add_argument("--watch-ci", action="store_true",
                   help="After a publish pushes, re-check CI once (ci-check --wait, up to "
                        "its 900s --wait-timeout per item) and reopen the item on red")
    r.add_argument("--verbose", action="store_true",
                   help="Also stream each step's stderr as step_log events "
                        "(it is always in the run's logs/)")

    m = sub.add_parser("resume", help="Continue a run")
    m.add_argument("run_id", nargs="?", help="Run to continue (default: the latest)")
    m.add_argument("--verbose", action="store_true",
                   help="Also stream each step's stderr as step_log events")

    v = sub.add_parser("resolve", help="Answer one decision")
    v.add_argument("run_id", help="Run the decision belongs to")
    v.add_argument("decision_id", help="Decision to answer")
    v.add_argument("--action", required=True,
                   help="The answer; which actions apply depends on the decision's kind")
    v.add_argument("--reason", default="", help="Why, recorded with the answer")
    v.add_argument("--body-file", default="", help="File holding a reply body, for actions that post one")
    v.add_argument("--commit", default="", help="Commit that settles the decision, for actions that cite one")

    c = sub.add_parser("cancel", help="Stop starting new steps")
    c.add_argument("run_id", nargs="?", help="Run to cancel (default: the latest)")
    c.add_argument("--kill", action="store_true", help="Also terminate running steps")

    s = sub.add_parser("status", help="Print a run's report: what needs action, and why (JSON)")
    s.add_argument("run_id", nargs="?", help="Run to show (default: the latest)")
    view = s.add_mutually_exclusive_group()
    view.add_argument("--full", action="store_true",
                      help="Print the raw run state instead of the report")
    view.add_argument("--decision", default="", metavar="ID",
                      help="Print one decision with its full payload and log tail")

    sub.add_parser("next", help="Print only what needs action: run, counts and next (JSON)"
                   ).add_argument("run_id", nargs="?", help="Run to show (default: the latest)")
    return parser


def _err(message: str) -> int:
    print(f"pr batch: {message}", file=sys.stderr)
    return 1


def _run_id(given: str | None) -> str | None:
    return given or batch.store.latest_run_id()


def _cfg(dirs: list[str]):
    return config.workbench_config.load_config_or_default(dirs[0] if dirs else None).batch


def _plan(args) -> batch.plan.Plan:
    if args.plan:
        return core.serde.from_dict(batch.plan.Plan, json.loads(Path(args.plan).read_text()))
    return batch.plan.build_plan(args.checkout)


def _save(run) -> None:
    """Persist *run*, then drop its refs if it has ended.

    Saving first means a cleanup that fails can never leave the stored run
    short of the terminal status it reached.
    """
    batch.store.save(run)
    if run.status in (RunStatus.DONE, RunStatus.CANCELLED):
        batch.plan.drop_refs(run.ref_dirs, run.ref_namespace)


def _apply_loaded(run, bin_dir: Path) -> list[str]:
    errors = []
    pr_bin = str(bin_dir / "pr")
    for raw in batch.store.take_requests(run.id):
        if isinstance(raw, dict) and raw.get("error") and "decision" not in raw:
            errors.append(raw["error"])
            continue
        try:
            batch.resolve.apply(run, batch.resolve.Request.from_dict(raw), pr_bin=pr_bin)
        except (batch.resolve.ResolveError, KeyError, TypeError) as exc:
            errors.append(str(exc))
    if (run.status in (RunStatus.WAITING, RunStatus.RUNNING)
            and run.items and all(item.terminal for item in run.items)):
        run.status = RunStatus.DONE
    return errors


def _report_apply_errors(errors: list[str]) -> None:
    for message in errors:
        print(f"pr batch: {message}", file=sys.stderr)


def _held_drive(run, bin_dir: Path, cfg, verbose: bool) -> int:
    status = batch.scheduler.Scheduler(
        run, pr_bin=str(bin_dir / "pr"), cfg=cfg, verbose=verbose).run_until_blocked()
    errors = _apply_loaded(run, bin_dir)
    _save(run)
    if run.status is RunStatus.DONE:
        status = RunStatus.DONE
    if errors:
        _report_apply_errors(errors)
        return 1
    return batch.report.exit_code(status)


def _emit_summary(run, code: int) -> None:
    """The last line of a settled run: `next`'s document as a `run_summary` event.

    Its `exit_hint` is *code*, the process's real exit status — apply errors
    make it 1 whatever the run's status alone would say.
    """
    view = batch.report.next_view(batch.report.build(
        run, active=core.run_lock.is_held(batch.store.run_dir(run.id))))
    view["run"]["exit_hint"] = code
    batch.events.emit("run_summary", run=view["run"], counts=view["counts"], next=view["next"])


def _drive(run, *, bin_dir: Path, cfg, verbose: bool = False) -> int:
    started = batch.store.now_iso()
    try:
        with core.run_lock.acquire(batch.store.run_dir(run.id), "batch", started):
            code = _held_drive(run, bin_dir, cfg, verbose)
    except core.run_lock.LockBusy as exc:
        core.run_lock.report_busy(exc)
        return 1
    # After the lock is released, so the summary reports the run as the
    # reader's to act on and lists what it is waiting for.
    _emit_summary(run, code)
    return code


def _cmd_run(args, bin_dir: Path) -> int:
    active = batch.store.active_run_id()
    if active:
        return _err(f"run {active} is still active; resolve its decisions or cancel it first")
    plan = _plan(args)
    try:
        keys = {k for k in args.prs.split(",") if k}
        rows = [r for r in plan.rows if not keys or r.key in keys]
        selected = {s.key: s.steps for s in args.select} if args.select else None
        dirs = sorted({r.repo_dir for r in rows}) or (args.checkout or [])
        cfg = _cfg(dirs)
        run = batch.scheduler.new_run(rows, steps=args.steps, selected=selected,
                                      pool=batch.admission.ceiling(args.pool, cfg),
                                      auto_publish=args.auto_publish,
                                      now=datetime.now(timezone.utc),
                                      ref_namespace=plan.ref_namespace, ref_dirs=plan.ref_dirs,
                                      watch_ci=args.watch_ci)
        trail = Trail.start(script="pr-batch", context={"run": run.id}, record=True)
        run.trail_root = os.environ.get(TRAIL_ROOT_ENV, "")
        batch.store.save(run)
    except BaseException:
        # Until the run is saved nothing records the plan's refs, so nothing
        # would ever drop them.
        batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace)
        raise
    batch.events.emit("run_started", run=run.id, pool=run.pool, trail_root=run.trail_root,
                steps=[s.value for s in run.steps],
                auto_publish=[s.value for s in run.auto_publish])
    for item in run.items:
        batch.events.emit("item_queued", run=run.id, item=item.key,
                    steps=[s.step.value for s in item.steps])
    try:
        return _drive(run, bin_dir=bin_dir, cfg=cfg, verbose=args.verbose)
    finally:
        trail.finish()


def _cmd_resume(args, bin_dir: Path) -> int:
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch run to resume")
    if core.run_lock.is_held(batch.store.run_dir(run_id)):
        return _err(f"run {run_id} is already running")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    if run.status in (RunStatus.DONE, RunStatus.CANCELLED):
        return _err(f"run {run_id} is {run.status.value}")
    batch.store.clear_cancel(run_id)
    batch.scheduler.mark_interrupted(run, emit=batch.events.emit)
    os.environ.setdefault(TRAIL_ROOT_ENV, run.trail_root)
    return _drive(run, bin_dir=bin_dir, cfg=_cfg([i.repo_dir for i in run.items]),
                  verbose=args.verbose)


def _apply_pending(run_id: str, bin_dir: Path, decision_id: str) -> int:
    try:
        with core.run_lock.acquire(batch.store.run_dir(run_id), "batch-resolve",
                                   batch.store.now_iso()):
            run = batch.store.load(run_id)
            errors = _apply_loaded(run, bin_dir)
            _save(run)
    except batch.store.RunNotFound as exc:
        return _err(str(exc))
    except core.run_lock.LockBusy:
        core.report.emit_json({"queued": True, "decision": decision_id})
        return EXIT_OK
    result = {"applied": not errors, "decision": decision_id,
              "open_decisions": len(run.open_decisions())}
    if errors:
        result["errors"] = errors
        _report_apply_errors(errors)
        core.report.emit_json(result)
        return 1
    core.report.emit_json(result)
    return EXIT_OK


def _cmd_resolve(args, bin_dir: Path) -> int:
    try:
        batch.store.load(args.run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {args.run_id}")
    request = {"decision": args.decision_id, "action": args.action, "reason": args.reason,
               "body_file": args.body_file, "commit": args.commit}
    batch.store.write_request(args.run_id, request)
    held = batch.store.run_dir(args.run_id)
    # A request queued while a scheduler holds the lock is applied by that
    # scheduler or drained by its _drive after the loop, or by the next
    # resolve/resume.
    if core.run_lock.is_held(held):
        core.report.emit_json({"queued": True, "decision": args.decision_id})
        return EXIT_OK
    return _apply_pending(args.run_id, bin_dir, args.decision_id)


def _cmd_cancel(args) -> int:
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch run to cancel")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    if run.status in (RunStatus.DONE, RunStatus.CANCELLED):
        return _err(f"run {run_id} is {run.status.value}")
    batch.store.request_cancel(run_id, kill=args.kill)
    if not core.run_lock.is_held(batch.store.run_dir(run_id)):
        # Reload: the scheduler may have finished and saved its own outcome in
        # the window since `run` was loaded above. Stamping that stale copy
        # CANCELLED here would clobber a completed run's real result.
        run = batch.store.load(run_id)
        if run.status not in (RunStatus.DONE, RunStatus.CANCELLED):
            run.status = RunStatus.CANCELLED
            _save(run)
    core.report.emit_json({"cancelled": True, "run": run_id})
    return EXIT_OK


def _cmd_status(args) -> int:
    """`status` and `next`: the report, a projection of it, or what it summarises."""
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch runs yet")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    active = core.run_lock.is_held(batch.store.run_dir(run_id))
    if args.command == "next":
        core.report.emit_json(batch.report.next_view(batch.report.build(run, active=active)))
        return EXIT_OK
    if args.full:
        core.report.emit_json({"schema_version": run.schema_version, "active": active,
                               "run": core.serde.to_dict(run)})
        return EXIT_OK
    if args.decision:
        try:
            core.report.emit_json(batch.report.decision_detail(run, args.decision))
        except KeyError:
            return _err(f"run {run_id} has no decision {args.decision}")
        return EXIT_OK
    core.report.emit_json(batch.report.build(run, active=active))
    return EXIT_OK


def _cmd_plan(args) -> int:
    plan = batch.plan.build_plan(args.checkout)
    try:
        core.report.emit_json(core.serde.to_dict(plan))
    finally:
        # Nothing runs from a standalone plan, so its refs go now, even
        # when the reader has gone; a saved plan passed to `run --plan`
        # reads mergeStateStatus instead.
        batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace)
    return EXIT_OK


def cmd_batch(argv: list[str], ctx, *, bin_dir: Path, schema_version: str | None = None,
              **_kw) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            return _cmd_plan(args)
        if args.command == "run":
            return _cmd_run(args, bin_dir)
        if args.command == "resume":
            return _cmd_resume(args, bin_dir)
        if args.command == "resolve":
            return _cmd_resolve(args, bin_dir)
        if args.command == "cancel":
            return _cmd_cancel(args)
        # status and next
        return _cmd_status(args)
    except batch.plan.PlanError as exc:
        return _err(str(exc))
