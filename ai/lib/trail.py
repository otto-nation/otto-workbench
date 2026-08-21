"""Structured trail logging for otto-workbench AI scripts.

Every script appends to one root, ``workbench_paths.trail_dir()``, in a file
named for the emitting event's UTC month. Months past ``TRAIL_KEEP_MONTHS``
are dropped as runs start, so the root stays bounded whatever writes to it.
The --debug flag controls stderr echo only; whether a run is recorded at all
is the caller's ``record`` argument to ``Trail.start``.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

import workbench_paths


# ── Enums ─────────────────────────────────────────────────────────────────

class Level(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class EventType(str, Enum):
    ACTION = "action"
    DECISION = "decision"
    SPAN_START = "span_start"
    SPAN_END = "span_end"
    ERROR = "error"
    SUMMARY = "summary"


# ── Constants ─────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# Hex characters of a uuid4 an invocation ID keeps. Every script on the machine
# writes into one root, and `otto-log show` loads every month of it that
# retention keeps — so an ID has to stay unique across all of them, not just one
# worktree's file. 8 characters put the birthday bound around 65k invocations,
# and one tool alone logged 9,134 in two months; 48 bits moves the bound to
# 16.7M. Readers match the field whole, so records minted at the old width keep
# resolving.
INVOCATION_HEX_WIDTH = 12

# Months of history the root keeps, counting the month in progress. Nothing
# used to drop anything, which was survivable while every writer was a human at
# a keyboard — but a query built to be polled writes on an interval rather than
# at human pace, and every `otto-log` query reads every file it finds. Six
# months covers what anyone actually traces, a run from this cycle or the last
# few; `otto-log prune --keep` overrides it for a one-off sweep.
TRAIL_KEEP_MONTHS = 6

# The stem the month naming produces, and the whole of what retention acts on.
# A stem that does not match — `legacy.jsonl`, where the cutover migration
# parked the pre-cutover history — cannot be placed in time by its name, so a
# reader always reads it and a sweep never drops it. Nothing appends to such a
# file, so it is a fixed size rather than a source of growth; removing one is a
# deliberate `rm`.
MONTH_STEM = re.compile(r"^\d{4}-\d{2}$")

# The action on the one summary event that reports a run's own duration.
# `pr gc` writes a second kind of summary with no duration, so readers select
# the run-end event by action rather than by type.
FINISH_ACTION = "finish"

# The stamp every event's `ts` carries, and the slices of it readers take. The
# offsets are only correct because of the format, so they are stated next to it
# rather than spelled as literals wherever a stamp is cut down.
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TS_MONTH = slice(0, 7)          # 2026-08 — names the file a record lands in
TS_TO_SECONDS = slice(0, 19)    # 2026-08-17T14:03:07 — a listing's stamp
TS_TIME_OF_DAY = slice(11, 19)  # 14:03:07 — what a rendered line leads with

# Column widths for a rendered event line, each the widest value its field can
# hold. `otto-log` renders the same fields from the same records, so the widths
# live with the enums that bound them rather than being restated per reader. An
# unknown value is padded past, not truncated: a reader's line simply widens.
LEVEL_WIDTH = max(len(level.value) for level in Level)
EVENT_TYPE_WIDTH = max(len(event_type.value) for event_type in EventType)

# Durations are measured with `time.monotonic_ns` and reported in milliseconds.
NS_PER_MS = 1_000_000

_ANSI_DIM = "\033[2m"
_ANSI_RESET = "\033[0m"
_ANSI_LEVELS = {
    Level.DEBUG: "\033[2m",
    Level.INFO: "\033[1;34m",
    Level.WARN: "\033[1;33m",
    Level.ERROR: "\033[1;31m",
}


# ── Retention ─────────────────────────────────────────────────────────────

def oldest_kept_month(now: datetime, keep_months: int) -> str:
    """The stem of the oldest month a *keep_months* horizon retains.

    Never later than the month *now* falls in, whatever is asked for: a run is
    appending to that file as this is computed, so a horizon that excluded it
    would delete the records of the invocation doing the deleting.
    """
    months = now.year * 12 + now.month - 1 - max(keep_months - 1, 0)
    return f"{months // 12:04d}-{months % 12 + 1:02d}"


def prune_trail(keep_months: int = TRAIL_KEEP_MONTHS) -> list[Path]:
    """Drop every month file older than the horizon; return what went.

    Runs as each trail opens, so it costs one readdir of a directory holding
    one file per retained month — less than the records the run is about to
    write. A file that is already gone is not an error: two runs sweeping at
    once is the ordinary case, not a race worth reporting.
    """
    cutoff = oldest_kept_month(datetime.now(timezone.utc), keep_months)
    try:
        files = sorted(workbench_paths.trail_dir().glob("*.jsonl"))
    except OSError:
        # Same reading as `otto-log`'s discovery: a root that is not there yet
        # holds nothing to drop, and a root that cannot be read is not one to
        # start deleting from.
        return []
    removed = []
    for path in files:
        if not MONTH_STEM.match(path.stem) or path.stem >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


# ── Event ─────────────────────────────────────────────────────────────────

@dataclass
class TrailEvent:
    ts: str
    schema_version: int
    invocation: str
    script: str
    level: Level
    event_type: EventType
    action: str
    detail: str
    context: dict
    reason: str | None = None
    span: str | None = None
    duration_ms: int | None = None
    data: dict | None = None

    def to_json(self) -> str:
        d = asdict(self)
        d["level"] = self.level.value
        d["event_type"] = self.event_type.value
        d = {k: v for k, v in d.items() if v is not None}
        return json.dumps(d, separators=(",", ":"))


# ── Trail ─────────────────────────────────────────────────────────────────

_emit_lock = threading.Lock()


class Trail:
    def __init__(
        self,
        script: str,
        context: dict,
        invocation: str,
        debug: bool,
        start_ns: int,
        record: bool = True,
    ):
        self._script = script
        self._context = context
        self.invocation = invocation
        self._debug = debug
        self._start_ns = start_ns
        self._record = record

    @classmethod
    def start(cls, script: str, context: dict, debug: bool = False, *,
              record: bool = True) -> Trail:
        """Open a trail for one invocation.

        ``record=False`` opens one that writes nothing. An invocation that
        resolves no subject and takes no lock reads the state root, prints, and
        returns — there is no action for an audit trail to hold, and a query
        built to be polled would otherwise write two records a tick into the
        file every `otto-log` query then reads. Such a run sweeps nothing and
        creates nothing either: it leaves the root exactly as it found it.

        ``--debug`` echoes either way, because the flag is about watching what a
        run decided rather than about what is worth keeping.
        """
        debug = debug or os.environ.get("WORKBENCH_DEBUG", "") == "1"
        if record:
            workbench_paths.trail_dir().mkdir(parents=True, exist_ok=True)
            prune_trail()
        return cls(
            script=script,
            context=context,
            invocation=uuid4().hex[:INVOCATION_HEX_WIDTH],
            debug=debug,
            start_ns=time.monotonic_ns(),
            record=record,
        )

    def _append(self, event: TrailEvent) -> None:
        # The month comes from the event, not from the run: a run crossing a
        # month boundary writes each record to the file its timestamp names.
        path = workbench_paths.trail_dir() / f"{event.ts[TS_MONTH]}.jsonl"
        # The flock covers the other processes appending to the same file —
        # `pr` and the script it spawned. A short write — NFS, a signal, an
        # rlimit — splits a record across two write() calls, and without the
        # flock the other process's append can land in the gap between them.
        with open(path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(event.to_json() + "\n")

    def _emit(self, event: TrailEvent) -> None:
        # _emit_lock covers this process's worker threads, for both halves: a
        # record is written and echoed before another thread's is.
        with _emit_lock:
            if self._record:
                self._append(event)
            if self._debug:
                self._echo_stderr(event)

    def _echo_stderr(self, event: TrailEvent) -> None:
        ts_short = event.ts[TS_TIME_OF_DAY]
        level_color = _ANSI_LEVELS.get(event.level, "")
        level_str = event.level.value.upper().ljust(LEVEL_WIDTH)
        etype = event.event_type.value.ljust(EVENT_TYPE_WIDTH)
        parts = [f"{_ANSI_DIM}[trail]{_ANSI_RESET} {ts_short} {level_color}{level_str}{_ANSI_RESET} {etype} {event.action}"]
        if event.detail:
            parts.append(f" — {event.detail}")
        if event.reason:
            parts.append(f" (reason: {event.reason})")
        if event.duration_ms is not None:
            parts.append(f" ({event.duration_ms}ms)")
        print("".join(parts), file=sys.stderr, flush=True)

    def _make_event(
        self,
        level: Level,
        event_type: EventType,
        action: str,
        detail: str,
        reason: str | None = None,
        span: str | None = None,
        duration_ms: int | None = None,
        data: dict | None = None,
        context: dict | None = None,
    ) -> TrailEvent:
        return TrailEvent(
            ts=datetime.now(timezone.utc).strftime(TS_FORMAT),
            schema_version=SCHEMA_VERSION,
            invocation=self.invocation,
            script=self._script,
            level=level,
            event_type=event_type,
            action=action,
            detail=detail,
            # Merged into a new dict rather than updated in place: the run's own
            # context has to survive an event that names a different subject.
            context={**self._context, **context} if context is not None else self._context,
            reason=reason,
            span=span,
            duration_ms=duration_ms,
            data=data,
        )

    def info(self, action: str, detail: str, data: dict | None = None) -> None:
        self._emit(self._make_event(Level.INFO, EventType.ACTION, action, detail, data=data))

    def decision(self, action: str, detail: str, *, reason: str, data: dict | None = None) -> None:
        self._emit(self._make_event(Level.INFO, EventType.DECISION, action, detail, reason=reason, data=data))

    def error(self, action: str, detail: str, data: dict | None = None) -> None:
        self._emit(self._make_event(Level.ERROR, EventType.ERROR, action, detail, data=data))

    def warn(self, action: str, detail: str, data: dict | None = None) -> None:
        self._emit(self._make_event(Level.WARN, EventType.ACTION, action, detail, data=data))

    def debug(self, action: str, detail: str, data: dict | None = None) -> None:
        self._emit(self._make_event(Level.DEBUG, EventType.ACTION, action, detail, data=data))

    @contextmanager
    def span(self, name: str):
        start_ns = time.monotonic_ns()
        self._emit(self._make_event(Level.INFO, EventType.SPAN_START, name, "", span=name))
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic_ns() - start_ns) // NS_PER_MS
            self._emit(self._make_event(Level.INFO, EventType.SPAN_END, name, "", span=name, duration_ms=elapsed_ms))

    def summary(self, action: str, detail: str, *, data: dict | None = None,
                context: dict | None = None) -> None:
        """A terminal record about something other than this run's own duration.

        `context` names the event's subject when it is not the run's — `pr gc`
        writes one of these per pruned PR, and `otto-log query --pr N` has to
        find the record for N rather than for the gc run.
        """
        self._emit(self._make_event(
            Level.INFO, EventType.SUMMARY, action, detail, data=data, context=context))

    def finish(self) -> None:
        elapsed_ms = (time.monotonic_ns() - self._start_ns) // NS_PER_MS
        self._emit(self._make_event(
            Level.INFO, EventType.SUMMARY, FINISH_ACTION, "", duration_ms=elapsed_ms))


# ── Argparse helper ───────────────────────────────────────────────────────

def add_trail_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--debug", action="store_true", help="Echo trail events to stderr")
