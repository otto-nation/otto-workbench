"""Per-repo authored memory, keyed by shared git dir rather than cwd.

Memory topic files live under ``workbench_paths.memory_dir() / repo_key()``.
The key is ``<canonical_slug[:64]>-<sha256(identity)[:8]>`` so three paths
that collide under a bare slug — ``/Users/x/a-b/c``, ``/Users/x/a/b/c``,
``/Users/x/a.b/c`` — still get three directories. Identity is
``git_layout.shared_dir``, never Python's process-randomised ``hash()``.
"""

# doc-group: platform

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import core.sessions
import core.workbench_paths

_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
from git_layout import shared_dir  # noqa: E402

FRONTMATTER_DELIMITER = "---"
MEMORY_INDEX = "MEMORY.md"
TOPIC_GLOB = "*.md"
DATE_FMT = "%Y-%m-%d"
STALE_DAYS = 90

_SLUG_MAX = 64
_DIGEST_LEN = 8


def repo_key(repo_path: Path | str) -> str:
    """Filesystem-safe directory name for one repository's memory.

    ``canonical_slug`` alone is lossy: a hyphen, a slash and a dot all become
    ``-``, so three repos share one directory and silently merge. The digest
    of the shared git dir makes the map injective for those collisions.
    """
    directory = os.fspath(repo_path)
    identity = shared_dir(directory)
    if identity is None:
        raise ValueError(f"no shared git dir for {directory!r}")
    # Both halves come off the identity, never off the path the caller passed.
    # Every worktree resolves to one shared git dir, and so does the same repo
    # reached through a symlinked parent — on macOS /tmp and /var are symlinks,
    # so one repo has two spellings. Slugging the argument would vary the
    # readable half while the digest held, which is the per-cwd keying this
    # store exists to end.
    slug = core.sessions.canonical_slug(identity)[:_SLUG_MAX]
    digest = hashlib.sha256(identity.encode()).hexdigest()[:_DIGEST_LEN]
    return f"{slug}-{digest}"


def memory_dir(repo_path: Path | str) -> Path:
    """Where this repository's topic files live under the data root."""
    return core.workbench_paths.memory_dir() / repo_key(repo_path)


def parse_frontmatter(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    lines = path.read_text().splitlines()
    return _parse_frontmatter_lines(lines)


def _parse_frontmatter_lines(lines: list[str]) -> dict:
    try:
        start = lines.index(FRONTMATTER_DELIMITER) + 1
        end = lines.index(FRONTMATTER_DELIMITER, start)
    except ValueError:
        return {}
    result: dict[str, str] = {}
    for line in lines[start:end]:
        if line.startswith(" ") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


@dataclass(frozen=True)
class TopicFile:
    """One topic file's scan row.

    ``body`` is filled only when ``scan_topic_file(..., with_body=True)``;
    dream's compact table does not need it, promote's preview does.
    """

    filename: str
    name: str
    description: str
    type: str
    modified: str
    stale: bool
    age_days: int
    body: str | None = None


@dataclass(frozen=True)
class MemoryState:
    """One repository's memory directory, as a scan found it."""

    repo_key: str
    path: Path
    line_count: int
    topic_files: tuple[TopicFile, ...]


def scan_topic_file(
    path: Path,
    now: datetime,
    *,
    with_body: bool = False,
) -> TopicFile | None:
    """One topic file, or None if it vanished between listing and the read.

    One ``Path.stat`` call, reused for mtime; the body, when requested, is
    parsed from the same read as the frontmatter so a second stat is not
    needed. Promote's ``OSError`` guard, not dream's unguarded ``f.stat()``.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    fm = _parse_frontmatter_lines(lines)
    mtime = datetime.fromtimestamp(stat.st_mtime)
    age_days = (now - mtime).days
    body: str | None = None
    if with_body:
        body = _body_from_lines(lines)
    return TopicFile(
        filename=path.name,
        name=fm.get("name", ""),
        description=fm.get("description", ""),
        type=fm.get("type", ""),
        modified=mtime.strftime(DATE_FMT),
        stale=age_days > STALE_DAYS,
        age_days=age_days,
        body=body,
    )


def _body_from_lines(lines: list[str]) -> str:
    try:
        start = lines.index(FRONTMATTER_DELIMITER) + 1
        end = lines.index(FRONTMATTER_DELIMITER, start)
        body_lines = lines[end + 1 :]
    except ValueError:
        body_lines = lines
    return "\n".join(body_lines).strip()


def gate_stamp_file(repo_path: Path | str, name: str) -> Path:
    """Where the gate cooldown ``name`` for this repo is recorded.

    Keyed off the repo identity, as ``_gate_stamp_file`` in
    ``lib/ai/session-count.sh`` is: the gates resolve a worktree to the repo
    behind it before slugging, so a scanner slugging the path it was handed
    looks for a file under a different name and reports the cooldown missing.
    The stamp lives under the gates root and not in the memory directory — a
    cooldown is regenerable state, the topic files beside it are not.

    Falls back to the path as given when git cannot name a shared directory,
    which matches ``_gate_repo_dir``'s own fallback.
    """
    directory = os.fspath(repo_path)
    identity = shared_dir(directory)
    if identity is not None:
        directory = identity[: -len("/.git")] if identity.endswith("/.git") else identity
    return core.workbench_paths.gates_dir() / f"{core.sessions.canonical_slug(directory)}.{name}"


def read_body(path: Path) -> str:
    """One topic file's prose, with any frontmatter block removed."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    return _body_from_lines(lines)


def memory_dirs() -> Iterator[Path]:
    """Every per-repo memory directory under the data root, in name order."""
    root = core.workbench_paths.memory_dir()
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            yield entry


def state_of(
    directory: Path,
    now: datetime | None = None,
    *,
    with_body: bool = False,
) -> MemoryState | None:
    """One memory directory's scan, or None when it has no ``MEMORY.md``.

    Takes the directory rather than finding it, so a caller holding a repo
    path can resolve forward through ``memory_dir`` instead of globbing the
    root and trying to work back from a key it cannot decode.
    """
    if now is None:
        now = datetime.now()
    index = directory / MEMORY_INDEX
    if not index.exists():
        return None
    try:
        line_count = len(index.read_text().splitlines())
    except OSError:
        return None
    topic_files = tuple(
        tf
        for f in sorted(directory.glob(TOPIC_GLOB))
        if f.name != MEMORY_INDEX
        for tf in (scan_topic_file(f, now, with_body=with_body),)
        if tf is not None
    )
    return MemoryState(
        repo_key=directory.name,
        path=directory,
        line_count=line_count,
        topic_files=topic_files,
    )


def scan_memory_state(
    now: datetime | None = None,
    *,
    with_body: bool = False,
) -> list[MemoryState]:
    """Every memory directory that has a ``MEMORY.md``, with its topic files.

    A sweep of the root, for callers that want whatever is on disk — the
    orphan report and ``dream-verify``. A caller that knows which repo it
    means should resolve through ``memory_dir`` and call ``state_of``.
    """
    if now is None:
        now = datetime.now()
    states: list[MemoryState] = []
    for directory in memory_dirs():
        state = state_of(directory, now, with_body=with_body)
        if state is not None:
            states.append(state)
    return states
