"""`pr batch` — rebase, CI, comments and (opt-in) self-review across my open PRs.

    pr batch plan   --checkout DIR …           which PRs need which steps (JSON)
    pr batch run    --checkout DIR … [opts]    start a run; NDJSON events on stdout
    pr batch resume [RUN_ID]                   continue an interrupted run
    pr batch cancel [RUN_ID] [--kill]          stop the run; a recorded cancel is final
    pr batch release RUN_ID PR [--kill]        drop one PR; frees its locks, leaves the worktree
    pr batch status [RUN_ID] [--full] [--offline]   the run report (JSON)
    pr batch report [RUN_ID] [--offline] [--format json|md]         per-PR session commands

Omitting RUN_ID on resume/cancel picks this checkout's newest unfinished covering run, else its newest covering, and refuses when none covers this repo or more than one unfinished run does. status/report fall back to the newest run on the machine and say so on stderr.

A default run never opens a decision: items end `done` or `needs_session` with findings, shown by `pr batch report`, and the run exits 0. AI and review are opt-in (`--with-ai`, `--with-review`). Every step runs drafted. Nothing is pushed unless `--auto-publish ff`. --no-verify skips the pre-push hook on those publishes.

Exit 0 when a run is done or cancelled, 1 when it stopped without settling. resume never clears a recorded cancel. cancel and release write the run's inbox; a live holder that drains applies a queued request.
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
import batch.findings
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
from batch.model import (FINISHED_RUN, STEP_ORDER, Item, RebaseWhen, Run, RunStatus, Step,
                         StepStatus)
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


_DEFAULT_STEPS = [Step.REBASE, Step.CI, Step.COMMENTS]

_RUN_EPILOG = """\
recipes:
  pr batch run --checkout DIR                     rebase, ci, comments; no AI, no review
  pr batch run --checkout DIR --with-ai=rebase,ci,comments --with-review
  pr batch run --checkout DIR --steps rebase,ci   rebase and CI where needed
  pr batch run --checkout DIR --rebase behind     also rebase PRs that are only behind
  pr batch run --checkout DIR --with-review --focus "…"   review with a focus
  pr batch resume --steps rebase,ci,comments      continue without re-running review

The rebase step takes only PRs whose merge with their base conflicts, checked
locally with `git merge-tree`; --rebase behind takes every PR behind its base.

`run` plans for itself — no `pr batch plan` first. It is long-running and
streams NDJSON events; start it as a background job. Step output stays in the
run's logs/ (--verbose streams it as step_log too), and the last line of a
run that settles is `run_summary`: the run, its counts, and report_path.

A default run never waits on a decision: it exits 0 and `pr batch report`
prints what needs a person.
--auto-publish ff pushes a clean fast-forward (`pr push --expect`) when an
item closes with no findings; never a force push, closeout post or issue filing.
`pr batch resume` converts a stored waiting v1 run to findings (one-way).
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
    p.add_argument("--needs", type=_steps, default=None, metavar="STEPS",
                   help=f"Judge only these steps' needs, any of {','.join(STEP_ORDER)}; "
                        "without rebase no branch is read or fetched (default: every step)")

    r = sub.add_parser("run", help="Start a run", epilog=_RUN_EPILOG,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--checkout", action="append", metavar="DIR",
                     help="A checkout whose open PRs to run on; repeatable")
    src.add_argument("--plan", metavar="FILE", help="A saved `pr batch plan` document")
    r.add_argument("--steps", type=_steps, default=list(_DEFAULT_STEPS), metavar="STEPS",
                   help=f"Comma-separated steps, any of {','.join(STEP_ORDER)} "
                        f"(default: {','.join(s.value for s in _DEFAULT_STEPS)}; "
                        "review needs --with-review). "
                        "Each runs only on PRs the plan marks as needing it")
    r.add_argument("--with-ai", type=_steps, default=[], metavar="STEPS",
                   help="Run these steps with today's AI argv; any of rebase,ci,comments. "
                        "review needs --with-review")
    r.add_argument("--with-review", action="store_true",
                   help="Add the review step (today's argv)")
    r.add_argument("--pool", type=int, default=None, help="Concurrency ceiling")
    r.add_argument("--auto-publish", choices=["ff"], default=None,
                   help="Push a clean fast-forward (`pr push --expect`) when an item closes "
                        "with no findings; never a force push, closeout post or issue filing")
    r.add_argument("--rebase", choices=[w.value for w in RebaseWhen],
                   default=RebaseWhen.CONFLICTS.value,
                   help="Which PRs the rebase step takes: only those that conflict with "
                        "their base (default), or every PR behind it")
    r.add_argument("--prs", default="", help="Comma-separated repo#number keys to include")
    r.add_argument("--select", type=_selection, action="append", default=[],
                   metavar="KEY=STEPS", help="Run exactly these steps for one PR")
    r.add_argument("--no-verify", action="store_true",
                   help="Skip the pre-push hook on every publish push (CI still runs the "
                        "gates). Persisted on the run so resume honors it")
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
    s.add_argument("--full", action="store_true",
                   help="Print the raw run state instead of the report")
    s.add_argument("--list", action="store_true",
                   help="List this machine's runs, newest first, instead of one run's report")
    cli.pr_batch_views.offline(s)

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
    # Named PRs are read by number: a search would read every open PR only for
    # _start_run to keep these.
    keys = [k for k in args.prs.split(",") if k]
    return batch.plan.build_run_plan(args.checkout, keys or None)


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
    """The inbox a `run`/`resume` session drains with: outcomes to *reporter*."""
    return batch.inbox.Inbox(batch.inbox.ApplyContext(report=reporter))


