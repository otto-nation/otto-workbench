"""Session transcripts, across every agent harness on this machine.

The Python owner of what a session is and where one lives. Before this module,
four skills — dream, promote, retro, wiki-capture — each answered that question
themselves, and each answered it with Claude Code's directory layout. Interactive
work moved to Pi and all four went quietly blind: on 2026-09-16 the trailing week
held 124 Pi sessions carrying 802 human messages that no consumer could see, and
``dream-scan`` reported 775 signals of which every one was an agent preamble.

Two things make a harness's sessions findable, and the harnesses disagree on
both, so both live in ``HARNESSES`` rather than at any call site:

*Layout.* Claude keeps one directory per session cwd, with its transcripts
directly inside. Pi does the same but also writes subagent transcripts flat at
its sessions root, so walking project directories alone excludes them — 405 of
them on this machine, dropped without reading a byte. Claude has no such
separation; its headless runs land beside interactive ones and are told apart by
content instead, which is what ``is_automation_prompt`` is for.

*Record shape.* Claude writes ``{"type": "user", "message": {...}}``; Pi writes
``{"type": "message", "message": {"role": "user", ...}}``. ``iter_user_messages``
normalises both to ``UserMessage`` so consumers never branch on harness.

The slug a directory is named for is deliberately never parsed back into a path.
Claude's transform maps every non-alphanumeric to ``-``, so ``a-b`` and ``a_b``
both become ``a-b`` and the original is unrecoverable; Pi keeps underscores and
wraps in a doubled delimiter, so the two harnesses do not even agree on the
encoding. Both write the cwd *into* the transcript, which is a fact rather than
an inference, so ``project_path_of`` reads that. The slug is written, never read.

``lib/ai/session-count.sh`` is the shell expression of the same model, for the
Stop-hook gates that cannot afford a Python start-up. ``tests/sessions_ssot.bats``
runs both against one fixture tree and fails when they disagree.
"""

# doc-group: platform

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from core import log

# ── Harness layouts ──────────────────────────────────────────────────────────

# Transcripts sit directly in a per-cwd directory under the harness root, and
# nothing else in that root is a session. Claude Code.
LAYOUT_PROJECT_DIRS = "project_dirs"

# As above, plus flat files at the root that are not interactive sessions. Pi
# writes subagent runs there. Walking the per-cwd directories skips them.
LAYOUT_PROJECT_DIRS_WITH_FLAT_SUBAGENTS = "project_dirs_with_flat_subagents"

TRANSCRIPT_GLOB = "*.jsonl"


@dataclass(frozen=True)
class Harness:
    """One agent harness's on-disk session store.

    ``root`` is relative to ``$HOME`` rather than absolute so tests can point a
    whole tree at a fixture directory by overriding home alone, which is how
    every other path-sensitive suite in the repo is written.
    """

    name: str
    root: Path
    layout: str


# ceiling: neither harness documents its session directory layout as a contract,
# so this table is an observation of what both do today. Upgrade trigger: when a
# harness reorganises its store, discover_sessions() silently returns nothing for
# it — dream-scan's report header prints a per-harness session count so that
# shows up as a zero in the output rather than as a quietly halved corpus.
HARNESSES: tuple[Harness, ...] = (
    Harness("claude", Path(".claude") / "projects", LAYOUT_PROJECT_DIRS),
    Harness(
        "pi",
        Path(".pi") / "agent" / "sessions",
        LAYOUT_PROJECT_DIRS_WITH_FLAT_SUBAGENTS,
    ),
)


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Session:
    """One transcript on disk, with the harness that wrote it."""

    path: Path
    harness: str

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime)


@dataclass(frozen=True)
class UserMessage:
    """One human turn, normalised across harnesses.

    A frozen type rather than a tuple because it carries three pieces of
    business data and callers group by each of them — see general.md § Types
    Over Tuples. ``when`` comes from the record's own timestamp, never from the
    file's mtime: a session spanning midnight would otherwise date every message
    in it to whenever the session last ended, and "how many distinct dates
    mention this" is exactly how dream weighs a signal.
    """

    text: str
    when: datetime
    project_path: Path | None
    harness: str

    @property
    def date(self) -> str:
        return self.when.strftime("%Y-%m-%d")


