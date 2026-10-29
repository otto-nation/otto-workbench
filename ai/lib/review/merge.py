"""What becomes of findings as the group reviews are folded into one document.

Two jobs over one vocabulary: merging the group reviews into a single
document, and giving each finding an identity that outlives the review
carrying it. They live together because they act on the same identity — the
path and description that decide whether two findings are duplicates are the
pair that hashes to a stable ID, and a stable ID is how a later reconciliation
recognises a finding a review restates.

Reading that identity off a finding's fields is `review.grammar`'s job, not
this module's: `FindingIdentity` answers both questions, so deduplication and
carry-forward cannot come to different conclusions about one record. Reading a
review is `review.document`'s job, checking findings against the tree is
`review.verify`'s, and the `Finding` every side holds is `review.types`' — a
consumer that only holds findings needs none of this. Deciding what became of
a finding the prior review reported is `review.reconcile`'s.

A `RecordFinding` already carries its fields. Deduplication walks those
records and drops a repeat by `FindingIdentity.dedup_key` within the same ID
prefix, so a duplicate is a record that never reaches the merged document
rather than a second copy of it.

Finding IDs (``M1``, ``S2``, ``N3``, ``I1``) are assigned mechanically and are
only meaningful inside the review that carries them. Agents write whatever IDs
they like; merging, deduplication, and evidence verification all remove
findings, and a final pass closes the gaps so each severity numbers from 1 with
no holes.

Only a finding on the record — a `RecordFinding` in `findings` — gets a number.
A mention of an ID inside a finding's body, summary, or other prose is a
reference, and references are rewritten through the same map, so a finding
that cites another one still cites the same one afterwards.

Every severity is renumbered over the whole set of sections rather than one
section at a time, because a reference need not sit in the section that
declares what it names. A Must-fix finding citing ``[S1]`` is renumbered by the
Should-fix map or by nothing at all; a pass confined to the Must-fix section
left that citation on whichever Should-fix finding later took the number.

Brackets are what make a reference unambiguous. A bare ``S3`` is also an object
store and a bare ``M1`` is also a laptop, so an unbracketed mention only counts
when a citing phrase introduces it — ``see S3``, ``duplicate of S3``, ``blocked
on S3``. Anything else is left as prose; ``_REFERENCE_CUES`` is the phrase list.

A reference to a finding that is no longer in the review becomes ``[removed]``.
Leaving the ID alone would be worse than useless: the number it names has since
been reassigned to a different finding, and a reader who follows it lands
somewhere unrelated with nothing to signal the misdirection. Deduplication is
the exception — a duplicate is merged rather than dropped, so references to it
move to the copy that survived.

Text that declares no findings of a given severity is left untouched, since
there is no map to rewrite through and every ID in it belongs to some other
document.

While the groups are still being merged the scope is one group, and there every
severity is answered for. A group numbers from ``[M1]`` independently of the
others and sees only its own files, so a reference can only mean the finding its
own group declared; one that names anything else becomes ``[removed]`` as the
group's IDs are shifted past the groups before it. Deferring that to the
merge-wide pass would misdirect it: group provenance is gone by then, and the
pooled map answers with whichever group happens to have declared that number.
"""

# doc-group: findings

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from review.grammar import (
    SEVERITY_KEY,
    DedupKey, FindingIdentity,
)
from review.record import LedgerLine, RecordFinding, ReviewRecord, SeveritySection
from review.types import SEVERITIES

# ── Renumbering ──────────────────────────────────────────────────────────────

# What a reference becomes when nothing declares the finding it names. It is
# deliberately not an ID: once the gaps close, the number that reference used to
# carry belongs to a different finding, and a reader who follows it lands on
# something unrelated without ever learning they were misdirected.
_REMOVED_REF = "[removed]"

# A bare `S3` is both a finding ID and an object store, and `M1` is both a
# finding ID and a laptop. So a mention without brackets only counts as a
# reference when a citing phrase introduces it — a review that says "stored in
# an S3 bucket" has to come out the other side still saying it.
# ceiling: fixed phrase list, extend it if reviews learn to cite some other way
_REFERENCE_CUES = (
    r"see(?:\s+also)?|cf\.?|per|once|duplicate of|related to|blocked on"
    r"|depends on|addressed by|superseded by"
)


# Every way a review names a finding: `[M1]`, and a cited bare `M1`. One pattern
# over every prefix rather than one per prefix, because a rewrite may retarget a
# reference onto a survivor of the same severity — and a pass per prefix would
# then hand what it just wrote to the next pass. Which prefixes those are is
# `review.grammar`'s `SEVERITY_KEY`, the same class every reader of a finding
# ID goes through.
_ID_REFERENCE_RE = re.compile(
    rf"\[({SEVERITY_KEY})(\d+)\]"
    rf"|(\b(?i:{_REFERENCE_CUES})\s+)({SEVERITY_KEY})(\d+)(?![\d\]])"
)


