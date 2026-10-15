"""Typed inbound requests for a `pr batch` run: decide, cancel, or malformed.

A request file without ``kind`` parses as a ``Decide``, so files queued by
older code stay valid. ``to_dict`` always writes the tag. ``drain_order``
puts every ``Cancel`` ahead of decisions so a cancel never loses to a
publish queued beside it.
"""

# doc-group: batch

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass

from batch.model import Decision


class Kind(enum.Enum):
    DECIDE = "decide"
    CANCEL = "cancel"


@dataclass(frozen=True)
class Decide:
    decision: str
    action: str
    reason: str = ""
    body_file: str = ""
    commit: str = ""


@dataclass(frozen=True)
class Cancel:
    kill: bool = False


@dataclass(frozen=True)
class Malformed:
    reason: str


Request = Decide | Cancel | Malformed


@dataclass(frozen=True)
class Envelope:
    name: str
    request: Request


@dataclass(frozen=True)
class Outcome:
    name: str
    request: Request
    error: str = ""
    created: tuple[Decision, ...] = ()
    finished: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.error


def parse(raw: object) -> Request:
    """Turn a JSON value into a ``Decide``, ``Cancel``, or ``Malformed``.

    A dict with no ``kind``, or ``kind == decide``, is a ``Decide`` and needs
    string ``decision`` and ``action``. ``kind == cancel`` is a ``Cancel``.
    Anything else is ``Malformed``.
    """
    if not isinstance(raw, dict):
        return Malformed("not an object")
    kind = raw.get("kind")
    if kind is None or kind == Kind.DECIDE.value:
        return _parse_decide(raw)
    if kind == Kind.CANCEL.value:
        return Cancel(kill=bool(raw.get("kill")))
    return Malformed(f"unknown kind {kind!r}")


def _parse_decide(raw: dict) -> Request:
    for key in ("decision", "action"):
        if key not in raw:
            return Malformed(f"missing {key}")
        if not isinstance(raw[key], str):
            return Malformed(f"{key} must be a str")
    return Decide(
        decision=raw["decision"], action=raw["action"],
        reason=raw.get("reason", ""), body_file=raw.get("body_file", ""),
        commit=raw.get("commit", ""))


def to_dict(request: Decide | Cancel) -> dict:
    """JSON object for *request*, always tagged with ``kind``."""
    if isinstance(request, Cancel):
        return {"kind": Kind.CANCEL.value, "kill": request.kill}
    return {
        "kind": Kind.DECIDE.value,
        "decision": request.decision,
        "action": request.action,
        "reason": request.reason,
        "body_file": request.body_file,
        "commit": request.commit,
    }


def drain_order(envelopes: Iterable[Envelope]) -> list[Envelope]:
    """Every ``Cancel`` first (by name), then the rest by name."""
    items = list(envelopes)
    cancels = [e for e in items if isinstance(e.request, Cancel)]
    rest = [e for e in items if not isinstance(e.request, Cancel)]
    return sorted(cancels, key=lambda e: e.name) + sorted(rest, key=lambda e: e.name)