# ── Automation prompts ───────────────────────────────────────────────────────

# Openings that mark a "user" turn as machine-generated. Every headless agent
# this workbench runs introduces itself with one of these, and none of them is
# something a person types to start a turn.
#
# Matched as a prefix against the stripped text, never as a substring: a human
# quoting a preamble back — "why did the reviewer say 'You are an adversarial
# reviewer'?" — is a real turn and must survive. Prefix matching keeps it.
#
# Pi needs this as much as Claude does despite keeping subagents in separate
# files, because `pr review` and friends invoke Pi in-place for fix passes.
AUTOMATION_PREFIXES: tuple[str, ...] = (
    # ai/lib/review — preflight context block, prepended to every review agent
    "### Project context",
    # ai/lib/review, ai/lib/fix — agent role preambles
    "You are an adversarial reviewer",
    "You are a code review triage assistant",
    "You are resolving merge conflicts",
    "You are resolving a merge conflict",
    "You are completing the final self-review",
    # ai/lib/rebase — per-commit conflict resolution
    "You are resolving",
    # pr review --self, pr comments --fix
    "Self-review of changes on branch",
    "Fix review findings for branch",
    # ai/lib/agent — retry preambles after a turn-limit or no-op pass
    "IMPORTANT: A previous attempt",
    "This is a RETRY of a prior fix pass",
    # Harness-injected wrappers: slash commands, skill bodies, hook output
    "<command-name>",
    "<local-command",
    "<skill name=",
    "<system-reminder",
    "Caveat: The messages below",
    # Pi job_start completion notices, delivered as a user turn
    "Job finished:",
)

# Below this a turn carries no signal worth storing — "ok", "yes", "continue".
MIN_SIGNAL_LENGTH = 10


def is_automation_prompt(text: str) -> bool:
    """True when this turn was written by a pipeline rather than a person.

    Pure and dependency-free so tests can exercise it without building a session
    tree — the same split ai/pi/extensions/sleep-guard uses, for the same reason.
    """
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in AUTOMATION_PREFIXES)


# ── Discovery ────────────────────────────────────────────────────────────────


def _harness_root(home: Path, harness: Harness) -> Path:
    return home / harness.root


def _session_dirs(home: Path, harness: Harness) -> Iterator[Path]:
    """Per-cwd directories under one harness's root.

    Both layouts keep interactive transcripts in directories; the flat files
    beside them, where a harness writes any, are not sessions. Selecting
    directories covers both cases without naming what the flat files are.
    """
    root = _harness_root(home, harness)
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            yield entry


def discover_sessions(
    home: Path,
    since: datetime | None = None,
    harness: str | None = None,
) -> list[Session]:
    """Every interactive transcript, newest-modified first.

    ``since`` filters on file mtime, which is the cheap approximation used to
    decide whether a transcript is worth opening at all. Per-message times come
    from the records themselves once it is open.
    """
    sessions: list[Session] = []
    for h in HARNESSES:
        if harness is not None and h.name != harness:
            continue
        sessions.extend(_discover_one(home, h, since))
    return sorted(sessions, key=lambda s: s.modified, reverse=True)


def _discover_one(home: Path, harness: Harness, since: datetime | None) -> list[Session]:
    found: list[Session] = []
    for session_dir in _session_dirs(home, harness):
        found.extend(_transcripts_in(session_dir, harness, since))
    return found


def _transcripts_in(
    session_dir: Path, harness: Harness, since: datetime | None
) -> list[Session]:
    return [
        Session(path=path, harness=harness.name)
        for path in sorted(session_dir.glob(TRANSCRIPT_GLOB))
        if _modified_since(path, since)
    ]


