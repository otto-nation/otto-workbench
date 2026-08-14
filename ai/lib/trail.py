"""Structured trail logging for otto-workbench AI scripts.

Every script writes an append-only JSONL trail to its artifact directory.
The trail is always written; the --debug flag controls stderr echo only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

import log


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


def _ensure_gitignored(artifact_path: Path) -> None:
    """Add the artifact directory to .gitignore when it sits inside a repo.

    The trail is the only thing that creates this directory now that state is
    user-scoped, so keeping it out of the consumer repo's diff lands here. Every
    trail passes through, not only `.workbench` inside a consumer repo, so
    whether the directory is inside a repo is checked rather than assumed: a
    review artifact dir under state_dir() usually is not, but a machine whose
    ~/.config is itself a git repo puts one there.

    The rule written is the directory's path relative to the repo root, anchored
    with a leading slash — a bare name would ignore every directory called that
    anywhere in the tree, and this function only ever means the one directory it
    just created.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(artifact_path),
             "rev-parse", "--show-toplevel", "--show-prefix"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return

    lines = r.stdout.splitlines()
    toplevel = lines[0].strip() if lines else ""
    # --show-prefix is empty when the artifact dir *is* the repo root, which is
    # not a directory anyone can ignore.
    rel = lines[1].strip().strip("/") if len(lines) > 1 else ""
    # Emptiness is checked alongside the exit code because Path("") is Path("."):
    # a rev-parse that somehow succeeds saying nothing would write .gitignore
    # into whatever directory the process happens to be standing in.
    if r.returncode != 0 or not toplevel or not rel:
        return

    ignored = subprocess.run(
        ["git", "-C", toplevel, "check-ignore", "-q", rel],
        capture_output=True,
    )
    if ignored.returncode == 0:
        return

    gitignore = Path(toplevel) / ".gitignore"
    try:
        content = gitignore.read_text() if gitignore.exists() else ""
        needs_newline = bool(content) and not content.endswith("\n")
        lead = "\n" if needs_newline else ""
        separator = "\n" if content else ""
        with open(gitignore, "a") as f:
            f.write(f"{lead}{separator}# Run artifacts (otto-workbench AI scripts)\n/{rel}/\n")
    except OSError as e:
        log.warn(f"trail: could not update {gitignore}: {e}")
        return
    log.info(f"trail: added /{rel}/ to {gitignore}")


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
        # mkdir's own FileExistsError, not a preceding exists() check, decides who
        # created the directory: two processes racing to start a trail at the same
        # target must not both see "created" and both append to .gitignore.
        try:
            artifact_path.mkdir(parents=True)
            created = True
        except FileExistsError:
            created = False
        if created:
            _ensure_gitignored(artifact_path)
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
