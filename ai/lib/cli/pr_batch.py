"""`pr batch` — run rebase, comments and self-review across my open PRs.

    pr batch plan   --checkout DIR …           which PRs need which steps (JSON)
    pr batch run    --checkout DIR … [opts]    start a run; NDJSON events on stdout
    pr batch resume [RUN_ID]                   continue a waiting or interrupted run
    pr batch resolve RUN_ID DECISION_ID --action A [--reason/--body-file/--commit]
    pr batch cancel [RUN_ID] [--kill]
    pr batch status [RUN_ID]

Exit 0 when a run is done or cancelled, 10 when it is waiting on decisions.
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
import batch.resolve
import batch.scheduler
import batch.store
import config.workbench_config
import core.report
import core.run_lock
import core.serde
from batch.model import STEP_ORDER, RunStatus, Step
from core.trail import TRAIL_ROOT_ENV, Trail

EXIT_OK = 0
EXIT_WAITING = 10
_STATUS_EXIT = {RunStatus.DONE: EXIT_OK, RunStatus.CANCELLED: EXIT_OK,
                RunStatus.WAITING: EXIT_WAITING}


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr batch", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="Show which PRs need which steps")
    p.add_argument("--checkout", action="append", required=True, metavar="DIR")

    r = sub.add_parser("run", help="Start a run")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--checkout", action="append", metavar="DIR")
    src.add_argument("--plan", metavar="FILE", help="A saved `pr batch plan` document")
    r.add_argument("--steps", type=_steps, default=list(STEP_ORDER))
    r.add_argument("--pool", type=int, default=None, help="Concurrency ceiling")
    r.add_argument("--auto-publish", type=_steps, default=[])
    r.add_argument("--prs", default="", help="Comma-separated repo#number keys to include")
    r.add_argument("--select", type=_selection, action="append", default=[],
                   metavar="KEY=STEPS", help="Run exactly these steps for one PR")

    sub.add_parser("resume", help="Continue a run").add_argument("run_id", nargs="?")

    v = sub.add_parser("resolve", help="Answer one decision")
    v.add_argument("run_id")
    v.add_argument("decision_id")
    v.add_argument("--action", required=True)
    v.add_argument("--reason", default="")
    v.add_argument("--body-file", default="")
    v.add_argument("--commit", default="")

    c = sub.add_parser("cancel", help="Stop starting new steps")
    c.add_argument("run_id", nargs="?")
    c.add_argument("--kill", action="store_true", help="Also terminate running steps")

    sub.add_parser("status", help="Print a run's state").add_argument("run_id", nargs="?")
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


def _held_drive(run, bin_dir: Path, cfg) -> int:
    status = batch.scheduler.Scheduler(
        run, pr_bin=str(bin_dir / "pr"), cfg=cfg).run_until_blocked()
    errors = _apply_loaded(run, bin_dir)
    batch.store.save(run)
    if run.status is RunStatus.DONE:
        status = RunStatus.DONE
    if errors:
        _report_apply_errors(errors)
        return 1
    return _STATUS_EXIT.get(status, 1)


def _drive(run, *, bin_dir: Path, cfg) -> int:
    started = batch.store.now_iso()
    try:
        with core.run_lock.acquire(batch.store.run_dir(run.id), "batch", started):
            return _held_drive(run, bin_dir, cfg)
    except core.run_lock.LockBusy as exc:
        core.run_lock.report_busy(exc)
        return 1


def _cmd_run(args, bin_dir: Path) -> int:
    active = batch.store.active_run_id()
    if active:
        return _err(f"run {active} is still active; resolve its decisions or cancel it first")
    plan = _plan(args)
    keys = {k for k in args.prs.split(",") if k}
    rows = [r for r in plan.rows if not keys or r.key in keys]
    selected = {s.key: s.steps for s in args.select} if args.select else None
    dirs = sorted({r.repo_dir for r in rows}) or (args.checkout or [])
    cfg = _cfg(dirs)
    run = batch.scheduler.new_run(rows, steps=args.steps, selected=selected,
                                  pool=batch.admission.ceiling(args.pool, cfg),
                                  auto_publish=args.auto_publish,
                                  now=datetime.now(timezone.utc))
    trail = Trail.start(script="pr-batch", context={"run": run.id}, record=True)
    run.trail_root = os.environ.get(TRAIL_ROOT_ENV, "")
    batch.store.save(run)
    batch.events.emit("run_started", run=run.id, pool=run.pool, trail_root=run.trail_root,
                steps=[s.value for s in run.steps],
                auto_publish=[s.value for s in run.auto_publish])
    for item in run.items:
        batch.events.emit("item_queued", run=run.id, item=item.key,
                    steps=[s.step.value for s in item.steps])
    try:
        return _drive(run, bin_dir=bin_dir, cfg=cfg)
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
    return _drive(run, bin_dir=bin_dir, cfg=_cfg([i.repo_dir for i in run.items]))


def _apply_pending(run_id: str, bin_dir: Path, decision_id: str) -> int:
    try:
        with core.run_lock.acquire(batch.store.run_dir(run_id), "batch-resolve",
                                   batch.store.now_iso()):
            run = batch.store.load(run_id)
            errors = _apply_loaded(run, bin_dir)
            batch.store.save(run)
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
    # A lock that drops between the two checks is applied, not left queued.
    if core.run_lock.is_held(held) and core.run_lock.is_held(held):
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
        run.status = RunStatus.CANCELLED
        batch.store.save(run)
    core.report.emit_json({"cancelled": True, "run": run_id})
    return EXIT_OK


def _cmd_status(args) -> int:
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch runs yet")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    core.report.emit_json({"schema_version": run.schema_version,
                           "active": core.run_lock.is_held(batch.store.run_dir(run_id)),
                           "run": core.serde.to_dict(run)})
    return EXIT_OK


def cmd_batch(argv: list[str], ctx, *, bin_dir: Path, schema_version: str | None = None,
              **_kw) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            core.report.emit_json(core.serde.to_dict(batch.plan.build_plan(args.checkout)))
            return EXIT_OK
        if args.command == "run":
            return _cmd_run(args, bin_dir)
        if args.command == "resume":
            return _cmd_resume(args, bin_dir)
        if args.command == "resolve":
            return _cmd_resolve(args, bin_dir)
        if args.command == "cancel":
            return _cmd_cancel(args)
        return _cmd_status(args)
    except batch.plan.PlanError as exc:
        return _err(str(exc))
