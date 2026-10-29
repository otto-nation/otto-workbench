"""What a review agent writes: one JSON document per phase, each a frozen dataclass.

Scout, holistic, group, single, synthesis and disprove each write one of the
types here. The prompt shows the agent `example()` as `core.serde.to_dict` writes
it, followed by `FIELD_NOTES` for every field, so what the prompt asks for and
what `review.ingest` accepts are one schema. A test
holds each example to the strict reader and to `problems()`, and holds the
notes to the fields.

The types are narrower than the record on purpose. `ReviewRecord` carries what
the harness decides — dispositions, stable IDs, failures, static analysis,
checkboxes — and an agent must not be able to write any of it. The strict
reader rejects an unknown key, so a harness field in an agent's document is a
named error rather than a value silently taken. `to_record` is the one
conversion, and `as_group_artifact` is its inverse for the one prompt that
shows a merged record back to an agent (synthesis).

A finding's severity is its ID's first letter, so the two cannot disagree.
`TriageEntry` and `LedgerLine` are the record's own types: what an agent
writes there is exactly what the record keeps.
"""

# doc-group: findings

from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum, StrEnum
from typing import get_type_hints

import core.log
import core.serde
from core.phases import Mode
from core.serde import HintKind, classify
from core.text import join_or
from pr.domains import ReviewVerdict
from review.grammar import SEVERITY_KEY, FindingIdentity, strip_line_suffix
from review.budget import DERIVED_AREA_RESERVE_BYTES
from review.ingest import IngestError, IngestErrorKind
from review.record import Disposition, LedgerLine, RecordFinding, ReviewRecord, TriageEntry
from review.store import StoredReview
from review.types import SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD, PriorDisposition

# A finding ID: a severity key, then a number from 1. The key class is the one
# every reader of a finding ID uses, built from `SEVERITIES`.
_ID_RE = re.compile(rf"^{SEVERITY_KEY}[1-9]\d*$")
# Disprove challenges must-fix and should-fix findings only.
_CHALLENGED_RE = re.compile(rf"^[{SEVERITY_MUST}{SEVERITY_SHOULD}][1-9]\d*$")
_KEYS = ", ".join(s.key for s in SEVERITIES)

# How many focus areas a phase may derive. Defined here, beside the artifacts
# that carry derived focus, and read by `review.focus` when it adopts them.
MAX_DERIVED_FOCUS = 3


def _error(kind: IngestErrorKind, at: str, detail: str) -> IngestError:
    return IngestError(kind, 0, detail, at=at)


def _empty(at: str, what: str) -> IngestError:
    return _error(IngestErrorKind.EMPTY_FIELD, at, f"{what} is empty")


def _location_problems(
    path: str | None, line: int | None, end_line: int | None, at: str,
) -> list[IngestError]:
    """The first thing wrong with a location, or nothing.

    One problem at most: each check below assumes the ones above it held.
    """
    if path is None and line is None and end_line is None:
        return []
    if path is None:
        return [_error(IngestErrorKind.BAD_LOCATION, f"{at}.line",
                       "a line needs a path; set path, or make line null")]
    if not path.strip():
        return [_error(IngestErrorKind.BAD_LOCATION, f"{at}.path",
                       "path is empty; use null for a finding about the whole PR")]
    if strip_line_suffix(path) != path:
        return [_error(IngestErrorKind.LINE_IN_PATH, f"{at}.path",
                       f"{path!r} carries a line number; put it in line and end_line")]
    if line is not None and line < 1:
        return [_error(IngestErrorKind.BAD_LOCATION, f"{at}.line", "line numbers start at 1")]
    if end_line is not None and (line is None or end_line < line):
        return [_error(IngestErrorKind.BAD_LOCATION, f"{at}.end_line",
                       "end_line needs a line at or before it")]
    return []


def _duplicates(ids: list[str], at: str) -> list[IngestError]:
    return [
        _error(IngestErrorKind.DUPLICATE_ID, at, f"{fid} is used {n} times")
        for fid, n in Counter(ids).items() if n > 1
    ]


