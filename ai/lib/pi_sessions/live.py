"""Which pi sessions are running here: markers the session-brief extension writes, read back.

A marker is ``<state>/pi-sessions/live/<pid>.json`` holding a ``Marker``: the
pid, session id and file, cwd, herdr terminal, and when it started.
``record(pid, session_file, header, env)`` writes it and, in a herdr terminal
(``HC_TERMINAL_ID``), labels that terminal ``otto.session=<id>`` — the label the
herdr mods find a live session's terminal by. ``clear(pid)`` removes it;
``markers()`` lists them by pid; ``marker_for(session_file)`` finds one.
``state(marker, now)`` asks ``ps`` for the pid's RSS and command name and returns
a ``Live`` — RSS in MB, idle seconds as *now* minus the session file's mtime
(pi appends every message), the terminal — or None when the pid is gone or is
not ``pi``. ``running(now)`` is every live marker keyed by session file.
``Live.close_candidate`` is idle over ``CLOSE_IDLE_S`` (16 h) and RSS over
``CLOSE_RSS_MB`` (200 MB). ``close(marker)`` sends SIGTERM to a live pi, whose
session_shutdown handlers run before it exits; the session file stays.
"""

# ceiling: a marker whose pi died without session_shutdown (SIGKILL, OOM) stays
# on disk and is only ignored, since ps no longer shows a pi at that pid; upgrade
# to pruning dead markers if the directory grows past a few hundred files.

# doc-group: platform

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import core.serde
import core.timeouts
import core.workbench_paths
from pi_sessions.session_file import Header

SESSION_LABEL = "otto.session"
CLOSE_IDLE_S = 16 * 3600
CLOSE_RSS_MB = 200


@dataclass(frozen=True)
class Marker:
    """What a running pi recorded about itself at session start."""

    pid: int
    session_id: str
    session_file: str
    cwd: str
    terminal: str
    started_at: str


@dataclass(frozen=True)
class Live:
    """A live session's process facts."""

    pid: int
    rss_mb: int
    idle_s: int
    terminal: str

    @property
    def close_candidate(self) -> bool:
        return self.idle_s > CLOSE_IDLE_S and self.rss_mb > CLOSE_RSS_MB

    def to_json(self) -> dict:
        return {"pid": self.pid, "rss_mb": self.rss_mb, "idle_s": self.idle_s,
                "terminal": self.terminal, "close_candidate": self.close_candidate}


def live_dir() -> Path:
    return core.workbench_paths.state_dir() / "pi-sessions" / "live"


def record(pid: int, session_file: Path, header: Header, env: Mapping[str, str], *,
           run: Callable = subprocess.run) -> Marker:
    terminal = env.get("HC_TERMINAL_ID", "")
    marker = Marker(pid=pid, session_id=header.id, session_file=str(session_file), cwd=header.cwd,
                    terminal=terminal, started_at=datetime.now(timezone.utc).isoformat())
    core.serde.write_json(live_dir() / f"{pid}.json", asdict(marker))
    if terminal:
        # Best-effort: an unlabelled terminal only means Open starts nothing new
        # for a live session and says it runs outside herdr.
        try:
            run(["hc", "label", terminal, f"{SESSION_LABEL}={header.id}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=core.timeouts.LOCAL, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return marker


def clear(pid: int) -> None:
    try:
        (live_dir() / f"{pid}.json").unlink(missing_ok=True)
    except OSError:
        return


def _read_marker(path: Path) -> Marker | None:
    try:
        data = json.loads(path.read_text())
        return Marker(pid=int(data["pid"]), session_id=str(data["session_id"]),
                      session_file=str(data["session_file"]), cwd=str(data.get("cwd", "")),
                      terminal=str(data.get("terminal", "")), started_at=str(data.get("started_at", "")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def markers() -> list[Marker]:
    found = [_read_marker(p) for p in sorted(live_dir().glob("*.json"))]
    return sorted((m for m in found if m is not None), key=lambda m: m.pid)


def marker_for(session_file: str, *, run: Callable = subprocess.run) -> Marker | None:
    """The marker for *session_file*, preferring one whose pid is a running pi over a stale one."""
    found = [m for m in markers() if m.session_file == session_file]
    return next((m for m in found if _rss_kb_if_pi(m.pid, run) is not None), found[0] if found else None)


def _rss_kb_if_pi(pid: int, run: Callable) -> int | None:
    """The pid's RSS in KB when it is running and is `pi`, else None (guards pid reuse)."""
    return rss_kb_if_command(pid, "pi", run)


def rss_kb_if_command(pid: int, command: str, run: Callable) -> int | None:
    """The pid's RSS in KB when it is running and its command name is *command*, else None."""
    try:
        done = run(["ps", "-o", "rss=", "-o", "comm=", "-p", str(pid)], capture_output=True,
                   text=True, timeout=core.timeouts.LOCAL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = (done.stdout or "").split()
    if done.returncode != 0 or len(fields) < 2 or not fields[0].isdigit():
        return None
    return int(fields[0]) if os.path.basename(fields[1]) == command else None


def state(marker: Marker, now: float, *, run: Callable = subprocess.run) -> Live | None:
    kb = _rss_kb_if_pi(marker.pid, run)
    if kb is None:
        return None
    try:
        mtime = os.stat(marker.session_file).st_mtime
    except OSError:
        return None
    return Live(pid=marker.pid, rss_mb=kb // 1024, idle_s=max(0, int(now - mtime)), terminal=marker.terminal)


def running(now: float, *, run: Callable = subprocess.run) -> dict[str, Live]:
    out: dict[str, Live] = {}
    for marker in markers():
        got = state(marker, now, run=run)
        if got is not None:
            out[marker.session_file] = got
    return out


def close(marker: Marker, *, run: Callable = subprocess.run, kill: Callable | None = None) -> bool:
    if _rss_kb_if_pi(marker.pid, run) is None:
        return False
    try:
        (kill or os.kill)(marker.pid, signal.SIGTERM)
    except OSError:
        return False
    return True
