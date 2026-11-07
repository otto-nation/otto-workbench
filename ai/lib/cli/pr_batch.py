"""`pr batch` — rebase, fix CI, address comments and self-review across my open PRs.

    pr batch plan   --checkout DIR …           which PRs need which steps (JSON)
    pr batch run    --checkout DIR … [opts]    start a run; NDJSON events on stdout
    pr batch resume [RUN_ID]                   continue a waiting or interrupted run
    pr batch resolve RUN_ID DECISION_ID --action A [--reason/--body-file/--commit]
    pr batch cancel [RUN_ID] [--kill]          stop the run; a recorded cancel is final
    pr batch release RUN_ID PR [--kill]        drop one PR; frees its locks, leaves the worktree
    pr batch status [RUN_ID] [--full | --decision ID] [--offline]   the run report (JSON)
    pr batch next   [RUN_ID] [--offline]       only what needs action (JSON)
    pr batch report [RUN_ID] [--offline] [--format json|md]         per-PR session commands

Omitting RUN_ID on resume/cancel picks this checkout's newest unfinished covering run, else its newest covering, and refuses when none covers this repo or more than one unfinished run does. status/next fall back to the newest run on the machine and say so on stderr.

Every step runs drafted; the batch alone publishes (see batch.publish). --auto-publish answers an item's publish decision when it closes clean; --no-verify skips the pre-push hook on those publishes.

Exit 0 when a run is done or cancelled, 10 when it is waiting on decisions — waiting, not failed. resume never clears a recorded cancel. resolve, cancel and release write the run's inbox; a live holder that drains applies a queued request. resolve JSON always includes `applied` (true only when this call applied the decision). Queued is `applied` false plus `holder`; exit 0 is not "the decision closed".
"""

# doc-group: cli

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import batch.admission
import batch.events
import batch.inbox
import batch.machine
import batch.plan
import batch.report
import batch.scheduler
import batch.store
import batch.targets
import config.workbench_config
import core.log
import core.run_lock
import core.serde
import review.focus
from batch.model import FINISHED_RUN, STEP_ORDER, Item, Run, RunStatus, Step, StepStatus
from batch.requests import Outcome
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
  pr batch run --checkout DIR --steps review --focus "…"   review with a focus
  pr batch resume --steps rebase,ci,comments      continue without re-running review

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

_DEFAULT_RUN = (
    "this repo's newest unfinished covering run, else its newest covering, else the latest"
)
_DEFAULT_RUN_COVERING = (
    "this repo's newest unfinished covering run, else its newest covering"
)
_DEFAULT_RUN_REFUSES = (
    f"{_DEFAULT_RUN_COVERING}; refused when several unfinished runs cover this repo"
)


def build_parser() -> argparse.ArgumentParser:
    import cli.pr_batch_views
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
    r.add_argument("--no-verify", action="store_true",
                   help="Skip the pre-push hook on every publish push (CI still runs the "
                        "gates). Persisted on the run so resume and resolve honor it")
    review.focus.add_focus_args(r)
    r.add_argument("--verbose", action="store_true",
                   help="Also stream each step's stderr as step_log events "
                        "(it is always in the run's logs/)")

    m = sub.add_parser("resume", help="Continue a run")
    m.add_argument("run_id", nargs="?", help=f"Run to continue (default: {_DEFAULT_RUN_REFUSES})")
    m.add_argument("--steps", type=_steps, default=None, metavar="STEPS",
                   help="Narrow the run to these steps (a subset of the run's). "
                        "Each excluded pending step becomes skipped; a later commit "
                        "does not re-queue a dropped review. Adding a step is refused")
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
    c.add_argument("run_id", nargs="?", help=f"Run to cancel (default: {_DEFAULT_RUN_REFUSES})")
    c.add_argument("--kill", action="store_true", help="Also terminate running steps")

    g = sub.add_parser("release", help="Free one PR from a run without cancelling it")
    g.add_argument("run_id", help="Run holding the PR")
    g.add_argument("pr", help="Item key owner/repo#N, or a PR number unique in the run")
    g.add_argument("--kill", action="store_true",
                   help="Also terminate the item's running step")

    s = sub.add_parser("status", help="Print a run's report: what needs action, and why (JSON)")
    s.add_argument("run_id", nargs="?", help=f"Run to show (default: {_DEFAULT_RUN})")
    view = s.add_mutually_exclusive_group()
    view.add_argument("--full", action="store_true",
                      help="Print the raw run state instead of the report")
    view.add_argument("--decision", default="", metavar="ID",
                      help="Print one decision with its full payload and log tail")
    cli.pr_batch_views.offline(s)

    n = sub.add_parser("next", help="Print only what needs action: run, counts and next (JSON)")
    n.add_argument("run_id", nargs="?", help=f"Run to show (default: {_DEFAULT_RUN})")
    cli.pr_batch_views.offline(n)

    p = sub.add_parser("report", help="Print per-PR session commands for a run")
    p.add_argument("run_id", nargs="?", help=f"Run to show (default: {_DEFAULT_RUN})")
    p.add_argument("--format", choices=("json", "md"), default="",
                   help="Print JSON or markdown (default: one line per PR, then the report path)")
    cli.pr_batch_views.offline(p)
    return parser


