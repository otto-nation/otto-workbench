"""`pr batch` — rebase, fix CI, address comments and self-review across my open PRs.

    pr batch plan   --checkout DIR …           which PRs need which steps (JSON)
    pr batch run    --checkout DIR … [opts]    start a run; NDJSON events on stdout
    pr batch resume [RUN_ID]                   continue a waiting or interrupted run
    pr batch resolve RUN_ID DECISION_ID --action A [--reason/--body-file/--commit]
    pr batch cancel [RUN_ID] [--kill]          stop the run; a recorded cancel is final
    pr batch status [RUN_ID] [--full | --decision ID] [--offline]   the run report (JSON)
    pr batch next   [RUN_ID] [--offline]       only what needs action (JSON)

Every step runs drafted; the batch alone publishes (see batch.publish). --auto-publish answers an item's publish decision when it closes clean; --watch-ci re-checks CI once after a publish; --no-verify skips the pre-push hook on those publishes.

Exit 0 when a run is done or cancelled, 10 when it is waiting on decisions — waiting, not failed. resume never clears a recorded cancel. resolve and cancel write the run's inbox; a live holder that drains applies a queued request. resolve JSON always includes `applied` (true only when this call applied the decision). Queued is `applied` false plus `holder`; exit 0 is not "the decision closed".
"""

# doc-group: cli

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import batch.admission
import batch.events
import batch.inbox
import batch.live
import batch.plan
import batch.report
import batch.scheduler
import batch.store
import batch.targets
import config.workbench_config
import core.report
import core.run_lock
import core.serde
from batch.model import FINISHED_RUN, STEP_ORDER, Run, RunStatus, Step
from batch.requests import Cancel, Decide, Outcome
from batch.store import Role
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
run's logs/ (--verbose streams it as step_log too), and the last line of a
run that settles is `run_summary`: the run, its counts, and what needs action
next.

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
                        "and every drafted step is in STEPS and finished done. An owed closeout "
                        "counts as comments work. Steps never push while they run")
    r.add_argument("--prs", default="", help="Comma-separated repo#number keys to include")
    r.add_argument("--select", type=_selection, action="append", default=[],
                   metavar="KEY=STEPS", help="Run exactly these steps for one PR")
    r.add_argument("--watch-ci", action="store_true",
                   help="After a publish pushes, re-check CI once (ci-check --wait, up to "
                        "its 900s --wait-timeout per item) and reopen the item on red")
    r.add_argument("--no-verify", action="store_true",
                   help="Skip the pre-push hook on every publish push (CI still runs the "
                        "gates). Persisted on the run so resume and resolve honor it")
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

    c = sub.add_parser("cancel", help="Stop the run; a recorded cancel is final")
    c.add_argument("run_id", nargs="?", help="Run to cancel (default: the latest)")
    c.add_argument("--kill", action="store_true", help="Also terminate running steps")

    s = sub.add_parser("status", help="Print a run's report: what needs action, and why (JSON)")
    s.add_argument("run_id", nargs="?", help="Run to show (default: the latest)")
    view = s.add_mutually_exclusive_group()
    view.add_argument("--full", action="store_true",
                      help="Print the raw run state instead of the report")
    view.add_argument("--decision", default="", metavar="ID",
                      help="Print one decision with its full payload and log tail")
    _offline(s)

    n = sub.add_parser("next", help="Print only what needs action: run, counts and next (JSON)")
    n.add_argument("run_id", nargs="?", help="Run to show (default: the latest)")
    _offline(n)
    return parser


def _offline(p: argparse.ArgumentParser) -> None:
    p.add_argument("--offline", action="store_true",
                   help="Skip the `github` section: report only what this machine saved, "
                        "without asking GitHub for the run's repos' open PRs. Without it, a read "
                        "runs `git fetch` in every repo and a GraphQL search (slower, uses API quota), "
                        "even while a driver holds the run")


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


class _Reporter:
    """Where a `run`/`resume` session's drains report: NDJSON events, and a failure count.

    Called with `(run, outcome)` for every request the session applies, so it
    emits at once with no buffering. `counting` is off while the scheduler
    loop runs: a request the loop applies is reported as an event, as it
    always was, but only the entry drain, `finish`, and this process's
    hand-off decide the exit code — hand-off drains count because this
    process applied them.
    """

    def __init__(self) -> None:
        self.failed = 0
        self.counting = True

    def __call__(self, run: Run, outcome: Outcome) -> None:
        if self.counting and not outcome.ok:
            self.failed += 1
            print(f"pr batch: {outcome.error}", file=sys.stderr)
        batch.scheduler.emit_outcome(run, outcome, emit=batch.events.emit)


