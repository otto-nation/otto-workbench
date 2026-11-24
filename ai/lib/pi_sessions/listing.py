"""The session list: which pi sessions to show and what each row says.

``list_sessions(root, now=, since_days=, live=, include_all=)`` lists session
files under *root* active (by mtime) in the last *since_days* days that carry a
brief, a name or a live process — every one of them with *include_all* — plus
any older session that is live (*live*, keyed by session file, from
``pi_sessions.live.running``) or named by a person (a name other than the auto
title its last brief recorded), newest first. ``show(root, key, live=)``
is one session by id or path, whatever its age. A ``SessionRow`` carries the
header, the tail's name and brief (``title_set``, ``tldr``, ``worktrees``,
``prs``, ``spend``) and the ``Live`` facts; ``to_json()`` is the row
``pi-sessions list --json`` prints. ``harness`` says which tool owns the session
(``pi`` or ``claude``). ``list_claude(root, now=, since_days=, live=, include_all=)``
and ``show_claude(root, key, live=)`` are the same two reads over Claude Code's
transcripts (``pi_sessions.claude``): a Claude row has no brief, so it is listed when
it is live, titled by a person, or recent and titled (every recent one with
*include_all*).
"""

# ceiling: every listing stats every session file and tail-reads the ones in the
# window, and the last 64 KB of each older one to find a name; move to a
# rebuildable index if `pi-sessions list` takes over a second on an agent host.

# doc-group: platform

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pi_sessions.claude
from pi_sessions.live import Live
from pi_sessions.session_file import Tail, find_session, read_header, read_tail, session_files

DAY_S = 86400
DEFAULT_SINCE_DAYS = 9
OLD_TAIL_LIMIT = 64 * 1024


@dataclass(frozen=True)
class SessionRow:
    """One listed session."""

    id: str
    file: str
    cwd: str
    started: str
    last_active: str
    name: str | None
    title_set: str | None
    tldr: str
    worktrees: tuple[str, ...]
    prs: tuple[dict, ...]
    live: Live | None
    spend: tuple[dict, ...]
    harness: str = "pi"

    def to_json(self) -> dict:
        return {"id": self.id, "file": self.file, "cwd": self.cwd, "started": self.started,
                "last_active": self.last_active, "name": self.name, "title_set": self.title_set,
                "tldr": self.tldr, "worktrees": list(self.worktrees), "prs": list(self.prs),
                "live": self.live.to_json() if self.live else None, "spend": list(self.spend),
                "harness": self.harness}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spend(brief: dict) -> tuple[dict, ...]:
    items = []
    for item in brief.get("spend") or ():
        if not isinstance(item, dict):
            continue
        label, cost = item.get("label"), item.get("cost")
        if isinstance(label, str) and isinstance(cost, (int, float)):
            items.append({"label": label, "cost": cost})
    return tuple(items)


def _row(path: Path, mtime: float, tail: Tail, live: Live | None) -> SessionRow | None:
    header = read_header(path)
    if header is None:
        return None
    brief = tail.brief or {}
    title_set = brief.get("title_set")
    return SessionRow(
        id=header.id, file=str(path), cwd=header.cwd, started=header.timestamp,
        last_active=_iso(mtime), name=tail.name,
        title_set=title_set if isinstance(title_set, str) and title_set else None,
        tldr=str(brief.get("tldr") or ""),
        worktrees=tuple(w for w in brief.get("worktrees") or () if isinstance(w, str)),
        prs=tuple(p for p in brief.get("prs") or () if isinstance(p, dict)),
        live=live, spend=_spend(brief))


def _named(tail: Tail) -> bool:
    """A name a person chose: set, and not the auto title the brief last recorded (T3-f)."""
    title_set = (tail.brief or {}).get("title_set")
    return tail.name is not None and tail.name != title_set


def list_sessions(root: Path, *, now: float, since_days: int, live: Mapping[str, Live],
                  include_all: bool = False) -> list[SessionRow]:
    cutoff = now - since_days * DAY_S
    found: list[tuple[float, SessionRow]] = []
    for path in session_files(root):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        alive = live.get(str(path))
        recent = mtime >= cutoff
        tail = read_tail(path) if recent or alive else read_tail(path, limit=OLD_TAIL_LIMIT)
        wanted = alive is not None or _named(tail) or (recent and (include_all or tail.brief is not None))
        if not wanted:
            continue
        row = _row(path, mtime, tail, alive)
        if row is not None:
            found.append((mtime, row))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in found]


def show(root: Path, key: str, *, live: Mapping[str, Live]) -> SessionRow | None:
    path = find_session(root, key)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return _row(path, mtime, read_tail(path), live.get(str(path)))


def _claude_row(path: Path, mtime: float, titles: pi_sessions.claude.Titles, live: Live | None) -> SessionRow | None:
    header = pi_sessions.claude.read_header(path)
    if header is None:
        return None
    return SessionRow(
        id=header.id, file=str(path), cwd=header.cwd, started=header.timestamp,
        last_active=_iso(mtime), name=titles.custom, title_set=titles.ai, tldr="",
        worktrees=(), prs=(), live=live, spend=(), harness="claude")


def list_claude(root: Path, *, now: float, since_days: int, live: Mapping[str, Live],
                include_all: bool = False) -> list[SessionRow]:
    cutoff = now - since_days * DAY_S
    found: list[tuple[float, SessionRow]] = []
    for path in pi_sessions.claude.transcripts(root):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        alive = live.get(str(path))
        recent = mtime >= cutoff
        titles = pi_sessions.claude.read_titles(
            path, limit=pi_sessions.claude.TITLE_LIMIT if recent or alive else pi_sessions.claude.OLD_TITLE_LIMIT)
        wanted = alive is not None or titles.custom is not None or (recent and (include_all or titles.ai is not None))
        row = _claude_row(path, mtime, titles, alive) if wanted else None
        if row is not None:
            found.append((mtime, row))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in found]


def show_claude(root: Path, key: str, *, live: Mapping[str, Live]) -> SessionRow | None:
    path = pi_sessions.claude.find_transcript(root, key)
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return _claude_row(path, mtime, pi_sessions.claude.read_titles(path), live.get(str(path)))
