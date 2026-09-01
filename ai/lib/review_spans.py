"""Where a finding declaration starts in a document, and where its body stops.

Where that body stops is the same one owner. `ends_finding_body` is the answer
and `finding_spans` is the traversal built on it, so a reader walking a review
a finding at a time gets the same line ranges wherever it walks from.
`drop_findings` is what an editing caller asks instead: the gates that trim a
finished review remove spans this module measured rather than lines each of
them recognised, because two gates that disagreed about where a body ended cut
one review two different ways — one of them swallowing the resolved finding
below the one it was told to drop.

`finding_spans` makes one traversal guarantee: a declaration starts where
`FINDING_ID_RE` says and its body stops where `ends_finding_body` says, in a
single pass over the text, so no two readers of one review can cut it in
different places.
"""

# doc-group: findings

from __future__ import annotations

import re
from collections.abc import Iterable

from review_grammar import FINDING_ID_RE, STRIKETHROUGH_RE, parse_finding_line
from review_types import SEVERITIES, Finding, FindingScope, FindingSpan


def _severity_names() -> dict[str, str]:
    """Every heading a severity answers to, lowercased, mapped to its key."""
    names: dict[str, str] = {}
    for s in SEVERITIES:
        names[s.section.lower()] = s.key
        # replace("-", " ") mirrors _match_severity_header's input normalisation so
        # future hyphenated section names (e.g. "Should-fix") still resolve correctly.
        names[s.section.lower().replace("-", " ")] = s.key
        for alias in s.aliases:
            names[alias.lower()] = s.key
    return names


_SEVERITY_NAMES = _severity_names()


def is_section_boundary(stripped: str) -> bool:
    """Whether the line ends whatever finding's body was being read.

    A sub-heading or a struck-through finding — the review resolved that one,
    and the lines below it belong to nothing.
    """
    return stripped.startswith("### ") or bool(STRIKETHROUGH_RE.match(stripped))


def starts_finding_or_section(stripped: str) -> bool:
    """Whether the line opens a finding declaration or a `## ` section.

    What ends a run of lines being read as one finding's body, for a reader
    walking the document a line at a time rather than parsing it.

    Deliberately not `is_section_boundary`, which answers a different question
    over the same lines: a struck-through finding ends the body before it and
    opens nothing, and a `### ` sub-heading ends one without starting either.
    A reader that needs both asks both.
    """
    return (
        stripped.startswith("- **[")
        or stripped.startswith("- [ ] **[")
        or stripped.startswith("- [x] **[")
        or stripped.startswith("## ")
    )


def _match_severity_header(stripped: str) -> str | None:
    if not stripped.startswith("#"):
        return None
    text = stripped.lstrip("#").strip().lower().replace("-", " ")
    return _SEVERITY_NAMES.get(text)


def ends_finding_body(stripped: str) -> bool:
    """Whether the line ends the finding body running above it.

    The one answer to where a finding stops. Five things end one: the next
    declaration, a `## ` section, a `### ` sub-heading, a struck-through
    finding the review resolved, and a heading of any depth naming a severity.
    Everything else belongs to the finding above it, an indented bullet and a
    blockquoted evidence fence included.

    A plain unindented `- ` bullet is body rather than boundary. `reviewer.md`
    asks for evidence indented two spaces under the finding line, so a flat
    bullet inside a severity section is a continuation someone typed without
    the indent rather than a list the finding is not part of — and no review
    written since this parser existed has one. Callers that read it the other
    way cut the same review two different ways.
    """
    return (
        starts_finding_or_section(stripped)
        or is_section_boundary(stripped)
        or bool(FINDING_ID_RE.match(stripped))
        or _match_severity_header(stripped) is not None
    )


def _finalize_finding(finding: Finding, body_lines: list[str]):
    body = "\n".join(body_lines).strip()
    if not body:
        return
    body = re.sub(r"\n+###[^\n]*$", "", body, flags=re.DOTALL).strip()
    finding.body = body


def _scope_after(stripped: str, scope: FindingScope) -> FindingScope:
    """The scope a heading line puts the walk into, or the one it was in."""
    if _match_severity_header(stripped) is not None:
        return FindingScope.DECLARED
    if stripped.startswith("## "):
        return FindingScope.REPORTED
    return scope


class _SpanWalk:
    """One pass over a text, accumulating a span per finding declaration."""

    def __init__(self) -> None:
        self.spans: list[FindingSpan] = []
        self.scope = FindingScope.UNHEADED
        self._finding: Finding | None = None
        self._line = ""
        self._start = 0
        self._opened_in = FindingScope.UNHEADED
        self._body: list[str] = []

    def open(self, stripped: str, index: int) -> None:
        """Start a span at `index` when `stripped` declares a finding."""
        finding = parse_finding_line(stripped)
        if finding is None:
            return
        self._finding = finding
        self._line = stripped
        self._start = index
        self._opened_in = self.scope
        self._body = [finding.body] if finding.body else []

    def extend(self, raw_line: str) -> None:
        """Add a body line to the open span, if one is open and it says anything."""
        if self._finding is not None and raw_line.strip():
            self._body.append(raw_line.rstrip())

    def close(self, end: int) -> None:
        """Finish the open span at `end`, if one is open."""
        if self._finding is None:
            return
        _finalize_finding(self._finding, self._body)
        self.spans.append(FindingSpan(
            self._finding, self._line, self._start, end, self._opened_in,
        ))
        self._finding = None
        self._body = []


def finding_spans(text: str) -> list[FindingSpan]:
    """Every finding `text` declares, each with the lines its body claims.

    One traversal, and the only one: a declaration starts where `FINDING_ID_RE`
    says and its body stops where `ends_finding_body` says, so no two readers
    of one review can cut it in different places. Spans come back in the order
    the text declares them and never overlap.

    Every declaration is returned wherever it sits, because the answer to
    "which of these count" differs by caller: one after the findings a review
    reports asks `ReviewDocument.findings`, and one editing the text filters on
    `reported` so the prior-findings ledger is left as its own review wrote it.
    """
    walk = _SpanWalk()
    lines = text.split("\n")
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not ends_finding_body(stripped):
            walk.extend(raw_line)
            continue
        walk.close(index)
        walk.scope = _scope_after(stripped, walk.scope)
        # A struck-through finding ends the body above it and opens nothing:
        # the review resolved that one, so the lines below belong to neither.
        if not is_section_boundary(stripped):
            walk.open(stripped, index)
    walk.close(len(lines))
    return walk.spans


def cut_spans(text: str, spans: Iterable[FindingSpan]) -> str:
    """`text` with the lines `spans` claim removed and every other byte intact.

    An edit rather than a re-render, so what is left is exactly what was there:
    a review a gate trimmed still reads as the document its author wrote.
    """
    removed: set[int] = set()
    for span in spans:
        removed.update(range(span.start, span.end))
    if not removed:
        return text
    return "\n".join(
        line for index, line in enumerate(text.split("\n")) if index not in removed
    )


def drop_findings(text: str, ids: Iterable[str]) -> str:
    """`text` with each finding `ids` names, and the body under it, removed.

    The one owner of what leaves a review when a gate drops a finding, so the
    evidence check and the disprove gate cannot take different amounts of it.
    A declaration under a heading naming no severity is left alone: the
    prior-findings ledger reports on the last review, and an ID that collides
    with a dropped finding numbers that review rather than this one.
    """
    wanted = set(ids)
    return cut_spans(text, [
        span for span in finding_spans(text)
        if span.finding.id in wanted and not span.reported
    ])