def _driver_inbox(bin_dir: Path, reporter: _Reporter) -> batch.inbox.Inbox:
    """The inbox a `run`/`resume` session drains with: resolve's real seams, outcomes to *reporter*."""
    return batch.inbox.Inbox(batch.inbox.ApplyContext(pr_bin=str(bin_dir / "pr"),
                                                      report=reporter))


def _active(run_id: str) -> bool:
    """Whether a scheduler is driving *run_id*: the live holder of its lock is a driver.

    An applier's brief hold is not a scheduler. A record with no role reads as
    a driver, which is what a held run lock meant before roles existed.
    """
    found = batch.store.holder(run_id)
    return found is not None and found.role in (Role.DRIVER, None)


def _report(run: Run, active: bool, github: dict | None = None) -> dict:
    """The whole report for *run*, with what its inbox still holds and what it rejected."""
    return batch.report.build(run, active=active,
                              pending_requests=batch.store.pending_requests(run.id),
                              failed_requests=batch.store.failed_requests(run.id),
                              github=github)


def _emit_summary(run: Run, code: int) -> None:
    """The last line of a settled run: `next`'s document as a `run_summary` event.

    Its `exit_hint` is *code*, the process's real exit status. Apply errors
    make it 1, whatever the run's status alone would say.
    """
    view = batch.report.next_view(_report(run, _active(run.id)))
    view["run"]["exit_hint"] = code
    batch.events.emit("run_summary", run=view["run"], counts=view["counts"], next=view["next"])


def _drive(session: batch.store.Session, reporter: _Reporter, *, bin_dir: Path, cfg,
           verbose: bool) -> RunStatus:
    """Drive *session*'s run until it blocks, then finish the session.

    The caller still holds every item's target, so publishes that the final
    drain applies stay narrowed to their items. Returns the status the
    scheduler stopped at.
    """
    scheduler = batch.scheduler.Scheduler(session, pr_bin=str(bin_dir / "pr"), cfg=cfg,
                                          verbose=verbose)
    reporter.counting = False
    stopped = scheduler.run_until_blocked()
    reporter.counting = True
    session.finish()
    return stopped


def _conclude(run_id: str, stopped: RunStatus, reporter: _Reporter) -> int:
    """The exit code and `run_summary`, from the run as last committed, after the session exited.

    `finish` or the hand-off can move the run past where the scheduler
    stopped: a cancel queued during the last tick turns WAITING into
    CANCELLED. The driver then emits `run_finished` for the new status before
    the summary. A request that failed in the session's own drains, including
    drains this process's hand-off ran, makes the code 1: those are applied
    by this process.
    """
    run = batch.store.load(run_id)
    if run.status is not stopped:
        batch.events.emit("run_finished", run=run.id, status=run.status.value,
                          open_decisions=len(run.open_decisions()),
                          ci_not_rechecked=batch.scheduler.ci_not_rechecked(run))
    code = 1 if reporter.failed else batch.report.exit_code(run.status)
    _emit_summary(run, code)
    return code


class _Refused(Exception):
    """A run or resume that may not start, and why. Printed as its refusal."""


@dataclass(frozen=True)
class _Started:
    """A run that has been planned, opened, item-locked and first committed, ready to drive."""

    session: batch.store.Session
    cfg: config.workbench_config.BatchConfig
    trail: Trail


def _busy_item(run, item, verb: str, exc: core.run_lock.LockBusy) -> str:
    """The refusal for *item*, whose target *exc* says another process holds."""
    action = "started" if verb == "run" else "resumed"
    hint = (" — wait for it, or leave the PR out with --prs" if verb == "run"
            else " — wait for it to finish")
    return f"run {run.id} not {action}: {item.key} is held by {exc.holder_text}{hint}"


