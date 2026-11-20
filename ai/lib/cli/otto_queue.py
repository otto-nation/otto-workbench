"""`otto-queue`: run a command through this machine's queue, see what it holds, wait for or cancel an entry.

The library is `otto_queue`; this module is the parser, the output and the exit
codes. Flags, exit codes and examples are `CONTRACT` below. Admission is
`otto_queue.admission.QueuePolicy` under the machine-wide config (`batch.*`), never a
repo's, because the budget belongs to the machine. Jobs are admitted against
`workbench.slice`'s cgroup chain, or the worker's own chain when no slice is
usable; tickets against their caller's. `worker` and `job-exec` are run by the
supervisor and the worker, not by hand.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import functools
import json
import os
import shlex
import sys
import time
from pathlib import Path

import config.workbench_config
import core.cli_reference
import core.command_contract
import core.log
import core.serde
import otto_queue.admission
import otto_queue.job
import otto_queue.lease
import otto_queue.ledger
import otto_queue.stuck
import otto_queue.submit
import otto_queue.supervisor
import otto_queue.ticket
import otto_queue.waiter
import otto_queue.worker
from core.exit_codes import ExitCode
from otto_queue.model import Entry, EntryClass, EntryState
from otto_queue.ticket import CancelOutcome

CONTRACT = core.command_contract.CommandContract(
    summary="Run background work through this machine's queue, so it waits for "
            "room instead of overloading the host.",
    groups=(
        core.command_contract.FlagGroup(
            "run", ("--ticket", "--class", "--label", "--key", "--force", "--no-reuse", "--wait", "argv")),
        core.command_contract.FlagGroup(
            "submit", ("--run-here", "--queue-only")),
        core.command_contract.FlagGroup("worker", ("--idle",)),
        core.command_contract.FlagGroup("status", ("--json", "--slots")),
        core.command_contract.FlagGroup("cancel", ("id",)),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "status listed the ledger; cancel stopped a waiting or running entry; "
            "run's command succeeded; submit queued or joined the job (its id on stdout), "
            "or with --run-here ran it here and it succeeded; wait's entry ended 0"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "cancel named no live entry (unknown id, or already finished); wait named "
            "no entry, or the entry ended without a status; a ticket ended stuck behind "
            "an unclaimed job; the ledger could not be opened",
            (core.command_contract.NextStep(
                "", ("otto-queue status", "cancel a live id from that list")),)),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "no subcommand, run without --ticket, or run with no command after --"),
        core.command_contract.ExitOutcome(
            ExitCode.UNAVAILABLE,
            "submit --queue-only: no worker could take the job, so it was withdrawn "
            "and nothing ran; or the ledger could not be opened, so nothing was queued",
            (core.command_contract.NextStep(
                "", ("run the command yourself", "or retry once a worker is up")),)),
        core.command_contract.ExitOutcome(
            ExitCode.TEMPFAIL,
            "no slot before --wait, or another process holds the ledger; retry; wait: the "
            "entry did not end before --wait and stays queued, so run `otto-queue wait ID` again",
            (core.command_contract.NextStep(
                "", ("otto-queue status", "retry once a slot or the ledger is free")),)),
        core.command_contract.ExitOutcome(
            ExitCode.CANCELLED,
            "the entry was cancelled before it ran, or Ctrl-C interrupted the CLI (a "
            "waited-on entry is left as it was)"),
    ),
    examples=(
        core.command_contract.Example(
            "otto-queue run --ticket --class light -- pytest tests/foo_test.py",
            "wait for a light slot, then run pytest here"),
        core.command_contract.Example(
            "otto-queue run --ticket --label nightly-build -- make",
            "show a label instead of the command in status"),
        core.command_contract.Example(
            "otto-queue run --ticket --key review:main -- pr review --self",
            "join a live review with that key, or reuse a done one's status"),
        core.command_contract.Example(
            "otto-queue submit --key nightly -- pr gc",
            "queue pr gc and print its entry id; never runs it here"),
        core.command_contract.Example(
            "otto-queue submit --run-here --key nightly -- pr gc",
            "the same, but run it here when no worker can start"),
        core.command_contract.Example(
            "otto-queue wait 12",
            "wait for entry 12 and exit with its status; runs it here when no worker is alive"),
        core.command_contract.Example(
            "otto-queue status", "list live entries and recent finished ones"),
        core.command_contract.Example(
            "otto-queue status --json", "the same, one JSON object per entry"),
        core.command_contract.Example(
            "otto-queue status --json --slots",
            "the same, with a leading line of slot counts, the host verdict, and the 24 h failure count"),
        core.command_contract.Example(
            "otto-queue cancel 12", "stop waiting entry 12, or SIGTERM a running one"),
    ),
    notes=(
        "run, submit --run-here when it ran the job here, and wait also exit with COMMAND's own status.",
        "--key is idempotent: a live match is joined, a done match returns its "
        "status, --force runs regardless.",
        "Admission is machine-wide `otto_queue.admission.QueuePolicy` (`batch.*` in "
        "the workbench config), never a repo's.",
        "The ledger is queue/queue.db under WORKBENCH_STATE_DIR "
        "(default ~/.local/state/workbench).",
    ),
    reference="docs/ai-automation.md#otto-queue-contract",
    # MCP's flag-only mapping cannot express remainder argv or a required
    # positional, and worker is a long-running supervisor process:
    #   run, submit — nargs=REMAINDER after -- (`--argv` is not a flag)
    #   cancel, wait — required positional id (`--id` is unrecognized)
    #   worker — started by the supervisor, not a request/response call
    mcp_exclude=("run", "submit", "worker", "cancel", "wait"),
)


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {value!r}") from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return seconds


def _add_entry_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--class", dest="entry_class", choices=[c.value for c in EntryClass],
                        default=EntryClass.HEAVY.value,
                        help="heavy: counted and memory-checked (default); light: counted only")
    parser.add_argument("--label",
                        help="What `status` and the Herdr queue view show for this entry")
    parser.add_argument("--key", help="Join a live entry with this key, or return a done one's status")
    parser.add_argument("--force", action="store_true", help="Run even when --key matches an entry")
    parser.add_argument("--no-reuse", dest="reuse", action="store_false",
                        help="With --key, run again when only a finished entry matches; a live one is still joined")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="otto-queue")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Run a command once the queue admits it")
    run.add_argument("--ticket", action="store_true", required=True,
                     help="Run the command in this process and terminal once admitted")
    _add_entry_flags(run)
    run.add_argument("--wait", type=_seconds, default=otto_queue.ticket.DEFAULT_WAIT_S,
                     help="Seconds to wait for a slot before exiting 75 (default: %(default)g)")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="-- and the command to run")
    submit = sub.add_parser("submit", help="Queue a command durably and print its entry id (not when it runs here: stdout is the job's); `otto-queue wait ID` waits for it")
    _add_entry_flags(submit)
    modes = submit.add_mutually_exclusive_group()
    modes.add_argument("--run-here", dest="mode", action="store_const", const=otto_queue.submit.SubmitMode.RUN_HERE.value,
                       help="When no worker can start, run the job in this process (unattended callers such as scheduled maintenance)")
    modes.add_argument("--queue-only", dest="mode", action="store_const", const=otto_queue.submit.SubmitMode.QUEUE_ONLY.value,
                       help="When no worker can start, withdraw the job and exit 69 instead of running it here")
    submit.set_defaults(mode=otto_queue.submit.SubmitMode.ADD.value)
    submit.add_argument("argv", nargs=argparse.REMAINDER, help="-- and the command to run")
    worker = sub.add_parser("worker", help="Run queued jobs until idle; started by the supervisor")
    worker.add_argument("--idle", type=_seconds, default=otto_queue.worker.IDLE_S,
                        help="Seconds with no job before exiting (default: %(default)g)")
    worker.add_argument("--slice", default=otto_queue.supervisor.SLICE, help=argparse.SUPPRESS)
    status = sub.add_parser("status", help="List live entries and the most recent finished ones")
    status.add_argument("--json", action="store_true", help="One JSON object per entry")
    status.add_argument("--slots", action="store_true",
                        help="With --json, a leading line of slot counts, the host verdict, and the 24 h failure count")
    cancel = sub.add_parser("cancel", help="Cancel a waiting or running entry")
    cancel.add_argument("id", type=int, help="The entry's id, from status")
    wait = sub.add_parser("wait", help="Wait for an entry to end and exit with its status")
    wait.add_argument("id", type=int, help="The entry's id, as `submit` printed it")
    wait.add_argument("--wait", type=_seconds, default=otto_queue.ticket.DEFAULT_WAIT_S,
                      help="Seconds to wait before exiting 75 and leaving the entry queued (default: %(default)g)")
    core.command_contract.attach_help(parser, CONTRACT)
    # Subcommand flags live on the subparsers; regrouping would leave empty
    # titled sections on the top-level help.
    parser._action_groups[:] = [g for g in parser._action_groups if g._group_actions]
    return parser


def reference_shape() -> core.cli_reference.CLIShape:
    """Everything otto-queue accepts, with CONTRACT on the CLI as a whole."""
    shape = core.cli_reference.shape_of(build_parser(), "otto-queue")
    return core.cli_reference.CLIShape(
        prog=shape.prog, globals=shape.globals, commands=shape.commands,
        contract=CONTRACT,
    )


def machine_policy() -> otto_queue.admission.QueuePolicy:
    return otto_queue.admission.QueuePolicy(config.workbench_config.load_config_or_default(None).batch)


def _command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> tuple[str, ...]:
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        parser.error(f"{args.command} needs a command after --")
    return tuple(argv)


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    request = otto_queue.ticket.TicketRequest(
        argv=_command(args, parser), cwd=os.getcwd(), entry_class=EntryClass(args.entry_class),
        idem_key=args.key, force=args.force, reuse=args.reuse, wait_s=args.wait,
        label=args.label)
    return otto_queue.ticket.run_ticket(request, machine_policy()).exit_status


def _submit(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    request = otto_queue.submit.SubmitRequest(
        argv=_command(args, parser), cwd=os.getcwd(), entry_class=EntryClass(args.entry_class),
        idem_key=args.key, force=args.force, reuse=args.reuse, mode=otto_queue.submit.SubmitMode(args.mode),
        label=args.label)
    result = otto_queue.submit.submit(request, machine_policy())
    if result.entry_id is not None and result.outcome in (
            otto_queue.submit.SubmitOutcome.QUEUED, otto_queue.submit.SubmitOutcome.JOINED):
        print(result.entry_id)
    return result.exit_status


def _shim() -> Path:
    return Path(__file__).resolve().parents[2] / "bin" / "otto-queue"


def _job_slice(name: str) -> str | None:
    """*name* when jobs can run in it here; None on macOS, or where systemd-run cannot place them.

    A None slice means jobs use the worker's own cgroup chain.
    """
    if not sys.platform.startswith("linux"):
        return None
    if otto_queue.supervisor.scopes_usable(name):
        return name
    core.log.warn(f"otto-queue: systemd-run --user cannot place jobs in {name}; they run without its memory cap")
    return None


def _job_policy(slice_name: str | None) -> otto_queue.admission.QueuePolicy:
    """Admission for jobs: where their memory lands (open decision a).

    The slice's cgroup chain when jobs run in *slice_name*; the worker's own
    chain when no slice is usable and they run as its children.
    """
    cgroup = None if slice_name is None else otto_queue.admission.slice_cgroup(slice_name)
    host = functools.partial(otto_queue.admission.read_host, cgroup=cgroup)
    return otto_queue.admission.QueuePolicy(config.workbench_config.load_config_or_default(None).batch, host=host)


def _worker(args: argparse.Namespace) -> int:
    slice_name = _job_slice(args.slice)
    launcher = otto_queue.worker.Launcher(job_exec=(sys.executable, str(_shim()), "job-exec"),
                                          slice_name=slice_name)
    return otto_queue.worker.run_worker(_job_policy(slice_name), launcher=launcher, idle_s=args.idle)


def _age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _line(entry: Entry, now: float, *, worker_alive: bool = False,
          running: int = 0) -> str:
    since = entry.started_at or entry.created_at
    status = "" if entry.exit_status is None else f" exit {entry.exit_status}"
    retry = ""
    if entry.state is EntryState.QUEUED and entry.not_before is not None and entry.not_before > now:
        retry = f"  retry in {_age(entry.not_before - now)}, attempt {entry.attempts + 1}"
    stuck = otto_queue.stuck.unclaimed_job(
        ahead=entry, now=now, worker_alive=worker_alive, running=running)
    note = f"  {stuck}" if stuck else ""
    title = entry.label if entry.label else shlex.join(entry.argv)
    return (f"  #{entry.id:<5} {entry.kind.value:<6} {entry.entry_class.value:<5} {entry.state.value:<9} "
            f"{_age(now - since):>5}{status}{retry}{note}  {title}")


def _json_row(entry: Entry, now: float, *, worker_alive: bool, running: int) -> dict:
    row = core.serde.to_dict(entry)
    stuck = otto_queue.stuck.unclaimed_job(
        ahead=entry, now=now, worker_alive=worker_alive, running=running)
    if stuck:
        row["stuck"] = stuck
    return row


SLOTS_HEADER_SCHEMA = 1
FAILED_24H_S = 24 * 3600


def _host_status(policy: otto_queue.admission.QueuePolicy) -> dict | None:
    """The host check a heavy entry uses; None when the probe cannot run."""
    try:
        verdict = otto_queue.admission.room(
            policy.host(), estimate=otto_queue.admission.QUEUE_HEAVY_ESTIMATE, cfg=policy.cfg)
    except Exception:
        return None
    return {"admit_heavy": verdict.admit, "reason": verdict.reason or None}


def _failed_24h(ledger: otto_queue.ledger.Ledger) -> int | None:
    try:
        return ledger.count_failed_since(time.time() - FAILED_24H_S)
    except Exception:
        return None


def _slot_header(entries: list[Entry], policy: otto_queue.admission.QueuePolicy,
                 ledger: otto_queue.ledger.Ledger) -> dict:
    running = [e for e in entries if e.state is EntryState.RUNNING]
    return {
        "slots": {
            c.value: {"running": sum(e.entry_class is c for e in running),
                      "limit": policy.limit(c)}
            for c in EntryClass
        },
        "host": _host_status(policy),
        "failed_24h": _failed_24h(ledger),
        "schema": SLOTS_HEADER_SCHEMA,
    }


def _status(args: argparse.Namespace) -> int:
    ledger = otto_queue.ledger.Ledger.open()
    ledger.reap_orphans()
    entries = ledger.entries()
    if args.json:
        if args.slots:
            print(json.dumps(_slot_header(entries, machine_policy(), ledger)))
        now = time.time()
        worker_alive = otto_queue.lease.worker_alive(ledger.root)
        running_by_class = {
            cls: sum(1 for e in entries if e.state is EntryState.RUNNING and e.entry_class is cls)
            for cls in EntryClass}
        for entry in entries:
            print(json.dumps(_json_row(
                entry, now, worker_alive=worker_alive,
                running=running_by_class[entry.entry_class])))
        return ExitCode.OK
    policy = machine_policy()
    running = [e for e in entries if e.state is EntryState.RUNNING]
    print(" · ".join(f"{c.value} {sum(e.entry_class is c for e in running)}/{policy.limit(c)} running"
                     for c in EntryClass))
    now = time.time()
    worker_alive = otto_queue.lease.worker_alive(ledger.root)
    running_by_class = {
        cls: sum(1 for e in running if e.entry_class is cls) for cls in EntryClass}
    for entry in entries:
        print(_line(entry, now, worker_alive=worker_alive,
                    running=running_by_class[entry.entry_class]))
    return ExitCode.OK


def _job_exec_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="otto-queue job-exec")
    parser.add_argument("id", type=int)
    parser.add_argument("--slot-fd", type=int, default=None)
    return parser


def _job_exec(args: argparse.Namespace) -> int:
    return otto_queue.job.run_job(args.id, args.slot_fd).exit_status


def _wait(args: argparse.Namespace) -> int:
    return otto_queue.waiter.wait_entry(args.id, machine_policy(), wait_s=args.wait).exit_status


def _cancel(args: argparse.Namespace) -> int:
    result = otto_queue.ticket.cancel_entry(otto_queue.ledger.Ledger.open(), args.id)
    if result.outcome is CancelOutcome.NOT_FOUND:
        core.log.error(f"otto-queue: no entry {args.id}")
        return ExitCode.FAILURE
    if result.outcome is CancelOutcome.FINISHED:
        core.log.error(f"otto-queue: entry {args.id} already ended ({result.entry.state.value})")
        return ExitCode.FAILURE
    if result.outcome is CancelOutcome.SIGNALLED:
        core.log.ok(f"otto-queue: cancelled entry {args.id}; sent SIGTERM to pid {result.entry.pid}")
        return ExitCode.OK
    core.log.ok(f"otto-queue: cancelled entry {args.id}")
    return ExitCode.OK


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        if argv[:1] == ["job-exec"]:
            return _job_exec(_job_exec_parser().parse_args(argv[1:]))
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return ExitCode.USAGE
        handlers = {
            "run": lambda: _run(args, parser),
            "submit": lambda: _submit(args, parser),
            "status": lambda: _status(args),
            "cancel": lambda: _cancel(args),
            "wait": lambda: _wait(args),
            "worker": lambda: _worker(args),
        }
        return handlers[args.command]()
    except KeyboardInterrupt:
        return ExitCode.CANCELLED
    except otto_queue.ledger.LedgerBusy as exc:
        core.log.error(f"otto-queue: {exc}")
        return ExitCode.TEMPFAIL
    except otto_queue.ledger.LedgerError as exc:
        core.log.error(f"otto-queue: {exc}")
        return ExitCode.FAILURE
