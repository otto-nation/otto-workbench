"""Structured trail logging for otto-workbench AI scripts.

Every script writes an append-only JSONL trail to its artifact directory.
The trail is always written; the --debug flag controls stderr echo only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4


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

TRAIL_FILENAME = "trail.jsonl"
SCHEMA_VERSION = 1

WORKBENCH_DIR = ".workbench"

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

_print_lock = threading.Lock()


class Trail:
    def __init__(
        self,
        script: str,
        artifact_dir: str,
        context: dict,
        invocation: str,
        debug: bool,
        start_ns: int,
    ):
        self._script = script
        self._artifact_dir = Path(artifact_dir)
        self._context = context
        self.invocation = invocation
        self._debug = debug
        self._start_ns = start_ns
        self._trail_path = self._artifact_dir / TRAIL_FILENAME

    @classmethod
    def start(
        cls,
        script: str,
        artifact_dir: str,
        context: dict,
        debug: bool = False,
    ) -> Trail:
        debug = debug or os.environ.get("WORKBENCH_DEBUG", "") == "1"
        artifact_path = Path(artifact_dir)
        artifact_path.mkdir(parents=True, exist_ok=True)
        invocation = uuid4().hex[:8]
        return cls(
            script=script,
            artifact_dir=artifact_dir,
            context=context,
            invocation=invocation,
            debug=debug,
            start_ns=time.monotonic_ns(),
        )

    def _emit(self, event: TrailEvent) -> None:
        line = event.to_json()
        with _print_lock:
            with open(self._trail_path, "a") as f:
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
            context=self._context,
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

    def finish(self) -> None:
        elapsed_ms = (time.monotonic_ns() - self._start_ns) // 1_000_000
        self._emit(self._make_event(Level.INFO, EventType.SUMMARY, "finish", "", duration_ms=elapsed_ms))


# ── Argparse helper ───────────────────────────────────────────────────────

def add_trail_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--debug", action="store_true", help="Echo trail events to stderr")
