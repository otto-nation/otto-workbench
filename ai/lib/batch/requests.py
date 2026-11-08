"""Typed inbound requests for a `pr batch` run: cancel, release, or malformed.

A request file without ``kind``, or ``kind: decide``, parses as ``Malformed``
naming that ``pr batch resolve`` was removed. ``to_dict`` always writes the
tag. ``drain_order`` puts every ``Cancel`` ahead of everything else (by name)
so a cancel never loses to a release queued beside it.
"""

# doc-group: batch

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass


class Kind(enum.Enum):
    CANCEL = "cancel"
    RELEASE = "release"


@dataclass(frozen=True)
class Cancel:
    kill: bool = False


@dataclass(frozen=True)
class Release:
    item: str
    kill: bool = False


@dataclass(frozen=True)
class Malformed:
    reason: str


Request = Cancel | Release | Malformed


@dataclass(frozen=True)
class Envelope:
    name: str
    request: Request


@dataclass(frozen=True)
class Outcome:
    name: str
    request: Request
    error: str = ""
    finished: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.error


def parse(raw: object) -> Request:
    """Turn a JSON value into a ``Cancel``, ``Release``, or ``Malformed``.

    A dict with no ``kind``, or ``kind == decide``, is ``Malformed`` naming
    that resolve was removed. ``kind == cancel`` is a ``Cancel``.
    ``kind == release`` is a ``Release`` and needs a string ``item``.
    Anything else is ``Malformed``.
    """
    if not isinstance(raw, dict):
        return Malformed("not an object")
    kind = raw.get("kind")
    if kind is None or kind == "decide":
        return Malformed("pr batch resolve was removed in schema v2")
    if kind == Kind.CANCEL.value:
        return Cancel(kill=bool(raw.get("kill")))
    if kind == Kind.RELEASE.value:
        return _parse_release(raw)
    return Malformed(f"unknown kind {kind!r}")


def _parse_release(raw: dict) -> Request:
    item = raw.get("item")
    if not isinstance(item, str):
        return Malformed("item must be a str" if "item" in raw else "missing item")
    return Release(item=item, kill=bool(raw.get("kill")))


def to_dict(request: Cancel | Release) -> dict:
    """JSON object for *request*, always tagged with ``kind``."""
    if isinstance(request, Cancel):
        return {"kind": Kind.CANCEL.value, "kill": request.kill}
    return {"kind": Kind.RELEASE.value, "item": request.item, "kill": request.kill}


def drain_order(envelopes: Iterable[Envelope]) -> list[Envelope]:
    """Every ``Cancel`` first (by name), then the rest by name."""
    items = list(envelopes)
    cancels = [e for e in items if isinstance(e.request, Cancel)]
    rest = [e for e in items if not isinstance(e.request, Cancel)]
    return sorted(cancels, key=lambda e: e.name) + sorted(rest, key=lambda e: e.name)