@dataclass(frozen=True)
class FindingId:
    """A finding ID as its two halves: the severity prefix and the number.

    They travel together because a number means nothing without its prefix, and
    the prefix is not implied by the section a line sits in — a review that
    files a Should-fix declaration under Must fix is malformed, but the ID it
    declared is still `S1`.
    """

    prefix: str
    number: int

    def __str__(self) -> str:
        return f"{self.prefix}{self.number}"


def _declared_order(findings, key: str) -> list[int]:
    """The IDs `findings` declare under `key`, in the order they first appear."""
    seen: list[int] = []
    for f in findings:
        if f.severity == key and f.seq not in seen:
            seen.append(f.seq)
    return seen


@dataclass(frozen=True)
class _Renumbering:
    """Where every finding ID moves, and which prefixes the map answers for.

    `prefixes` is not `moves`' own key set, because "nothing declares this
    prefix" means different things at different scopes. Inside one group it
    means every reference to that prefix is to a finding that is gone, since a
    group can only cite its own. Over the merged document it means the mentions
    belong to some other document — a prior review's ledger, a quoted log line
    — and have to come out exactly as they went in.
    """

    moves: dict[FindingId, FindingId]
    prefixes: frozenset[str]

    def rewrite(self, text: str) -> str:
        """Move every ID and every reference to one onto its new number."""
        return _ID_REFERENCE_RE.sub(self._rewrite_one, text)

    def _rewrite_one(self, m: re.Match[str]) -> str:
        bracketed, cue = m.group(1), m.group(3) or ""
        old = FindingId(bracketed or m.group(4), int(m.group(2) or m.group(5)))
        if old.prefix not in self.prefixes:
            return m.group(0)
        new = self.moves.get(old)
        if new is None:
            return f"{cue}{_REMOVED_REF}"
        return f"{cue}[{new}]" if bracketed else f"{cue}{new}"


def _renumbering(
    declared: dict[str, list[int]], merged_into: dict[FindingId, FindingId],
) -> _Renumbering:
    """Where each declared ID lands once the gaps between them close.

    Only the severities declared here: at merge-wide scope an undeclared one
    belongs to some other document, so `_Renumbering` has to leave it alone.
    A deduplicated finding was not dropped, it was merged: its references
    belong on the copy that survived, which says the same thing. The survivor
    is always declared here — it is the copy dedup kept — but guard anyway, so
    a map built from other text cannot quietly point a reference somewhere new.
    """
    moves: dict[FindingId, FindingId] = {}
    for severity in SEVERITIES:
        for new, old in enumerate(declared[severity.key], 1):
            moves[FindingId(severity.key, old)] = FindingId(severity.key, new)
    prefixes = {old.prefix for old in moves}
    for gone, survivor in merged_into.items():
        if survivor in moves:
            moves.setdefault(gone, moves[survivor])
            prefixes.add(gone.prefix)
    return _Renumbering(moves, frozenset(prefixes))


def _rewrite_record(record: ReviewRecord, renumbering: _Renumbering) -> ReviewRecord:
    """`record` with every visible finding moved and every reference rewritten.

    The prior-review ledger is held out: those IDs belong to the previous
    review, not this one, and must survive merging unchanged.
    """
    def moved(f: RecordFinding) -> RecordFinding:
        if not f.visible:
            return f
        new_id = renumbering.moves.get(FindingId(f.severity, f.seq))
        return replace(f, id=str(new_id) if new_id else f.id, body=renumbering.rewrite(f.body))
    return replace(
        record,
        findings=tuple(moved(f) for f in record.findings),
        summary=renumbering.rewrite(record.summary),
        verdict=renumbering.rewrite(record.verdict),
        severity_sections=tuple(
            replace(s, note=renumbering.rewrite(s.note)) for s in record.severity_sections
        ),
        extra=tuple(replace(e, body=renumbering.rewrite(e.body)) for e in record.extra),
        focus=tuple(replace(a, coverage=renumbering.rewrite(a.coverage)) for a in record.focus),
    )


def renumber_record(
    record: ReviewRecord, merged_into: dict[FindingId, FindingId] | None = None,
) -> ReviewRecord:
    """Close the gaps in every severity's visible IDs, taking every reference along."""
    declared = {
        severity.key: _declared_order(record.visible, severity.key)
        for severity in SEVERITIES
    }
    return _rewrite_record(record, _renumbering(declared, merged_into or {}))


def append_findings(
    base: ReviewRecord, added: Sequence[RecordFinding],
) -> ReviewRecord:
    """Append *added* with IDs above each severity's highest in *base*.

    `renumber_record` maps by old ID and cannot split two findings that share
    one, so newcomers are numbered first and the gap-closer runs after.
    """
    if not added:
        return base
    highest: dict[str, int] = {}
    for finding in base.findings:
        if not finding.visible:
            continue
        highest[finding.severity] = max(highest.get(finding.severity, 0), finding.seq)
    numbered = []
    for finding in added:
        next_seq = highest.get(finding.severity, 0) + 1
        highest[finding.severity] = next_seq
        numbered.append(replace(finding, id=f"{finding.severity}{next_seq}"))
    return renumber_record(replace(base, findings=base.findings + tuple(numbered)))


