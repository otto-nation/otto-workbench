"""What becomes of findings across the reviews that report them.

Three jobs over one vocabulary: merging the group reviews into a single
document, giving each finding an identity that outlives the review carrying
it, and reconciling what the previous review reported against what this one
did. They live together because they read the same finding line — the path and
description that decide whether two findings are duplicates are the pair that
hashes to a stable ID, and a stable ID is how reconciliation recognises a
finding a later review restates.

Reading a review is `review_document`'s job, checking findings against the
tree is `review_verify`'s, and the `Finding` every side holds is
`review_types`' — a consumer that only holds findings needs none of this.

Where a finding's body ends is `review_document`'s too. Deduplication and the
prior-review reading both walk `finding_spans`, and a repeat is removed with
`cut_spans`, so a duplicate takes exactly the lines out of the merged document
that a falsified finding does. Each keeps its own head pattern for *which*
declarations it wants — `_finding_dedup_key` and `_ANNOTATE_FINDING_RE` read
different things off a finding line — and neither says where one stops.

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
document. The same reasoning applies while groups are still being merged: each
group's IDs are shifted past the groups before it, references included, but a
reference the group cannot resolve is left alone — another group may well
declare it, and the merge-wide pass is the first place that can tell.

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
"""

# doc-group: findings

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import git_client
import log
import serde
from review_document import (
    BOLD_FINDING_ID_RE, FINDING_ID_RE, SECTION_FILE_TRIAGE,
    SECTION_PRIOR_FINDINGS, FindingSpan, ReviewDocument, ReviewHeader,
    cut_spans, finding_location, finding_spans, parse_finding_line,
)
from review_paths import FILENAME_PRIOR_FINDINGS, review_artifact_path
from review_types import (
    DISPOSITION_TAIL_PUNCTUATION, SEVERITIES, PriorDisposition,
    disposition_precedence,
)
from text import plural


# ── Prior-finding vocabulary ─────────────────────────────────────────────────

@dataclass(frozen=True)
class FindingRef:
    """How a re-review names a prior finding: its ID and its path.

    The pair travels together because neither half identifies a finding on its
    own — IDs are per-review sequence numbers and one file holds many findings.
    """

    finding_id: str = ""
    path: str = ""

    @property
    def label(self) -> str:
        """How the reference reads in a log line.

        A reference with no path is the ID alone rather than the ID and an
        empty pair of backticks — the line is already reporting that nothing
        read a path off the finding, and printing `` there says it twice.
        """
        if not self.path:
            return self.finding_id
        return f"{self.finding_id} `{self.path}`".strip()


@dataclass(frozen=True)
class LedgerEntry:
    """One `## Prior findings` line: a prior finding, and what became of it."""

    ref: FindingRef
    disposition: PriorDisposition | None
    text: str

    def covers(self, ref: FindingRef) -> bool:
        """Whether this entry accounts for `ref`.

        An entry that names no path stands on its ID alone; one that names a
        path has to name the right one, or a single entry would account for
        every prior finding in its file.
        """
        if self.ref.finding_id != ref.finding_id:
            return False
        return not self.ref.path or self.ref.path == ref.path


@dataclass(frozen=True)
class PriorFinding:
    """A finding line from the prior review, as reconciliation sees it.

    `text` runs from the finding line to whatever ends it, so a finding that
    quotes the code it objects to below its first line keeps the quotation —
    which is what lets reconciliation ask whether that code is still there.
    """

    ref: FindingRef
    stable_id: str
    text: str = ""


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


def _declared_ids(text: str, prefix: str) -> list[int]:
    """The IDs `text` declares under `prefix`, in the order they first appear.

    A finding declares its ID at the head of its own list item; every other
    occurrence is a reference to a declaration. Only declarations get numbers,
    because a reference can name a finding that evidence verification or
    deduplication has since taken out of the review.
    """
    ids: list[int] = []
    for line in text.split("\n"):
        m = FINDING_ID_RE.match(line.strip())
        if not m or m.group(2) != prefix or int(m.group(3)) in ids:
            continue
        ids.append(int(m.group(3)))
    return ids


def _declared_id(line: str, prefix: str) -> int | None:
    ids = _declared_ids(line, prefix)
    return ids[0] if ids else None