def _emit_summary(run: Run, code: int, report_path: Path | None,
                  doc: dict | None = None) -> None:
    """The last line of a settled run: `run_summary` with run, counts, report_path.

    Its `exit_hint` is *code*, the process's real exit status. Apply errors
    make it 1, whatever the run's status alone would say. *report_path* is the
    `report.md` `_conclude` wrote; omitted when nothing was written.
    *doc* is that report when the write succeeded; None rebuilds the view.
    """
    if doc is None:
        import cli.pr_batch_common
        import cli.pr_batch_views
        doc = cli.pr_batch_views.report(run, cli.pr_batch_common.is_active(run.id))
    run_view = dict(doc["run"])
    run_view["exit_hint"] = code
    extra = {"report_path": str(report_path)} if report_path is not None else {}
    batch.events.emit("run_summary", run=run_view, counts=doc["counts"], **extra)


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
        batch.events.emit("run_finished", run=run.id, status=run.status.value)
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


def _check_run_args(parser: argparse.ArgumentParser, args) -> None:
    if Step.REVIEW in (args.with_ai or []):
        parser.error("review needs --with-review, not --with-ai")
    selected_review = any(Step.REVIEW in s.steps for s in args.select)
    if Step.REVIEW in args.steps and not args.with_review and not selected_review:
        parser.error("review in --steps needs --with-review")
    if (args.with_review or selected_review) and Step.REVIEW not in args.steps:
        args.steps = list(args.steps) + [Step.REVIEW]


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
        selected = {s.key: s.steps for s in args.select} if args.select else None
        dirs = sorted({r.repo_dir for r in rows}) or (args.checkout or [])
        cfg = _cfg(dirs)
        run = batch.scheduler.new_run(rows, steps=args.steps, selected=selected,
                                      pool=batch.admission.ceiling(args.pool, cfg),
                                      now=datetime.now(timezone.utc),
                                      ref_namespace=plan.ref_namespace, ref_dirs=plan.ref_dirs,
                                      no_verify=args.no_verify, focus=focus,
                                      with_ai=args.with_ai,
                                      auto_publish_ff=args.auto_publish == "ff",
                                      rebase_when=RebaseWhen(args.rebase))
        if focus:
            _warn_focus_without_review(run)
        session = held.enter_context(batch.store.create(run, inbox=inbox,
                                                        command=f"pr batch run {run.id}"))
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
                      auto_publish="ff" if run.auto_publish_ff else None,
                      with_ai=None if run.with_ai is None else [s.value for s in run.with_ai],
                      rebase=run.rebase_when.value)
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
        holds = _hold_items(held, run, "resume")
    except _Refused as exc:
        return exc
    batch.findings.upgrade_v1(run, emit=batch.events.emit)
    if steps is not None:
        _narrow_resume(run, steps)
    batch.scheduler.mark_interrupted(run)
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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return cli.pr_batch_views.cmd_plan(args)
        if args.command == "run":
            _check_run_args(parser, args)
            return _cmd_run(args, bin_dir)
        if args.command == "resume":
            return _cmd_resume(args, bin_dir)
        if args.command == "cancel":
            return cli.pr_batch_requests.cmd_cancel(args, bin_dir)
        if args.command == "release":
            return cli.pr_batch_requests.cmd_release(args, bin_dir)
        if args.command == "report":
            return cli.pr_batch_views.cmd_report(args)
        # status
        return cli.pr_batch_views.cmd_status(args)
    except batch.plan.PlanError as exc:
        return cli.pr_batch_common.fail(str(exc))
