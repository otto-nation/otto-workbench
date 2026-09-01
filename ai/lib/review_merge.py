"""What becomes of findings across the reviews that report them.

Three jobs over one vocabulary: merging the group reviews into a single
document, giving each finding an identity that outlives the review carrying
it, and reconciling what the previous review reported against what this one
did. They live together because they act on the same identity — the path and
description that decide whether two findings are duplicates are the pair that
hashes to a stable ID, and a stable ID is how reconciliation recognises a
finding a later review restates.

Reading that identity off a finding line is `review_grammar`'s job, not this
module's: `FindingIdentity` answers both questions, so deduplication and
carry-forward cannot come to different conclusions about one line. Reading a
review is `review_document`'s job, checking findings against the tree is
`review_verify`'s, and the `Finding` every side holds is `review_types`' — a
consumer that only holds findings needs none of this.

Where a finding's body ends is `review_document`'s too. Deduplication and the
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

Reconciliation is the cross-review half. A re-review ends with a `## Prior
findings` ledger: one line per finding the previous review reported, saying
whether the change fixed it, left it open, or declined it. The ledger is
bookkeeping — it is stripped before the review is published — and it is written
by the agent that has just spent its attention on the review itself, so it is
the first thing to come up short.

Coming up short used to mean a line on stderr and nothing else. `reconcile`
replaces that with a disposition for every prior finding and a record of them
that outlives the run. A finding the ledger passes over is not automatically
unaccounted for: whether the file it names is still in the tree, and whether
the code it quotes is still in that file, are questions the worktree answers
without asking an agent anything. Only what neither the review nor the tree
settles is reported as undecided, and every record says which of the two
settled it, so an inference is never read back as a statement.

Reporting is not the only outcome. `passed_over` asks the same question one
phase early, against the merged group findings, and hands back the findings
neither the groups nor the tree settled — while there is still an agent that
can decide them, rather than after the document is written and the only thing
left to do is warn.

The record is a sidecar in the review directory rather than a section of the
review. Reconciliation parses its input for finding-shaped lines, so a
reconciliation written into the review would come back to the next round
looking like a fresh set of prior findings.

The ledger is not the only account of what became of a prior finding. Every
finding posted inline opened a review thread, and what the author did with that
thread — answered it, argued with it, resolved it — is the other one.
`fetch_reply_threads` classifies those threads into `ReplyState` and matches
each back to the finding ID its root comment declared, so a re-review reads both
accounts of the same set of findings.
"""

# doc-group: findings

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import git_client
import log
import serde
from pr_comments import _is_acknowledgment, _is_pushback, fetch_threads
from review_dedup import _get_bot_login
from review_document import (
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, ReviewDocument, ReviewHeader,
    cut_spans, finding_spans,
)
from review_grammar import (
    ANNOTATE_FINDING_RE, BOLD_FINDING_ID_RE, FINDING_ID_RE, TRIAGE_LINE_RE,
    DedupKey, FindingIdentity, parse_finding_line,
)
from review_github import PRData
from review_paths import FILENAME_PRIOR_FINDINGS, review_artifact_path
from review_types import (
    DISPOSITION_TAIL_PUNCTUATION, SEVERITIES, FindingRef, FindingSpan,
    LedgerEntry, PriorDisposition, PriorFinding, ReplyState,
    disposition_precedence,
)
from text import plural


