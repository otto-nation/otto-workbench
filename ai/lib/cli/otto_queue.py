"""`otto-queue`: run a command through this machine's queue, see what it holds, cancel an entry.

The library is `otto_queue`; this module is the parser, the output and the exit
codes. Flags, exit codes and examples are `CONTRACT` below. Admission is
`batch.admission.QueuePolicy` under the machine-wide config (`batch.*`), never a
repo's, because the budget belongs to the machine.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import os
import shlex
import time

import batch.admission
import config.workbench_config
import core.cli_reference
import core.command_contract
import core.log
import core.serde
import otto_queue.ledger
import otto_queue.ticket
from core.exit_codes import ExitCode
from otto_queue.model import Entry, EntryClass, EntryState
from otto_queue.ticket import CancelOutcome

CONTRACT = core.command_contract.CommandContract(
    summary="Run background work through this machine's queue, so it waits for "
            "room instead of overloading the host.",
    groups=(
        core.command_contract.FlagGroup(
            "run", ("--ticket", "--class", "--key", "--force", "--wait", "argv")),
        core.command_contract.FlagGroup("status", ("--json",)),
        core.command_contract.FlagGroup("cancel", ("id",)),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "status listed the ledger; cancel stopped a waiting or running entry; "
            "run's command succeeded"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "cancel named no live entry (unknown id, or already finished); the "
            "ledger could not be opened",
            (core.command_contract.NextStep(
                "", ("otto-queue status", "cancel a live id from that list")),)),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "no subcommand, run without --ticket, or run with no command after --"),
        core.command_contract.ExitOutcome(
            ExitCode.TEMPFAIL,
            "no slot before --wait, or another process holds the ledger; retry",
            (core.command_contract.NextStep(
                "", ("otto-queue status", "retry once a slot or the ledger is free")),)),
        core.command_contract.ExitOutcome(
            ExitCode.CANCELLED,
            "the entry was cancelled before it ran, or Ctrl-C interrupted the CLI"),
    ),
    examples=(
        core.command_contract.Example(
            "otto-queue run --ticket --class light -- pytest tests/foo_test.py",
            "wait for a light slot, then run pytest here"),
        core.command_contract.Example(
            "otto-queue run --ticket --key review:main -- pr review --self",
            "join a live review with that key, or reuse a done one's status"),
        core.command_contract.Example(
            "otto-queue status", "list live entries and recent finished ones"),
        core.command_contract.Example(
            "otto-queue status --json", "the same, one JSON object per entry"),
        core.command_contract.Example(
            "otto-queue cancel 12", "stop waiting entry 12, or SIGTERM a running one"),
    ),
    notes=(
        "run also exits with COMMAND's own status when the command ran.",
        "--key is idempotent: a live match is joined, a done match returns its "
        "status, --force runs regardless.",
        "Admission is machine-wide `batch.admission.QueuePolicy` (`batch.*` in "
        "the workbench config), never a repo's.",
        "The ledger is queue/queue.db under WORKBENCH_STATE_DIR "
        "(default ~/.local/state/workbench).",
    ),
    reference="docs/ai-automation.md#otto-queue-contract",
)


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {value!r}") from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="otto-queue")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Run a command once the queue admits it")
    run.add_argument("--ticket", action="store_true", required=True,
                     help="Run the command in this process and terminal once admitted")
    run.add_argument("--class", dest="entry_class", choices=[c.value for c in EntryClass],
                     default=EntryClass.HEAVY.value,
                     help="heavy: counted and memory-checked (default); light: counted only")
    run.add_argument("--key", help="Join a live entry with this key, or return a done one's status")
    run.add_argument("--force", action="store_true", help="Run even when --key matches an entry")
    run.add_argument("--wait", type=_seconds, default=otto_queue.ticket.DEFAULT_WAIT_S,
                     help="Seconds to wait for a slot before exiting 75 (default: %(default)g)")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="-- and the command to run")
    status = sub.add_parser("status", help="List live entries and the most recent finished ones")
    status.add_argument("--json", action="store_true", help="One JSON object per entry")
    cancel = sub.add_parser("cancel", help="Cancel a waiting or running entry")
    cancel.add_argument("id", type=int, help="The entry's id, from status")
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


def _policy() -> batch.admission.QueuePolicy:
    return batch.admission.QueuePolicy(config.workbench_config.load_config_or_default(None).batch)


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        parser.error("run needs a command after --")
    request = otto_queue.ticket.TicketRequest(
        argv=tuple(argv), cwd=os.getcwd(), entry_class=EntryClass(args.entry_class),
        idem_key=args.key, force=args.force, wait_s=args.wait)
    return otto_queue.ticket.run_ticket(request, _policy()).exit_status


def _age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _line(entry: Entry, now: float) -> str:
    since = entry.started_at or entry.created_at
    status = "" if entry.exit_status is None else f" exit {entry.exit_status}"
    return (f"  #{entry.id:<5} {entry.entry_class.value:<5} {entry.state.value:<9} "
            f"{_age(now - since):>5}{status}  {shlex.join(entry.argv)}")


def _status(args: argparse.Namespace) -> int:
    ledger = otto_queue.ledger.Ledger.open()
    ledger.reap_orphans()
    entries = ledger.entries()
    if args.json:
        for entry in entries:
            print(json.dumps(core.serde.to_dict(entry)))
        return ExitCode.OK
    policy = _policy()
    running = [e for e in entries if e.state is EntryState.RUNNING]
    print(" · ".join(f"{c.value} {sum(e.entry_class is c for e in running)}/{policy.limit(c)} running"
                     for c in EntryClass))
    now = time.time()
    for entry in entries:
        print(_line(entry, now))
    return ExitCode.OK


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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return ExitCode.USAGE
    try:
        if args.command == "run":
            return _run(args, parser)
        return _status(args) if args.command == "status" else _cancel(args)
    except KeyboardInterrupt:
        return ExitCode.CANCELLED
    except otto_queue.ledger.LedgerBusy as exc:
        core.log.error(f"otto-queue: {exc}")
        return ExitCode.TEMPFAIL
    except otto_queue.ledger.LedgerError as exc:
        core.log.error(f"otto-queue: {exc}")
        return ExitCode.FAILURE
