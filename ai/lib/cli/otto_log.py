"""Query trail files and AI usage across otto-workbench scripts.

Trails are discovered from one root — `workbench_paths.trail_dir()`, monthly
files under the state root. The `stats` subcommand reads the global AI usage
ledger instead — a separate, monthly-rotated store that every AI call appends to.

One user command spans several processes, each with its own `invocation` and all
sharing a `root` (see `core.trail`). `show` takes any of those IDs and renders
the whole command, labelling each event with the script that wrote it; `--only`
narrows it back to the single process named. `list` rows are whole commands for
the same reason, so `pr review` is one row rather than three.

A window selects *commands*, not events: a command whose first event predates the
window is listed whole when any part of it falls inside, because half a timeline
answers no question anyone asks of it.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys

import agent.usage_stats
import core.log
import core.trail
import core.trail_view


def _positive_int(value: str) -> int:
    """An argparse type for a month count: never negative.

    `oldest_kept_month` clamps a negative count to the same cutoff as zero, so
    a negative `--keep` would silently keep the current month instead of
    erroring on the nonsensical value it actually was.
    """
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative integer: {value!r}")
    return n


def _record_data(pairs: list[str]) -> dict:
    """`--data k=v` pairs as the object the record carries.

    A value that reads as a number is stored as one: the counts these records
    exist to hold are summed and compared by whatever reads them back, and a
    string `"12"` sorts before `"9"`. Anything else stays the text it was.
    """
    data: dict = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            core.log.error(f"--data expects key=value, got: {pair!r}")
            sys.exit(2)
        data[key] = _coerce(value)
    return data


def _coerce(value: str) -> int | float | str:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query trail files across otto-workbench AI scripts.",
    )
    sub = parser.add_subparsers(dest="command")

    # recent
    p_recent = sub.add_parser("recent", help="Recent events (default: last 1h)")
    p_recent.add_argument("--since", default="1h", help="Time window (e.g. 2h, 1d, 30m)")
    p_recent.add_argument("--repo", help="Filter by repo (org/repo)")
    p_recent.add_argument("--json", action="store_true", help="Output raw JSONL")

    # query
    p_query = sub.add_parser("query", help="Filter events")
    p_query.add_argument("--script", help="Filter by script name")
    p_query.add_argument("--level", help="Filter by level (debug, info, warn, error)")
    p_query.add_argument("--event-type", help="Filter by event type")
    p_query.add_argument("--invocation", help="Filter by invocation ID (one process)")
    p_query.add_argument(
        "--root", help="Filter by root invocation ID (one whole user command)")
    p_query.add_argument("--pr", type=int, help="Filter by PR number")
    p_query.add_argument("--repo", help="Filter by repo (org/repo)")
    p_query.add_argument("--since", help="Time window (e.g. 2h, 1d)")
    p_query.add_argument("--json", action="store_true", help="Output raw JSONL")

    # show
    p_show = sub.add_parser("show", help="Show one command's timeline")
    p_show.add_argument(
        "invocation", help="Invocation ID — the command's own, or any process under it")
    p_show.add_argument(
        "--only", action="store_true",
        help="Just the named process, not the whole command it belongs to")
    p_show.add_argument("--json", action="store_true", help="Output raw JSONL")

    # list
    p_list = sub.add_parser("list", help="List invocations")
    p_list.add_argument(
        "--script",
        help="Filter by script name — lists the whole command that reached it")
    p_list.add_argument(
        "--since",
        help="Time window (e.g. 2h, 1d) — selects commands active in it, "
             "each listed whole even if it started earlier")
    p_list.add_argument("--repo", help="Filter by repo (org/repo)")
    p_list.add_argument("--json", action="store_true", help="Output raw JSONL")

    # record
    p_record = sub.add_parser(
        "record", help="Write one event to the trail (for shell and agent callers)")
    p_record.add_argument("--script", required=True, help="Name the event is filed under")
    p_record.add_argument("--action", required=True, help="What happened, as a short key")
    p_record.add_argument("--detail", default="", help="One line of prose about it")
    p_record.add_argument(
        "--level", default="info", choices=core.trail.RECORD_LEVELS, help="Severity (default: info)")
    p_record.add_argument(
        "--data", action="append", default=[], metavar="KEY=VALUE",
        help="Structured field, repeatable — numeric values are stored as numbers")
    p_record.add_argument("--repo", help="Subject repo (org/repo), recorded as context")
    p_record.add_argument("--pr", type=int, help="Subject PR number, recorded as context")
    core.trail.add_trail_args(p_record)

    # prune
    p_prune = sub.add_parser("prune", help="Drop trail months past the horizon")
    p_prune.add_argument(
        "--keep", type=_positive_int, default=core.trail.TRAIL_KEEP_MONTHS,
        help=f"Months of history to keep (default: {core.trail.TRAIL_KEEP_MONTHS})",
    )

    # stats
    p_stats = sub.add_parser("stats", help="Aggregate AI cost and token usage")
    p_stats.add_argument("--since", default="7d", help="Time window (e.g. 24h, 7d)")
    p_stats.add_argument(
        "--by", default="script", choices=agent.usage_stats.STATS_GROUPINGS, help="Group rows by",
    )
    p_stats.add_argument(
        "--json", action="store_true", help="Output one JSON object per group",
    )

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    if args.command == "recent":
        core.trail_view.recent(args.since, repo=args.repo, as_json=args.json)
    elif args.command == "query":
        core.trail_view.query(
            script=args.script, level=args.level, event_type=args.event_type,
            invocation=args.invocation, root=args.root, pr=args.pr,
            repo=args.repo, since=args.since, as_json=args.json,
        )
    elif args.command == "show":
        core.trail_view.show(args.invocation, only=args.only, as_json=args.json)
    elif args.command == "list":
        core.trail_view.list_commands(
            script=args.script, since=args.since, repo=args.repo, as_json=args.json,
        )
    elif args.command == "record":
        data = _record_data(args.data) or None
        print(core.trail.record_event(
            args.script, args.action, detail=args.detail, level=args.level,
            data=data, repo=args.repo, pr=args.pr, debug=args.debug,
        ))
    elif args.command == "prune":
        core.trail_view.prune(args.keep)
    elif args.command == "stats":
        agent.usage_stats.stats(args.since, args.by, as_json=args.json)
    return 0
