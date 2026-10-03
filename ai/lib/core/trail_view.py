"""What `otto-log` prints about the trail.

That is one event as a line, one command as a timeline with its header, a
listing as one row per command, and the `prune` report, plus the read
subcommands that select what to print.

Not: discovery, parsing or filtering (`core.trail_query`), writing or sweeping
(`core.trail`), the usage table (`agent.usage_stats`), argument parsing
(`cli.otto_log`).
"""

# doc-group: platform

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import core.trail
import core.trail_query
import core.workbench_paths

# Reading the root is `core.trail_query`'s, not this module's: the scans report
# on the machine's own tooling from the same records, and a reader they can
# import beats each one re-parsing what this CLI renders. Only what this module
# calls is imported — a name a test wants comes from `core.trail_query` too,
# rather than being re-exported from here for it.

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_LEVELS = {
    "debug": "\033[2m",
    "info": "\033[1;34m",
    "warn": "\033[1;33m",
    "error": "\033[1;31m",
}


# ── Formatting ────────────────────────────────────────────────────────────

# The level and event-type columns are sized by `trail`, which owns the enums
# that bound them. Actions are free-form strings, so this one is chosen rather
# than derived: it holds every action written today, and a longer one pushes the
# rest of its own line right instead of being cut off.
_ACTION_WIDTH = 20

# Where the detail column starts, so a reason folded onto its own line lands
# under the detail it explains. Summed from the columns ahead of it — including
# the single space after each — rather than stated, because a column that
# changes width moves this with it.
_DETAIL_COLUMN = sum((
    core.trail.TS_TIME_OF_DAY.stop - core.trail.TS_TIME_OF_DAY.start, 1,
    core.trail.LEVEL_WIDTH, 1,
    core.trail.EVENT_TYPE_WIDTH, 1,
    _ACTION_WIDTH, 1,
))

# Durations are recorded in milliseconds. Every line that prints one abbreviates
# to the largest unit that still reads as a number rather than a digit count.
MS_PER_SECOND = 1_000

# The invocation listing is a second layout — one row per run rather than per
# event. Both widths are chosen rather than derived, since neither a script name
# nor an event count is bounded; a value past either widens its own row instead
# of being cut off. The longest script name writing trails today is
# `review-orchestrate`, at 18 characters.
_SCRIPT_WIDTH = 20
_EVENT_COUNT_WIDTH = 3


def _format_event_line(e: dict, script_width: int = 0) -> str:
    """One event as a line, optionally naming the script that wrote it.

    *script_width* is 0 for a listing whose records all come from one process,
    where the name would be the same on every line. A timeline spanning a whole
    command passes a width, because there the script is the only thing
    distinguishing `pr`'s dispatch from the review its child ran.
    """
    ts = e.get("ts", "")[core.trail.TS_TIME_OF_DAY]
    level = e.get("level", "?").upper().ljust(core.trail.LEVEL_WIDTH)
    etype = e.get("event_type", "?").ljust(core.trail.EVENT_TYPE_WIDTH)
    action = e.get("action", "?").ljust(_ACTION_WIDTH)
    detail = e.get("detail", "")
    color = ANSI_LEVELS.get(e.get("level", ""), "")
    origin = f"{e.get('script', '?'):<{script_width}} " if script_width else ""
    parts = [f"{ts} {origin}{color}{level}{ANSI_RESET} {etype} {action} {detail}"]
    # The continuation lines indent past the script column too, so a reason or a
    # log path still lands under the detail it belongs to.
    hang = _DETAIL_COLUMN + (script_width + 1 if script_width else 0)
    if e.get("reason"):
        parts.append(f"\n{'':>{hang}}reason: {e['reason']}")
    if e.get("duration_ms") is not None:
        parts.append(f" ({e['duration_ms']}ms)")
    artifact = (e.get("data") or {}).get("log")
    if artifact:
        # Absolute, because the reader's next move is to open it, and the record
        # stores it relative so a state root that moves still resolves.
        full = core.workbench_paths.trail_dir() / artifact
        parts.append(f"\n{'':>{hang}}log: {full}")
    return "".join(parts)


def _finish_event(events: list[dict], invocation: str | None = None) -> dict | None:
    """The one summary that reports a run's own duration.

    `summary` is no longer a single kind of event — `pr gc` writes a terminal
    per-PR outcome with the same type and no duration — so the run-end event is
    selected by action.

    *invocation* names which process's finish to take. A command spans several,
    each writing its own, and the outermost one's is the only one that measures
    the command: a child's covers the part of it that child ran. Omitted, the
    first finish in the list wins, which is the same event whenever the records
    come from a single process.
    """
    for e in events:
        if e.get("event_type") != "summary" or e.get("action") != core.trail.FINISH_ACTION:
            continue
        if invocation is None or e.get("invocation") == invocation:
            return e
    return None