# ── Findings ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentFinding:
    """One finding as an agent writes it."""

    id: str
    path: str | None = None
    line: int | None = None
    end_line: int | None = None
    body: str = ""
    evidence: str = ""
    sid: str = ""
    declined: str = ""

    @property
    def severity(self) -> str:
        return self.id[:1]

    def problems(self, at: str) -> list[IngestError]:
        out = _location_problems(self.path, self.line, self.end_line, at)
        if not _ID_RE.fullmatch(self.id):
            out.append(_error(IngestErrorKind.UNKNOWN_SEVERITY, f"{at}.id",
                              f"{self.id!r} is not a severity key ({_KEYS}) and a number from 1"))
        if not self.body.strip():
            out.append(_empty(f"{at}.body", "a finding's body"))
        if self.declined.strip() and not self.sid.strip():
            out.append(_error(IngestErrorKind.BAD_CARRY, f"{at}.declined",
                              "only a carried prior finding can be declined; set sid to its "
                              "`<!-- sid: … -->` value, or make declined \"\""))
        return out

    def to_record(self, *, checkbox: bool, declinable: frozenset[str]) -> RecordFinding:
        """This finding as the record keeps it, open unless it carries a decline the harness believes.

        A decline is believed only when `sid` names a finding the prior round
        held (`declinable`, from `declinable_sids`). Any other is kept open, its
        reason dropped, and logged: an agent cannot adjudicate its own finding.
        """
        path, body, sid = self.path or "", self.body.strip(), self.sid.strip()
        declined = self.declined.strip()
        if declined and (not sid or sid not in declinable):
            if not sid:
                core.log.warn(f"{self.id}: declined without a sid — kept open")
            else:
                core.log.warn(f"{self.id}: declined, but {sid} names no prior finding — kept open")
            declined = ""
        return RecordFinding(
            id=self.id, severity=self.severity, path=path, line=self.line,
            end_line=self.end_line, body=body, evidence=self.evidence.strip("\n"),
            stable_id=FindingIdentity.of_fields(path, self.line, body).stable_id,
            sid=sid,
            disposition=Disposition.DECLINED if declined else Disposition.OPEN,
            reason=declined, checkbox=checkbox,
        )


def _findings_problems(findings: tuple[AgentFinding, ...], at: str) -> list[IngestError]:
    out: list[IngestError] = []
    for i, f in enumerate(findings):
        out += f.problems(f"{at}[{i}]")
    return out + _duplicates([f.id for f in findings], at)


def _triage_problems(triage: tuple[TriageEntry, ...], at: str) -> list[IngestError]:
    return [_empty(f"{at}[{i}].path", "a triage entry's path")
            for i, e in enumerate(triage) if not e.path.strip()]


def _ledger_problems(lines: tuple[LedgerLine, ...], at: str) -> list[IngestError]:
    out: list[IngestError] = []
    for i, entry in enumerate(lines):
        if not _ID_RE.fullmatch(entry.id):
            out.append(_error(IngestErrorKind.UNKNOWN_SEVERITY, f"{at}[{i}].id",
                              f"{entry.id!r} is not a prior finding's ID"))
        if entry.path is not None and not entry.path.strip():
            out.append(_error(IngestErrorKind.BAD_LOCATION, f"{at}[{i}].path",
                              "path is empty; use null when the prior finding named none"))
    return out