def _hold_items(held: contextlib.ExitStack, run, verb: str) -> None:
    """Hold every item's target on *held*, sorted by path; raise _Refused if one is busy.

    All or nothing: one PR that another process is working on refuses the
    whole batch rather than silently dropping it, and the targets taken before
    it are released as the stack unwinds. An item with no `origin` has no
    target to hold. It is named on stderr, and its steps take their own locks.
    """
    targets = {}
    for item in run.items:
        target = batch.targets.item_target_dir(item)
        if target is None:
            print(f"pr batch: cannot name the pr target of {item.key} (no origin remote in "
                  f"{item.repo_dir}) — its steps lock it themselves", file=sys.stderr)
            continue
        targets[target] = item
    started = batch.store.now_iso()
    for target in sorted(targets):
        item = targets[target]
        try:
            held.enter_context(core.run_lock.hold(
                {core.run_lock.TARGET}, target_dir=target, target_label=item.key,
                command=f"pr batch {verb} {run.id}", started=started))
        except core.run_lock.LockBusy as exc:
            raise _Refused(_busy_item(run, item, verb, exc)) from exc


def _start_run(held: contextlib.ExitStack, args, plan, inbox: batch.inbox.Inbox) -> _Started:
    """Mint the run from *plan*, open it with `batch.store.create` on *held*, hold its items, commit.

    The run reaches disk only at that first commit, after every item's target
    is held. If the start is refused or fails before then, `create` leaves no
    directory behind. Raises _Refused when an item is held elsewhere. Any
    failure before the commit drops the plan's refs, since nothing else would
    record them to drop later.
    """
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
                                      watch_ci=args.watch_ci, no_verify=args.no_verify)
        session = held.enter_context(batch.store.create(run, inbox=inbox,
                                                        command=f"pr batch run {run.id}"))
        # The id exists only now. From here a batch refused the lock is told
        # which run to wait for or cancel, not only a pid.
        core.run_lock.note_holder(batch.store.BATCH_LOCK, run=run.id)
        _hold_items(held, run, "run")
        trail = Trail.start(script="pr-batch", context={"run": run.id}, record=True)
        run.trail_root = os.environ.get(TRAIL_ROOT_ENV, "")
        session.commit()
    except BaseException:
        batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace)
        raise
    return _Started(session, cfg, trail)


def _emit_started(run: Run) -> None:
    batch.events.emit("run_started", run=run.id, pool=run.pool, trail_root=run.trail_root,
                      steps=[s.value for s in run.steps],
                      auto_publish=[s.value for s in run.auto_publish])
    for item in run.items:
        batch.events.emit("item_queued", run=run.id, item=item.key,
                          steps=[s.step.value for s in item.steps])


def _cmd_run(args, bin_dir: Path) -> int:
    plan = _plan(args)
    reporter = _Reporter()
    # The run's own lock goes on `held` first, so it is released last. Every
    # item's target is released before the session's exit (release, then
    # hand-off), and a manual `pr` on one of these PRs is refused until then.
    with contextlib.ExitStack() as held:
        try:
            started = _start_run(held, args, plan, _driver_inbox(bin_dir, reporter))
        except _Refused as exc:
            return _err(str(exc))
        _emit_started(started.session.run)
        try:
            stopped = _drive(started.session, reporter, bin_dir=bin_dir, cfg=started.cfg,
                             verbose=args.verbose)
        finally:
            started.trail.finish()
    return _conclude(started.session.run.id, stopped, reporter)


def _try_resume(held: contextlib.ExitStack, session: batch.store.Session,
                reporter: _Reporter, bin_dir: Path, verbose: bool) -> RunStatus | _Refused:
    """Drive *session* or return the refusal; the caller raises after the session exits."""
    run = session.run
    if run.status in FINISHED_RUN:
        return _Refused(f"run {run.id} is {run.status.value}")
    try:
        core.run_lock.note_holder(batch.store.BATCH_LOCK, run=run.id)
        _hold_items(held, run, "resume")
    except _Refused as exc:
        return exc
    batch.scheduler.mark_interrupted(run, emit=batch.events.emit)
    os.environ.setdefault(TRAIL_ROOT_ENV, run.trail_root)
    return _drive(session, reporter, bin_dir=bin_dir,
                  cfg=_cfg([i.repo_dir for i in run.items]), verbose=verbose)


def _resume(run_id: str, reporter: _Reporter, bin_dir: Path, verbose: bool) -> RunStatus:
    """Open *run_id* as its driver and drive it: the body of `pr batch resume`.

    The session's entry drain applies whatever is queued, cancels first,
    before anything here looks at the run. So a queued cancel has already
    made the run CANCELLED, and the resume is refused. Nothing is emitted
    before the run is open. A refusal (finished, or an item held elsewhere)
    leaves the run as the entry drain committed it. Events the entry drain
    already emitted describe that committed work. The refusal is raised after
    the session exits, so `finish` and the hand-off still run.
    """
    with contextlib.ExitStack() as held:
        session = held.enter_context(batch.store.session(
            run_id, Role.DRIVER, inbox=_driver_inbox(bin_dir, reporter),
            command=f"pr batch resume {run_id}"))
        result = _try_resume(held, session, reporter, bin_dir, verbose)
    if isinstance(result, _Refused):
        raise result
    return result