def _command_duration_ms(events: list[dict], invocation: str | None = None) -> int | None:
    """How long the whole command took, not just the process that opened it.

    The root's own `duration_ms` measures that process, which is the answer
    whenever the command ends when the root exits — every process tree does.
    A command can also gain records *after* its root has gone: `otto-log record`
    files an event under an inherited root, so the phases an agent runs between
    a scan and its close land under the scan's command minutes after the scan
    exited. Taking the root's figure there reports the scan alone, and prints a
    sub-second duration beside an hour of work.

    So the run's start is derived from its own finish — `ts` back off
    `duration_ms`, the one point where a wall clock and the monotonic clock name
    the same instant — and the command runs from there to its last record. When
    the root's finish *is* the last record the two agree exactly, which is what
    keeps every command written before `record` existed reading as it always did.

    None when no finish survives: a killed run's duration is unknown, and a
    span between whatever records it managed to write would be a guess with no
    lower bound on how wrong it is.

    The extension is only as good as the clocks involved — it trusts a later
    process's wall clock, where the root's own figure was one monotonic reading.
    Same machine, so the skew is not worth modelling; it is worth knowing that
    a timestamp is second-granular, so a trailing record rounds the total to the
    second it landed in.
    """
    finish = _finish_event(events, invocation=invocation)
    if finish is None or finish.get("duration_ms") is None:
        return None
    started = _event_time(finish)
    last = _event_time(events[-1]) if events else None
    if started is None or last is None:
        return finish["duration_ms"]
    started -= timedelta(milliseconds=finish["duration_ms"])
    # Never shorter than what the root measured: a record whose clock reads
    # behind the root's would otherwise shorten a run that demonstrably ran.
    return max(finish["duration_ms"], round((last - started).total_seconds() * MS_PER_SECOND))


def _event_time(event: dict) -> datetime | None:
    """An event's stamp as a time, or None when it does not parse.

    A record with an unreadable `ts` is rendered rather than dropped elsewhere,
    so timing it falls back instead of failing the whole listing.
    """
    try:
        return datetime.strptime(event.get("ts", ""), core.trail.TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _command_label(scripts: list[str]) -> str:
    """How a listing names a command: its outermost script, and how many others.

    A row stands for every process in the command, so a `pr review` reads
    `pr +2` rather than claiming to be `pr` alone. A command of one process —
    which is every pre-cutover record — is named exactly as it always was.
    """
    outermost = scripts[0] if scripts else "?"
    return f"{outermost} +{len(scripts) - 1}" if len(scripts) > 1 else outermost


def _scripts_in_order(events: list[dict]) -> list[str]:
    """The distinct scripts that wrote *events*, outermost first.

    Ordered by first appearance rather than sorted: the events are already in
    timestamp order, so this is the order the processes started in, which is the
    order the reader watched them run.
    """
    return list(dict.fromkeys(e.get("script", "?") for e in events))


def _format_show(events: list[dict], focus: str | None = None) -> str:
    """One command's events as a timeline, headed by what it was.

    The header names the root invocation and every script that ran under it, so
    a `pr review` reads as the three processes it is. A command of one process
    renders as it always did, which is also what a pre-cutover record gives.

    *focus* names the run the header reports on, for a listing narrowed to one
    process inside a command: without it the header would name the whole
    command's root, and look for a duration under an ID no surviving event
    carries. Defaults to the root, which is what the events describe.
    """
    if not events:
        return "No events found."
    first = events[0]
    root = focus or core.trail_query.root_of(first)
    scripts = _scripts_in_order(events)
    ts = first.get("ts", "?")
    # That run's own finish, not whichever process ended first: a child's
    # duration covers its part, and only the headline run's covers the whole.
    duration_ms = _command_duration_ms(events, invocation=root)
    duration = f" — {duration_ms / MS_PER_SECOND:.1f}s" if duration_ms is not None else ""
    ctx = first.get("context", {})
    ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())

    # Only worth a column when there is more than one name to tell apart.
    width = max((len(s) for s in scripts), default=0) if len(scripts) > 1 else 0
    lines = [
        f"{ANSI_BOLD}Invocation {root}{ANSI_RESET} — {' → '.join(scripts)} — {ts}{duration}",
        f"Context: {ctx_str}",
    ]
    started = _format_origin(first.get("origin"))
    if started:
        lines.append(f"Started by: {started}")
    lines.append("")
    for e in events:
        lines.append(_format_event_line(e, script_width=width))
    return "\n".join(lines)


def _format_origin(origin: dict | None) -> str:
    """Who started the run, as one line, or "" when nothing was recorded.

    Rendered in the header rather than left to `--json` because this is the
    question somebody has when they open a trail for a run they did not expect:
    a fix pass wrote into a worktree and there was no process left to ask.
    Absent on records written before the field existed, which is why nothing is
    printed rather than a row of unknowns.
    """
    if not origin:
        return ""
    parent = origin.get("parent") or "?"
    parts = [f"{parent} (ppid {origin.get('ppid', '?')})"]
    if origin.get("harness"):
        parts.append(f"via {origin['harness']}")
    # Only the absence is worth saying: a run on a terminal is the ordinary case
    # and naming the device adds nothing, while no terminal is the thing a
    # surprising run has in common with every other unattended one.
    if origin.get("tty") == "":
        parts.append("no terminal")
    if origin.get("command"):
        parts.append(f"\n  {origin['command']}")
    return " ".join(parts)


