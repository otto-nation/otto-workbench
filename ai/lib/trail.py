"""Structured trail logging for otto-workbench AI scripts.

Every script appends to one root, ``workbench_paths.trail_dir()``, in a file
named for the emitting event's UTC month. The trail is always written; the
--debug flag controls stderr echo only.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
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
# now writes into one root that nothing prunes, and `otto-log show` loads all of
# it — so an ID has to stay unique across the machine's whole recorded history,
# not just one worktree's file. 8 characters put the birthday bound around 65k
# invocations, and one tool alone logged 9,134 in two months, so a machine
# reaches that in about a year; 48 bits moves the bound to 16.7M. Readers match
# the field whole, so records minted at the old width keep resolving.
INVOCATION_HEX_WIDTH = 12

# The action on the one summary event that reports a run's own duration.
# `pr gc` writes a second kind of summary with no duration, so readers select
# the run-end event by action rather than by type.
FINISH_ACTION = "finish"

_ANSI_DIM = "\033[2m"
_ANSI_RESET = "\033[0m"
_ANSI_LEVELS = {
    Level.DEBUG: "\033[2m",
    Level.INFO: "\033[1;34m",
    Level.WARN: "\033[1;33m",
    Level.ERROR: "\033[1;31m",
}


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
    ):
        self._script = script
        self._context = context
        self.invocation = invocation
        self._debug = debug
        self._start_ns = start_ns

    @classmethod
    def start(cls, script: str, context: dict, debug: bool = False) -> Trail:
        debug = debug or os.environ.get("WORKBENCH_DEBUG", "") == "1"
        workbench_paths.trail_dir().mkdir(parents=True, exist_ok=True)
        return cls(
            script=script,
            context=context,
            invocation=uuid4().hex[:INVOCATION_HEX_WIDTH],
            debug=debug,
            start_ns=time.monotonic_ns(),
        )

    def _emit(self, event: TrailEvent) -> None:
        line = event.to_json()
        # The month comes from the event, not from the run: a run crossing a
        # month boundary writes each record to the file its timestamp names.
        path = workbench_paths.trail_dir() / f"{event.ts[:7]}.jsonl"
        with _emit_lock:
            # _emit_lock covers this process's worker threads; the flock covers
            # the other processes appending to the same file — `pr` and the
            # script it spawned. A short write — NFS, a signal, an rlimit —
            # splits a record across two write() calls, and without the flock
            # the other process's append can land in the gap between them.
            with open(path, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(line + "\n")
            if self._debug:
                self._echo_stderr(event)

    def _echo_stderr(self, event: TrailEvent) -> None:
        ts_short = event.ts[11:19]
        level_color = _ANSI_LEVELS.get(event.level, "")
        level_str = event.level.value.upper().ljust(5)
        etype = event.event_type.value.ljust(11)
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
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            schema_version=SCHEMA_VERSION,
            invocation=self.invocation,
            script=self._script,
            level=level,
            event_type=event_type,
            action=action,
            detail=detail,
            # Merged into a new dict rather than updated in place: the run's own
            # context has to survive an event that names a different subject.
            context={**self._context, **context} if context else self._context,
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
            elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
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
        elapsed_ms = (time.monotonic_ns() - self._start_ns) // 1_000_000
        self._emit(self._make_event(
            Level.INFO, EventType.SUMMARY, FINISH_ACTION, "", duration_ms=elapsed_ms))


# ── Argparse helper ───────────────────────────────────────────────────────

def add_trail_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--debug", action="store_true", help="Echo trail events to stderr")
