"""The `--closes` contract: validate, stage, and append closing references.

A GitHub issue number (`941` or `#941`) closes anywhere. A tracker key
(`ENG-123`) only auto-closes where `issues.provider` is Linear, so it is
refused anywhere else rather than becoming a dead link in a published body.

Appended rather than prepended because a templated body's section headers are
a contract — content above the first heading, or injected into a section the
AI wrote, is content the template did not ask for. GitHub honours a closing
keyword anywhere in the body, so the end costs nothing, and re-running over a
body that already links is then a no-op.
"""

# doc-group: publishing

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from config.workbench_config import IssueProvider

_NUMERIC = re.compile(r"[0-9]+")
_TRACKER = re.compile(r"[A-Z]+-[0-9]+")


class CloseRefError(ValueError):
    """A `--closes` value nothing can close. The message is the user-facing ✗ line."""


def normalise(value: str, provider: IssueProvider | None) -> str:
    """Return a staged ref, or raise :class:`CloseRefError`.

    ``941`` / ``#941`` become ``#941``. ``ENG-123`` is kept only when
    ``provider`` is Linear. One leading ``#`` is stripped before classifying,
    so ``#ENG-123`` is a tracker key and ``##941`` is refused as ``#941``.
    """
    raw = value[1:] if value.startswith("#") else value
    if _NUMERIC.fullmatch(raw):
        return f"#{raw}"
    if _TRACKER.fullmatch(raw):
        if provider is not IssueProvider.LINEAR:
            raise CloseRefError(
                f"✗ --closes {raw}: a tracker key only auto-closes on Linear, "
                f"and issues.provider is '{provider or 'unset'}'"
            )
        return raw
    raise CloseRefError(
        f"✗ --closes {raw}: expected a GitHub issue number (941 or #941) "
        f"or an uppercase tracker key (ENG-123)"
    )


def stage(refs: list[str], ref: str) -> list[str]:
    """Return a new list with ``ref`` appended unless it is already present.

    Deduplicating on the way in rather than on the way out covers every later
    reading at once, and ``--closes 941 --closes #941`` normalise to one entry
    before they get here.
    """
    if ref in refs:
        return list(refs)
    return [*refs, ref]


def present(body: str, ref: str) -> bool:
    """Whether ``body`` already closes ``ref`` under any GitHub closing keyword.

    The trailing ``(?!\\d)`` is what keeps ``#1`` from matching a body that
    closes ``#12``.
    """
    pattern = (
        rf"(?i)\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+"
        rf"{re.escape(ref)}(?!\d)"
    )
    return re.search(pattern, body) is not None


@dataclass(frozen=True)
class LinkResult:
    """What :func:`append` did to a body."""

    body: str
    linked: tuple[str, ...]
    already: tuple[str, ...]


def append(body: str, refs: Sequence[str]) -> LinkResult:
    """Append one ``Closes {ref}`` line per missing ref.

    No refs leaves the body unchanged. Missing refs are separated from the
    body by exactly one blank line and from each other by a blank line.
    """
    if not refs:
        return LinkResult(body=body, linked=(), already=())

    linked: list[str] = []
    already: list[str] = []
    for ref in refs:
        if present(body, ref):
            already.append(ref)
        else:
            linked.append(ref)

    if not linked:
        return LinkResult(body=body, linked=(), already=tuple(already))

    lines = "\n\n".join(f"Closes {ref}" for ref in linked)
    return LinkResult(
        body=body.rstrip("\n") + "\n\n" + lines,
        linked=tuple(linked),
        already=tuple(already),
    )
