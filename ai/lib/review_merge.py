"""What becomes of findings as the group reviews are folded into one document.

Two jobs over one vocabulary: merging the group reviews into a single
document, and giving each finding an identity that outlives the review
carrying it. They live together because they act on the same identity — the
path and description that decide whether two findings are duplicates are the
pair that hashes to a stable ID, and a stable ID is how a later reconciliation
recognises a finding a review restates.

Reading that identity off a finding line is `review_grammar`'s job, not this
module's: `FindingIdentity` answers both questions, so deduplication and
carry-forward cannot come to different conclusions about one line. Reading a
review is `review_document`'s job, checking findings against the tree is
`review_verify`'s, and the `Finding` every side holds is `review_types`' — a
consumer that only holds findings needs none of this. Deciding what became of
a finding the prior review reported is `review_reconcile`'s.

Where a finding's body ends is `review_spans`'s. Deduplication and the
prior-review reading both walk `finding_spans`, and a repeat is removed with
`cut_spans`, so a duplicate takes exactly the lines out of the merged document
that a falsified finding does. Neither says where one stops.

Finding IDs (``M1``, ``S2``, ``N3``, ``I1``) are assigned mechanically and are
only meaningful inside the review that carries them. Agents write whatever IDs
they like; merging, deduplication, and evidence verification all remove
findings, and a final pass closes the gaps so each severity numbers from 1 with
no holes.

Only a *declaration* — a finding at the head of its own list item, ``- **[M1]**
…`` or ``- [ ] **[M1]** …`` — gets a number. Everything else that names an ID is
a reference, and references are rewritten through the same map, so a finding
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
from dataclasses import dataclass, field
from pathlib import Path

from review_document import (
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, ReviewDocument,
)
from review_grammar import (
    ANNOTATE_FINDING_RE, FINDING_ID_RE, SEVERITY_KEY, TRIAGE_LINE_RE,
    DedupKey, FindingIdentity, has_sid_marker, parse_ledger_line, sid_marker,
)
from review_spans import cut_spans, finding_spans
from review_types import (
    SEVERITIES, FindingRef, FindingSpan, LedgerEntry,
    disposition_precedence,
)

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
# over every prefix rather than one per prefix, because a rewrite may move a
# reference from one severity to another — a duplicate filed under the wrong one
# is merged into a survivor that carries the right one — and a pass per prefix
# would then hand what it just wrote to the next pass. Which prefixes those are
# is `review_grammar`'s `SEVERITY_KEY`, the same class every reader of a finding
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


def _declaration(line: str) -> FindingId | None:
    """The ID `line` declares, or None when it declares none.

    A finding declares its ID at the head of its own list item; every other
    occurrence is a reference to a declaration. Only declarations get numbers,
    because a reference can name a finding that evidence verification or
    deduplication has since taken out of the review.
    """
    m = FINDING_ID_RE.match(line.strip())
    return FindingId(m.group(2), int(m.group(3))) if m else None


def _declared_ids(text: str, prefix: str) -> list[int]:
    """The IDs `text` declares under `prefix`, in the order they first appear."""
    ids: list[int] = []
    for line in text.split("\n"):
        declared = _declaration(line)
        if declared and declared.prefix == prefix and declared.number not in ids:
            ids.append(declared.number)
    return ids


def _joined(sections: dict[str, str]) -> str:
    """The severity sections as one text, in the order the review prints them."""
    return "\n".join(sections[severity.key] for severity in SEVERITIES)


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


def _gap_renumbering(
    text: str, merged_into: dict[FindingId, FindingId],
) -> _Renumbering:
    """Where each ID `text` declares lands once the gaps between them close.

    Every severity is read over the whole text rather than section by section:
    a reference need not sit in the section that declares what it names, and a
    declaration filed under the wrong severity is still a declaration — a map
    that missed it would rewrite the declaration itself to `[removed]`.
    """
    moves: dict[FindingId, FindingId] = {}
    for severity in SEVERITIES:
        for new, old in enumerate(_declared_ids(text, severity.key), 1):
            moves[FindingId(severity.key, old)] = FindingId(severity.key, new)
    # Only the severities declared here: at merge-wide scope an undeclared one
    # belongs to some other document, so `_Renumbering` has to leave it alone.
    prefixes = {old.prefix for old in moves}
    # A deduplicated finding was not dropped, it was merged: its references
    # belong on the copy that survived, which says the same thing. The survivor
    # is always declared here — it is the copy dedup kept — but guard anyway, so
    # a map built from other text cannot quietly point a reference somewhere new.
    for gone, survivor in merged_into.items():
        if survivor in moves:
            moves.setdefault(gone, moves[survivor])
            prefixes.add(gone.prefix)
    return _Renumbering(moves, frozenset(prefixes))


def _close_gaps(
    sections: dict[str, str], merged_into: dict[FindingId, FindingId],
) -> dict[str, str]:
    """Close the gaps in every severity's IDs, taking every reference along.

    The map is built from all the sections at once and applied to all of them,
    so a reference reaching across severities lands on the finding it named
    rather than on whatever ended up at that number. `merged_into` says which
    IDs deduplication folded into which survivor.
    """
    renumbering = _gap_renumbering(_joined(sections), merged_into)
    return {key: renumbering.rewrite(text) for key, text in sections.items()}


def renumber_findings(text: str) -> str:
    """Close the gaps in every severity's IDs, taking every reference along.

    References are rewritten through the same map as the declarations, so a
    finding that cites another one still cites the same one afterwards, and one
    that names a finding nothing declares any more becomes `[removed]`.
    """
    return _gap_renumbering(text, {}).rewrite(text)


# ── Triage and deduplication ─────────────────────────────────────────────────

_EMPTY_SECTION_LINE_RE = re.compile(
    r"^(?:_none\._|_\(none\)_|_none in this file group\._|---)\s*$",
    re.IGNORECASE,
)


def _clean_section_text(text: str) -> str:
    lines = [line for line in text.split("\n") if not _EMPTY_SECTION_LINE_RE.match(line.strip())]
    return "\n".join(lines).strip()


def _clean_triage(text: str) -> str:
    return "\n".join(
        line for line in text.split("\n")
        if TRIAGE_LINE_RE.match(line)
    )


def _record_merge(
    merged_into: dict[FindingId, FindingId],
    survivor: FindingId | None,
    dup: FindingId | None,
) -> None:
    """Remember that `dup`'s references should end up on `survivor`."""
    if survivor is None or dup is None:
        return
    merged_into[dup] = survivor