def _modified_since(path: Path, since: datetime | None) -> bool:
    if since is None:
        return True
    try:
        return datetime.fromtimestamp(path.stat().st_mtime) >= since
    except OSError:
        return False


def session_counts(home: Path, since: datetime | None = None) -> dict[str, int]:
    """Transcripts per harness. Surfaced in dream-scan's report header so a
    harness that has stopped being discovered reads as a zero rather than as a
    corpus that quietly halved."""
    return {
        h.name: len(discover_sessions(home, since=since, harness=h.name))
        for h in HARNESSES
    }


# ── Reading ──────────────────────────────────────────────────────────────────


def _iter_records(session: Session) -> Iterator[dict]:
    try:
        raw = session.path.read_text(errors="replace")
    except OSError:
        log.warn(f"Could not read {session.path}")
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield record


def project_path_of(session: Session) -> Path | None:
    """The cwd a session ran in, read from the transcript's own records.

    Never derived from the directory name: see the module docstring on why both
    harnesses' slugs are one-way. Pi states the cwd once in a leading ``session``
    record; Claude repeats it on each record. Scanning for the first record that
    carries one covers both without branching on harness.
    """
    for record in _iter_records(session):
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd:
            return Path(cwd)
    return None


def iter_user_messages(session: Session) -> Iterator[UserMessage]:
    """Human turns in one transcript, with automation and empties dropped."""
    project_path = project_path_of(session)
    for record in _iter_records(session):
        text = _user_text(record)
        if text is None or len(text) < MIN_SIGNAL_LENGTH:
            continue
        if is_automation_prompt(text):
            continue
        yield UserMessage(
            text=text,
            when=_record_time(record) or session.modified,
            project_path=project_path,
            harness=session.harness,
        )


def _user_text(record: dict) -> str | None:
    """The human-typed text of a record, or None when it is not a user turn.

    Claude marks the turn on the record (``type: "user"``); Pi marks it on the
    message (``type: "message"`` with ``message.role: "user"``). Both then carry
    either a bare string or a content-block list.
    """
    message = record.get("message")
    if not isinstance(message, dict):
        return None

    record_type = record.get("type")
    if record_type == "user":
        pass  # Claude: the record type is the role.
    elif record_type == "message" and message.get("role") == "user":
        pass  # Pi: the role is on the message.
    else:
        return None

    return _content_text(message.get("content"))


def _content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    joined = " ".join(p for p in parts if p).strip()
    return joined or None


def _record_time(record: dict) -> datetime | None:
    """The record's own timestamp.

    Claude writes an ISO string on the record. Pi writes one there too and an
    epoch-milliseconds copy on the message; either answers the question, so the
    ISO form is tried first and the numeric one is the fallback.
    """
    stamp = record.get("timestamp")
    if isinstance(stamp, str):
        parsed = _parse_iso(stamp)
        if parsed is not None:
            return parsed

    message = record.get("message")
    if isinstance(message, dict):
        millis = message.get("timestamp")
        if isinstance(millis, (int, float)):
            return datetime.fromtimestamp(millis / 1000)
    return None


def _parse_iso(stamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Local time throughout: every consumer groups by calendar date as the user
    # experienced it, and a UTC date rolls over mid-evening here.
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


# ── Project identity ─────────────────────────────────────────────────────────

# What a memory directory is named for. Pi's transform rather than Claude's
# because it keeps underscores, so `feat/add_auth` and `feat/add-auth` stay
# distinct where Claude's would collide them. The doubled delimiter is Pi's too
# and is kept so a slug is recognisable as one on sight.
#
# lib/ai/session-count.sh spells the same transform for shell; a divergence is
# two tools disagreeing about which directory a repo's memory lives in, so
# tests/sessions_ssot.bats fails when they drift.
def canonical_slug(path: Path | str) -> str:
    """The directory name standing for a project path."""
    text = str(path).strip("/")
    encoded = "".join(c if c.isalnum() or c == "_" else "-" for c in text)
    return f"--{encoded}--"
