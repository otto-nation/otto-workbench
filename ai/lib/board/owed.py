"""Comments-owed map from a comments-only `batch.plan`.

Each owed PR carries the plan row's head SHA so a later document can drop a
stale reason.
"""

# doc-group: board

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import batch.plan
from batch.model import Step


OWED_TTL_MS = 60_000


@dataclass(frozen=True)
class Owed:
    reason: str
    head_sha: str = ""


@dataclass(frozen=True)
class OwedEntry:
    repo: str
    number: int
    reason: str
    head_sha: str = ""


def _field(obj, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj[name] if name in obj else default
    return getattr(obj, name, default)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _comments_need(row):
    needs = _field(row, "needs")
    if not needs:
        return None
    if isinstance(needs, dict):
        if Step.COMMENTS in needs:
            return needs[Step.COMMENTS]
        return needs.get("comments")
    return None


def owed_entries(doc) -> tuple[OwedEntry, ...]:
    out: list[OwedEntry] = []
    for row in _field(doc, "rows") or ():
        number = _field(row, "pr")
        if not _is_int(number):
            continue
        need = _comments_need(row)
        if not _field(need, "needed"):
            continue
        out.append(OwedEntry(
            repo=str(_field(row, "repo") or ""),
            number=number,
            reason=str(_field(need, "reason") or ""),
            head_sha=str(_field(row, "head_sha") or ""),
        ))
    return tuple(out)


def owed_from(doc, slug: str) -> dict[int, Owed]:
    want = (slug or "").casefold()
    return {
        entry.number: Owed(reason=entry.reason, head_sha=entry.head_sha)
        for entry in owed_entries(doc)
        if entry.repo.casefold() == want
    }


def load_owed(checkouts: list[str], slug: str) -> dict[int, Owed]:
    plan = batch.plan.build_plan(list(checkouts), frozenset({Step.COMMENTS}))
    return owed_from(plan, slug)


def reason_for(number: int, owed: dict[int, Owed] | None) -> str | None:
    if not owed:
        return None
    entry = owed.get(number)
    return entry.reason if entry is not None else None


def owed_due(owed_at, now, ttl_ms: int = OWED_TTL_MS) -> bool:
    if owed_at is None:
        return True
    try:
        age = now - owed_at
    except TypeError:
        return True
    if isinstance(age, (int, float)) and not isfinite(age):
        return True
    if isinstance(owed_at, (int, float)) and not isfinite(owed_at):
        return True
    return age >= ttl_ms