def _cfg(dirs: list[str]):
    return config.workbench_config.load_config_or_default(dirs[0] if dirs else None).batch


def _plan(args) -> batch.plan.Plan:
    if args.plan:
        return core.serde.from_dict(batch.plan.Plan, json.loads(Path(args.plan).read_text()))
    return batch.plan.build_run_plan(args.checkout)


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


def _emit_summary(run: Run, code: int, report_path: Path | None,
                  doc: dict | None = None) -> None:
    """The last line of a settled run: `next`'s document as a `run_summary` event.

    Its `exit_hint` is *code*, the process's real exit status. Apply errors
    make it 1, whatever the run's status alone would say. *report_path* is the
    `report.md` `_conclude` wrote; omitted when nothing was written.
    *doc* is that report when the write succeeded; None rebuilds the view.
    """
    if doc is None:
        import cli.pr_batch_common
        import cli.pr_batch_views
        doc = cli.pr_batch_views.report(run, cli.pr_batch_common.is_active(run.id))
    view = batch.report.next_view(doc)
    view["run"]["exit_hint"] = code
    extra = {"report_path": str(report_path)} if report_path is not None else {}
    batch.events.emit("run_summary", run=view["run"], counts=view["counts"],
                      next=view["next"], **extra)


def _drive(session: batch.store.Session, reporter: _Reporter, holds: batch.targets.ItemHolds,
           *, bin_dir: Path, cfg, verbose: bool) -> RunStatus:
    """Drive *session*'s run until it blocks, then finish the session.

    The scheduler releases an item's target from *holds* once the item is
    terminal and committed. The caller still holds every other item's target,
    so publishes that the final drain applies stay narrowed to their items.
    Returns the status the scheduler stopped at.
    """
    with batch.machine.register(session.run.id) as machine:
        scheduler = batch.scheduler.Scheduler(session, pr_bin=str(bin_dir / "pr"), cfg=cfg,
                                              verbose=verbose, release=holds.release_finished,
                                              machine=machine, hold_tree=_tree_holder(holds))
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
                          open_decisions=len(run.open_decisions()))
    code = 1 if reporter.failed else batch.report.exit_code(run.status)
    import cli.pr_batch_views
    path: Path | None = None
    doc = None
    try:
        path, doc = cli.pr_batch_views.write_report(run, offline=True)
    except Exception as exc:
        core.log.warn(f"could not write report: {exc}")
    _emit_summary(run, code, path, doc)
    return code


class _Refused(Exception):
    """A run or resume that may not start, and why. Printed as its refusal."""


def _held_batch_key(key: str) -> bool:
    return core.run_lock.holds(
        core.run_lock.global_lock_path(batch.store.batch_lock(key).name))


def _require_batch_locks(keys: list[str], refused: str) -> None:
    """Raise `_Refused` unless this process holds every per-repo batch lock in *keys*.

    The dispatcher took the locks `_batch_need` declared before this handler
    chose the run; a run whose repos differ from those (a plan file edited, a
    covering run that changed in between) is refused rather than driven
    unlocked. *refused* opens the message: "run not started", "run X not resumed".
    """
    missing = [k for k in keys if not _held_batch_key(k)]
    if missing:
        raise _Refused(
            f"{refused}: the batch lock for {', '.join(missing)} is not held — "
            "the repos changed since the command started; re-run")


