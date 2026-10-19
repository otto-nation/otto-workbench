"""The review record — `review.json`, the one source of truth for a review.

A review used to be a markdown file that agents wrote and a dozen code paths
edited in place. It is now this frozen tree, written atomically beside
`meta.json`; `review.md` is rendered from it by `review.render` and never read
back. Header facts — SHAs, mode, status, dates, generator — are `meta.json`'s
(`ReviewMeta`); this record holds what the review *says*.

Each finding carries its `Disposition`, which replaces the four partial ledgers
that used to disagree: the checkbox, the `*(declined — …)*` and `*(skipped — …)*`
annotations, and `ReviewMeta.open_findings`. `view()` hands the read API the
`review.types.Finding` it always handed out, so readers did not change.

Some fields carry today's markdown verbatim — `lead`, `verdict`, `ledger`,
`extra`, `severity_sections` — so the cut-over renders what the markdown
pipeline wrote. They go when agents write JSON and the verdict is derived.
"""

# doc-group: findings

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import core.log
import core.serde
from review.paths import FILENAME_RECORD
from review.types import SEVERITIES, Finding

SCHEMA_VERSION = 1


class Disposition(StrEnum):
    """What became of a finding. Values are on disk; keep them stable."""

    OPEN = "open"
    FIXED = "fixed"
    DECLINED = "declined"
    # The fix pass handed it to a person (`FixOutcome.NEEDS_HUMAN`).
    SKIPPED = "skipped"
    # The fix pass never reached it (`FixOutcome.DEFERRED`). Still open, and
    # written as nothing on the page — the line reads as it did.
    UNREACHED = "unreached"
    # The disprove gate refuted it. Kept, never rendered.
    FALSIFIED = "falsified"


class FocusSource(StrEnum):
    USER = "user"
    DERIVED = "derived"


def defuse(text: str) -> str:
    """`text` with every `*(` broken, so no annotation reader can find one in it."""
    return text.replace("*(", "* (")


def defuse_first_line(body: str) -> str:
    """`body` with its first line defused and rstripped — the line an annotation joins."""
    first, sep, rest = body.partition("\n")
    return defuse(first.rstrip()) + sep + rest


@dataclass(frozen=True)
class RecordFinding:
    """One finding. `lead` is the declaration text between the ID tag and the
    body — the location as written and its separator (`**`a.py:3`** — `);
    `body` is everything after it, continuation lines included, annotations
    excluded; `evidence` is the quoted code, never rendered."""

    id: str
    severity: str
    path: str = ""
    line: int | None = None
    end_line: int | None = None
    lead: str = ""
    body: str = ""
    evidence: str = ""
    stable_id: str = ""
    # The `<!-- sid: -->` a carried-forward finding arrived with, which
    # reconciliation recognises it by. Cleared when the review is finished.
    sid: str = ""
    disposition: Disposition = Disposition.OPEN
    # Why it was declined, skipped or unreached; on an unverified fix, the gate's detail.
    reason: str = ""
    unverified: bool = False
    # Whether the declaration carries a box. A PR-mode finding has none, and a
    # fix pass cannot tick what is not there — it stays open, as it always has.
    checkbox: bool = False
    # The tracker issue `--track` filed it under. PR 3 replaces this with
    # `deferred(IssueRef)`.
    filed: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", Disposition(self.disposition))

    @property
    def seq(self) -> int:
        return int(self.id[1:])

    @property
    def visible(self) -> bool:
        return self.disposition is not Disposition.FALSIFIED

    @property
    def annotation(self) -> str:
        """The `*(…)*` the rendered line ends with, or "" when it ends with none."""
        if self.disposition is Disposition.FIXED and self.unverified:
            word = "unverified"
        elif self.disposition is Disposition.DECLINED:
            word = "declined"
        elif self.disposition is Disposition.SKIPPED:
            word = "skipped"
        else:
            return ""
        return f"*({word} — {self.reason})*" if self.reason else f"*({word})*"

    def view(self) -> Finding:
        """The mutable `Finding` the read API has always returned.

        Its `body` is what the markdown parser produced: the first line with its
        annotation, then the non-blank continuation lines.
        """
        first, _, rest = self.body.partition("\n")
        note = self.annotation
        head = f"{first.rstrip()} {note}".strip() if note else first
        cont = [line.rstrip() for line in rest.split("\n") if line.strip()] if rest else []
        return Finding(
            id=self.id, severity=self.severity, seq=self.seq, path=self.path,
            line=self.line, end_line=self.end_line,
            body="\n".join([head, *cont]).strip(), stable_id=self.stable_id,
            checked=self.disposition is Disposition.FIXED,
            declined=self.disposition is Disposition.DECLINED,
            decline_reason=self.reason if self.disposition is Disposition.DECLINED else "",
        )


@dataclass(frozen=True)
class Question:
    id: str
    path: str | None = None
    line: int | None = None
    body: str = ""