@dataclass(frozen=True)
class GroupArtifact:
    """What one group agent writes (`group-N.json`)."""

    triage: tuple[TriageEntry, ...] = ()
    findings: tuple[AgentFinding, ...] = ()
    prior_findings: tuple[LedgerLine, ...] = ()

    def problems(self) -> tuple[IngestError, ...]:
        return tuple(
            _triage_problems(self.triage, "$.triage")
            + _findings_problems(self.findings, "$.findings")
            + _ledger_problems(self.prior_findings, "$.prior_findings")
        )

    def to_record(self, *, declinable: frozenset[str]) -> ReviewRecord:
        """The group's record. A group writes no checkboxes; the review's are synthesis's."""
        return ReviewRecord(
            triage=self.triage or None,
            findings=tuple(f.to_record(checkbox=False, declinable=declinable) for f in self.findings),
            ledger=self.prior_findings,
        )

    @classmethod
    def example(cls, mode: Mode) -> GroupArtifact:
        return cls(triage=_EXAMPLE_TRIAGE, findings=_EXAMPLE_FINDINGS, prior_findings=_EXAMPLE_LEDGER)


@dataclass(frozen=True)
class ReviewArtifact:
    """What the single agent and synthesis write (`single.json`, `synthesis.json`)."""

    summary: str
    findings: tuple[AgentFinding, ...] = ()
    triage: tuple[TriageEntry, ...] = ()
    prior_findings: tuple[LedgerLine, ...] = ()
    verdict: str = ""

    def problems(self) -> tuple[IngestError, ...]:
        summary = [] if self.summary.strip() else [_empty("$.summary", "the summary")]
        return tuple(
            summary
            + _triage_problems(self.triage, "$.triage")
            + _findings_problems(self.findings, "$.findings")
            + _ledger_problems(self.prior_findings, "$.prior_findings")
        )

    def to_record(self, *, checkbox: bool, declinable: frozenset[str]) -> ReviewRecord:
        """The review's record. `checkbox` is the mode's: a self-review is a checklist."""
        return ReviewRecord(
            summary=self.summary.strip(),
            findings=tuple(f.to_record(checkbox=checkbox, declinable=declinable) for f in self.findings),
            triage=self.triage or None,
            ledger=self.prior_findings,
            verdict=self.verdict.strip(),
        )

    @classmethod
    def example(cls, mode: Mode) -> ReviewArtifact:
        full = cls(
            summary=_EXAMPLE_SUMMARY, findings=_EXAMPLE_FINDINGS, triage=_EXAMPLE_TRIAGE,
            prior_findings=_EXAMPLE_LEDGER, verdict=_EXAMPLE_VERDICT,
        )
        return full if mode is Mode.PR else replace(full, triage=(), verdict="")


def _agent_finding(f: RecordFinding) -> AgentFinding:
    declined = f.disposition is Disposition.DECLINED
    return AgentFinding(
        id=f.id, path=f.path or None, line=f.line, end_line=f.end_line,
        body=f.body, evidence=f.evidence, sid=f.sid or f.stable_id,
        declined=(f.reason or "declined") if declined else "",
    )


def as_group_artifact(record: ReviewRecord) -> GroupArtifact:
    """`record`'s visible findings, triage and ledger as the artifact a group writes.

    What synthesis is shown of the merged groups, so it copies JSON forward
    rather than translating markdown. Each finding carries a `sid` — the one it
    arrived with, else its own stable ID — so a carried finding stays
    recognisable however synthesis rewords it. Triage lines that name no single
    file are left out; they are not entries an agent could write.
    """
    return GroupArtifact(
        triage=tuple(e for e in record.triage or () if e.path),
        findings=tuple(_agent_finding(f) for f in record.visible),
        prior_findings=record.ledger,
    )


def declinable_sids(prior: StoredReview | None) -> frozenset[str]:
    """The sids a carried decline may name: every identity the prior round's findings hold.

    A finding's stable ID is the sid the prior section shows it with (or the
    sid it was itself carried under). With no prior round there is nothing to
    carry, so nothing can be declined.
    """
    if prior is None:
        return frozenset()
    return frozenset(s for f in prior.record.findings for s in (f.stable_id, f.sid) if s)


# ── Phase 1: scout and holistic ─────────────────────────────────────────────


class SeverityHint(StrEnum):
    """How serious a scout thinks a lead is. Values are `SeverityConfig.label`s."""

    MUST_FIX = "must-fix"
    SHOULD_FIX = "should-fix"
    NIT = "nit"


