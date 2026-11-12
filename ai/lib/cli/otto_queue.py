"""`otto-queue`: run a command through this machine's queue, see what it holds, cancel an entry.

The library is `otto_queue`; this module is the parser, the output and the exit
codes. `run` exits with the command's own status: 75 when the command, or the
wait for a slot, found its resource busy, and 130 when the entry was cancelled
before it ran. `status` exits 0. `cancel` exits 0, or 1 when it names no live
entry. Admission is `batch.admission.QueuePolicy` under the machine-wide config
(`batch.*`), never a repo's, because the budget belongs to the machine.
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
import core.log
import core.proc
import core.serde
import otto_queue.ledger
import otto_queue.ticket
from otto_queue.model import Entry, EntryClass, EntryState
from otto_queue.ticket import CancelOutcome


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {value!r}") from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError("cannot be negative")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="otto-queue", description="Run background work through this machine's queue.")
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
    return parser


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
        return 0
    policy = _policy()
    running = [e for e in entries if e.state is EntryState.RUNNING]
    print(" · ".join(f"{c.value} {sum(e.entry_class is c for e in running)}/{policy.limit(c)} running"
                     for c in EntryClass))
    now = time.time()
    for entry in entries:
        print(_line(entry, now))
    return 0


def _cancel(args: argparse.Namespace) -> int:
    result = otto_queue.ticket.cancel_entry(otto_queue.ledger.Ledger.open(), args.id)
    if result.outcome is CancelOutcome.NOT_FOUND:
        core.log.error(f"otto-queue: no entry {args.id}")
        return 1
    if result.outcome is CancelOutcome.FINISHED:
        core.log.error(f"otto-queue: entry {args.id} already ended ({result.entry.state.value})")
        return 1
    if result.outcome is CancelOutcome.SIGNALLED:
        core.log.ok(f"otto-queue: cancelled entry {args.id}; sent SIGTERM to pid {result.entry.pid}")
        return 0
    core.log.ok(f"otto-queue: cancelled entry {args.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "run":
            return _run(args, parser)
        return _status(args) if args.command == "status" else _cancel(args)
    except KeyboardInterrupt:
        return core.proc.INTERRUPT_RETURNCODE
    except otto_queue.ledger.LedgerBusy as exc:
        core.log.error(f"otto-queue: {exc}")
        return os.EX_TEMPFAIL
    except otto_queue.ledger.LedgerError as exc:
        core.log.error(f"otto-queue: {exc}")
        return 1