def recent(since: str, *, repo: str | None = None, as_json: bool = False) -> None:
    events = core.trail_query.load_trail_events(since)
    filtered = core.trail_query.filter_events(events, repo=repo, since=since)
    if as_json:
        for e in filtered:
            print(json.dumps(e, separators=(",", ":")))
    else:
        for e in filtered:
            print(_format_event_line(e))


def query(
    *,
    script=None,
    level=None,
    event_type=None,
    invocation=None,
    root=None,
    pr=None,
    repo=None,
    since=None,
    as_json=False,
) -> None:
    events = core.trail_query.load_trail_events(since)
    filtered = core.trail_query.filter_events(
        events,
        script=script,
        level=level,
        event_type=event_type,
        invocation=invocation,
        root=root,
        pr=pr,
        repo=repo,
        since=since,
    )
    if as_json:
        for e in filtered:
            print(json.dumps(e, separators=(",", ":")))
    else:
        for e in filtered:
            print(_format_event_line(e))


def _command_events(events: list[dict], invocation: str) -> list[dict]:
    """Every event of the command *invocation* names, from either end of it.

    A user has one ID in hand and does not know whether it was the outermost
    process or one of its children — `pr` logs its own, a delegate's console
    line logs the delegate's. Both resolve to the same command: the named
    record's root is found first, then everything sharing it.
    """
    named = [e for e in events if e.get("invocation") == invocation]
    root = core.trail_query.root_of(named[0]) if named else invocation
    return core.trail_query.filter_events(events, root=root)


def show(invocation: str, *, only: bool = False, as_json: bool = False) -> None:
    events = core.trail_query.load_trail_events(None)
    if only:
        filtered = core.trail_query.filter_events(events, invocation=invocation)
    else:
        filtered = _command_events(events, invocation)
    if as_json:
        for e in filtered:
            print(json.dumps(e, separators=(",", ":")))
    else:
        print(_format_show(filtered, focus=invocation if only else None))


def list_commands(*, script=None, since=None, repo=None, as_json=False) -> None:
    """One row per user command, not per process.

    `--script` still selects by the script that wrote a record, but the row it
    produces is the whole command that record belongs to — asking for
    `review-orchestrate` lists the `pr review`s that reached it, with their own
    durations, rather than the fragment of each that one process logged.
    """
    events = core.trail_query.load_trail_events(since)
    selected = core.trail_query.filter_events(
        events, since=since, repo=repo, script=script)
    roots = {core.trail_query.root_of(e) for e in selected}
    # Re-read from the unfiltered set, so a row covers the command's whole tree
    # even when the filter matched only one process in it.
    commands: dict[str, list[dict]] = {}
    for e in events:
        root = core.trail_query.root_of(e)
        if root in roots:
            commands.setdefault(root, []).append(e)

    # Sort by first event timestamp, most recent first
    sorted_invs = sorted(commands.items(), key=lambda x: x[1][0].get("ts", ""), reverse=True)

    if as_json:
        for inv_id, inv_events in sorted_invs:
            first = inv_events[0]
            scripts = _scripts_in_order(inv_events)
            print(json.dumps({
                "invocation": inv_id,
                # The outermost script, as before. `scripts` beside it is the
                # whole tree, so a consumer reading the old key still gets the
                # same answer it always did.
                "script": first.get("script"),
                "scripts": scripts,
                "ts": first.get("ts"),
                "event_count": len(inv_events),
                "duration_ms": _command_duration_ms(inv_events, invocation=inv_id),
            }, separators=(",", ":")))
    else:
        for inv_id, inv_events in sorted_invs:
            first = inv_events[0]
            duration_ms = _command_duration_ms(inv_events, invocation=inv_id)
            duration = (
                f"{duration_ms / MS_PER_SECOND:.1f}s" if duration_ms is not None else "?")
            ts = first.get("ts", "?")[core.trail.TS_TO_SECONDS]
            script = _command_label(_scripts_in_order(inv_events))
            n = len(inv_events)
            # Padded to the current width so records minted before it widened
            # do not shift every column to their right.
            print(f"{inv_id:<{core.trail.INVOCATION_HEX_WIDTH}}  {script:<{_SCRIPT_WIDTH}} "
                  f"{ts}  {n:>{_EVENT_COUNT_WIDTH}} events  {duration}")


def prune(keep: int) -> None:
    """Sweep now, at whatever horizon the caller names.

    Every trail already sweeps at the default horizon as it opens, so this is
    for the two cases that cannot wait for one: reclaiming the root after a
    burst without running something else first, and taking history down past
    the default when a machine is short of space.
    """
    removed = core.trail.prune_trail(keep)
    if not removed:
        print(f"Trail: nothing older than the last {keep} month(s).")
        return
    root = core.workbench_paths.trail_dir()
    for path in removed:
        print(f"Trail: dropped {path.relative_to(root)}")
