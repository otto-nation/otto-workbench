"""The tracking file a fix pass hands its agent, rendered and read back.

A fix pass cannot watch an agent work, so it gives it a checklist and reads the
checklist afterwards to find out what happened. That file is the interface
between the two, and this module is both halves of it: :func:`render` writes
one from :class:`~fix_types.FixItem`s, :func:`parse` reads one back as
:class:`~pr_fix.ItemOutcome`s, and the format itself is stated once, here.

Three properties the formats this replaces did not have:

**Every section carries its id**, in an HTML comment the agent has no reason to
touch. A parse therefore returns outcomes keyed by item rather than a count of
ticked boxes, which is what lets a pass say *which* work it did and lets a later
round reconcile against it.

**The vocabulary is** :class:`~pr_fix.FixOutcome`. A single checkbox is a
boolean, and a boolean cannot tell an agent that ran out of turns from one that
read the item and disagreed with it. Three boxes can, and every domain gets the
distinction rather than only the one whose format happened to encode it.

**An untouched item reads as work still owed.** No box checked parses to
`DEFERRED`, matching `ItemOutcome`'s own default and for the same reason: a
tracking file the agent never wrote to is not evidence that anything was fixed.

The format is deliberately plain markdown. The agent edits it with the same
tool it edits source with, and an operator reading the file afterwards sees
what the agent saw.
"""

# doc-group: pr-state

from __future__ import annotations

import re
from pathlib import Path

from fix_types import FixItem
from pr_fix import FixOutcome, ItemOutcome


# The boxes offered for each item, in the order they are rendered and the order
# a contradiction is resolved in. FIXED leads because it is the only one of the
# three that is a fact about the tree rather than a position about the item: an
# agent that both applied a fix and argued against the item still applied it.
_BOXES: tuple[tuple[str, FixOutcome], ...] = (
    ("fixed", FixOutcome.FIXED),
    ("declined", FixOutcome.DECLINED),
    ("needs a person", FixOutcome.NEEDS_HUMAN),
)

# What the render leaves where the agent writes its reason. Parsed back to an
# empty reason, so a box ticked without the placeholder being replaced does not
# report the placeholder as the agent's words.
_WHY = "<why>"

# FIXED needs no reason — the change speaks for itself — so it is rendered as a
# bare box and the other two ask for one.
_REASONED = frozenset({FixOutcome.DECLINED, FixOutcome.NEEDS_HUMAN})

_SECTION_RE = re.compile(
    r"^## <!-- fix:(?P<id>[^\s>]+) -->[ ]*(?P<heading>.*)$", re.MULTILINE,
)

_BOX_RE = re.compile(
    r"^- \[(?P<mark>[ xX])\] (?P<label>%s)(?:[ ]*—[ ]*(?P<reason>.*?))?[ ]*$"
    % "|".join(re.escape(label) for label, _ in _BOXES),
    re.MULTILINE,
)

_LOCATION_RE = re.compile(r"^(?P<file>.+):(?P<line>\d+)$")

_HEADING_SEP = " — "


def render(title: str, items: list[FixItem]) -> str:
    """The tracking file for `items`, under `title`, as markdown.

    Rendered rather than accumulated so the file is a function of the work: a
    pass that rebuilds it per batch, or rebuilds it for a retry over what is
    left, gets a file describing exactly the items in hand and nothing else.
    """
    sections = [f"# {title}\n"]
    for item in items:
        heading = f"## <!-- fix:{item.id} --> {item.location()}"
        if item.label:
            heading += f"{_HEADING_SEP}{item.label}"
        body = item.body.strip()
        section = heading + "\n\n" + (f"{body}\n\n" if body else "")
        for label, outcome in _BOXES:
            suffix = f" — {_WHY}" if outcome in _REASONED else ""
            section += f"- [ ] {label}{suffix}\n"
        sections.append(section)
    return "\n".join(sections)


def write(path: Path, title: str, items: list[FixItem]) -> None:
    """Render `items` to `path`, creating the directory that holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(title, items))


def parse(path: Path) -> list[ItemOutcome]:
    """What the agent recorded, one outcome per section, in file order.

    A file that does not exist is a pass that never got as far as writing one,
    which is no outcomes rather than an error — the caller knows which items it
    handed over and reads their absence as work still owed.

    Everything the outcome carries comes from the file itself, including `file`
    and `line`, which are read back out of the section heading. That is what
    makes this a round trip rather than a half one: the caller does not have to
    hold the items it rendered in order to interpret the answer.
    """
    if not path.exists():
        return []
    text = path.read_text()
    outcomes: list[ItemOutcome] = []
    matches = list(_SECTION_RE.finditer(text))
    for n, match in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
        outcomes.append(_section_outcome(match, text[match.end():end]))
    return outcomes


def checked(path: Path) -> int:
    """How many boxes the agent ticked, across every section.

    The guard that decides whether a pass produced anything asks this, so it
    counts boxes rather than items: an agent that ticked one box out of thirty
    was working, and only an agent that ticked none was thrashing.
    """
    if not path.exists():
        return 0
    return sum(
        1 for box in _BOX_RE.finditer(path.read_text())
        if box.group("mark") in "xX"
    )


def _section_outcome(match: re.Match, body: str) -> ItemOutcome:
    """One section's outcome — its heading gives the anchor, its boxes the verdict."""
    outcome = _anchor(match.group("heading"))
    outcome.id = match.group("id")
    _record_verdict(outcome, body)
    return outcome


def _anchor(heading: str) -> ItemOutcome:
    """An outcome carrying the file and line a section heading points at.

    The heading is `<location> — <label>` and the label is free text, so the
    split takes the first separator only. A location that is not `path:line` —
    a whole file, or the em dash that stands in for no path at all — anchors to
    the path it does have and a zero line.
    """
    location = heading.split(_HEADING_SEP, 1)[0].strip()
    if location in ("", "—"):
        return ItemOutcome()
    anchored = _LOCATION_RE.match(location)
    if not anchored:
        return ItemOutcome(file=location)
    return ItemOutcome(file=anchored.group("file"), line=int(anchored.group("line")))


def _record_verdict(into: ItemOutcome, body: str) -> None:
    """Set the outcome and reason a section's boxes record.

    Ticking nothing leaves `ItemOutcome`'s own `DEFERRED` default standing.
    Ticking more than one resolves by `_BOXES` order rather than by position in
    the file, so an agent that reorders the list cannot change what its answer
    means.
    """
    ticked = {
        box.group("label"): (box.group("reason") or "").strip()
        for box in _BOX_RE.finditer(body)
        if box.group("mark") in "xX"
    }
    for label, outcome in _BOXES:
        if label not in ticked:
            continue
        into.outcome = outcome
        into.reason = "" if ticked[label] == _WHY else ticked[label]
        return