def _id_reference_re(prefix: str) -> re.Pattern[str]:
    """Every way a review names a finding: `[M1]`, and a cited bare `M1`."""
    return re.compile(
        rf"\[{prefix}(\d+)\]"
        rf"|(\b(?i:{_REFERENCE_CUES})\s+){prefix}(\d+)(?![\d\]])"
    )


def _rewrite_ids(
    text: str, prefix: str, new_by_old: dict[int, int], *, mark_dangling: bool,
) -> str:
    """Move every ID and every reference to one onto its new number."""
    def rewrite(m: re.Match[str]) -> str:
        bracketed, cue, bare = m.group(1), m.group(2) or "", m.group(3)
        new = new_by_old.get(int(bracketed or bare))
        if new is None:
            return m.group(0) if not mark_dangling else f"{cue}{_REMOVED_REF}"
        return f"{cue}[{prefix}{new}]" if bracketed else f"{cue}{prefix}{new}"

    return _id_reference_re(prefix).sub(rewrite, text)


def _declared_across(sections: dict[str, str], prefix: str) -> list[int]:
    """The IDs `prefix` is declared with anywhere in `sections`, in reading order.

    Every section is read, not just the one whose severity `prefix` names. A
    review that files a Should-fix declaration under Must fix is malformed, but
    its ID is still declared, and a map that missed it would rewrite the
    declaration itself to `[removed]`.
    """
    return _declared_ids(_joined(sections), prefix)


def _joined(sections: dict[str, str]) -> str:
    """The severity sections as one text, in the order the review prints them."""
    return "\n".join(sections[severity.key] for severity in SEVERITIES)


def _rewrite_every_prefix(
    sections: dict[str, str],
    maps: dict[str, dict[int, int]],
    *,
    mark_dangling: bool,
) -> dict[str, str]:
    """Rewrite every section through every severity's map, references included.

    A section is rewritten through all four maps rather than its own alone: a
    reference does not have to live in the section that declares the finding it
    names, and a Must-fix finding that says "blocked on [S1]" is renumbered by
    the Should-fix map or by nothing at all. Looking only at each section's own
    prefix left such a citation on whichever finding later took its number.

    A prefix absent from `maps` is left alone wherever it appears — there is no
    numbering to move it onto, and every mention of it belongs to some other
    document.
    """
    rewritten: dict[str, str] = {}
    for key, text in sections.items():
        for prefix, new_by_old in maps.items():
            text = _rewrite_ids(text, prefix, new_by_old, mark_dangling=mark_dangling)
        rewritten[key] = text
    return rewritten


def _gap_map(declared: list[int], merged_into: dict[int, int]) -> dict[int, int]:
    """Where each declared ID lands once the gaps between them close."""
    new_by_old = {old: new for new, old in enumerate(declared, 1)}
    # A deduplicated finding was not dropped, it was merged: its references
    # belong on the copy that survived, which says the same thing. The survivor
    # is always declared here — it is the copy dedup kept — but guard anyway, so
    # a map built from other text cannot quietly point a reference somewhere new.
    for gone, survivor in merged_into.items():
        if survivor in new_by_old:
            new_by_old.setdefault(gone, new_by_old[survivor])
    return new_by_old


def _close_gaps(
    sections: dict[str, str], merged_into: dict[str, dict[int, int]],
) -> dict[str, str]:
    """Close the gaps in every severity's IDs, taking every reference along.

    The maps are built from all the sections at once and applied to all of them,
    so a reference reaching across severities lands on the finding it named
    rather than on whatever ended up at that number. `merged_into` says, per
    severity, which IDs deduplication folded into which survivor.
    """
    maps = {}
    for severity in SEVERITIES:
        declared = _declared_across(sections, severity.key)
        if declared:
            maps[severity.key] = _gap_map(declared, merged_into.get(severity.key, {}))
    return _rewrite_every_prefix(sections, maps, mark_dangling=True)