def _cmd_resume(args, bin_dir: Path) -> int:
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch run to resume")
    reporter = _Reporter()
    try:
        stopped = _resume(run_id, reporter, bin_dir, args.verbose)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    except (batch.store.RunBusy, batch.store.RunUnreachable, _Refused) as exc:
        return _err(str(exc))
    return _conclude(run_id, stopped, reporter)


def _unreachable(run_id: str) -> str:
    return (f"run {run_id} is unreachable; the request is in its inbox and "
            f"will be applied by the next session to open it")


def _deliver(run_id: str, request: Decide | Cancel, bin_dir: Path,
             verb: str) -> batch.inbox.Delivery:
    """`batch.inbox.deliver`, naming on stderr each earlier request the same drain failed."""
    delivery = batch.inbox.deliver(run_id, request,
                                   batch.inbox.ApplyContext(pr_bin=str(bin_dir / "pr")),
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


@dataclass(frozen=True)
class _HolderView:
    """The queued-resolve `holder` object."""

    role: str | None
    pid: int
    command: str


def _holder_view(holder: batch.store.Holder | None) -> dict | None:
    """The queued-resolve `holder` object, or None when the delivery named nobody."""
    if holder is None:
        return None
    return asdict(_HolderView(None if holder.role is None else holder.role.value,
                              holder.pid, holder.command))


def _cmd_resolve(args, bin_dir: Path) -> int:
    run_id = args.run_id
    try:
        batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    request = Decide(decision=args.decision_id, action=args.action, reason=args.reason,
                     body_file=args.body_file, commit=args.commit)
    try:
        delivery = _deliver(run_id, request, bin_dir, "resolve")
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    except batch.store.RunUnreachable:
        return _err(_unreachable(run_id))
    if delivery.queued:
        result: dict = {"applied": False, "queued": True, "decision": args.decision_id}
        if view := _holder_view(delivery.holder):
            result["holder"] = view
        core.report.emit_json(result)
        return EXIT_OK
    error = _own_error(run_id, delivery)
    if delivery.own is None and not error:
        error = "the drain did not report this request"
    applied = delivery.own is not None and not error
    result = {"applied": applied, "decision": args.decision_id,
              "open_decisions": len(batch.store.load(run_id).open_decisions())}
    if error:
        result["errors"] = [error]
        print(f"pr batch: {error}", file=sys.stderr)
    core.report.emit_json(result)
    return EXIT_OK if applied else 1


def _cmd_cancel(args, bin_dir: Path) -> int:
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch run to cancel")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    if run.status in FINISHED_RUN:
        return _err(f"run {run_id} is {run.status.value}")
    try:
        delivery = _deliver(run_id, Cancel(kill=args.kill), bin_dir, "cancel")
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    except batch.store.RunUnreachable:
        return _err(_unreachable(run_id))
    error = "" if delivery.queued else _own_error(run_id, delivery)
    result = {"cancelled": not error, "run": run_id, "queued": delivery.queued}
    if error:
        # The run finished before the cancel reached it; say what it ended as.
        result["status"] = batch.store.load(run_id).status.value
        print(f"pr batch: {error}", file=sys.stderr)
    core.report.emit_json(result)
    return 1 if error else EXIT_OK


def _cmd_status(args) -> int:
    """`status` and `next`: the report, a projection of it, or what it summarises."""
    run_id = _run_id(args.run_id)
    if not run_id:
        return _err("no batch runs yet")
    try:
        run = batch.store.load(run_id)
    except batch.store.RunNotFound:
        return _err(f"no run {run_id}")
    active = _active(run_id)
    if args.command == "next":
        core.report.emit_json(batch.report.next_view(_report(run, active, _github(run, args))))
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
    core.report.emit_json(_report(run, active, _github(run, args)))
    return EXIT_OK


def _github(run: Run, args) -> dict:
    """The report's `github` section: GitHub's open PRs for *run*'s repos, unless --offline."""
    return batch.live.skipped() if args.offline else batch.live.view(run, cwd=os.getcwd())


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
            return _cmd_cancel(args, bin_dir)
        # status and next
        return _cmd_status(args)
    except batch.plan.PlanError as exc:
        return _err(str(exc))