def _parse_ledger_line(raw: str) -> LedgerEntry | None:
    """The entry a ledger line carries, or None when it names no finding."""
    parsed = parse_finding_line(raw.strip())
    if not parsed:
        return None
    return LedgerEntry(
        ref=FindingRef(parsed.id, parsed.path),
        disposition=PriorDisposition.parse(parsed.body),
        text=raw,
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


_ID_PREFIXES = "".join(severity.key for severity in SEVERITIES)

# Every way a review names a finding: `[M1]`, and a cited bare `M1`. One pattern
# over every prefix rather than one per prefix, because a rewrite may move a
# reference from one severity to another — a duplicate filed under the wrong one
# is merged into a survivor that carries the right one — and a pass per prefix
# would then hand what it just wrote to the next pass.
_ID_REFERENCE_RE = re.compile(
    rf"\[([{_ID_PREFIXES}])(\d+)\]"
    rf"|(\b(?i:{_REFERENCE_CUES})\s+)([{_ID_PREFIXES}])(\d+)(?![\d\]])"
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
        entry = _parse_ledger_line(line)
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
    kept = _parse_ledger_line(lines[index])
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
    return f"{m.group(1)} <!-- sid:{identity.stable_id} --> {line[m.end():]}"


def annotate_prior_with_stable_ids(review_text: str) -> str:
    lines = review_text.split("\n")
    result: list[str] = []
    for line in lines:
        m = ANNOTATE_FINDING_RE.match(line)
        if m and "<!-- sid:" not in line:
            result.append(_annotate_finding_line(line, m))
        else:
            result.append(line)
    return "\n".join(result)


def strip_stable_ids(text: str) -> str:
    return re.sub(r" <!-- sid:\w+ -->", "", text)


_SID_MARKER_RE = re.compile(r"<!-- sid:(\w+) -->")


def _stable_ids(text: str) -> set[str]:
    """Every prior-finding identity the text carries.

    Both the markers in carried-forward text and the ID each finding line
    hashes to on its own — a synthesis agent that retypes a carried finding
    drops the marker but keeps the path and the wording, so the recomputed ID
    is the part that survives it.
    """
    ids = set(_SID_MARKER_RE.findall(text))
    for raw in text.split("\n"):
        identity = FindingIdentity.of(raw.strip())
        if identity:
            ids.add(identity.stable_id)
    return ids


# ── Reading the prior review ─────────────────────────────────────────────────

def _parse_ledger(review_text: str) -> list[LedgerEntry]:
    """The entries of the review's prior-findings ledger, in order."""
    section = ReviewDocument.parse(review_text).section(SECTION_PRIOR_FINDINGS)
    entries = (_parse_ledger_line(raw) for raw in section.split("\n"))
    return [entry for entry in entries if entry]


def _prior_finding(span: FindingSpan, prior_text: str) -> PriorFinding:
    """One prior finding, read from its own line down to whatever ends it."""
    line = span.line
    identity = FindingIdentity.of(line)
    label_m = BOLD_FINDING_ID_RE.search(line)
    body = [raw.strip() for raw in span.text_of(prior_text).split("\n")]
    return PriorFinding(
        ref=FindingRef(
            label_m.group(1) if label_m else "",
            identity.path if identity else "",
        ),
        stable_id=identity.stable_id if identity else "",
        text="\n".join(body).strip(),
    )


def _parse_prior_findings(prior_text: str) -> list[PriorFinding]:
    """Every finding the prior review reported, each with the text reporting it.

    `ANNOTATE_FINDING_RE` chooses which declarations count — the shape the
    stable-ID annotator writes, which is what carry-forward matches on — and
    `finding_spans` says how far down each one's text runs.
    """
    return [
        _prior_finding(span, prior_text)
        for span in finding_spans(prior_text)
        if ANNOTATE_FINDING_RE.match(span.line)
    ]


# ── Reconciliation against the prior review ──────────────────────────────────

# The bucket a finding lands in when nothing settled it. Not a
# `PriorDisposition` member: those are verdicts a review may state, and "no
# verdict" is the absence of one rather than a fourth thing to state.
UNDECIDED_LABEL = "Undecided"

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")

# Below this a backtick span is a token rather than a quotation — `ok`, `nil`,
# a flag name — and it leaves a file for reasons that have nothing to do with
# the finding that mentioned it.
_MIN_QUOTE_CHARS = 12

# How much of a quoted span a basis line reproduces before it stops being a
# log line and starts being the code.
_BASIS_QUOTE_CHARS = 60


class DispositionSource(StrEnum):
    """What settled a prior finding's disposition.

    Recorded next to the disposition rather than folded into it: a verdict the
    pipeline worked out from the tree carries less authority than one the
    review stated, and the two must never be read as the same claim.
    """

    LEDGER = "ledger"
    CARRIED = "carried"
    TREE = "tree"
    NONE = "none"

    @property
    def stated(self) -> bool:
        """Whether the review said this, rather than the pipeline inferring it."""
        return self in (DispositionSource.LEDGER, DispositionSource.CARRIED)


class UndecidedReason(StrEnum):
    """Why a finding reached no verdict.

    Undecided is only a useful warning while it means "the review passed this
    by". Three of the four members below are something else — two of them a
    line this pipeline could not read, one a check it was in no position to run
    — and lumping them in with the omissions is what turns the warning into
    noise the reader learns to skip.

    Declaration order is the order `_report` prints the groups: the ones a
    reader can act on come first.
    """

    UNREADABLE_VERDICT = "unreadable verdict"
    NO_LOCATION = "no location parsed"
    NOT_MENTIONED = "not accounted for"
    NOT_CHECKABLE = "not checkable"

    @property
    def heading(self) -> str:
        """How the group reads above the findings in it."""
        return _UNDECIDED_HEADINGS[self]


_UNDECIDED_HEADINGS = {
    UndecidedReason.UNREADABLE_VERDICT: "the ledger names these but states no verdict this could read",
    UndecidedReason.NO_LOCATION: "these name no file this could read, so nothing could be checked",
    UndecidedReason.NOT_MENTIONED: "neither this review nor the tree accounts for these",
    UndecidedReason.NOT_CHECKABLE: "there was nothing to check these against",
}


@dataclass(frozen=True)
class PriorRecord:
    """One prior finding, what became of it, and how that was settled.

    `disposition` is None when nothing settled it, and `reason` then says which
    kind of nothing — the ledger naming the finding without a verdict this can
    read is a different failure from the review never mentioning it, and only
    the second is the review's.
    """

    ref: FindingRef = field(default_factory=FindingRef)
    disposition: PriorDisposition | None = None
    source: DispositionSource = DispositionSource.NONE
    basis: str = ""
    reason: UndecidedReason | None = None

    @property
    def decided(self) -> bool:
        """Whether this finding reached a verdict."""
        return self.disposition is not None


@dataclass
class Reconciliation:
    """Every finding of the prior review, and what this review made of each."""

    prior_sha: str = ""
    prior_date: str = ""
    head_sha: str = ""
    records: list[PriorRecord] = field(default_factory=list)

    @property
    def undecided(self) -> list[PriorRecord]:
        """The findings neither the review nor the tree accounted for."""
        return [record for record in self.records if not record.decided]

    @property
    def unaccounted(self) -> list[str]:
        """How the undecided findings read in a log line."""
        return [record.ref.label for record in self.undecided]

    @property
    def undecided_groups(self) -> list[tuple[UndecidedReason, list[PriorRecord]]]:
        """The undecided findings by why they are, in the order they are reported."""
        return [
            (reason, [record for record in self.undecided if record.reason is reason])
            for reason in UndecidedReason
            if any(record.reason is reason for record in self.undecided)
        ]

    @property
    def inferred(self) -> int:
        """How many verdicts came from the tree rather than from the review."""
        return sum(1 for r in self.records if r.source is DispositionSource.TREE)

    @property
    def counts(self) -> dict[str, int]:
        """How many findings reached each verdict, the undecided among them."""
        tally = {d.value: 0 for d in PriorDisposition}
        tally[UNDECIDED_LABEL] = 0
        for record in self.records:
            key = record.disposition.value if record.decided else UNDECIDED_LABEL
            tally[key] += 1
        return tally

    @property
    def tally(self) -> str:
        """The counts as a log line, with the empty buckets left out."""
        parts = [f"{n} {label}" for label, n in self.counts.items() if n]
        if self.inferred:
            parts.append(f"{self.inferred} inferred from the tree")
        return ", ".join(parts)

    @property
    def range_label(self) -> str:
        """The pair of trees the reconciliation compared, as far as it knows them."""
        ends = [git_client.abbrev(sha) for sha in (self.prior_sha, self.head_sha) if sha]
        return " → ".join(ends) if len(ends) == 2 else (ends[0] if ends else "an unnamed commit")


@dataclass(frozen=True)
class _Inference:
    """What the tree says about a prior finding, and why it says it.

    `reason` is what the finding falls to when the tree settles nothing, and
    defaults to the review's own omission because that is what most of the
    tree's silences mean: it looked, and found the finding's subject intact.
    """

    disposition: PriorDisposition | None = None
    basis: str = ""
    reason: UndecidedReason = UndecidedReason.NOT_MENTIONED


@dataclass
class _Tree:
    """The two versions of the worktree a reconciliation compares.

    Blob reads are cached because findings cluster in files — a review reports
    four things about one module, and re-reading its prior text once per
    finding is four `git show` calls for one answer.
    """

    wt_path: str = ""
    prior_sha: str = ""
    _before: dict[str, str] = field(default_factory=dict)

    def infer(self, finding: PriorFinding) -> _Inference:
        """What the tree makes of `finding`, or why it makes nothing of it."""
        path = finding.ref.path
        if not self.wt_path:
            return _Inference(
                basis="there was no worktree to check it against",
                reason=UndecidedReason.NOT_CHECKABLE,
            )
        if not path:
            return _Inference(
                basis="it names no file",
                reason=UndecidedReason.NO_LOCATION,
            )
        if not self.prior_sha:
            return _Inference(
                basis="the prior review names no commit to compare against",
                reason=UndecidedReason.NOT_CHECKABLE,
            )
        if not (Path(self.wt_path) / path).is_file():
            return self._absent(path)
        return self._quotes(finding, path)

    def _absent(self, path: str) -> _Inference:
        """The verdict on a finding whose file is not in the current tree.

        A file the prior commit did not hold either was never this finding's
        subject — the path was misread — so its absence now settles nothing.
        """
        if not self._existed(path):
            return _Inference(basis=f"`{path}` is in neither tree")
        return _Inference(PriorDisposition.FIXED, f"`{path}` is no longer in the tree")

    def _existed(self, path: str) -> bool:
        return git_client.ok("cat-file", "-e", f"{self.prior_sha}:{path}", cwd=self.wt_path)

    def _before_text(self, path: str) -> str:
        if path not in self._before:
            blob = git_client.out("show", f"{self.prior_sha}:{path}", cwd=self.wt_path)
            self._before[path] = _norm(blob)
        return self._before[path]

    def _quoted(self, finding: PriorFinding, path: str) -> list[str]:
        """The spans the finding quotes that its file really held at `prior_sha`.

        Quoting is how a review points at code, but a review also quotes what
        it is merely naming: a symbol defined elsewhere, a phrase from the
        prompt, a paraphrase of what the code ought to say instead. Requiring
        the span to reproduce in the file's own prior text drops all three, and
        leaves the spans whose disappearance means something happened here.
        """
        before = self._before_text(path)
        spans = dict.fromkeys(m.group(1) for m in _CODE_SPAN_RE.finditer(finding.text))
        return [
            span for span in spans
            if len(span) >= _MIN_QUOTE_CHARS
            and not span.startswith(path)
            and _norm(span) in before
        ]

    def _quotes(self, finding: PriorFinding, path: str) -> _Inference:
        """The verdict on a finding by whether the code it quoted survived."""
        quoted = self._quoted(finding, path)
        if not quoted:
            return _Inference(
                basis=f"nothing it quotes was in `{path}` at {git_client.abbrev(self.prior_sha)}")
        after = _norm((Path(self.wt_path) / path).read_text(errors="replace"))
        gone = [span for span in quoted if _norm(span) not in after]
        if not gone:
            return _Inference(basis=f"the code it quotes is still in `{path}`")
        # ceiling: one vanished quotation is read as the finding's subject
        # having gone, which a large enough unrelated edit to the same file can
        # fake. The record names the span that decided it, so a wrong call is
        # auditable rather than silent — upgrade to matching the span against
        # the diff hunks if a review is ever recorded as fixed on a span the
        # change only moved.
        basis = f"`{_shorten(gone[0])}` is no longer in `{path}`"
        if len(gone) > 1:
            basis += f", nor {len(gone) - 1} other span{plural(len(gone) - 1)} it quotes"
        return _Inference(PriorDisposition.FIXED, basis)


def _norm(text: str) -> str:
    """Collapse whitespace, so a quotation matches code that has been rewrapped."""
    return " ".join(text.split())


def _shorten(span: str) -> str:
    if len(span) <= _BASIS_QUOTE_CHARS:
        return span
    return span[:_BASIS_QUOTE_CHARS] + "…"


def _settle(
    finding: PriorFinding,
    carried: set[str],
    ledger: list[LedgerEntry],
    tree: _Tree,
) -> PriorRecord:
    """The disposition of one prior finding, from the first source that has one.

    The ledger goes first because it is the only source that can say `Declined`
    — a judgement no amount of reading the tree recovers. A carry-forward comes
    next and means the finding is still open, whatever the tree looks like: a
    reviewer who just looked at the code outranks an inference about it.
    """
    entry = next((e for e in ledger if e.covers(finding.ref)), None)
    if entry and entry.disposition:
        return PriorRecord(finding.ref, entry.disposition, DispositionSource.LEDGER,
                           "the ledger says so")
    if finding.stable_id in carried:
        return PriorRecord(finding.ref, PriorDisposition.STILL_OPEN,
                           DispositionSource.CARRIED, "this review restates it")
    inference = tree.infer(finding)
    if inference.disposition:
        return PriorRecord(finding.ref, inference.disposition, DispositionSource.TREE,
                           inference.basis)
    if entry:
        return PriorRecord(finding.ref, None, DispositionSource.LEDGER,
                           f'the ledger line reads "{entry.text.strip()}"',
                           UndecidedReason.UNREADABLE_VERDICT)
    return PriorRecord(finding.ref, None, DispositionSource.NONE, inference.basis,
                       inference.reason)


def reconcile(
    prior_text: str,
    review_text: str,
    wt_path: str = "",
    head_sha: str = "",
) -> Reconciliation:
    """What `review_text` made of every finding the prior review reported.

    `wt_path` is the worktree the review was written against. Without it
    nothing is inferred and the review's own account stands alone, which is
    what a caller reconciling two documents rather than a run wants.
    """
    carried = _stable_ids(review_text)
    ledger = _parse_ledger(review_text)
    prior = ReviewHeader.parse(prior_text)
    tree = _Tree(wt_path, prior.head_sha)
    return Reconciliation(
        prior_sha=tree.prior_sha,
        prior_date=prior.date,
        head_sha=head_sha or (git_client.head_sha(wt_path) if wt_path else ""),
        records=[
            _settle(finding, carried, ledger, tree)
            for finding in _parse_prior_findings(prior_text)
        ],
    )


def passed_over(
    prior_text: str, review_text: str, wt_path: str = "", head_sha: str = "",
) -> list[PriorFinding]:
    """The prior findings `review_text` left out that the tree at `wt_path` still holds.

    Findings rather than records: a `PriorRecord` says what became of a
    finding, and a caller asking for a disposition needs the finding itself —
    the lines the prior review wrote, to put back in front of an agent.

    `head_sha` only reaches `reconcile()` to skip its own `git_client.head_sha`
    lookup — this function never reads `Reconciliation.head_sha` itself, so a
    caller that already has the value in hand should pass it.

    Only `NOT_MENTIONED` qualifies. It is the one undecided reason that is the
    review's own omission; the other three are this pipeline's failures — a
    verdict it could not parse, a location it could not read, a tree it had
    nothing to check against — and asking an agent again settles none of them.

    Records come back one per finding in parse order, so the two lists are
    zipped rather than matched on a label, which two findings in one file may
    share.
    """
    if not prior_text:
        return []
    reconciliation = reconcile(prior_text, review_text, wt_path, head_sha)
    return [
        finding
        for finding, record in zip(
            _parse_prior_findings(prior_text), reconciliation.records, strict=True,
        )
        if not record.decided and record.reason is UndecidedReason.NOT_MENTIONED
    ]


def _write(review_file: str, reconciliation: Reconciliation) -> str:
    """Record `reconciliation` beside `review_file`, returning the sidecar's path."""
    path = review_artifact_path(review_file, FILENAME_PRIOR_FINDINGS)
    serde.write_json(Path(path), serde.to_dict(reconciliation))
    return path


def _report(reconciliation: Reconciliation, sidecar: str = "") -> None:
    """Say what was reconciled, against which review, and what is left undecided.

    An undecided finding is a gap in the bookkeeping, not a defect in the code,
    so the line says what was checked before it says what was missed — a
    warning that only ever names findings reads as a defect report and trains
    the reader to skip it.

    The undecided are then printed by `UndecidedReason` rather than as one
    list, because they are not one thing: a line this could not read is a
    defect here and says what shape would have parsed, while a finding the
    review passed by is a defect there. Run together they read as the second,
    which is how six unreadable verdicts once reported as six omissions.
    """
    if not reconciliation.records:
        return
    total = len(reconciliation.records)
    scope = (
        f"{total} prior finding{plural(total)} across {reconciliation.range_label}"
        f": {reconciliation.tally}"
    )
    undecided = reconciliation.undecided
    if not undecided:
        log.info(f"Reconciled {scope}")
        return
    log.warn(f"{len(undecided)} of {total} prior finding{plural(total)} undecided")
    log.dim(f"checked {scope}")
    for reason, records in reconciliation.undecided_groups:
        _report_group(reason, records)
    if sidecar:
        log.dim(f"recorded in {sidecar}")


def _report_group(reason: UndecidedReason, records: list[PriorRecord]) -> None:
    """Print one reason's findings under a heading saying what the reason means."""
    log.dim(f"{reason.heading} ({len(records)}):")
    for record in records:
        log.dim(f"  {record.ref.label} — {record.basis}")
    if reason is UndecidedReason.UNREADABLE_VERDICT:
        log.dim(f"  {_VERDICT_SHAPE}")


# What to write instead, for the reader who has just been shown a line that did
# not parse. Both the words and the punctuation come from what the parser
# accepts, so this cannot describe a shape the parser would reject.
_VERDICT_SHAPE = (
    f"write one of {', '.join(d.value for d in PriorDisposition)} first, then "
    f"end the line or break with one of: {' '.join(DISPOSITION_TAIL_PUNCTUATION)}"
)


def record_prior_findings(
    review_file: str,
    prior_text: str,
    wt_path: str = "",
) -> Reconciliation | None:
    """Reconcile the review at `review_file` against `prior_text` and record it.

    Runs before post-processing strips the ledger, which is the only window in
    which the review still says what it made of the prior findings. Returns
    None when there is nothing to reconcile — no prior review, no review file,
    or a prior review that reported no findings.
    """
    path = Path(review_file)
    if not prior_text or not path.exists():
        return None
    reconciliation = reconcile(prior_text, path.read_text(), wt_path)
    if not reconciliation.records:
        return None
    _report(reconciliation, _write(review_file, reconciliation))
    return reconciliation


# ── Reply threads on the prior review ────────────────────────────────────────

def _classify_thread_for_rereview(
    comments: list[dict], is_resolved: bool, bot_login: str,
) -> tuple[ReplyState, list[dict]]:
    """Classify a review thread from the bot-reviewer's perspective.

    Returns (state, author_replies) where author_replies are non-bot comments
    after the first bot comment.
    """
    if is_resolved:
        return ReplyState.RESOLVED, []

    bot_lower = bot_login.lower()
    author_replies = []
    seen_bot = False
    for c in comments:
        login = (c.get("author") or {}).get("login", "").lower()
        if login == bot_lower:
            seen_bot = True
        elif seen_bot:
            author_replies.append(c)

    if not author_replies:
        return ReplyState.UNREPLIED, []

    last_reply = author_replies[-1]
    body = last_reply.get("body", "")
    if _is_acknowledgment(body):
        return ReplyState.ACKNOWLEDGED, author_replies
    if _is_pushback(body):
        return ReplyState.CONTESTED, author_replies
    return ReplyState.REPLIED, author_replies


def _match_thread_to_finding(root_body: str) -> str:
    """Extract finding ID (e.g. 'M1') from a bot-posted review comment body."""
    m = BOLD_FINDING_ID_RE.search(root_body)
    return m.group(1) if m else ""


def fetch_reply_threads(
    repo: str, pr_number: str, bot_login: str = "",
    pr_data: PRData | None = None,
) -> dict:
    """Fetch and classify reply threads on bot-authored review comments.

    ``bot_login`` is the reviewer whose comments count as roots; it is read off
    ``pr_data`` or detected when the caller does not name one. ``pr_data`` is a
    consolidated query's answer, which the threads are taken from rather than
    re-fetched.

    Returns a dict with:
      - threads: list of per-thread dicts with state, finding_id, replies, path, line
      - summary: count per state
    """
    if not bot_login:
        bot_login = pr_data.viewer_login if pr_data is not None else _get_bot_login()
    if not bot_login:
        log.warn("Could not detect bot login — skipping reply thread analysis")
        return {"threads": [], "summary": {}}

    owner, name = repo.split("/", 1)
    try:
        raw_threads = fetch_threads(owner, name, int(pr_number), pr_data)
    except Exception:
        return {"threads": [], "summary": {}}

    if not raw_threads:
        return {"threads": [], "summary": {}}

    bot_lower = bot_login.lower()
    classified = []
    summary: dict[ReplyState, int] = {}

    for thread in raw_threads:
        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue
        root = comments[0]
        root_author = (root.get("author") or {}).get("login", "").lower()
        if root_author != bot_lower:
            continue

        is_resolved = thread.get("isResolved", False)
        state, author_replies = _classify_thread_for_rereview(
            comments, is_resolved, bot_login,
        )
        finding_id = _match_thread_to_finding(root.get("body", ""))

        classified.append({
            "state": state,
            "finding_id": finding_id,
            "path": thread.get("path", ""),
            "line": thread.get("line"),
            "replies": [
                {
                    "author": (r.get("author") or {}).get("login", ""),
                    "body": r.get("body", ""),
                }
                for r in author_replies
            ],
        })
        summary[state] = summary.get(state, 0) + 1

    return {"threads": classified, "summary": summary}
