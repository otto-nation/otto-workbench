"""One ingest boundary: an agent's markdown review becomes a `ReviewRecord`, once.

Every agent artifact that becomes review state passes through `ingest_review`
where it enters. Ingest is strict: an entry it cannot represent faithfully — an
unknown severity token, a duplicate ID, a declaration with nothing after it — is
an `IngestError`, never a silent repair. `ingest_with_repair` gives the agent one
turn to fix a rejection and returns an `IngestOutcome`; a second rejection is an
agent failure, handled as failures already are.

Text carrying the render `BANNER` is refused outright: `review.md` is a view of
`review.json` and is never parsed back.

This reads the markdown grammar — `review.grammar` for a finding line —
and walks each finding's body here (`finding_spans`).
"""

# doc-group: findings

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Generic, Protocol, TypeVar

import core.log
import core.strict
from agent.diagnosis import Diagnosis, DiagnosisKind

from review.document import (
    SECTION_FAILURES, SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS, SECTION_SUMMARY, SECTION_VERDICT,
)
from review.grammar import (
    DECLINED_HEAD_RE, DECLINED_TAIL_RE, FINDING_ID_RE, SID_MARKER_RE,
    SKIP_HEAD_RE, SKIP_TAIL_RE, STRIKETHROUGH_RE, TRIAGE_LINE_RE, UNVERIFIED_TAIL_RE,
    FindingIdentity, extract_body_text, finding_location, parse_finding_line,
    parse_ledger_line,
)
from review.record import (
    AgentFailure, Disposition, LedgerLine, ProseSection, RecordFinding,
    ReviewRecord, SeveritySection, TriageEntry, unquote_evidence,
)
from review.render import BANNER, RECOVER_HINT
from review.types import SEVERITIES, Finding, FindingScope, FindingSpan

# ── Where a finding's body ends ──

def _severity_names() -> dict[str, str]:
    """Every heading a severity answers to, lowercased, mapped to its key."""
    names: dict[str, str] = {}
    for s in SEVERITIES:
        names[s.section.lower()] = s.key
        # replace("-", " ") mirrors severity_of_heading's input normalisation so
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


def severity_of_heading(stripped: str) -> str | None:
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
        or severity_of_heading(stripped) is not None
    )


def _finalize_finding(finding: Finding, body_lines: list[str]):
    body = "\n".join(body_lines).strip()
    if not body:
        return
    body = re.sub(r"\n+###[^\n]*$", "", body, flags=re.DOTALL).strip()
    finding.body = body


def _scope_after(stripped: str, scope: FindingScope) -> FindingScope:
    """The scope a heading line puts the walk into, or the one it was in."""
    if severity_of_heading(stripped) is not None:
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


# An evidence block under a finding: an indented blockquote fence and every
# indented blockquote line. Evidence is kept on the record and never rendered.
EVIDENCE_FENCE_RE = re.compile(r"^ {2,}>\s*```")
EVIDENCE_LINE_RE = re.compile(r"^ {2,}>")

_SECTION_RE = re.compile(r"^## (.+?)\s*$")
# No `X`: FINDING_ID_RE never accepted `[X]`, so such a line stays prose.
_DECLARATION_LIKE_RE = re.compile(r"^- (?:\[[ x]\] )?\*\*\[([A-Z]+)(\d+)\]")
_FAILURE_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*(\w+)\s*\|$")
_FAILURE_HEADER = ("| agent |", "|---")
_SEVERITY_KEYS = frozenset(s.key for s in SEVERITIES)
# Named location at the start of the remainder when the line has no dash
# separator. extract_body_text then returns "" and R2 still needs the body
# (`text` in `- [ ] **[S1]** \`a.py:1\` text *(declined — x)*`). The `.+?`
# alternatives match `_PATH_SECTION_RE`; this exists only for that no-dash case.
_LOCATION_OPEN_RE = re.compile(r"^(?:\*\*`.+?`\*\*|\*\*.+?\*\*|`.+?`)\s*")