def _renumber_prefix(text: str, prefix: str, merged_into: dict[int, int] | None = None) -> str:
    """Close the gaps in `prefix` IDs, taking every reference along with them.

    References are rewritten through the same map as the declarations, so a
    finding that cites another one still cites the same one afterwards.
    """
    declared = _declared_ids(text, prefix)
    if not declared:
        # Nothing here declares an ID, so there is no map to rewrite through and
        # every occurrence is a reference into text we are not looking at.
        return text
    return _rewrite_ids(
        text, prefix, _gap_map(declared, merged_into or {}), mark_dangling=True,
    )


def renumber_findings(text: str) -> str:
    for severity in SEVERITIES:
        text = _renumber_prefix(text, severity.key)
    return text


# ── Triage and deduplication ─────────────────────────────────────────────────

_EMPTY_SECTION_LINE_RE = re.compile(
    r"^(?:_none\._|_\(none\)_|_none in this file group\._|---)\s*$",
    re.IGNORECASE,
)


def _clean_section_text(text: str) -> str:
    lines = [line for line in text.split("\n") if not _EMPTY_SECTION_LINE_RE.match(line.strip())]
    return "\n".join(lines).strip()


_TRIAGE_LINE_RE = re.compile(r"^- `[^`]+`\s")


def _clean_triage(text: str) -> str:
    return "\n".join(
        line for line in text.split("\n")
        if _TRIAGE_LINE_RE.match(line)
    )


_FINDING_PATH_RE = re.compile(
    r"^- (?:\[ \] )?\*\*\[\w+\d+\]\*\*"
    r"\s+(?:<!-- sid:\w+ -->\s+)?"
    r"\*\*(?:`([^`]+)`|([^*]+))\*\*"
)
_FINDING_DESC_RE = re.compile(r"—\s*(.{0,80})")


@dataclass(frozen=True)
class FindingKey:
    """What makes two findings the same one: where they point and what they say."""

    path: str
    desc: str


def _finding_dedup_key(line: str) -> FindingKey | None:
    m = _FINDING_PATH_RE.match(line)
    if not m:
        return None
    path = (m.group(1) or m.group(2) or "").replace("\\_", "_").strip()
    dm = _FINDING_DESC_RE.search(line)
    return FindingKey(path, dm.group(1).strip().lower() if dm else "")


def _record_merge(merged_into: dict[int, int], survivor: int | None, dup: int | None) -> None:
    """Remember that `dup`'s references should end up on `survivor`."""
    if survivor is None or dup is None:
        return
    merged_into[dup] = survivor


@dataclass(frozen=True)
class _Deduped:
    """One severity's findings with the repeats dropped, and where they went.

    `merged_into` maps each dropped ID to the copy that survived it, so the
    renumbering that follows can move references onto the survivor instead of
    marking them `[removed]`. It is kept apart from `text` because the gaps
    close across every severity at once, after every section has been deduped.
    """

    text: str
    merged_into: dict[int, int] = field(default_factory=dict)


def _dedup_findings(text: str, prefix: str) -> _Deduped:
    """One severity's text with the repeats removed, and where each one went.

    `_finding_dedup_key` chooses which declarations are candidates; how much of
    the text a dropped one takes with it is `finding_spans`', so a repeat and a
    finding the disprove gate falsifies lose exactly the same lines.
    """
    seen: dict[FindingKey, int | None] = {}
    merged_into: dict[int, int] = {}
    repeats: list[FindingSpan] = []
    for span in finding_spans(text):
        key = _finding_dedup_key(span.line)
        if key is None:
            continue
        if key in seen:
            repeats.append(span)
            _record_merge(merged_into, seen[key], _declared_id(span.line, prefix))
            continue
        seen[key] = _declared_id(span.line, prefix)
    return _Deduped(cut_spans(text, repeats), merged_into)


def _dedup_sections(sections: dict[str, str]) -> dict[str, str]:
    """Drop each severity's repeated findings, then close the gaps they leave.

    Two findings are duplicates only within one severity — the same problem
    reported as a Must-fix by one group and a Nit by another is a disagreement,
    not a repeat — but the numbering closes over all four sections together, so
    a reference from one severity to another survives the drop.
    """
    deduped = {
        severity.key: _dedup_findings(sections[severity.key], severity.key)
        for severity in SEVERITIES
    }
    return _close_gaps(
        {key: entry.text for key, entry in deduped.items()},
        {key: entry.merged_into for key, entry in deduped.items()},
    )


