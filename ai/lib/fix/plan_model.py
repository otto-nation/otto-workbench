"""The plan a deep pass writes for one fix item.

Owned by the shared engine, not by a domain: a comments round is the first
adopter, but CI and review will hand the same record to the same stages. The
JSON lives under the pass's artifacts directory and is never posted publicly.
"""

# doc-group: pipeline

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from fix.types import FixItem
from pr.fix import ItemOutcome

PLAN_SCHEMA = 1
CHANGE_CAP = 4096


class PlanVerdict(StrEnum):
    """What the deep pass decided per item. Persisted strings; do not rename."""

    FIX = "fix"
    ALREADY_ADDRESSED = "already_addressed"
    DISMISS = "dismiss"
    NEEDS_HUMAN = "needs_human"


class PlanMode(StrEnum):
    """What the engine may do with the verdict."""

    ACT = "act"
    ADVISE = "advise"


class Origin(StrEnum):
    """Why the item is in the pass."""

    WORK = "work"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class StagedItem:
    """Adapter → engine, only when plan stages are on."""

    item: FixItem
    mode: PlanMode = PlanMode.ACT
    origin: Origin = Origin.WORK
    prior: ItemOutcome | None = None


@dataclass(frozen=True)
class ItemPlan:
    """One item's plan, as written beside the run.

    `files` and `outside_pr` are frozen collections of paths, not tuples of
    business data.
    """

    schema: int
    item_id: str
    verdict: PlanVerdict
    mode: PlanMode
    reason: str
    files: tuple[str, ...]
    change: str
    test: str
    evidence_file: str = ""
    evidence_line: int = 0
    outside_pr: tuple[str, ...] = ()
    item_digest: str = ""
    head_sha: str = ""
    model: str = ""

    @property
    def valid(self) -> bool:
        """FIX needs files and change; NEEDS_HUMAN and DISMISS need a reason."""
        if self.schema != PLAN_SCHEMA:
            return False
        if self.verdict is PlanVerdict.FIX:
            return bool(self.files) and bool(self.change.strip())
        if self.verdict in (PlanVerdict.NEEDS_HUMAN, PlanVerdict.DISMISS):
            return bool(self.reason.strip())
        if self.verdict is PlanVerdict.ALREADY_ADDRESSED:
            return bool(self.reason.strip())
        return False


def item_digest(item: FixItem) -> str:
    """sha256 of the FixItem as rendered, stable across processes."""
    payload = f"{item.id}|{item.file}|{item.line}|{item.label}|{item.body}"
    return hashlib.sha256(payload.encode()).hexdigest()


def unsafe_relpath(path: str) -> bool:
    """True when `path` is not a worktree-relative file the implementer may touch."""
    text = path.strip()
    if not text:
        return True
    candidate = Path(text)
    if candidate.is_absolute() or text.startswith("/") or text.startswith("~"):
        return True
    parts = candidate.parts
    if ".." in parts or ".git" in parts:
        return True
    return False


def classify_paths(
    files: tuple[str, ...] | list[str],
    pr_files: frozenset[str] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split `files` into (rejected, outside_pr).

    Rejected paths are absolute, parent-traversing, or under `.git`.
    `outside_pr` is the subset inside the worktree but not in `pr_files`;
    unknown `pr_files` yields an empty tuple.
    """
    rejected: list[str] = []
    outside: list[str] = []
    for raw in files:
        if unsafe_relpath(raw):
            rejected.append(raw)
            continue
        if pr_files is not None and raw not in pr_files:
            outside.append(raw)
    return tuple(rejected), tuple(outside)