@dataclass(frozen=True)
class VerifiedNote:
    path: str | None = None
    line: int | None = None
    body: str = ""


@dataclass(frozen=True)
class FocusArea:
    id: str
    title: str = ""
    rationale: str = ""
    source: FocusSource = FocusSource.DERIVED
    coverage: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", FocusSource(self.source))


@dataclass(frozen=True)
class TriageEntry:
    """One File Triage line, verbatim; `path` is "" for a line naming none."""

    path: str = ""
    text: str = ""


@dataclass(frozen=True)
class AgentFailure:
    agent: str
    reason: str
    status: str
    recoverable: bool = False


@dataclass(frozen=True)
class SeveritySection:
    """A severity heading the review wrote, and any text under it that is not a finding."""

    severity: str
    note: str = ""


@dataclass(frozen=True)
class ProseSection:
    heading: str
    body: str = ""


@dataclass(frozen=True)
class ReviewRecord:
    schema_version: int = SCHEMA_VERSION
    summary: str = ""
    approach: RecordFinding | None = None
    findings: tuple[RecordFinding, ...] = ()
    questions: tuple[Question, ...] = ()
    verified: tuple[VerifiedNote, ...] = ()
    focus: tuple[FocusArea, ...] = ()
    # None: no File Triage section. (): the heading with nothing under it.
    triage: tuple[TriageEntry, ...] | None = None
    static_analysis: str = ""
    failures: tuple[AgentFailure, ...] = ()
    # PR 0 carriers of today's markdown; see the module docstring.
    verdict: str = ""
    severity_sections: tuple[SeveritySection, ...] = ()
    ledger: str = ""
    extra: tuple[ProseSection, ...] = ()

    @property
    def visible(self) -> tuple[RecordFinding, ...]:
        return tuple(f for f in self.findings if f.visible)

    @property
    def open_counts(self) -> dict[str, int]:
        """Visible findings not yet fixed, by severity key, every key present."""
        tally = Counter(f.severity for f in self.visible if f.disposition is not Disposition.FIXED)
        return {s.key: tally[s.key] for s in SEVERITIES}

    def finding(self, finding_id: str) -> RecordFinding | None:
        return next((f for f in self.visible if f.id == finding_id), None)

    def with_finding(self, finding_id: str, **changes) -> ReviewRecord:
        """This record with the first visible finding `finding_id` changed."""
        done = False
        out = []
        for f in self.findings:
            if not done and f.visible and f.id == finding_id:
                f, done = replace(f, **changes), True
            out.append(f)
        return replace(self, findings=tuple(out))


def _field_dropped(written, loaded, hint) -> bool:
    """True when serde dropped or shortened this on-disk value."""
    args = get_args(hint)
    inner, optional = hint, False
    if type(None) in args:
        non_none = tuple(a for a in args if a is not type(None))
        if len(non_none) == 1:
            inner, optional = non_none[0], True
    if get_origin(inner) is not tuple:
        return (
            optional
            and is_dataclass(inner)
            and written is not None
            and not isinstance(written, dict)
        )
    if isinstance(written, list):
        return not (isinstance(loaded, tuple) and len(loaded) == len(written))
    # to_dict never writes null for a required tuple; an Optional
    # tuple (triage) is written as null when the section is absent.
    return not (written is None and optional)


def _dropped_field(raw: dict, record: ReviewRecord) -> str | None:
    """Name of a present-on-disk field serde dropped or shortened, if any."""
    for name, hint in get_type_hints(ReviewRecord).items():
        if name not in raw:
            continue
        if _field_dropped(raw[name], getattr(record, name), hint):
            return name
    return None


def load_record(review_dir: Path, filename: str = FILENAME_RECORD) -> ReviewRecord | None:
    """The record in `review_dir`, or None — absent, unreadable, another schema,
    or a field serde could not keep.

    None is the stale answer every reader treats as "no review here".
    """
    path = review_dir / filename
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
        version = raw.get("schema_version")
    except (OSError, ValueError, AttributeError):
        raw, version = None, None
    if version != SCHEMA_VERSION:
        core.log.warn(f"{path} is schema {version!r}, not {SCHEMA_VERSION} — treating the review as stale")
        return None
    try:
        record = core.serde.from_dict(ReviewRecord, raw)
    except (TypeError, ValueError):
        core.log.warn(f"{path} is unreadable — discarding it")
        return None
    dropped = _dropped_field(raw, record)
    if dropped is not None:
        core.log.warn(f"{path} dropped {dropped} on load — treating the review as stale")
        return None
    return record


def save_record(review_dir: Path, record: ReviewRecord, filename: str = FILENAME_RECORD) -> None:
    """Write `record` atomically; a reader sees the old record or the new one."""
    core.serde.write_json(review_dir / filename, core.serde.to_dict(record))