@dataclass(frozen=True)
class _Deduped:
    """One severity's findings with the repeats dropped, and where they went.

    `merged_into` maps each dropped ID to the copy that survived it, so the
    renumbering that follows can move references onto the survivor instead of
    marking them `[removed]`. Both halves are whole IDs rather than numbers,
    because a duplicate filed under the wrong severity still declares one and
    the copy that survives it need not carry the same prefix. It is kept apart
    from `text` because the gaps close across every severity at once, after
    every section has been deduped.
    """

    text: str
    merged_into: dict[FindingId, FindingId] = field(default_factory=dict)


def _dedup_findings(text: str) -> _Deduped:
    """One severity's text with the repeats removed, and where each one went.

    `FindingIdentity` chooses which declarations are candidates; how much of
    the text a dropped one takes with it is `finding_spans`', so a repeat and a
    finding the disprove gate falsifies lose exactly the same lines.
    """
    seen: dict[DedupKey, FindingId | None] = {}
    merged_into: dict[FindingId, FindingId] = {}
    repeats: list[FindingSpan] = []
    for span in finding_spans(text):
        identity = FindingIdentity.of(span.line)
        if identity is None:
            continue
        key = identity.dedup_key
        if key in seen:
            repeats.append(span)
            _record_merge(merged_into, seen[key], _declaration(span.line))
            continue
        seen[key] = _declaration(span.line)
    return _Deduped(cut_spans(text, repeats), merged_into)


def _dedup_sections(sections: dict[str, str]) -> dict[str, str]:
    """Drop each severity's repeated findings, then close the gaps they leave.

    Two findings are duplicates only within one severity — the same problem
    reported as a Must-fix by one group and a Nit by another is a disagreement,
    not a repeat — but the numbering closes over all four sections together, so
    a reference from one severity to another survives the drop.
    """
    deduped = {
        severity.key: _dedup_findings(sections[severity.key])
        for severity in SEVERITIES
    }
    merged_into: dict[FindingId, FindingId] = {}
    for entry in deduped.values():
        merged_into.update(entry.merged_into)
    return _close_gaps(
        {key: entry.text for key, entry in deduped.items()}, merged_into,
    )


def _dedup_triage(triage: str) -> str:
    seen: set[str] = set()
    lines = []
    for line in triage.split("\n"):
        m = TRIAGE_LINE_RE.match(line)
        key = m.group(1) if m else line
        if key not in seen:
            seen.add(key)
            lines.append(line)
    return "\n".join(lines)


def _dedup_ledger(ledger: str) -> str:
    """Collapse repeats — two groups can disposition the same prior finding.

    When two groups disagree about one finding, the strongest verdict wins:
    a finding is gone only when no group reports it, and a declined one stays
    declined however many groups still see the code it describes.
    """
    slot: dict[FindingRef, int] = {}
    seen_prose: set[str] = set()
    lines: list[str] = []
    for line in ledger.split("\n"):
        entry = parse_ledger_line(line)
        if entry and entry.ref in slot:
            _keep_strongest_disposition(lines, slot[entry.ref], entry)
            continue
        if not entry and line.strip() in seen_prose:
            continue
        if entry:
            slot[entry.ref] = len(lines)
        else:
            seen_prose.add(line.strip())
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _keep_strongest_disposition(
    lines: list[str], index: int, entry: LedgerEntry,
) -> None:
    """Let a stronger verdict overwrite the one already kept for its finding.

    Strictly stronger, so the first group to reach a verdict keeps the wording
    when a later one only repeats it. The ordering is `PriorDisposition`'s —
    a still-open verdict cannot reopen a finding another group declined.
    """
    kept = parse_ledger_line(lines[index])
    kept_rank = disposition_precedence(kept.disposition if kept else None)
    if disposition_precedence(entry.disposition) > kept_rank:
        lines[index] = entry.text


