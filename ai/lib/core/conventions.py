"""Bridge to the repo's conventional-commit rules in lib/conventions.sh.

``conventions.sh`` is the SSOT for the commit-type list and the header
format; ``git.generated.md`` is generated from it.  This module is the
Python door to it, so every layer can reach the same rules without
duplicating the types or the path.

No Python module reads it today — the pre-push fixer was the last one, and it
stopped generating a commit subject when it became a ``fix.engine`` pass. It is
kept rather than deleted because it is a bridge and not logic: the shell SSOT
still has many readers, this is the layer-1 door through which any Python layer
reaches the same rules, and the copy of the type list is exactly what the module
exists to prevent. A module recreated later is a module recreated as that copy.
It goes if a second release ships with no Python reader.
"""

# doc-group: platform

from __future__ import annotations

from functools import cache
from pathlib import Path

# ai/lib/core/conventions.py  →  parents[3] is the repo root
_CONVENTIONS_SH = Path(__file__).resolve().parents[3] / "lib" / "conventions.sh"

COMMIT_HEADER_MAX = 72


@cache
def commit_types() -> list[str]:
    """Read the allowed conventional-commit types from lib/conventions.sh.

    conventions.sh is the single source of truth for the type list; an
    unreadable file degrades to an empty list, which skips type validation
    rather than duplicating the types here and letting the copies drift.
    """
    try:
        text = _CONVENTIONS_SH.read_text()
    except OSError:
        return []
    line = next(
        (ln for ln in text.splitlines() if ln.startswith("COMMIT_TYPES=")), "",
    )
    return line.split("=", 1)[1].strip().strip('"').split() if line else []


def valid_commit_header(subject: str) -> bool:
    """Check a generated subject against the repo's commit conventions."""
    if not subject or len(subject) > COMMIT_HEADER_MAX or subject.endswith("."):
        return False
    head, sep, rest = subject.partition(":")
    if not sep or not rest.strip():
        return False
    types = commit_types()
    commit_type = head.split("(", 1)[0].removesuffix("!")
    return not types or commit_type in types
