"""``pi-sessions`` — list pi sessions with their briefs, and act on live ones.

Usage:
  pi-sessions list [--since DAYS] [--all] [--json]
  pi-sessions show ID [--json]
  pi-sessions facts SESSION_FILE [--json]
  pi-sessions live --pid PID (--session-file FILE | --clear)
  pi-sessions close ID

``list`` prints sessions active in the last DAYS days (default 9) that carry a
brief, a name or a live process (``--all``: every one in the window), plus older
named and live ones (``pi_sessions.listing``); ``--json`` prints ``{"schema": 1,
"since_days", "now", "sessions": [row…]}``. ``show`` prints one row. ``facts``
prints ``{"worktrees", "prs"}`` for one session file: its worktrees
(``pi_sessions.facts``) and its PRs — open PRs on each worktree's branch from the
cached ``pr board`` listing (``board.cache``, no GitHub call) merged with those
its ``pr`` / ``gh pr`` commands named. ``live`` records or clears a pi process's
live marker (``pi_sessions.live``); the session-brief Pi extension calls it.
``close`` sends SIGTERM to the session's live pi; the file stays.

Exit codes: 0 done; 1 no such session, or not running here; 2 usage.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import board.cache
import pi_sessions.facts
import pi_sessions.listing
import pi_sessions.live
import pi_sessions.session_file

SCRIPT = "pi-sessions"
SCHEMA = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SCRIPT, description="List pi sessions with their briefs, and act on live ones.")
    sub = parser.add_subparsers(dest="command", required=True)
    lister = sub.add_parser("list", help="recent briefed, named and live sessions")
    lister.add_argument("--since", type=int, default=pi_sessions.listing.DEFAULT_SINCE_DAYS, metavar="DAYS",
                        help="window in days (default %(default)s)")
    lister.add_argument("--all", action="store_true", help="every session in the window, briefed or not")
    lister.add_argument("--json", action="store_true")
    shower = sub.add_parser("show", help="one session by id or path")
    shower.add_argument("id")
    shower.add_argument("--json", action="store_true")
    facter = sub.add_parser("facts", help="the worktrees and PRs one session touched")
    facter.add_argument("session_file")
    facter.add_argument("--json", action="store_true")
    liver = sub.add_parser("live", help="record or clear a pi process's live marker")
    liver.add_argument("--pid", type=int, required=True)
    liver.add_argument("--session-file")
    liver.add_argument("--clear", action="store_true")
    closer = sub.add_parser("close", help="end a live session's pi process; the file stays")
    closer.add_argument("id")
    return parser


def _root() -> Path:
    return pi_sessions.session_file.sessions_root(os.environ)


def _line(row: pi_sessions.listing.SessionRow) -> str:
    state = "live" if row.live else "closed"
    return f"{row.id}  {row.last_active}  {state}  {row.name or row.title_set or ''}"


def _list(args: argparse.Namespace) -> int:
    now = time.time()
    rows = pi_sessions.listing.list_sessions(_root(), now=now, since_days=args.since,
                                             live=pi_sessions.live.running(now), include_all=args.all)
    if not args.json:
        for row in rows:
            print(_line(row))
        return 0
    print(json.dumps({"schema": SCHEMA, "since_days": args.since,
                      "now": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "sessions": [row.to_json() for row in rows]}))
    return 0


def _show(args: argparse.Namespace) -> int:
    row = pi_sessions.listing.show(_root(), args.id, live=pi_sessions.live.running(time.time()))
    if row is None:
        print(f"{SCRIPT}: no pi session {args.id}", file=sys.stderr)
        return 1
    print(json.dumps(row.to_json()) if args.json else _line(row))
    return 0


def _facts(args: argparse.Namespace) -> int:
    found = pi_sessions.facts.collect(Path(args.session_file))
    on_board = [ref for wt in found.worktrees if wt.repo_key and wt.branch
                for row in board.cache.open_prs_on_branch(wt.repo_key, wt.branch)
                if (ref := pi_sessions.facts.pr_from_board_row(row)) is not None]
    prs = pi_sessions.facts.merge_prs(found.seen_prs, on_board)
    worktrees = [wt.path for wt in found.worktrees]
    if args.json:
        print(json.dumps({"worktrees": worktrees, "prs": [p.to_json() for p in prs]}))
    else:
        print("\n".join(worktrees + [p.url for p in prs]))
    return 0


def _live(args: argparse.Namespace) -> int:
    if args.clear:
        pi_sessions.live.clear(args.pid)
        return 0
    if not args.session_file:
        print(f"{SCRIPT}: live needs --session-file or --clear", file=sys.stderr)
        return 2
    path = Path(args.session_file)
    header = pi_sessions.session_file.read_header(path)
    if header is None:
        print(f"{SCRIPT}: {path} is not a pi session file", file=sys.stderr)
        return 1
    pi_sessions.live.record(args.pid, path, header, os.environ)
    return 0


def _close(args: argparse.Namespace) -> int:
    path = pi_sessions.session_file.find_session(_root(), args.id)
    marker = pi_sessions.live.marker_for(str(path)) if path else None
    if marker is None or not pi_sessions.live.close(marker):
        print(f"{SCRIPT}: {args.id} is not running here", file=sys.stderr)
        return 1
    return 0


_COMMANDS = {"list": _list, "show": _show, "facts": _facts, "live": _live, "close": _close}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _COMMANDS[args.command](args)
