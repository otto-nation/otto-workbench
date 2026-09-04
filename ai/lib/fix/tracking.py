"""The tracking file a fix pass hands its agent, rendered and read back.

A fix pass cannot watch an agent work, so it gives it a checklist and reads the
checklist afterwards to find out what happened. That file is the interface
between the two, and this module is both halves of it: :func:`render` writes
one from :class:`~fix.types.FixItem`s, :func:`parse` reads one back as
:class:`~pr.fix.ItemOutcome`s, and the format itself is stated once, here.

Three properties the formats this replaces did not have:

**Every section carries its id**, in an HTML comment the agent has no reason to
touch. A parse therefore returns outcomes keyed by item rather than a count of
ticked boxes, which is what lets a pass say *which* work it did and lets a later
round reconcile against it.

**The vocabulary is** :class:`~pr.fix.FixOutcome`. A single checkbox is a
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
import textwrap
from pathlib import Path
from typing import NamedTuple

from fix.types import FixItem
from pr.fix import FixOutcome, ItemOutcome


class _Box(NamedTuple):
    """One box, in the label the file writes and the sentence the prompt uses.

    Both halves sit on the same record so a box cannot exist in one and not the
    other: a label with no contract beside it would render an ask no prompt
    explains, and a contract with no label would explain a box nothing writes.
    """

    label: str
    outcome: FixOutcome
    contract: str


# The boxes offered for each item, in the order they are rendered and the order
# a contradiction is resolved in. FIXED leads because it is the only one of the
# three that is a fact about the tree rather than a position about the item: an
# agent that both applied a fix and argued against the item still applied it.
#
# The contract sentences are held here, rather than in the templates, because
# the label each one explains is here: a box renamed and explained in three
# prompt files is three places to remember, which is the drift the shared render
# exists to prevent. `{noun}` is what the calling domain calls one item. The
# examples of what earns each box are the domain's and stay in its own template.
_BOXES: tuple[_Box, ...] = (
    _Box("fixed", FixOutcome.FIXED,
         "you applied the change. Apply it with the Edit tool on the source "
         "file first"),
    _Box("declined", FixOutcome.DECLINED,
         "you read the {noun} and it should not be acted on. Replace `<why>` "
         "with the reason, in one sentence"),
    _Box("needs a person", FixOutcome.NEEDS_HUMAN,
         "the {noun} is real but the call is not yours. Replace `<why>` with "
         "what the decision turns on"),
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

# Whatever follows the label is `rest`, left for `_reason` to interpret rather
# than pinned to the separator the render happens to write. The one character it
# may not start with is a newline: a bare `- [ ] fixed` would otherwise swallow
# the line below it as its own reason and the box on that line would never be
# seen at all.
_BOX_RE = re.compile(
    r"^- \[(?P<mark>[ xX])\] (?P<label>%s)(?P<rest>[^\w\n].*)?$"
    % "|".join(re.escape(box.label) for box in _BOXES),
    re.MULTILINE,
)

# What stands between a ticked box's label and the agent's words after it. The
# render writes an em dash, but the agent is writing prose and reaches for a
# colon or a plain hyphen often enough that matching only the one character
# would read those lines as no box at all — and a tick read as untouched sends a
# later pass back over work already done. Anything after the label is the
# reason; this only says where the reason starts. Brackets are left in place:
# half of a wrapped aside is worse to read than the whole of one.
_REASON_LEAD = re.compile(r"^[\s—–:.,;-]+")

_LOCATION_RE = re.compile(r"^(?P<file>.+):(?P<line>\d+)$")

_HEADING_SEP = " — "


def _suffix(box: _Box) -> str:
    """What the render leaves after a box's label for the agent to fill in."""
    return f" — {_WHY}" if box.outcome in _REASONED else ""


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
        for box in _BOXES:
            section += f"- [ ] {box.label}{_suffix(box)}\n"
        sections.append(section)
    return "\n".join(sections)


def instructions(noun: str) -> str:
    """How to answer the file, in the words `parse` reads back.

    Every domain's prompt asks for the same three boxes, so the ask is rendered
    from `_BOXES` rather than restated once per template. `noun` is what the
    domain calls one item — "finding", "thread", "failure" — and what belongs
    in each box is the domain's own to say, in its own template.

    The alternative is the format written down four times: here, and in each
    prompt that describes it. Three fix passes drifted apart doing exactly that
    with their pipelines, and a prompt that asks for a box `parse` no longer
    looks for fails silently — every item comes back reading as work still owed.
    """
    boxes = "\n".join(
        f"- `- [x] {box.label}{_suffix(box)}` — {box.contract.format(noun=noun)}"
        for box in _BOXES
    )
    return textwrap.dedent("""\
        Every {noun} above carries three boxes. Answer each one by ticking exactly
        one of them with the Edit tool, in the tracking file, having read the file
        it points at first:

        {boxes}

        Leave all three boxes unticked only for a {noun} you never got to. That
        reads as work still owed and the {noun} is handed to another pass, so use
        it for what you ran out of turns for — never as a way to pass over a
        {noun} you read and had an answer for.""").format(noun=noun, boxes=boxes)


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

    Only the boxes that ask for a reason keep one. `ItemOutcome` states that a
    FIXED entry carries no reason, and an agent that annotates its tick anyway
    should not be the one thing that makes that untrue.
    """
    ticked = {
        box.group("label"): _reason(box.group("rest"))
        for box in _BOX_RE.finditer(body)
        if box.group("mark") in "xX"
    }
    for box in _BOXES:
        if box.label not in ticked:
            continue
        into.outcome = box.outcome
        into.reason = ticked[box.label] if box.outcome in _REASONED else ""
        return


def _reason(rest: str | None) -> str:
    """The agent's words after a ticked box's label, without the separator.

    The placeholder the render leaves behind reads as no reason: a box ticked
    without it being replaced said nothing, and reporting `<why>` back to a
    reviewer as the agent's reasoning is worse than reporting none.
    """
    text = _REASON_LEAD.sub("", (rest or "").strip()).strip()
    return "" if text == _WHY else text