@dataclass(frozen=True)
class Lead:
    path: str
    line: int | None = None
    end_line: int | None = None
    concern: str = ""
    severity_hint: SeverityHint = SeverityHint.SHOULD_FIX
    why: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity_hint", SeverityHint(self.severity_hint))


@dataclass(frozen=True)
class QuietFile:
    path: str
    reason: str = ""


@dataclass(frozen=True)
class AgentFocus:
    """One derived focus area as a scan writes it."""

    title: str
    rationale: str = ""
    paths: tuple[str, ...] = ()


def _focus_path_problems(path: str, at: str) -> list[IngestError]:
    if not path.strip():
        return [_error(
            IngestErrorKind.BAD_LOCATION, at,
            "path is empty; use [] when the area spans the PR",
        )]
    if strip_line_suffix(path) != path:
        return [_error(
            IngestErrorKind.LINE_IN_PATH, at,
            f"{path!r} carries a line number; drop the :N suffix",
        )]
    return []


def _focus_line_bytes(item: AgentFocus) -> int:
    return len((item.title + item.rationale + "".join(item.paths)).encode())


def _focus_item_problems(item: AgentFocus, at: str) -> list[IngestError]:
    out: list[IngestError] = []
    if not item.title.strip():
        out.append(_empty(f"{at}.title", "a focus area's title"))
    for j, path in enumerate(item.paths):
        out += _focus_path_problems(path, f"{at}.paths[{j}]")
    size = _focus_line_bytes(item)
    if size > DERIVED_AREA_RESERVE_BYTES:
        out.append(_error(
            IngestErrorKind.FOCUS_SIZE, at,
            f"{size} bytes; a derived area is capped at {DERIVED_AREA_RESERVE_BYTES}",
        ))
    return out


def _agent_focus_problems(
    items: tuple[AgentFocus, ...], at: str, *, lo: int = 1, hi: int = MAX_DERIVED_FOCUS,
) -> list[IngestError]:
    out: list[IngestError] = []
    if not lo <= len(items) <= hi:
        out.append(_error(
            IngestErrorKind.FOCUS_COUNT, at,
            f"{len(items)} focus areas; need {lo}–{hi}",
        ))
    for i, item in enumerate(items):
        out += _focus_item_problems(item, f"{at}[{i}]")
    return out


@dataclass(frozen=True)
class ScoutArtifact:
    """What the scout writes (`scout.json`)."""

    leads: tuple[Lead, ...] = ()
    no_scrutiny: tuple[QuietFile, ...] = ()
    direction_concern: str = ""
    focus: tuple[AgentFocus, ...] = ()

    def problems(self) -> tuple[IngestError, ...]:
        out: list[IngestError] = []
        for i, lead in enumerate(self.leads):
            out += _location_problems(lead.path, lead.line, lead.end_line, f"$.leads[{i}]")
            if not lead.concern.strip():
                out.append(_empty(f"$.leads[{i}].concern", "a lead's concern"))
        out += [_empty(f"$.no_scrutiny[{i}].reason", "a no-scrutiny reason")
                for i, q in enumerate(self.no_scrutiny) if not q.reason.strip()]
        out += _agent_focus_problems(self.focus, "$.focus")
        return tuple(out)

    @classmethod
    def example(cls, mode: Mode) -> ScoutArtifact:
        return cls(
            leads=(
                Lead("pkg/handler.go", 42, None, "Error return value ignored after db.Query",
                     SeverityHint.MUST_FIX, "No error check between the query and the use of its rows."),
                Lead("pkg/kinds.go", 118, 125, "Enum case added but the sibling switch is not updated",
                     SeverityHint.SHOULD_FIX, "The new FooType is handled here but not in bar_handler.go."),
            ),
            no_scrutiny=(QuietFile("pkg/api.pb.go", "generated code, no custom logic"),),
            focus=_EXAMPLE_FOCUS,
        )