@dataclass
class _Merge:
    """The one document the group reviews are being folded into.

    Severity text is held per severity because each gets its own heading in the
    output, but renumbering spans all of them at once: a finding may cite one of
    another severity, and a pass that read each section alone left that citation
    on whatever number the other groups had put there.

    `offsets` is the highest ID each severity is already using, and a group's
    IDs are shifted clear of it. Counting the findings before them instead would
    under-shift any group whose agent skipped a number — nothing closes those
    gaps before the merge — and two groups would land on the same ID.
    """

    sections: dict[str, str] = field(
        default_factory=lambda: {s.key: "" for s in SEVERITIES})
    offsets: dict[str, int] = field(
        default_factory=lambda: {s.key: 0 for s in SEVERITIES})
    triage: str = ""
    ledger: str = ""

    def add(self, content: str) -> None:
        """Fold one group review in, its IDs shifted past the groups before it."""
        doc = ReviewDocument.parse(content)
        triage = _clean_triage(doc.section(SECTION_FILE_TRIAGE))
        if triage:
            self.triage += triage + "\n"
        # Kept out of the renumbering below: these IDs belong to the prior
        # review, and each group only dispositions the prior findings for its
        # own files, so the merged ledger is the union of what every group
        # accounted for.
        ledger = _clean_section_text(doc.section(SECTION_PRIOR_FINDINGS))
        if ledger:
            self.ledger += ledger + "\n"
        group = {
            s.key: _clean_section_text(doc.section(s.section)) for s in SEVERITIES
        }
        for key, text in self._shift(group).items():
            if text:
                self.sections[key] += text + "\n"

    def _shift(self, group: dict[str, str]) -> dict[str, str]:
        """One group's sections with every ID moved past what is already in use.

        Every severity is answered for, so a reference to one this group
        declares nothing of becomes `[removed]` here rather than being left for
        `_close_gaps`. Groups are told to number from `[M1]`, `[S1]`, `[N1]`,
        `[I1]` independently and each sees only its own files, so a reference
        can only mean a finding its own group declared — and this is the last
        pass that knows which group a line came from. Left alone, such a
        reference would resolve through the pooled map onto a real finding of
        another group's, in a different file about a different problem.
        """
        joined = _joined(group)
        declared = {
            severity.key: _declared_ids(joined, severity.key)
            for severity in SEVERITIES
        }
        moves = {
            FindingId(key, old): FindingId(key, old + self.offsets[key])
            for key, ids in declared.items() for old in ids
        }
        # Every severity, declared here or not: at group scope an undeclared one
        # is a reference to nothing, which `_Renumbering` marks.
        renumbering = _Renumbering(moves, frozenset(declared))
        shifted = {key: renumbering.rewrite(text) for key, text in group.items()}
        for key, ids in declared.items():
            self.offsets[key] += max(ids, default=0)
        return shifted

    def document(self) -> str:
        """The merged review: the triage, each severity, then the union ledger."""
        sections = _dedup_sections(self.sections)
        parts = [f"## {SECTION_FILE_TRIAGE}\n{_dedup_triage(self.triage)}"]
        for severity in SEVERITIES:
            if sections[severity.key]:
                parts.append(f"## {severity.section}\n{sections[severity.key]}")
        if self.ledger:
            parts.append(
                f"## {SECTION_PRIOR_FINDINGS}\n{_dedup_ledger(self.ledger)}"
            )
        return "\n".join(parts)


def merge_reviews(group_files: list[str]) -> str:
    merge = _Merge()
    for path in group_files:
        p = Path(path)
        if p.exists():
            merge.add(p.read_text())
    return merge.document()


# ── Stable IDs for carry-forward ─────────────────────────────────────────────

def _annotate_finding_line(line: str, m: re.Match) -> str:
    identity = FindingIdentity.of(line)
    if identity is None:
        return line
    return f"{m.group(1)}{sid_marker(identity.stable_id)} {line[m.end():]}"


def annotate_prior_with_stable_ids(review_text: str) -> str:
    lines = review_text.split("\n")
    result: list[str] = []
    for line in lines:
        m = ANNOTATE_FINDING_RE.match(line)
        if m and not has_sid_marker(line):
            result.append(_annotate_finding_line(line, m))
        else:
            result.append(line)
    return "\n".join(result)