def _row_repo_keys(rows: list[batch.plan.PlanRow]) -> list[str]:
    """The origin keys *rows* cover: recorded at plan time, or read from a saved
    plan's checkout that predates the field."""
    return sorted({k for r in rows if (k := r.repo_key or batch.targets.origin_key(r.repo_dir))})


def _note_batch_locks(run: Run) -> None:
    for key in batch.targets.run_repo_keys(run):
        core.run_lock.note_holder(batch.store.batch_lock(key), run=run.id)


@dataclass(frozen=True)
class _Started:
    """A run that has been planned, opened, item-locked and first committed, ready to drive."""

    session: batch.store.Session
    cfg: config.workbench_config.BatchConfig
    trail: Trail
    holds: batch.targets.ItemHolds


def _busy_item(run, item, verb: str, exc: core.run_lock.LockBusy) -> str:
    """The refusal for *item*, whose target *exc* says another process holds."""
    action = "started" if verb == "run" else "resumed"
    hint = (" — wait for it, or leave the PR out with --prs" if verb == "run"
            else " — wait for it to finish")
    return f"run {run.id} not {action}: {item.key} is held by {exc.holder_text}{hint}"


def _hold_items(held: contextlib.ExitStack, run, verb: str) -> batch.targets.ItemHolds:
    """Hold every unfinished item's target, sorted by path; raise _Refused if one is busy.

    The holds go on *held* as one `batch.targets.ItemHolds`, returned so the
    scheduler can release a target once its items are terminal. Terminal items
    are not held. All or nothing: a busy target refuses the whole batch, and
    targets already taken are released as the stack unwinds. An item with no
    `origin` is named on stderr; its steps take their own locks. An item whose
    worktree an earlier run recorded has that checkout held too, with its
    target; the scheduler holds the rest as it assigns them.
    """
    targets: dict[Path, list[Item]] = {}
    for item in run.items:
        if item.terminal:
            continue
        target = batch.targets.item_target_dir(item)
        if target is None:
            print(f"pr batch: cannot name the pr target of {item.key} (no origin remote in "
                  f"{item.repo_dir}) — its steps lock it themselves", file=sys.stderr)
            continue
        targets.setdefault(target, []).append(item)
    started = batch.store.now_iso()
    command = f"pr batch {verb} {run.id}"

    def checkout(worktree: str) -> contextlib.AbstractContextManager:
        return core.run_lock.hold({core.run_lock.CHECKOUT}, worktree=Path(worktree),
                                  command=command, started=started)

    holds = held.enter_context(batch.targets.ItemHolds(checkout))
    for target in sorted(targets):
        items = targets[target]
        try:
            _hold_target(holds, target, items, command=command, started=started)
        except core.run_lock.LockBusy as exc:
            raise _Refused(_busy_item(run, items[0], verb, exc)) from exc
    return holds


def _hold_target(holds: batch.targets.ItemHolds, target: Path, items: list[Item], *,
                 command: str, started: str) -> None:
    """Hold *target* for *items*, then each worktree they recorded; raises LockBusy."""
    holds.take(target, [i.key for i in items], core.run_lock.hold(
        {core.run_lock.TARGET}, target_dir=target, target_label=items[0].key,
        command=command, started=started))
    for item in items:
        holds.hold_checkout(item.key, item.worktree)


def _tree_holder(holds: batch.targets.ItemHolds) -> Callable[[Item], str]:
    """The scheduler's `hold_tree`: hold an item's new worktree, or say who holds it."""
    def hold(item: Item) -> str:
        try:
            holds.hold_checkout(item.key, item.worktree)
        except core.run_lock.LockBusy as exc:
            return f"{item.worktree} is held by {exc.holder_text}"
        return ""
    return hold