@dataclass(frozen=True)
class HolisticArtifact:
    """What the holistic scan writes (`holistic.json`)."""

    assessment: str
    flags: tuple[str, ...] = ()
    cross_module: tuple[str, ...] = ()
    direction_concern: str = ""
    focus: tuple[AgentFocus, ...] = ()

    def problems(self) -> tuple[IngestError, ...]:
        out: list[IngestError] = []
        if not self.assessment.strip():
            out.append(_empty("$.assessment", "the assessment"))
        out += _agent_focus_problems(self.focus, "$.focus")
        return tuple(out)

    @classmethod
    def example(cls, mode: Mode) -> HolisticArtifact:
        return cls(
            assessment="Does what the description claims: retry handling moves into one helper. "
                       "The scope is focused.",
            flags=("`retry.go` changes the backoff — check every caller that assumed a fixed delay",),
            cross_module=("`client` and `worker` now disagree on the timeout's unit",),
            focus=_EXAMPLE_FOCUS,
        )


# ── Disprove ────────────────────────────────────────────────────────────────


class DisproveOutcome(StrEnum):
    SURVIVES = "survives"
    FALSIFIED = "falsified"


@dataclass(frozen=True)
class DisproveVerdict:
    id: str
    outcome: DisproveOutcome
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", DisproveOutcome(self.outcome))


@dataclass(frozen=True)
class DisproveArtifact:
    """What the disprove gate writes (`disprove.json`)."""

    verdicts: tuple[DisproveVerdict, ...] = ()

    def problems(self) -> tuple[IngestError, ...]:
        out: list[IngestError] = []
        for i, v in enumerate(self.verdicts):
            if not _CHALLENGED_RE.fullmatch(v.id):
                out.append(_error(IngestErrorKind.UNKNOWN_SEVERITY, f"$.verdicts[{i}].id",
                                  f"{v.id!r} is not a must-fix or should-fix finding ID"))
            if not v.reason.strip():
                out.append(_empty(f"$.verdicts[{i}].reason", "a verdict's reason"))
        return tuple(out + _duplicates([v.id for v in self.verdicts], "$.verdicts"))

    @classmethod
    def example(cls, mode: Mode) -> DisproveArtifact:
        return cls(verdicts=(
            DisproveVerdict("M1", DisproveOutcome.SURVIVES,
                            "nothing upstream checks the error; handler.go:47 ranges over rows directly"),
            DisproveVerdict("S2", DisproveOutcome.FALSIFIED,
                            "the nil check exists in the caller at handler.go:31"),
        ))


# ── Examples ────────────────────────────────────────────────────────────────

_EXAMPLE_FINDINGS = (
    AgentFinding(
        id="M1", path="pkg/handler.go", line=42, end_line=44,
        body="`db.Query`'s error is dropped, so a failed query reads as an empty result.\n"
             "Return the error before ranging over `rows`.",
        evidence="rows, _ := db.Query(query)\nfor rows.Next() {",
    ),
    AgentFinding(
        id="S1", path="pkg/retry.go", line=18,
        body="The retry budget is fixed at three attempts.",
        evidence="for attempt := 0; attempt < 3; attempt++ {",
        sid="3f9a2c1d", declined="the bound is a documented `ceiling:` tradeoff",
    ),
    AgentFinding(id="N1", body="The PR title says docs-only, but the change edits code."),
)
_EXAMPLE_TRIAGE = (TriageEntry("pkg/handler.go", "**Tier 2** (application logic) — M1"),)
_EXAMPLE_LEDGER = (
    LedgerLine("M2", PriorDisposition.FIXED, "pkg/old.go", "the guard now runs before the call"),
)
_EXAMPLE_SUMMARY = (
    "Moves retry handling into one helper. Sound overall; the top risk is M1, "
    "a dropped query error on the hot path."
)
_EXAMPLE_VERDICT = f"{ReviewVerdict.CHANGES_REQUESTED.prose} — M1 loses query failures."
_EXAMPLE_FOCUS = (
    AgentFocus("Retry handling", "Backoff changed", ("pkg/retry.go",)),
    AgentFocus("Caller contracts", "Every caller assumed a fixed delay", ("pkg/handler.go",)),
)
_SCAN_FOCUS_NOTE = (
    "1–3 areas this PR most needs reviewed — the risks particular to this "
    "change, each with a title, a rationale, and the paths it concerns"
)


