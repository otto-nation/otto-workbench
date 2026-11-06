"""Cross-owner references in published text.

GitHub creates a "referenced this" event when published text names another
repository as ``owner/repo#N``, ``owner/repo@sha``, a github.com (or
configured-host) URL, or an ``@owner`` mention. Only configured owners
count: an ``@handle`` that is not ours is not a reference, and an upstream
owner is not returned.

``gh.owners`` imports this module, so ``references`` takes the owner map
structurally — ``classify`` and ``hosts`` — and never imports ``gh.owners``.
"""

# doc-group: platform

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import config.github_owners
from config.github_owners import Owner

_NAME = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_LOGIN = r"[A-Za-z0-9][A-Za-z0-9_-]*"
_HOST = r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?"

_URL_RE = re.compile(
    rf"(?<!\w)https?://({_HOST})/({_NAME})(?:/[^\s]*)?",
    re.IGNORECASE,
)
_REPO_RE = re.compile(
    rf"(?<![\w/.])({_NAME})/({_NAME})"
    rf"(?:#(\d+)|@([0-9a-fA-F]{{7,40}})(?![0-9a-fA-F]))",
)
# A mention login does not include `.`, so `a@owner.com` is `@owner` plus
# `.com` and the lookbehind is what rejects it.
_MENTION_RE = re.compile(rf"(?<!\w)@({_LOGIN})(/{_LOGIN})?")


class ReferenceKind(StrEnum):
    """How published text named the owner."""

    REPO_ISSUE = "REPO_ISSUE"
    REPO_COMMIT = "REPO_COMMIT"
    URL = "URL"
    MENTION = "MENTION"


@dataclass(frozen=True)
class Reference:
    """One configured owner named in published text, as it was written."""

    kind: ReferenceKind
    owner: Owner
    text: str


class _Classified(Protocol):
    @property
    def upstream(self) -> bool: ...


class _OwnerMap(Protocol):
    def classify(self, owner: Owner) -> _Classified: ...

    def hosts(self) -> frozenset[str]: ...


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    reference: Reference


def references(text: str, owner_map: _OwnerMap) -> tuple[Reference, ...]:
    """Configured-owner references in *text*, first occurrence of each, in order."""
    hits = _url_hits(text, owner_map)
    hits.extend(_repo_hits(text, owner_map))
    hits.extend(_mention_hits(text, owner_map))
    hits.sort(key=lambda hit: hit.start)
    return _unique(_without_overlap(hits))


def _url_hits(text: str, owner_map: _OwnerMap) -> list[_Hit]:
    allowed = {config.github_owners.DEFAULT_HOST} | {host.lower() for host in owner_map.hosts()}
    hits: list[_Hit] = []
    for match in _URL_RE.finditer(text):
        host = match.group(1).lower()
        if host not in allowed:
            continue
        owner = config.github_owners.owner(host, match.group(2))
        reference = _configured(owner_map, ReferenceKind.URL, owner, match.group(0))
        if reference is not None:
            hits.append(_Hit(match.start(), match.end(), reference))
    return hits


def _repo_hits(text: str, owner_map: _OwnerMap) -> list[_Hit]:
    hits: list[_Hit] = []
    for match in _REPO_RE.finditer(text):
        owner = config.github_owners.owner(config.github_owners.DEFAULT_HOST, match.group(1))
        if match.group(3) is not None:
            kind = ReferenceKind.REPO_ISSUE
        else:
            kind = ReferenceKind.REPO_COMMIT
        reference = _configured(owner_map, kind, owner, match.group(0))
        if reference is not None:
            hits.append(_Hit(match.start(), match.end(), reference))
    return hits


def _mention_hits(text: str, owner_map: _OwnerMap) -> list[_Hit]:
    hits: list[_Hit] = []
    for match in _MENTION_RE.finditer(text):
        owner = config.github_owners.owner(config.github_owners.DEFAULT_HOST, match.group(1))
        reference = _configured(
            owner_map, ReferenceKind.MENTION, owner, match.group(0))
        if reference is not None:
            hits.append(_Hit(match.start(), match.end(), reference))
    return hits


def _configured(
    owner_map: _OwnerMap, kind: ReferenceKind, owner: Owner, text: str,
) -> Reference | None:
    if owner_map.classify(owner).upstream:
        return None
    return Reference(kind, owner, text)


def _without_overlap(hits: list[_Hit]) -> list[_Hit]:
    kept: list[_Hit] = []
    end = -1
    for hit in hits:
        if hit.start < end:
            continue
        kept.append(hit)
        end = hit.end
    return kept


def _unique(hits: list[_Hit]) -> tuple[Reference, ...]:
    seen: set[tuple[ReferenceKind, Owner, str]] = set()
    out: list[Reference] = []
    for hit in hits:
        key = (hit.reference.kind, hit.reference.owner, hit.reference.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit.reference)
    return tuple(out)