def _dedup_triage(triage: str) -> str:
    seen: set[str] = set()
    lines = []
    for line in triage.split("\n"):
        m = re.match(r"^- `([^`]+)`", line)
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

        Dangling references are left as they are: this runs per group, and an ID
        this group does not declare may still be declared by another one. The
        merge-wide pass in `_close_gaps` is the first place that can tell.
        """
        declared = {
            severity.key: _declared_across(group, severity.key)
            for severity in SEVERITIES
        }
        maps = {
            key: {old: old + self.offsets[key] for old in ids}
            for key, ids in declared.items() if ids and self.offsets[key]
        }
        shifted = _rewrite_every_prefix(group, maps, mark_dangling=False)
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

def compute_stable_id(path: str, desc: str) -> str:
    key = f"{path.strip().lower()}:{desc[:80].strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:8]


_ANNOTATE_FINDING_RE = re.compile(
    r"^(- (?:\[ \] )?\*\*\[[A-Z]\d+\]\*\*)\s+"
)


def _extract_finding_path(line: str, after: str) -> str:
    """The file a finding line names, or "" when it names none.

    `finding_location` answers first because it is the same reading the rest of
    the parser gives a location, and it is the only rung that recognises a
    plain-backtick file with no `:<line>` after it — the shape a review writes
    whenever the finding is about a file rather than a line of one. Reading
    that as no path at all left the finding with no stable ID either, so
    neither carry-forward nor the tree could account for it.

    The two rungs below it are what this function used to be, kept because they
    read locations `finding_location` declines: a bold span that is a label
    rather than a filename, and a bare `path:12` in no span at all. Identity
    is the point here — a finding whose location is `**Documentation**` still
    has to hash to the same thing across reviews to be carried forward.
    """
    location = finding_location(after)
    if location.named:
        return location.path
    path_m = _FINDING_PATH_RE.match(line)
    if path_m:
        path_str = (path_m.group(1) or path_m.group(2) or "").replace("\\_", "_").strip()
        return path_str.rsplit(":", 1)[0] if ":" in path_str else path_str
    cb_path_re = re.match(r"[`]?(\S+?)[`]?:\d+", after)
    return cb_path_re.group(1) if cb_path_re else ""


def _finding_stable_id(line: str, m: re.Match) -> str:
    """The stable ID a finding line hashes to, or "" when it names no path."""
    path_str = _extract_finding_path(line, line[m.end():])
    if not path_str:
        return ""
    desc_m = _FINDING_DESC_RE.search(line)
    return compute_stable_id(path_str, desc_m.group(1).strip() if desc_m else "")


def _annotate_finding_line(line: str, m: re.Match) -> str:
    sid = _finding_stable_id(line, m)
    if not sid:
        return line
    return f"{m.group(1)} <!-- sid:{sid} --> {line[m.end():]}"


def annotate_prior_with_stable_ids(review_text: str) -> str:
    lines = review_text.split("\n")
    result: list[str] = []
    for line in lines:
        m = _ANNOTATE_FINDING_RE.match(line)
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
        line = raw.strip()
        m = _ANNOTATE_FINDING_RE.match(line)
        if m:
            ids.add(_finding_stable_id(line, m))
    ids.discard("")
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
    m = _ANNOTATE_FINDING_RE.match(line)
    label_m = BOLD_FINDING_ID_RE.search(line)
    body = [raw.strip() for raw in span.text_of(prior_text).split("\n")]
    return PriorFinding(
        ref=FindingRef(
            label_m.group(1) if label_m else "",
            _extract_finding_path(line, line[m.end():]),
        ),
        stable_id=_finding_stable_id(line, m),
        text="\n".join(body).strip(),
    )


def _parse_prior_findings(prior_text: str) -> list[PriorFinding]:
    """Every finding the prior review reported, each with the text reporting it.

    `_ANNOTATE_FINDING_RE` chooses which declarations count — the shape the
    stable-ID annotator writes, which is what carry-forward matches on — and
    `finding_spans` says how far down each one's text runs.
    """
    return [
        _prior_finding(span, prior_text)
        for span in finding_spans(prior_text)
        if _ANNOTATE_FINDING_RE.match(span.line)
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