def _skip_held(rows: list[batch.plan.PlanRow]) -> list[batch.plan.PlanRow]:
    """Drop rows an unfinished run still holds; name that run and how to free each."""
    held = batch.store.held_items()
    kept = []
    for row in rows:
        holder = next((h for h in held if batch.store.holds_item(
            h, row.pr, row.repo, row.repo_key)), None)
        if holder is None:
            kept.append(row)
            continue
        age = batch.store.started_ago(holder.started_at)
        print(f"pr batch: skipping {row.key} — run {holder.run_id} (started {age}) "
              f"still holds it; free it with `pr batch release {holder.run_id} {row.key}` "
              f"or `pr batch cancel {holder.run_id}`", file=sys.stderr)
        batch.events.emit("item_skipped_held", item=row.key, holder=holder.run_id)
    return kept


def _warn_focus_without_review(run: Run) -> None:
    """Warn when --focus was given but some items have no review step planned."""
    keys = [i.key for i in run.items if not any(
        s.step is Step.REVIEW and s.status is not StepStatus.SKIPPED for s in i.steps)]
    if not keys:
        return
    core.log.warn(
        "--focus applies only to review steps; these items have no review step: "
        + ", ".join(keys) + " — add --select KEY=review to review them")


def _start_run(held: contextlib.ExitStack, args, plan, inbox: batch.inbox.Inbox, *, focus) -> _Started:
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
        rows = _skip_held(rows)
        _require_batch_locks(_row_repo_keys(rows), "run not started")
        selected = {s.key: s.steps for s in args.select} if args.select else None
        dirs = sorted({r.repo_dir for r in rows}) or (args.checkout or [])
        cfg = _cfg(dirs)
        run = batch.scheduler.new_run(rows, steps=args.steps, selected=selected,
                                      pool=batch.admission.ceiling(args.pool, cfg),
                                      auto_publish=args.auto_publish,
                                      now=datetime.now(timezone.utc),
                                      ref_namespace=plan.ref_namespace, ref_dirs=plan.ref_dirs,
                                      no_verify=args.no_verify, focus=focus)
        if focus:
            _warn_focus_without_review(run)
        session = held.enter_context(batch.store.create(run, inbox=inbox,
                                                        command=f"pr batch run {run.id}"))
        # The id exists only now. From here a batch refused the lock is told
        # which run to wait for or cancel, not only a pid.
        _note_batch_locks(run)
        holds = _hold_items(held, run, "run")
        trail = Trail.start(script="pr-batch", context={"run": run.id}, record=True)
        run.trail_root = os.environ.get(TRAIL_ROOT_ENV, "")
        session.commit()
    except BaseException:
        batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace)
        raise
    # The committed run owns the namespace now; the plan's claim only covered
    # the window before it existed, and one owner is what gc reads.
    batch.plan.remove_claim(plan.ref_namespace)
    return _Started(session, cfg, trail, holds)


def _emit_started(run: Run) -> None:
    batch.events.emit("run_started", run=run.id, pool=run.pool, trail_root=run.trail_root,
                      steps=[s.value for s in run.steps],
                      auto_publish=[s.value for s in run.auto_publish])
    for item in run.items:
        batch.events.emit("item_queued", run=run.id, item=item.key,
                          steps=[s.step.value for s in item.steps])


def _cmd_run(args, bin_dir: Path) -> int:
    import cli.pr_batch_common
    try:
        focus = review.focus.operator_texts(args)
        review.focus.operator_areas(focus)
    except review.focus.FocusInputError as exc:
        return cli.pr_batch_common.fail(str(exc))
    plan = _plan(args)
    reporter = _Reporter()
    # The run's own lock goes on `held` first, so it is released last. An
    # item's target is released once the item is terminal, and every other
    # one before the session's exit (release, then hand-off); a manual `pr`
    # on one of these PRs is refused until then.
    with contextlib.ExitStack() as held:
        try:
            started = _start_run(held, args, plan, _driver_inbox(bin_dir, reporter), focus=focus)
        except _Refused as exc:
            return cli.pr_batch_common.fail(str(exc))
        _emit_started(started.session.run)
        try:
            stopped = _drive(started.session, reporter, started.holds, bin_dir=bin_dir,
                             cfg=started.cfg, verbose=args.verbose)
        finally:
            started.trail.finish()
    return _conclude(started.session.run.id, stopped, reporter)