# ── Triage and deduplication ─────────────────────────────────────────────────

_EMPTY_SECTION_LINE_RE = re.compile(
    r"^(?:_none\._|_\(none\)_|_none in this file group\._|---)\s*$",
    re.IGNORECASE,
)


def _clean_section_text(text: str) -> str:
    lines = [line for line in text.split("\n") if not _EMPTY_SECTION_LINE_RE.match(line.strip())]
    return "\n".join(lines).strip()


def _dedup_ledger(lines: list[LedgerLine]) -> tuple[LedgerLine, ...]:
    """One line per prior finding — two groups can disposition the same one.

    When they disagree the stronger verdict wins, in `PriorDisposition`'s
    order: a finding is gone only when no group still sees it, and a declined
    one stays declined. The first group to reach a verdict keeps its slot and
    its wording when a later one only repeats it. Lines are matched on the ID
    alone, whatever path each group copied: the prior review numbers each
    finding once.
    """
    kept: dict[str, LedgerLine] = {}
    for line in lines:
        held = kept.get(line.id)
        if held is None or line.disposition.precedence > held.disposition.precedence:
            kept[line.id] = line
    return tuple(kept.values())


def _shift_record(group: ReviewRecord, offsets: dict[str, int]) -> ReviewRecord:
    """One group's IDs moved past the groups before it; foreign references become `[removed]`.

    Every severity is answered for, so a reference to one this group declares
    nothing of becomes `[removed]` here rather than being left for the
    merge-wide pass. Groups number from `[M1]` independently and each sees only
    its own files, so a reference can only mean a finding its own group
    declared — and this is the last pass that knows which group a line came
    from.
    """
    declared = {s.key: _declared_order(group.findings, s.key) for s in SEVERITIES}
    moves = {
        FindingId(key, old): FindingId(key, old + offsets[key])
        for key, ids in declared.items() for old in ids
    }
    shifted = _rewrite_record(group, _Renumbering(moves, frozenset(declared)))
    for key, ids in declared.items():
        offsets[key] += max(ids, default=0)
    return shifted


@dataclass(frozen=True)
class _DedupedRecords:
    """Every severity's findings with the repeats dropped, and where they went.

    `merged_into` maps each dropped ID to the copy that survived it, so the
    renumbering that follows can move references onto the survivor instead of
    marking them `[removed]`. Both halves are whole IDs rather than numbers.
    Dedup keys on `(severity, dedup_key)`, so a same-issue finding at another
    severity is kept and the survivor always carries the same prefix. It is
    kept apart from `findings` because the gaps close across every severity at
    once, after every finding has been deduped.
    """

    findings: tuple[RecordFinding, ...]
    merged_into: dict[FindingId, FindingId]


def _dedup_records(findings: list[RecordFinding]) -> _DedupedRecords:
    """Drop a finding another of its severity already states; remember where it went."""
    seen: dict[tuple[str, DedupKey], FindingId] = {}
    merged_into: dict[FindingId, FindingId] = {}
    kept: list[RecordFinding] = []
    for f in findings:
        # A finding with no file has no location for two groups to agree on.
        identity = FindingIdentity.of_fields(f.path, f.line, f.body) if f.path else None
        key = (f.severity, identity.dedup_key) if identity else None
        if key is not None and key in seen:
            merged_into[FindingId(f.severity, f.seq)] = seen[key]
            continue
        if key is not None:
            seen[key] = FindingId(f.severity, f.seq)
        kept.append(f)
    return _DedupedRecords(tuple(kept), merged_into)


def merge_records(groups: list[ReviewRecord]) -> ReviewRecord:
    """The group reviews folded into one record, in group order."""
    offsets = {s.key: 0 for s in SEVERITIES}
    findings: list[RecordFinding] = []
    triage: list = []
    ledgers: list[LedgerLine] = []
    notes: list[SeveritySection] = []
    for group in groups:
        shifted = _shift_record(group, offsets)
        triage += [e for e in (shifted.triage or ()) if e.path]
        ledgers += shifted.ledger
        notes += [
            replace(s, note=_clean_section_text(s.note)) for s in shifted.severity_sections
            if _clean_section_text(s.note)
        ]
        # `_None._` / `---` lines that landed in a finding body are stripped
        # the way the text merge stripped them from the section as a whole.
        findings += [replace(f, body=_clean_section_text(f.body)) for f in shifted.findings]
    by_severity = sorted(findings, key=lambda f: [s.key for s in SEVERITIES].index(f.severity))
    deduped = _dedup_records(by_severity)
    seen_paths: set[str] = set()
    unique_triage = []
    for entry in triage:
        key = entry.path or entry.note
        if key not in seen_paths:
            seen_paths.add(key)
            unique_triage.append(entry)
    merged = ReviewRecord(
        triage=tuple(unique_triage), findings=deduped.findings,
        severity_sections=tuple(notes),
        ledger=_dedup_ledger(ledgers),
    )
    return renumber_record(merged, deduped.merged_into)