# ── What the prompt says about each field ───────────────────────────────────

_SEVERITY_LEGEND = "; ".join(f"{s.key} = {s.section}" for s in SEVERITIES)
_LEDGER_NOTE = (
    "only when the prompt has a `## Prior review` section: one entry for EVERY prior "
    "finding shown there; [] otherwise"
)

FIELD_NOTES: dict[type, dict[str, str]] = {
    AgentFinding: {
        "id": f"a severity key and a number from 1 ({_SEVERITY_LEGEND}); numbers restart "
              "per severity, never repeat within one, and are renumbered after you write",
        "path": "the file, relative to the repository root, with no `:line` on it; null for "
                "a finding about the PR as a whole",
        "line": "the first line it is about; null for the whole file",
        "end_line": "the last line of a range; null for a single line",
        "body": "markdown: the first line states the defect, later lines explain it and the fix",
        "evidence": "must-fix and should-fix: 1–5 lines copied verbatim from `path` at `line` "
                    "— the code itself, with no `>` markers and no fence; \"\" for nits and idioms",
        "sid": "carrying a prior finding forward: the value in its `<!-- sid: … -->` marker, "
               "or the `sid` the merged findings show it with (synthesis sees one on every "
               "finding, and keeps it); \"\" for a finding you raise yourself",
        "declined": "carrying forward a finding the prior review records as declined, or one "
                    "you decline on the merits: its one-line reason, so it is not raised or "
                    "auto-fixed again. Needs `sid`; \"\" otherwise",
    },
    TriageEntry: {
        "path": "a file you were asked to review",
        "note": "its tier and what you found there, on one line",
    },
    LedgerLine: {
        "id": "the prior finding's ID, copied exactly",
        "disposition": "what became of it",
        "path": "the prior finding's path, copied exactly; null if it named none",
        "note": "any detail; the verdict is `disposition`, never the note",
    },
    GroupArtifact: {
        "triage": "one entry per file in your group",
        "findings": "your findings, any severity, in any order",
        "prior_findings": _LEDGER_NOTE,
    },
    ReviewArtifact: {
        "summary": "markdown: what the change does and its overall quality",
        "findings": "every finding, any severity, in any order",
        "triage": "a PR review: one entry per changed file; a self-review: []",
        "prior_findings": _LEDGER_NOTE,
        "verdict": "a PR review: the verdict the task asks for, then why; a self-review: \"\"",
    },
    Lead: {
        "path": "the file the lead is about",
        "line": "the line it is about; null for the whole file",
        "end_line": "the last line of a range; null for a single line",
        "concern": "what looks wrong, in one sentence",
        "severity_hint": "how serious you think it is; the deep reviewer decides",
        "why": "why it matters, in a sentence",
    },
    QuietFile: {
        "path": "a file that needs no scrutiny",
        "reason": "why, in a few words: generated, renamed, formatting-only",
    },
    AgentFocus: {
        "title": "what this area is, in a few words",
        "rationale": "why it needs review",
        "paths": "the files or directories it concerns; [] when it spans the PR",
    },
    ScoutArtifact: {
        "leads": "one per concern; [] if nothing looks suspicious",
        "no_scrutiny": "files deep reviewers can skip",
        "direction_concern": "only if the approach itself is wrong: why, and what to do "
                             "instead; \"\" otherwise",
        "focus": _SCAN_FOCUS_NOTE,
    },
    HolisticArtifact: {
        "assessment": "markdown: whether the PR does what it claims, and whether its scope "
                      "is focused",
        "flags": "things the detailed reviewers should watch for, one per entry",
        "cross_module": "cross-module design issues, one per entry",
        "direction_concern": "only if the approach itself is wrong: why, and what to do "
                             "instead; \"\" otherwise",
        "focus": _SCAN_FOCUS_NOTE,
    },
    DisproveVerdict: {
        "id": "a must-fix or should-fix finding ID from the review",
        "outcome": "whether it survived your attempt to falsify it",
        "reason": "concrete: for a falsified finding, the file and line that disproves it",
    },
    DisproveArtifact: {
        "verdicts": "one per finding you challenged; a finding left out survives",
    },
}

