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
both become ``a-b`` and the original is unrecoverable; Pi's transform
(``pi_session_slug``) only replaces ``/``, ``\`` and ``:`` — a dot, a space, an
accent, an emoji all survive verbatim — so the two harnesses do not even agree
on the encoding, and the harness-neutral ``canonical_slug`` (which replaces
everything outside ``[A-Za-z0-9_]``) matches neither one's store. Both write
the cwd *into* the transcript, which is a fact rather than an inference, so
``project_path_of`` reads that. The slug is written, never read.

Memory is the one thing here that is genuinely Claude-shaped: it still lives in
that harness's tree, one ``memory/`` directory per project slug. That is not a
statement about which harness a session ran in — sessions come from every
harness in the table — and moving those artifacts out is tracked separately.
``memory_dirs`` is here so the location is stated once rather than at each
consumer, which is how the transform came to be spelled four different ways.

``lib/ai/session-count.sh`` is the shell expression of the same model, for the
Stop-hook gates that cannot afford a Python start-up. ``tests/sessions_ssot.bats``
runs both against one fixture tree and fails when they disagree.
"""

# doc-group: platform

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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
#
# Every prompt template in ai/lib/review-templates must be covered by an entry
# here. The list is spelled out rather than read from those files because this
# module is layer 1 and the templates are rendered from layer 3 — core cannot
# import upward, and a scan that opened thirteen files on every session read
# would cost more than it saves. tests/sessions_test.py walks the template
# directory and fails when one of them starts with something no prefix matches,
# which is what keeps a hand-written list honest: six templates were already
# uncovered when this check was added, and their preambles were reaching dream
# as "corrections".
AUTOMATION_PREFIXES: tuple[str, ...] = (
    # ai/lib/review — preflight context block, prepended to every review agent
    "### Project context",
    # ai/lib/review, ai/lib/fix — agent role preambles
    "You are an adversarial reviewer",
    "You are a code review triage assistant",
    "You are resolving merge conflicts",
    "You are resolving a merge conflict",
    "You are completing the final self-review",
    "You are completing the final review",
    "You are doing a holistic scan",
    "You are a lead scout",
    "You are verifying fixes",
    # ai/lib/rebase — per-commit conflict resolution
    "You are resolving",
    # pr review --self, pr comments --fix, pr ci --fix
    "Self-review of changes on branch",
    "Fix review findings for branch",
    "Fix PR review comment suggestions for branch",
    "Fix CI failures for branch",
    "Fix pre-push check failures on branch",
    "Review PR #",
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
    if any(stripped.startswith(prefix) for prefix in AUTOMATION_PREFIXES):
        return True
    return _opens_with_heading(stripped)


def _opens_with_heading(stripped: str) -> bool:
    """True when a turn opens with a markdown H1, which a person does not type.

    Slash commands are the case this catches. Claude Code wraps an expanded
    command in ``<command-name>``, which the prefix list already matches, but Pi
    expands one into an ordinary user turn carrying the command file's body and
    marks it in no way at all. Those bodies live in each repo's own
    ``.claude/commands`` and ``.pi/prompts``, so there is no list of openings to
    enumerate the way there is for this workbench's templates.

    A structural rule rather than a prose one for that reason: what the bodies
    share is being a document, and a document opens with its title. Measured
    over this machine's trailing month, every turn opening with an H1 was a
    command body — 9 of them, three distinct commands — and no human turn was.

    Only the first line is considered, so a turn that discusses a heading, or
    quotes one further down, is untouched.
    """
    return stripped.startswith("# ")


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

def pi_session_slug(path: Path | str) -> str:
    """The directory name Pi gives a session whose cwd is ``path``.

    Pi's own transform, from ``getDefaultSessionDir()`` in
    ``@earendil-works/pi-coding-agent/dist/core/session-manager.js``: strip one
    leading separator, replace ``/``, ``\\`` and ``:`` with a hyphen, wrap the
    result in ``--``. Everything else survives verbatim — a dot, a space, an
    accent, an emoji.

    Not ``canonical_slug`` below: that one replaces everything outside
    ``[A-Za-z0-9_]``, so it cannot address Pi's store. It looked for
    ``--Users-dev-git-otto-io--`` where Pi had written
    ``--Users-dev-git-otto.io--``, which made every repo whose path holds a dot
    or a non-ASCII character invisible to the session gates.

    ``str.replace`` on single BMP characters, which neither decodes nor
    validates the subject — so this agrees with the shell half on an
    undecodable byte, and with Pi on an astral one. ``_pi_session_slug`` in
    ``lib/ai/session-count.sh`` is that half, held to this by
    ``tests/sessions_ssot.bats``.

    No caller in ``ai/lib`` today — nothing here yet addresses Pi's own store
    the way ``claude_slug`` below addresses Claude's for ``claude_memory_dir``.
    It exists so the shell and Python transforms can be held to each other by
    ``tests/sessions_ssot.bats``, ahead of the consumer that will need it.
    """
    text = str(path)
    while "//" in text:
        text = text.replace("//", "/")
    while text.endswith("/") and text != "/":
        text = text[:-1]
    if text[:1] in ("/", "\\"):
        text = text[1:]
    for separator in ("\\", "/", ":"):
        text = text.replace(separator, "-")
    return f"--{text}--"


# The harness-neutral canonical name for a project path, and not any harness's
# directory name. It keeps underscores, so `feat/add_auth` and `feat/add-auth`
# stay distinct where Claude's transform would collide them; the doubled
# delimiter makes a slug recognisable as one on sight.
#
# lib/ai/session-count.sh spells the same transform for shell; a divergence is
# two tools disagreeing about what a project is called, so
# tests/sessions_ssot.bats fails when they drift.
def canonical_slug(path: Path | str) -> str:
    """The harness-neutral canonical name for a project path.

    Stable and filesystem-safe, which is what the things this repo names
    itself need: the gate stamps under ``$GATE_STAMPS_DIR`` and ``dream-scan``'s
    per-project grouping key. Addressing a harness's own store needs that
    harness's transform instead — ``pi_session_slug`` above, or ``claude_slug``
    below, which is also where memory hangs.

    ASCII alnum, not ``str.isalnum()``, for the reason ``claude_slug`` below
    spells it that way: the shell half is ``_encode_slug`` in
    ``lib/ai/session-count.sh`` with the class ``A-Za-z0-9_``, which only ever
    spares ASCII, while ``str.isalnum()`` is Unicode-aware and would keep an
    accented letter the shell replaced.
    """
    text = str(path).strip("/")
    encoded = "".join(
        c if (c.isascii() and c.isalnum()) or c == "_" else "-" for c in text
    )
    return f"--{encoded}--"


# ── Memory ───────────────────────────────────────────────────────────────────

# What the per-project memory directory is called inside a project's directory.
MEMORY_DIRNAME = "memory"


def _harness_named(name: str) -> Harness:
    """One harness out of the table, by name.

    Raising rather than returning None: every caller names a harness spelled in
    HARNESSES above, so a miss is a typo at the call site and not a machine
    without that harness installed — which is an empty root, not a missing row.
    """
    for harness in HARNESSES:
        if harness.name == name:
            return harness
    raise KeyError(f"no harness named {name!r} in HARNESSES")


def claude_projects_root(home: Path) -> Path:
    """Claude Code's session store, which is also where memory lives.

    Read off HARNESSES rather than spelled again, so the root has one owner
    whichever of its two jobs a caller came for.
    """
    return _harness_root(home, _harness_named("claude"))


def claude_slug(path: Path | str) -> str:
    """The directory name Claude Code gives a session whose cwd is ``path``.

    Claude's transform, not ``canonical_slug`` above: every character outside
    ``[A-Za-z0-9]`` becomes a hyphen, underscores included. The two disagree on
    purpose and both are needed — this one addresses Claude's own store, which
    is where memory lives, and that one names the harness-neutral slug.

    Lossy, so it is never inverted: ``a-b`` and ``a_b`` both arrive as ``a-b``.
    A caller wanting every memory directory sweeps with ``memory_dirs``.

    ASCII alnum, not ``str.isalnum()``: ``_claude_project_dir`` in
    ``lib/ai/session-count.sh`` encodes with the class ``A-Za-z0-9``, which
    only ever spares the 62 ASCII letters and digits. ``str.isalnum()`` is
    Unicode-aware and returns ``True`` for an accented or non-Latin letter,
    leaving it untouched and landing on a different directory than the shell
    half for the same path.

    Per code point, as the shell half is. That matches Claude's own JavaScript
    regex across the BMP and not beyond it; the ceiling on
    ``_claude_project_dir`` carries the reasoning.
    """
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in str(path))


def claude_memory_dir(home: Path, repo_path: Path | str) -> Path:
    """Where the memory for the repo at ``repo_path`` lives."""
    return claude_projects_root(home) / claude_slug(repo_path) / MEMORY_DIRNAME


def memory_dirs(home: Path) -> Iterator[Path]:
    """Every project memory directory on this machine, in name order.

    The project slug each one hangs off is ``dir.parent.name``. It is not
    decoded back into a repo path anywhere — Claude's transform is lossy, as the
    module docstring explains — so a caller wanting the repo starts from the
    project registry and encodes forward instead.
    """
    root = claude_projects_root(home)
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        candidate = entry / MEMORY_DIRNAME
        if candidate.is_dir():
            yield candidate
