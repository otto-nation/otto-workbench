"""Claude Code sessions, read into the same rows as pi's: transcripts, titles and live processes.

A transcript is ``<root>/projects/<cwd-slug>/<session-id>.jsonl`` (``claude_root(env)``
is ``$CLAUDE_CONFIG_DIR`` when set and non-empty, else ``~/.claude``). It carries no
brief: the id is the file name, the cwd is the first entry that names one
(``read_header(path)``), and the title is the latest ``custom-title`` entry (a name
a person chose) else the latest ``ai-title`` (``read_titles(path, limit=)`` returns a
``Titles``). ``transcripts(root)`` lists them sorted; ``find_transcript(root, key)``
takes an absolute path or a session id.

A running Claude Code records itself in ``<root>/sessions/<pid>.json`` (pid, session
id, cwd). ``running(root, now)`` reads those back into ``Live`` facts keyed by
transcript, the way ``pi_sessions.live.running`` does for pi — RSS from ``ps`` for a
pid whose command is ``claude``, idle as *now* minus the transcript's mtime — with no
herdr terminal, since Claude records none. ``close(root, session_id)`` sends SIGTERM
to that process; the transcript stays.
"""

# ceiling: a title is found only within the last TITLE_LIMIT bytes of a transcript;
# upgrade to a head-and-tail read if a long session loses its title there.

# doc-group: platform

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pi_sessions.live import Live, rss_kb_if_command
from pi_sessions.session_file import Header, lines_from_end, parse_entry

COMMAND = "claude"
HEADER_LINES = 64
TITLE_LIMIT = 1024 * 1024
OLD_TITLE_LIMIT = 64 * 1024


@dataclass(frozen=True)
class Titles:
    """The names a transcript carries: one a person chose, and the one Claude derived."""

    custom: str | None
    ai: str | None


def claude_root(env: Mapping[str, str]) -> Path:
    override = env.get("CLAUDE_CONFIG_DIR", "")
    return Path(override) if override else Path.home() / ".claude"


def transcripts(root: Path) -> list[Path]:
    return sorted((root / "projects").glob("*/*.jsonl"))


def find_transcript(root: Path, key: str) -> Path | None:
    candidate = Path(key)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    if not key or "/" in key:
        return None
    found = sorted((root / "projects").glob(f"*/{key}.jsonl"))
    return found[0] if found else None


def read_header(path: Path) -> Header | None:
    """The id from the file name; the cwd and start from the first entry that names a cwd."""
    try:
        with path.open("rb") as fh:
            for _, line in zip(range(HEADER_LINES), fh):
                entry = parse_entry(line)
                cwd = entry.get("cwd") if entry else None
                if isinstance(cwd, str) and cwd:
                    return Header(id=path.stem, cwd=cwd, timestamp=str(entry.get("timestamp") or ""))
    except OSError:
        return None
    return None


def _title(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) and value else None


def read_titles(path: Path, *, limit: int = TITLE_LIMIT) -> Titles:
    """The latest custom and AI titles within the last *limit* bytes of *path*."""
    custom: str | None = None
    ai: str | None = None
    try:
        for line in lines_from_end(path, limit):
            entry = parse_entry(line)
            kind = entry.get("type") if entry else None
            if kind == "custom-title" and custom is None:
                custom = _title(entry, "customTitle")
            elif kind == "ai-title" and ai is None:
                ai = _title(entry, "aiTitle")
            if custom is not None and ai is not None:
                break
    except OSError:
        return Titles(custom=None, ai=None)
    return Titles(custom=custom, ai=ai)


def _record(path: Path) -> tuple[int, str] | None:
    try:
        data = json.loads(path.read_text())
        return int(data["pid"]), str(data["sessionId"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _records(root: Path) -> list[tuple[int, str]]:
    found = [_record(p) for p in sorted((root / "sessions").glob("*.json"))]
    return [r for r in found if r is not None]


def running(root: Path, now: float, *, run: Callable = subprocess.run) -> dict[str, Live]:
    """Every live Claude Code here that has a transcript, keyed by transcript file."""
    out: dict[str, Live] = {}
    for pid, session_id in _records(root):
        path = find_transcript(root, session_id)
        kb = rss_kb_if_command(pid, COMMAND, run)
        if path is None or kb is None:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        out[str(path)] = Live(pid=pid, rss_mb=kb // 1024, idle_s=max(0, int(now - mtime)), terminal="")
    return out


def close(root: Path, session_id: str, *, run: Callable = subprocess.run, kill: Callable | None = None) -> bool:
    for pid, sid in _records(root):
        if sid != session_id or rss_kb_if_command(pid, COMMAND, run) is None:
            continue
        try:
            (kill or os.kill)(pid, signal.SIGTERM)
        except OSError:
            return False
        return True
    return False