# Fields an example leaves at their default on purpose: showing a filled-in
# direction concern would invite one on every run.
EXAMPLE_LEAVES_EMPTY: dict[type, frozenset[str]] = {
    ScoutArtifact: frozenset({"direction_concern"}),
    HolisticArtifact: frozenset({"direction_concern"}),
}


def _nested_dataclass(hint) -> type | None:
    """The dataclass a list or tuple field holds, or None."""
    kind, args = classify(hint)
    if kind in (HintKind.TUPLE, HintKind.LIST) and args and dataclasses.is_dataclass(args[0]):
        return args[0]
    return None


def _enum_values(hint) -> str:
    if not (isinstance(hint, type) and issubclass(hint, Enum)):
        return ""
    return f" — one of {join_or([json.dumps(m.value) for m in hint])}"


# The fields only a re-review fills: what carries a prior finding forward. A
# first-round prompt neither shows nor explains them (D20), so an agent with
# no prior review in front of it is never invited to invent a sid or a decline.
# Each has a default, so a document without them is still a valid artifact.
CARRY_FIELDS: dict[type, frozenset[str]] = {
    AgentFinding: frozenset({"sid", "declined"}),
    GroupArtifact: frozenset({"prior_findings"}),
    ReviewArtifact: frozenset({"prior_findings"}),
}


def _shown_fields(kind: type, hidden: dict[type, frozenset[str]]) -> list:
    """`kind`'s fields a prompt shows, each with its type hint."""
    hints = get_type_hints(kind)
    skip = hidden.get(kind, frozenset())
    return [(f.name, hints[f.name]) for f in dataclasses.fields(kind) if f.name not in skip]


def _example_dict(kind: type, data: dict, hidden: dict[type, frozenset[str]]) -> dict:
    """`data` (a `to_dict` of a `kind`) without the fields `hidden` leaves out, at any depth."""
    out = {}
    for name, hint in _shown_fields(kind, hidden):
        nested = _nested_dataclass(hint)
        value = data[name]
        out[name] = [_example_dict(nested, item, hidden) for item in value] if nested else value
    return out


def _field_guide(kind: type, hidden: dict[type, frozenset[str]], prefix: str = "") -> list[str]:
    lines = []
    for name, hint in _shown_fields(kind, hidden):
        lines.append(f"- `{prefix}{name}`: {FIELD_NOTES[kind][name]}{_enum_values(hint)}")
        nested = _nested_dataclass(hint)
        if nested is not None:
            lines += _field_guide(nested, hidden, f"{prefix}{name}[].")
    return lines


def output_format(kind: type, mode: Mode, *, rereview: bool) -> str:
    """What a prompt's Output section shows: the example, then every field's meaning.

    `rereview` is whether the prompt carries prior findings to account for;
    without it the carry fields (`CARRY_FIELDS`) are neither shown nor explained.
    """
    hidden = {} if rereview else CARRY_FIELDS
    shown = _example_dict(kind, core.serde.to_dict(kind.example(mode)), hidden)
    return (
        "The file holds ONE JSON document — nothing before or after it, no code fence, "
        "no commentary — shaped like this example, with your own content in place of "
        "the example's. Write \"\" or [] where a field's note says so.\n\n"
        f"```json\n{json.dumps(shown, indent=2)}\n```\n\n"
        "What each field holds:\n" + "\n".join(_field_guide(kind, hidden))
    )