class IngestErrorKind(StrEnum):
    RENDERED_INPUT = "rendered_input"
    UNKNOWN_SEVERITY = "unknown_severity"
    DUPLICATE_ID = "duplicate_id"
    MISSING_ANCHOR = "missing_anchor"
    INVALID_JSON = "invalid_json"
    SCHEMA_VIOLATION = "schema_violation"
    EMPTY_FIELD = "empty_field"
    LINE_IN_PATH = "line_in_path"
    BAD_LOCATION = "bad_location"
    BAD_CARRY = "bad_carry"


@dataclass(frozen=True)
class IngestError:
    """One entry ingest will not represent, and where it is.

    A markdown error names its `line`. A JSON decode error names `line` and
    `column`. A schema or semantic error names the JSON path `at`, because a
    parsed document has no lines left to point at.
    """

    kind: IngestErrorKind
    line: int
    detail: str
    column: int = 0
    at: str = ""

    def render(self) -> str:
        where = self.at or (
            f"line {self.line}, column {self.column}" if self.column else f"line {self.line}"
        )
        return f"{where}: {self.kind.value} — {self.detail}"


class IngestRejected(Exception):
    """The text holds entries ingest will not represent; `errors` names each."""

    def __init__(self, errors: tuple[IngestError, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(e.render() for e in errors))


# ceiling: a fixed repair budget rather than one scaled to the error count.
# Upgrade when repair turns regularly end with errors left that a larger budget
# would have reached — the trail records every second rejection.
REPAIR_MAX_TURNS = 10


@dataclass(frozen=True)
class IngestOutcome:
    """What ingesting an agent file produced, after at most one repair turn.

    `ok` is whether a record was built. `repaired` is whether the repair turn
    ran. `diagnosis` is the repair invoke's own failure — a non-zero exit or a
    cause `diagnose` named — and is unset when the repair ran and ingest still
    rejected the file.
    """

    record: ReviewRecord | None
    errors: tuple[IngestError, ...] = ()
    repaired: bool = False
    diagnosis: Diagnosis | None = None

    @property
    def ok(self) -> bool:
        return self.record is not None


class Artifact(Protocol):
    """An agent artifact type: strict-loadable, and able to name its own problems."""

    def problems(self) -> tuple[IngestError, ...]: ...


A = TypeVar("A", bound=Artifact)


@dataclass(frozen=True)
class ArtifactOutcome(Generic[A]):
    """What ingesting an agent's JSON produced, after at most one repair turn.

    The same contract as `IngestOutcome`, for a typed artifact instead of a
    record.
    """

    artifact: A | None
    errors: tuple[IngestError, ...] = ()
    repaired: bool = False
    diagnosis: Diagnosis | None = None

    @property
    def ok(self) -> bool:
        return self.artifact is not None


def _decode_error(text: str, exc: json.JSONDecodeError) -> IngestError:
    detail = exc.msg
    if text.lstrip().startswith("```"):
        detail += " — the file starts with a code fence; write only the JSON document"
    return IngestError(IngestErrorKind.INVALID_JSON, exc.lineno, detail, column=exc.colno)


def load_artifact(path: str, kind: type[A]) -> A:
    """The artifact the JSON at `path` holds. Raises `IngestRejected` when it is malformed.

    Three gates, in order, each one only reached when the last passed:
    `json.loads` (line and column), the strict schema (JSON path), and the
    artifact's own `problems()` (JSON path).
    """
    text = Path(path).read_text()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestRejected((_decode_error(text, exc),)) from None
    try:
        artifact = core.strict.load_strict(kind, raw)
    except core.strict.SchemaRejected as exc:
        raise IngestRejected(tuple(
            IngestError(IngestErrorKind.SCHEMA_VIOLATION, 0, v.detail, at=v.at)
            for v in exc.violations
        )) from None
    problems = artifact.problems()
    if problems:
        raise IngestRejected(problems)
    return artifact


@dataclass(frozen=True)
class EvidenceSplit:
    """Body lines vs indented-blockquote evidence; trailing blanks are kept."""

    body: tuple[str, ...]
    evidence: tuple[str, ...]


def split_evidence(lines: list[str]) -> EvidenceSplit:
    """Partition `lines` into non-evidence body and indented-blockquote evidence.

    Trailing blank body lines are kept; a caller that wants them gone trims.
    """
    evidence: list[str] = []
    body: list[str] = []
    in_fence = False
    for raw in lines:
        fence = EVIDENCE_FENCE_RE.match(raw)
        if in_fence or fence or EVIDENCE_LINE_RE.match(raw):
            evidence.append(raw)
            in_fence = (not fence) if in_fence else bool(fence)
            continue
        body.append(raw)
    return EvidenceSplit(tuple(body), tuple(evidence))


_TRIAGE_SEPARATOR_RE = re.compile(r"^(?:—|--|-)\s*")


def _triage_entry(text: str) -> TriageEntry:
    """A File Triage line as its file and its note."""
    m = TRIAGE_LINE_RE.match(text)
    if not m:
        return TriageEntry("", text.strip().removeprefix("- ").strip())
    return TriageEntry(m.group(1), _TRIAGE_SEPARATOR_RE.sub("", text[m.end():].strip()))


@dataclass(frozen=True)
class _Section:
    heading: str
    start: int
    end: int


def _sections(lines: list[str]) -> list[_Section]:
    heads = [i for i, line in enumerate(lines) if _SECTION_RE.match(line)]
    ends = heads[1:] + [len(lines)]
    return [_Section(_SECTION_RE.match(lines[s]).group(1), s, e) for s, e in zip(heads, ends)]


@dataclass(frozen=True)
class _Annotation:
    first: str
    disposition: Disposition
    reason: str = ""
    unverified: bool = False


def _take_tail(after: str, checked: bool) -> _Annotation:
    """Declined/unverified tail taken off `after`. Skip is applied later."""
    if checked:
        m = UNVERIFIED_TAIL_RE.search(after)
        if m:
            return _Annotation(after[:m.start()].rstrip(), Disposition.FIXED,
                               (m.group(1) or "").strip(), True)
        return _Annotation(after, Disposition.FIXED)
    if m := DECLINED_TAIL_RE.search(after):
        return _Annotation(after[:m.start()].rstrip(), Disposition.DECLINED,
                           (m.group(1) or "").strip())
    return _Annotation(after, Disposition.OPEN)


def _skip_on_declaration(remainder: str, has_later: bool, note: _Annotation) -> _Annotation:
    """Skip tail on the declaration line only when it is also the last body line.

    `SKIP_TAIL_RE` is searched against the whole span body, so
    a skip at the end of the declaration does not count once a continuation
    (evidence included) follows. Strip here only when nothing follows, so the
    skip's em dash cannot steal `extract_body_text`'s separator.
    """
    if note.disposition is not Disposition.OPEN or has_later:
        return note
    if m := SKIP_TAIL_RE.search(remainder):
        return _Annotation(remainder[:m.start()].rstrip(), Disposition.SKIPPED,
                           (m.group(1) or "").strip())
    return note


@dataclass(frozen=True)
class _SkipLater:
    first: str
    cont: tuple[str, ...]
    note: _Annotation


@dataclass(frozen=True)
class _LaterEnds:
    last: str
    first: str


def _later_ends(rest: list[str]) -> _LaterEnds:
    last = ""
    first = ""
    for raw in rest:
        if not raw.strip():
            continue
        last = raw.rstrip()
        if not first:
            first = raw.strip()
    return _LaterEnds(last, first)


def _rewrite_last_nonblank(cont: list[str], replacement: str) -> list[str]:
    new_cont = list(cont)
    for i in range(len(new_cont) - 1, -1, -1):
        if not new_cont[i].strip():
            continue
        if SKIP_TAIL_RE.search(new_cont[i]):
            new_cont[i] = replacement
        break
    return new_cont


def _rewrite_first_nonblank(cont: list[str], leftover: str) -> list[str]:
    new_cont = list(cont)
    for i, line in enumerate(new_cont):
        if not line.strip():
            continue
        if SKIP_HEAD_RE.match(line.strip()):
            indent = line[: len(line) - len(line.lstrip())]
            new_cont[i] = f"{indent}{leftover}".rstrip()
        break
    return new_cont


def _skip_on_later(
    first: str,
    cont: list[str],
    rest: list[str],
    note: _Annotation,
    *,
    declared_empty: bool,
) -> _SkipLater:
    """Skip on a continuation that a whole-span skip search would see.

    Tail: the last non-blank span line after the declaration (`rest` in
    document order, evidence included). Head: the first non-blank of those
    lines, only when the declaration body is empty, so that line is the start
    of today's stripped `finding.body`.
    """
    if note.disposition is not Disposition.OPEN:
        return _SkipLater(first, tuple(cont), note)
    ends = _later_ends(rest)
    if ends.last and (m := SKIP_TAIL_RE.search(ends.last)):
        note = _Annotation(first, Disposition.SKIPPED, (m.group(1) or "").strip())
        stripped = ends.last[:m.start()].rstrip()
        return _SkipLater(first, tuple(_rewrite_last_nonblank(cont, stripped)), note)
    if declared_empty and ends.first and (m := SKIP_HEAD_RE.match(ends.first)):
        note = _Annotation(first, Disposition.SKIPPED, (m.group(1) or "").strip())
        leftover = ends.first[m.end():].lstrip()
        return _SkipLater(first, tuple(_rewrite_first_nonblank(cont, leftover)), note)
    return _SkipLater(first, tuple(cont), note)


def _apply_head(first: str, note: _Annotation) -> _Annotation:
    """Head annotation on the body's first line, if the tail left the finding open."""
    if note.disposition is not Disposition.OPEN:
        return _Annotation(first, note.disposition, note.reason, note.unverified)
    for head_re, disposition in (
        (DECLINED_HEAD_RE, Disposition.DECLINED),
        (SKIP_HEAD_RE, Disposition.SKIPPED),
    ):
        if m := head_re.match(first):
            return _Annotation(first[m.end():].lstrip(), disposition, (m.group(1) or "").strip())
    return _Annotation(first, Disposition.OPEN)


@dataclass(frozen=True)
class _Lead:
    lead: str
    first: str
    declared: str


def _split_lead(remainder: str) -> _Lead:
    declared = extract_body_text(remainder)
    if declared and remainder.endswith(declared):
        return _Lead(remainder[: len(remainder) - len(declared)], declared, declared)
    if not declared and ("—" in remainder or " -- " in remainder or " - " in remainder):
        # extract_body_text found a separator and nothing after it. Consume
        # the dash into lead ("`a.py:1` — ") rather than leaving it for
        # _LOCATION_OPEN_RE, which would park it in the body.
        return _Lead(remainder.rstrip() + " ", "", declared)
    opened = _LOCATION_OPEN_RE.match(remainder)
    if opened:
        return _Lead(remainder[:opened.end()], remainder[opened.end():].strip(), declared)
    return _Lead(remainder, "", declared)


def _finding(span: FindingSpan, lines: list[str]) -> RecordFinding:
    line = span.line
    m = FINDING_ID_RE.match(line)
    box = m.group(1)
    sid = SID_MARKER_RE.search(line[:m.end()])
    after = line[m.end():]
    tail = _take_tail(after, checked=box == "x")
    rest = lines[span.start + 1: span.end]
    has_later = any(raw.strip() for raw in rest)
    tail = _skip_on_declaration(tail.first, has_later, tail)
    remainder = tail.first
    location = finding_location(remainder)
    if location.named:
        parts = _split_lead(remainder)
    else:
        parts = _Lead("", remainder.strip(), remainder.strip())
    note = _apply_head(parts.first, tail)
    split = split_evidence(rest)
    cont = [part.rstrip() for part in split.body]
    while cont and not cont[-1].strip():
        cont.pop()
    skipped = _skip_on_later(
        note.first, cont, rest, note, declared_empty=not parts.declared,
    )
    first, cont, note = skipped.first, skipped.cont, skipped.note
    identity = FindingIdentity.of(line)
    return RecordFinding(
        id=f"{m.group(2)}{m.group(3)}", severity=m.group(2),
        path=location.path, line=location.line, end_line=location.end_line,
        body="\n".join([first, *cont]),
        evidence=unquote_evidence("\n".join(split.evidence)),
        stable_id=identity.stable_id if identity else "",
        sid=sid.group(1) if sid else "",
        disposition=note.disposition, reason=note.reason, unverified=note.unverified,
        checkbox=box is not None,
    )


@dataclass
class _Builder:
    """Accumulates the record one section at a time."""

    summary: str = ""
    verdict: str = ""
    static_analysis: str = ""
    ledger: list[LedgerLine] = field(default_factory=list)
    triage: list[TriageEntry] | None = None
    failures: list[AgentFailure] = field(default_factory=list)
    severity_sections: list[SeveritySection] = field(default_factory=list)
    extra: list[ProseSection] = field(default_factory=list)
    errors: list[IngestError] = field(default_factory=list)

    def add(self, section: _Section, residual: list[tuple[int, str]]) -> None:
        heading = section.heading.strip()
        key = heading.lower()
        body = "\n".join(text for _, text in residual).strip()
        severity = severity_of_heading(f"## {heading}")
        if severity:
            self._severity(severity, residual, body)
        elif key == SECTION_FILE_TRIAGE.lower():
            self.triage = (self.triage or []) + [_triage_entry(t) for _, t in residual if t.strip()]
        elif key == SECTION_FAILURES.lower():
            self.failures += _failures([t for _, t in residual])
        elif key == SECTION_SUMMARY.lower() and not self.summary:
            self.summary = body
        elif key == SECTION_VERDICT.lower() and not self.verdict:
            self.verdict = body
        elif key == SECTION_STATIC_ANALYSIS.lower() and not self.static_analysis:
            self.static_analysis = body
        elif key == SECTION_PRIOR_FINDINGS.lower():
            self.ledger += [e for raw in body.split("\n") if (e := parse_ledger_line(raw))]
        else:
            self.extra.append(ProseSection(heading, body))

    def _severity(self, severity: str, residual: list[tuple[int, str]], note: str) -> None:
        """Record the heading, and reject any declaration-shaped line no span claimed.

        A residual line shaped like a declaration is one the grammar could not
        read: an unknown severity key, or a known one with nothing after its
        tag (`FINDING_ID_RE` requires text after the closing `**`).
        """
        self.severity_sections.append(SeveritySection(severity, note))
        for index, text in residual:
            m = _DECLARATION_LIKE_RE.match(text.strip())
            if not m:
                continue
            tag = f"[{m.group(1)}{m.group(2)}]"
            if m.group(1) not in _SEVERITY_KEYS:
                self.errors.append(IngestError(
                    IngestErrorKind.UNKNOWN_SEVERITY, index + 1,
                    f"{tag} is not a severity this review knows ({', '.join(sorted(_SEVERITY_KEYS))})",
                ))
            else:
                self.errors.append(IngestError(
                    IngestErrorKind.MISSING_ANCHOR, index + 1,
                    f"could not parse a finding declaration (nothing usable after its tag): {text.strip()}",
                ))

    def record(self, findings: list[RecordFinding]) -> ReviewRecord:
        return ReviewRecord(
            summary=self.summary, findings=tuple(findings),
            triage=tuple(self.triage) if self.triage is not None else None,
            static_analysis=self.static_analysis, failures=tuple(self.failures),
            verdict=self.verdict, severity_sections=tuple(self.severity_sections),
            ledger=tuple(self.ledger),
            extra=tuple(self.extra),
        )


def _failures(lines: list[str]) -> list[AgentFailure]:
    recoverable = any(RECOVER_HINT in line for line in lines)
    rows = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(_FAILURE_HEADER):
            continue
        if m := _FAILURE_ROW_RE.match(stripped):
            rows.append(AgentFailure(
                agent=m.group(1), reason=m.group(2).replace("\\|", "|"),
                status=m.group(3), recoverable=recoverable,
            ))
    return rows


def _declaration_errors(spans: list[FindingSpan]) -> list[IngestError]:
    errors = []
    seen: Counter[str] = Counter()
    for span in spans:
        m = FINDING_ID_RE.match(span.line)
        fid = f"{m.group(2)}{m.group(3)}"
        seen[fid] += 1
        if seen[fid] == 2:
            errors.append(IngestError(IngestErrorKind.DUPLICATE_ID, span.start + 1,
                                      f"{fid} is declared more than once"))
    return errors


def ingest_review(text: str, *, strict: bool = True) -> ReviewRecord:
    """The record `text` declares. Raises `IngestRejected` when it is malformed.

    The title and header are not read — they are `meta.json`'s. `strict=False`
    keeps what it can and drops the errors. That path is for callers ingesting
    recovery artifacts and for test helpers that are not agent output.
    Text carrying `BANNER` is refused whatever `strict` says.
    """
    if BANNER in text:
        raise IngestRejected((IngestError(
            IngestErrorKind.RENDERED_INPUT, 1,
            "this is a rendered review.md; only agent output is ingested",
        ),))
    lines = text.split("\n")
    spans = [s for s in finding_spans(text) if s.scope is FindingScope.DECLARED]
    claimed = {i for s in spans for i in range(s.start, s.end)}
    builder = _Builder()
    for section in _sections(lines):
        residual = [(i, lines[i]) for i in range(section.start + 1, section.end) if i not in claimed]
        builder.add(section, residual)
    errors = _declaration_errors(spans) + builder.errors
    if strict and errors:
        raise IngestRejected(tuple(sorted(errors, key=lambda e: e.line)))
    return builder.record([_finding(span, lines) for span in spans])


def repair_prompt(prompt: str, path: str, errors: tuple[IngestError, ...]) -> str:
    """Re-send `prompt` with the ingest errors the agent must fix in `path`."""
    listed = "\n".join(f"- {e.render()}" for e in errors)
    return (
        f"{prompt}\n\n## Your output was rejected\n\n"
        f"What you wrote to `{path}` cannot be read:\n\n{listed}\n\n"
        "Fix that file in place — only what is named above — so it is the format "
        "the Output section asks for. Do not rewrite anything else."
    )


def _with_repair(path, prompt, invoke, read, wrap, diagnose):
    """Read `path` with `read`, giving the agent one turn to fix a rejection.

    `wrap` builds the outcome type the caller expects. A second rejection is
    returned, not raised. A repair turn that itself fails — a non-zero exit, or
    a cause `diagnose` names — is that failure, not a malformed-output rejection.
    """
    try:
        return wrap(read())
    except IngestRejected as exc:
        errors = exc.errors
    core.log.warn(f"{path} rejected ({len(errors)} error(s)) — one repair turn")
    rc = invoke(repair_prompt(prompt, path, errors), REPAIR_MAX_TURNS)
    try:
        return wrap(read(), repaired=True)
    except IngestRejected as exc:
        if rc == 0:
            return wrap(None, exc.errors, repaired=True)
        failure = diagnose() if diagnose is not None else Diagnosis(
            DiagnosisKind.AGENT_ERROR, detail=f"exited with code {rc}",
        )
        return wrap(None, repaired=True, diagnosis=failure)


def ingest_with_repair(
    path: str, prompt: str, invoke: Callable[[str, int], int],
    *, diagnose: Callable[[], Diagnosis] | None = None,
) -> IngestOutcome:
    """Ingest the agent's markdown at `path`, with one repair turn (see `_with_repair`)."""
    return _with_repair(path, prompt, invoke,
                        lambda: ingest_review(Path(path).read_text()), IngestOutcome, diagnose)


def ingest_artifact(
    path: str, prompt: str, invoke: Callable[[str, int], int], kind: type[A],
    *, diagnose: Callable[[], Diagnosis] | None = None,
) -> ArtifactOutcome:
    """Load the agent's JSON at `path` as a `kind`, with one repair turn (see `_with_repair`)."""
    return _with_repair(path, prompt, invoke,
                        lambda: load_artifact(path, kind), ArtifactOutcome, diagnose)