class _NarrowOnly(_Refused):
    """Resume asked to add a step; a usage error (exit 2) rather than a refusal."""


def _narrow_resume(run: Run, steps: list[Step]) -> None:
    """Keep only *steps* on *run*; skip each non-terminal item's pending excluded step."""
    wanted = set(steps)
    excluded = set(run.steps) - wanted
    run.steps = [s for s in run.steps if s in wanted]
    pending = (r for item in run.items if not item.terminal for r in item.steps)
    for rec in pending:
        if rec.step in excluded and rec.status is StepStatus.PENDING:
            rec.status = StepStatus.SKIPPED


def _try_resume(held: contextlib.ExitStack, session: batch.store.Session,
                reporter: _Reporter, bin_dir: Path, verbose: bool,
                steps: list[Step] | None = None) -> RunStatus | _Refused:
    """Drive *session* or return the refusal; the caller raises after the session exits."""
    run = session.run
    if run.status in FINISHED_RUN:
        return _Refused(f"run {run.id} is {run.status.value}")
    if steps is not None and not set(steps) <= set(run.steps):
        return _NarrowOnly(
            "resume can only narrow steps; start a new run to add one")
    try:
        _note_batch_locks(run)
        holds = _hold_items(held, run, "resume")
    except _Refused as exc:
        return exc
    if steps is not None:
        _narrow_resume(run, steps)
    batch.scheduler.mark_interrupted(run, emit=batch.events.emit)
    os.environ.setdefault(TRAIL_ROOT_ENV, run.trail_root)
    return _drive(session, reporter, holds, bin_dir=bin_dir,
                  cfg=_cfg([i.repo_dir for i in run.items]), verbose=verbose)


def _resume(run_id: str, reporter: _Reporter, bin_dir: Path, verbose: bool,
            steps: list[Step] | None = None) -> RunStatus:
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
        result = _try_resume(held, session, reporter, bin_dir, verbose, steps=steps)
    if isinstance(result, _Refused):
        raise result
    return result


def _cmd_resume(args, bin_dir: Path) -> int:
    import cli.pr_batch_common
    import cli.pr_batch_requests
    if refusal := cli.pr_batch_requests.ambiguous(args.run_id):
        return cli.pr_batch_common.fail(refusal)
    run_id = cli.pr_batch_common.resolve_run_id(args.run_id, covering_only=not args.run_id)
    if not run_id:
        return cli.pr_batch_common.fail("no batch run covers this repo; pass a run id")
    try:
        _require_batch_locks(batch.targets.run_repo_keys(batch.store.load(run_id)),
                             f"run {run_id} not resumed")
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    except _Refused as exc:
        return cli.pr_batch_common.fail(str(exc))
    reporter = _Reporter()
    try:
        stopped = _resume(run_id, reporter, bin_dir, args.verbose, steps=args.steps)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {run_id}")
    except _Refused as exc:
        print(f"pr batch: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, _NarrowOnly) else 1
    except (batch.store.RunBusy, batch.store.RunUnreachable) as exc:
        return cli.pr_batch_common.fail(str(exc))
    return _conclude(run_id, stopped, reporter)


def cmd_batch(argv: list[str], ctx, *, bin_dir: Path, schema_version: str | None = None,
              **_kw) -> int:
    import cli.pr_batch_common
    import cli.pr_batch_requests
    import cli.pr_batch_views
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            return cli.pr_batch_views.cmd_plan(args)
        if args.command == "run":
            return _cmd_run(args, bin_dir)
        if args.command == "resume":
            return _cmd_resume(args, bin_dir)
        if args.command == "resolve":
            return cli.pr_batch_requests.cmd_resolve(args, bin_dir)
        if args.command == "cancel":
            return cli.pr_batch_requests.cmd_cancel(args, bin_dir)
        if args.command == "release":
            return cli.pr_batch_requests.cmd_release(args, bin_dir)
        if args.command == "report":
            return cli.pr_batch_views.cmd_report(args)
        # status and next
        return cli.pr_batch_views.cmd_status(args)
    except batch.plan.PlanError as exc:
        return cli.pr_batch_common.fail(str(exc))
