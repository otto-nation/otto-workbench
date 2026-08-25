"""Finding parsing, renumbering, deduplication, verification, and stable IDs.

Shared between review-orchestrate (merging/verification) and review-post (parsing).

Finding IDs (``M1``, ``S2``, ``N3``, ``I1``) are assigned mechanically and are
only meaningful inside the review that carries them. Agents write whatever IDs
they like; merging, deduplication, and evidence verification all remove
findings, and a final pass closes the gaps so each severity numbers from 1 with
no holes.

Only a *declaration* — a finding at the head of its own list item, ``- **[M1]**
…`` or ``- [ ] **[M1]** …`` — gets a number. Everything else that names an ID is
a reference, and references are rewritten through the same map, so a finding
that cites another one still cites the same one afterwards.

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
"""

# doc-group: findings

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import log
from pr_domains import ReviewVerdict
from review_common import (
    SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD,
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, PriorDisposition,
    disposition_precedence, plural, severity_by_key,
)

# Severity header name -> key mapping (section + aliases, lowercased)
def _build_severity_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for s in SEVERITIES:
        names[s.section.lower()] = s.key
        # replace("-", " ") mirrors _match_severity_header's input normalisation so
        # future hyphenated section names (e.g. "Should-fix") still resolve correctly.
        names[s.section.lower().replace("-", " ")] = s.key
        for alias in s.aliases:
            names[alias.lower()] = s.key
    return names


_SEVERITY_NAMES = _build_severity_names()


# ── Finding dataclass ────────────────────────────────────────────────────────

@dataclass
class Finding:
    id: str
    severity: str
    seq: int
    path: str
    line: int | None
    end_line: int | None
    body: str
    full_path: str = ""
    posted_id: str = ""
    classification: str = ""
    skip_reason: str = ""
    checked: bool = False
    # Adjudicated, not outstanding: the finding was considered and rejected on
    # the merits. A skip is the fix pass saying "not me"; a decline is the
    # review saying "not at all", and the fix pass must not revisit it.
    declined: bool = False
    decline_reason: str = ""


BOLD_FINDING_ID_RE = re.compile(r"\*\*\[([MSNI]\d+)\]\*\*")

# ── Finding parsing ──────────────────────────────────────────────────────────

FINDING_ID_RE = re.compile(
    r"^- (?:\[([ x])\] )?"
    r"(?:~~)?"
    r"\*\*\[([MSNI])(\d+)\](?:\*\*)?"
    r"\s+"
    r"(?:<!-- sid:\w+ -->\s+)?"
)

PATH_SECTION_RE = re.compile(
    r"\*\*`(.+?)`\*\*"
    r"|"
    r"\*\*(.+?)\*\*"
    r"|"
    r"`(.+?)`"
)
# What a path may hold, now that PATH_SECTION_RE has already delimited the
# span. The class says what a path is not: whitespace, the `:` that introduces
# the line suffix, the `*` and backtick that close the span, and the em dash
# that separates a location from its body. Everything else is an ordinary
# filename character — non-ASCII included, because `src/café.py` names a file
# the same way `src/cafe.py` does.
_PATH_CHAR = r"[^\s:*`—]"
_SEGMENT_CHAR = r"[^\s/:*`—]"

# The `:12` or `:12-18` a location may carry after its filename.
_LINE_SUFFIX = r"(?::\d+(?:[-–]\d+)?)?"

# A filename holding spaces. It has no character class to stop it, so every
# use has to bound it: the extension ends it, and whatever follows has to be
# the end of the span it was found in.
_SPACED_FILE = rf"{_PATH_CHAR}+(?: {_PATH_CHAR}+)+\.\w+"

# Three shapes, tried in this order:
#
#   1. pkg/handler.go            — an extension ends the filename
#   2. src/café brûlé.py         — a filename holding spaces
#   3. ai/claude/bin/ci-check    — an extensionless script, slash required
#
# Shapes 1 and 3 keep prose out by starting at the span's first character and
# stopping at the first space: a sentence only passes if its opening word is
# already shaped like a file. A slash is what shape 3 has instead of an
# extension, and prose is full of slashes, so that shape cannot be allowed to
# reach across a space either.
#
# Shape 2 has no such boundary, so it earns the space a different way: it must
# end in an extension and, per the lookahead, account for the whole span, line
# suffix included. "the fix lands in v2.0 of the tool" fails that — the words
# after the dotted token are left over — while "src/café brûlé.py:12-18"
# satisfies it. The same lookahead is what stops a greedy space run from
# walking past the real filename, since anything it swallowed would have to be
# part of the span's final extension.
#
# Shape 2 is tried after shape 1 so it only runs where the space-free shapes
# found nothing: every location a review already parsed still parses the same
# way, and a span naming one path before some prose still yields that path
# rather than the whole span.
FIRST_FILE_RE = re.compile(
    r"("
    rf"{_PATH_CHAR}+\.\w+"
    r"|"
    rf"{_SPACED_FILE}(?={_LINE_SUFFIX}\s*$)"
    r"|"
    rf"{_SEGMENT_CHAR}+(?:/{_SEGMENT_CHAR}+)+"
    r")"
    r"(?::(\d+)(?:[-–](\d+))?)?"
)

STRIKETHROUGH_RE = re.compile(r"^- ~~\*\*\[")

# The fix pass's record of work it did not do. The reason is optional for the
# same reason it is on `_DECLINED`: a skip written without one is still a skip,
# and reading it as an ordinary open finding is what lets a sibling finding's
# edit report it as fixed.
_SKIP = r"\*\(skipped(?:\s*[—–-]+\s*(.+?))?\)\*"

# Matched at the head or tail of the finding body for the same reason
# `DECLINED_HEAD_RE`/`DECLINED_TAIL_RE` are anchored below: matched anywhere, a
# finding whose prose quotes the annotation — this file's own docs do,
# verbatim — would be misread as carrying it and barred from
# `_reconcile_checkboxes` forever.
SKIP_HEAD_RE = re.compile(rf"^{_SKIP}")
SKIP_TAIL_RE = re.compile(rf"{_SKIP}\s*$")

# The review file's record of `PriorDisposition.DECLINED`. The ledger is
# stripped before the file is finished, so the verdict has to survive on the
# finding line itself or the next `--fix` sees an ordinary open finding. The
# reason is optional so a decline written without one still registers.
_DECLINED = r"\*\(declined(?:\s*[—–-]+\s*(.+?))?\)\*"

# An annotation, not prose. Matched anywhere in the line, a finding that merely
# quotes the annotation — which any review of this parser writes — reads as
# declined and is silently dropped from the fix pass's work set with no warning
# anywhere. So the match is anchored to the two places the templates put an
# annotation: at the head of the finding body, and at the end of the line.
DECLINED_HEAD_RE = re.compile(rf"^{_DECLINED}", re.IGNORECASE)
DECLINED_TAIL_RE = re.compile(rf"{_DECLINED}\s*$", re.IGNORECASE)


def match_decline(body: str, line: str) -> re.Match[str] | None:
    """The decline annotation a finding line carries, if it carries one."""
    return DECLINED_HEAD_RE.match(body) or DECLINED_TAIL_RE.search(line)


def _extract_path(after_id: str) -> tuple[str, int | None, int | None]:
    section_match = PATH_SECTION_RE.match(after_id)
    if not section_match:
        return "", None, None
    section_text = section_match.group(1) or section_match.group(2) or section_match.group(3) or ""
    section_text = section_text.replace("\\_", "_")
    file_match = FIRST_FILE_RE.match(section_text)
    if not file_match:
        return "", None, None
    path = file_match.group(1).strip()
    line_num = int(file_match.group(2)) if file_match.group(2) else None
    end_line = int(file_match.group(3)) if file_match.group(3) else None
    return path, line_num, end_line


def _extract_body_text(line: str) -> str:
    em_dash_pos = line.find("—")
    if em_dash_pos != -1:
        return line[em_dash_pos + 1:].strip()
    for sep in (" -- ", " - "):
        pos = line.find(sep)
        if pos != -1:
            return line[pos + len(sep):].strip()
    return ""


def _parse_finding_line(stripped: str) -> Finding | None:
    id_match = FINDING_ID_RE.match(stripped)
    if not id_match:
        return None
    checkbox = id_match.group(1)
    sev = id_match.group(2)
    seq = int(id_match.group(3))
    after_id = stripped[id_match.end():]
    path, line_num, end_line = _extract_path(after_id)
    if path:
        body = _extract_body_text(stripped)
    else:
        body = after_id.strip()
    declined = match_decline(body, stripped)
    return Finding(
        id=f"{sev}{seq}", severity=sev, seq=seq,
        path=path, line=line_num, end_line=end_line, body=body,
        checked=(checkbox is not None and checkbox.lower() == "x"),
        declined=declined is not None,
        decline_reason=(declined.group(1) or "").strip() if declined else "",
    )


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
        """How the reference reads in a log line."""
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
    parsed = _parse_finding_line(raw.strip())
    if not parsed:
        return None
    return LedgerEntry(
        ref=FindingRef(parsed.id, parsed.path),
        disposition=PriorDisposition.parse(parsed.body),
        text=raw,
    )


def _finalize_finding(finding: Finding, body_lines: list[str]):
    body = "\n".join(body_lines).strip()
    if not body:
        return
    body = re.sub(r"\n+###[^\n]*$", "", body, flags=re.DOTALL).strip()
    finding.body = body


def _close_previous(findings: list[Finding], body_lines: list[str]):
    if findings:
        _finalize_finding(findings[-1], body_lines)
    body_lines.clear()


def _is_section_boundary(stripped: str) -> bool:
    return stripped.startswith("### ") or bool(STRIKETHROUGH_RE.match(stripped))


def _match_severity_header(stripped: str) -> str | None:
    if not stripped.startswith("#"):
        return None
    text = stripped.lstrip("#").strip().lower().replace("-", " ")
    return _SEVERITY_NAMES.get(text)


def parse_findings(text: str) -> list[Finding]:
    findings: list[Finding] = []
    current_severity: str | None = None
    body_lines: list[str] = []
    accumulating = False

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        severity = _match_severity_header(stripped)
        if severity is not None:
            _close_previous(findings, body_lines)
            current_severity = severity
            accumulating = False
            continue
        if stripped.startswith("## "):
            _close_previous(findings, body_lines)
            current_severity = None
            accumulating = False
            continue
        if current_severity is None:
            continue
        if _is_section_boundary(stripped):
            _close_previous(findings, body_lines)
            accumulating = False
            continue
        finding = _parse_finding_line(stripped)
        if finding:
            _close_previous(findings, body_lines)
            body_lines = [finding.body] if finding.body else []
            findings.append(finding)
            accumulating = True
            continue
        if accumulating and stripped:
            body_lines.append(raw_line.rstrip())

    _close_previous(findings, body_lines)
    return findings


def match_skip(finding: Finding) -> re.Match[str] | None:
    """The skip annotation a finding carries, if it carries one.

    The single owner of "did the fix pass skip this?". Everything that acts on
    a skip asks here, so the auto-check guard and the fix summary cannot come
    to different answers about the same finding — the way they did while the
    guard read only `Finding.declined`.

    A checked finding carries no skip: whatever its body says, the box says the
    work was done.
    """
    if finding.checked:
        return None
    return SKIP_HEAD_RE.match(finding.body) or SKIP_TAIL_RE.search(finding.body)


def extract_skip_reasons(findings: list[Finding]) -> None:
    """Extract skip reasons from finding body text (mutates in place)."""
    for f in findings:
        m = match_skip(f)
        if m:
            f.skip_reason = (m.group(1) or "").strip()


# ── Diff hunk parsing ────────────────────────────────────────────────────────

_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    hunks: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    for line in diff_text.splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            current_file = m.group(1)
            hunks.setdefault(current_file, [])
            continue
        m = _DIFF_HUNK_RE.match(line)
        if m and current_file is not None:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) is not None else 1
            hunks[current_file].append((start, start + length - 1))
    return hunks


# ── Section extraction ───────────────────────────────────────────────────────

def _extract_section(content: str, header: str) -> str:
    pattern = rf"^## {re.escape(header)}\s*\n"
    m = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    next_header = re.search(r"^## ", content[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(content)
    return content[start:end].strip()


_EMPTY_SECTION_LINE_RE = re.compile(
    r"^(?:_none\._|_\(none\)_|_none in this file group\._|---)\s*$",
    re.IGNORECASE,
)


def _clean_section_text(text: str) -> str:
    lines = [line for line in text.split("\n") if not _EMPTY_SECTION_LINE_RE.match(line.strip())]
    return "\n".join(lines).strip()


_VALID_SECTION_HEADERS = (
    {s.section.lower() for s in SEVERITIES}
    | {SECTION_FILE_TRIAGE.lower(), SECTION_PRIOR_FINDINGS.lower()}
)


def _validate_group_output(output_path: str, group_name: str) -> bool:
    content = Path(output_path).read_text()
    if not content.strip():
        return True
    has_section = any(
        line.strip()[3:].strip().lower() in _VALID_SECTION_HEADERS
        for line in content.split("\n")
        if line.strip().startswith("## ")
    )
    if not has_section:
        log.warn(f"Group {group_name} output has no recognized sections — findings may be lost")
    return has_section


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


def renumber_section(prefix: str, text: str, offset: int) -> tuple[str, int]:
    """Shift one group's IDs past the groups already merged, references included.

    Returns the highest ID the group leaves in use, which is what the next group
    has to clear. Counting declarations instead would under-shift any group
    whose agent skipped a number — nothing closes those gaps before the merge —
    and two groups would land on the same ID. Gaps are closed afterwards, over
    the merged text, where a number freed up by one group can be handed to
    another.

    Dangling references are left as they are: this runs per group, and an ID
    this group does not declare may still be declared by another one. The
    merge-wide pass is the first place that can tell.
    """
    if not text:
        return "", 0
    declared = _declared_ids(text, prefix)
    if offset > 0:
        shifted = {old: old + offset for old in declared}
        text = _rewrite_ids(text, prefix, shifted, mark_dangling=False)
    return text, max(declared, default=0)


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

    new_by_old = {old: new for new, old in enumerate(declared, 1)}
    # A deduplicated finding was not dropped, it was merged: its references
    # belong on the copy that survived, which says the same thing. The survivor
    # is always declared here — it is the copy dedup kept — but guard anyway, so
    # a map built from other text cannot quietly point a reference somewhere new.
    for gone, survivor in (merged_into or {}).items():
        if survivor in new_by_old:
            new_by_old.setdefault(gone, new_by_old[survivor])

    return _rewrite_ids(text, prefix, new_by_old, mark_dangling=True)


def renumber_findings(text: str) -> str:
    for severity in SEVERITIES:
        text = _renumber_prefix(text, severity.key)
    return text


# ── Triage and deduplication ─────────────────────────────────────────────────

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


def _dedup_findings(text: str, prefix: str) -> str:
    seen: dict[FindingKey, int | None] = {}
    merged_into: dict[int, int] = {}
    kept: list[str] = []
    skipping = False
    for line in text.split("\n"):
        key = _finding_dedup_key(line)
        if key is not None and key in seen:
            skipping = True
            _record_merge(merged_into, seen[key], _declared_id(line, prefix))
            continue
        if key is not None:
            seen[key] = _declared_id(line, prefix)
            skipping = False
        if skipping:
            continue
        kept.append(line)
    return _renumber_prefix("\n".join(kept), prefix, merged_into)


def _merge_one_review(
    content: str, merged_triage: str,
    merged: dict[str, str], offsets: dict[str, int],
) -> str:
    triage = _extract_section(content, SECTION_FILE_TRIAGE)
    if triage:
        cleaned = _clean_triage(triage)
        if cleaned:
            merged_triage += cleaned + "\n"
    # Kept out of the renumbering below: these IDs belong to the prior review,
    # and each group only dispositions the prior findings for its own files, so
    # the merged ledger is the union of what every group accounted for.
    ledger = _clean_section_text(_extract_section(content, SECTION_PRIOR_FINDINGS))
    if ledger:
        merged[SECTION_PRIOR_FINDINGS] += ledger + "\n"
    for severity in SEVERITIES:
        section = severity.section
        raw = _clean_section_text(_extract_section(content, section))
        text, highest = renumber_section(severity.key, raw, offsets[section])
        if text:
            merged[section] += text + "\n"
        offsets[section] += highest
    return merged_triage


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


def merge_reviews(group_files: list[str]) -> str:
    merged_triage = ""
    merged: dict[str, str] = {s.section: "" for s in SEVERITIES}
    merged[SECTION_PRIOR_FINDINGS] = ""
    offsets: dict[str, int] = {s.section: 0 for s in SEVERITIES}

    for path in group_files:
        p = Path(path)
        if not p.exists():
            continue
        merged_triage = _merge_one_review(p.read_text(), merged_triage, merged, offsets)

    merged_triage = _dedup_triage(merged_triage)

    for severity in SEVERITIES:
        if merged[severity.section]:
            merged[severity.section] = _dedup_findings(
                merged[severity.section], severity.key
            )

    parts = [f"## {SECTION_FILE_TRIAGE}\n{merged_triage}"]
    for severity in SEVERITIES:
        if merged[severity.section]:
            parts.append(f"## {severity.section}\n{merged[severity.section]}")
    if merged[SECTION_PRIOR_FINDINGS]:
        parts.append(
            f"## {SECTION_PRIOR_FINDINGS}\n"
            f"{_dedup_ledger(merged[SECTION_PRIOR_FINDINGS])}"
        )
    return "\n".join(parts)


# ── Counting ─────────────────────────────────────────────────────────────────

def _count_findings(text: str) -> dict[str, int]:
    return {
        severity.key: len(set(re.findall(
            rf"^- (?:\[ \] )?\*\*\[{severity.key}(\d+)\]\*\*",
            text,
            re.MULTILINE,
        )))
        for severity in SEVERITIES
    }


def _has_findings(merged_content: str) -> bool:
    for severity in SEVERITIES:
        if re.search(rf"\[{severity.key}\d+\]", merged_content):
            return True
    return False


# ── Evidence verification ────────────────────────────────────────────────────

_EVIDENCE_RE = re.compile(
    r">\s*```\w*\n(.*?)\n\s*>\s*```",
    re.DOTALL,
)


def _extract_evidence(body: str) -> str | None:
    m = _EVIDENCE_RE.search(body)
    if not m:
        return None
    lines = m.group(1).split("\n")
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("> "):
            stripped = stripped[2:]
        elif stripped.startswith(">"):
            stripped = stripped[1:]
        cleaned.append(stripped.rstrip())
    return "\n".join(ln for ln in cleaned if ln)


def _normalize_code(text: str) -> str:
    lines = text.strip().split("\n")
    return "\n".join(line.strip() for line in lines if line.strip())


_TRAILING_LINE_COMMENT_RE = re.compile(
    r"^("
    r'(?:"(?:[^"\\]|\\.)*")'       # double-quoted string
    r"|(?:'(?:[^'\\]|\\.)*')"       # single-quoted string
    r"|(?:`[^`]*`)"                 # backtick string (Go raw / JS template)
    r"|[^\"'`/#]"                   # normal chars
    r")+"                           # greedy: consume all safe content
    r"(\s+//\s.*|\s+#\s.*)$"        # trailing // or # comment
)

_TEMPLATE_COMMENT_RE = re.compile(r"\s*\{\{/\*.*?\*/\}\}\s*$")

# A line whose content is nothing but prose commentary. The space after the
# marker is what separates prose from a directive that is really code —
# `#!/usr/bin/env bash`, `#include`, `//nolint:errcheck` — which is kept, on the
# same rule the trailing form already applies.
_COMMENT_ONLY_RE = re.compile(r"^\s*(?://|#)(?:\s|$)")


def _strip_comments(text: str) -> str:
    """Drop comments from one side of the evidence match.

    A whole-line comment goes entirely: reviewers annotate evidence with lines
    that are not in the file at all, so a comment line is not something the
    match can insist on. A comment on a code line loses only its tail.

    Both sides of the comparison run through this — see `_check_fragments`.
    """
    lines = []
    for line in text.split("\n"):
        line = _TEMPLATE_COMMENT_RE.sub("", line)
        if _COMMENT_ONLY_RE.match(line):
            lines.append("")
            continue
        m = _TRAILING_LINE_COMMENT_RE.match(line)
        if m:
            line = line[:m.start(2)]
        lines.append(line)
    return "\n".join(lines)


def _find_mismatch(norm_frag: str, norm_file: str) -> dict:
    for i in range(len(norm_frag), 0, -1):
        if norm_frag[:i] in norm_file:
            return {"longest_match_prefix": i, "first_mismatch": norm_frag[i:i + 60]}
    return {"longest_match_prefix": 0, "first_mismatch": norm_frag[:60]}


def _check_fragments(evidence: str, file_content: str) -> dict:
    """Match each evidence fragment against the file it was quoted from.

    The two sides are normalized the same way — `_strip_comments` then
    `_normalize_code` — because the comparison is a substring test. Stripping
    only the quote leaves the file holding text the quote no longer has, and a
    verbatim quote then fails to match itself.
    """
    cleaned = _strip_comments(evidence)
    norm_file = _normalize_code(_strip_comments(file_content))
    fragments = re.split(r"(?m)^\s*\.\.\.\s*$", cleaned)
    fragments = [f for f in fragments if f.strip()]
    if not fragments:
        return {"match_result": True}
    result: dict = {"evidence_length": len(evidence), "fragments": len(fragments)}
    for frag in fragments:
        norm_frag = _normalize_code(frag)
        if norm_frag not in norm_file:
            result.update(_find_mismatch(norm_frag, norm_file))
            result["match_result"] = False
            return result
    result["match_result"] = True
    return result


def _match_evidence(path: str, evidence: str | None, wt_path: str) -> dict:
    resolved = Path(wt_path) / path
    detail: dict = {
        "path": path,
        "has_evidence": evidence is not None,
        "file_exists": resolved.exists(),
    }
    if evidence is None or not resolved.exists():
        detail["match_result"] = resolved.exists() and evidence is None
        return detail
    try:
        file_content = resolved.read_text()
    except OSError:
        detail["match_result"] = False
        return detail
    detail.update(_check_fragments(evidence, file_content))
    return detail


def _verify_finding(path: str, evidence: str | None, wt_path: str) -> bool:
    return _match_evidence(path, evidence, wt_path)["match_result"]


# This pattern selects as well as reads: a finding line it does not match is
# appended to the previous finding's body, so the previous finding is then
# evidence-checked against text that is not its own. That is why the
# space-free class stays exactly as it was — anything the delimiters cannot
# hold, line suffix included, which `rsplit` strips below — and why the spaced
# shape is added beside it rather than replacing it. Every location that
# parsed before parses the same way, since a space-free span never reaches
# the second alternative at all.
#
# `_SPACED_FILE` needs the same bound it has in FIRST_FILE_RE, where a
# lookahead makes the filename account for the whole span. Here the closing
# delimiter is that bound: the extension and its optional line suffix have to
# run right up to it, so "the fix lands in v2.0 of the tool" is still no path
# and a greedy space run cannot walk past the real filename.
_VERIFY_FINDING_RE = re.compile(
    r"^- (?:\[ \] )?"
    r"\*\*\[([MSNI])(\d+)\]\*\*"
    r"\s+(?:<!-- sid:\w+ -->\s+)?"
    rf"(?:\*\*[`]?([^`*\s]+?|{_SPACED_FILE}{_LINE_SUFFIX})[`]?\*\*"
    rf"|[`]([^`\s]+?|{_SPACED_FILE}{_LINE_SUFFIX})[`])"
    rf"{_LINE_SUFFIX}"
    r"\s*—\s*(.*)"
)


def _flush_finding(findings: list[dict], current: dict | None, body_lines: list[str]) -> None:
    if not current:
        return
    current["body"] = "\n".join(body_lines)
    findings.append(current)


def _parse_findings_for_verification(text: str) -> list[dict]:
    findings: list[dict] = []
    current: dict | None = None
    body_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        m = _VERIFY_FINDING_RE.match(stripped)
        if m:
            _flush_finding(findings, current, body_lines)
            raw_path = (m.group(3) or m.group(4) or "").replace("\\_", "_")
            path_str = raw_path.rsplit(":", 1)[0] if ":" in raw_path else raw_path
            current = {
                "id": f"{m.group(1)}{m.group(2)}",
                "severity": m.group(1),
                "path": path_str,
                "body": m.group(5),
            }
            body_lines = [m.group(5)] if m.group(5) else []
        elif current and stripped and not stripped.startswith("## "):
            body_lines.append(line.rstrip())
        elif current and stripped.startswith("## "):
            _flush_finding(findings, current, body_lines)
            current = None
            body_lines = []
    _flush_finding(findings, current, body_lines)
    return findings


def _is_next_finding_or_section(stripped: str) -> bool:
    return (
        stripped.startswith("- **[")
        or stripped.startswith("- [ ] **[")
        or stripped.startswith("- [x] **[")
        or stripped.startswith("## ")
    )


def _remove_dropped_findings(text: str, dropped: list[str]) -> str:
    dropped_set = set(dropped)
    kept: list[str] = []
    skip_until_next = False
    for line in text.split("\n"):
        stripped = line.strip()
        m = _VERIFY_FINDING_RE.match(stripped)
        if m:
            skip_until_next = f"{m.group(1)}{m.group(2)}" in dropped_set
        elif skip_until_next and _is_next_finding_or_section(stripped):
            skip_until_next = False
        if skip_until_next:
            continue
        kept.append(line)
    return "\n".join(kept)


def _verification_detail(
    finding: dict, evidence: str | None, wt_path: str,
) -> dict:
    detail = _match_evidence(finding["path"], evidence, wt_path)
    detail["id"] = finding["id"]
    detail["severity"] = finding["severity"]
    return detail


def _drop_reason(detail: dict) -> str:
    reason = "file not found" if not detail["file_exists"] else "evidence mismatch"
    if detail.get("longest_match_prefix") is not None:
        reason += f" at char {detail['longest_match_prefix']}"
    return reason


def verify_findings(text: str, wt_path: str) -> tuple[str, dict]:
    findings = _parse_findings_for_verification(text)
    dropped: list[str] = []
    details: list[dict] = []
    for f in findings:
        if f["severity"] not in (SEVERITY_MUST, SEVERITY_SHOULD):
            continue
        evidence = _extract_evidence(f["body"])
        detail = _verification_detail(f, evidence, wt_path)
        details.append(detail)
        if not detail["match_result"]:
            dropped.append(f["id"])
            log.info(f"Dropping {f['id']} ({f['path']}): {_drop_reason(detail)}")
    result = {
        "findings_checked": len(details),
        "findings_passed": len(details) - len(dropped),
        "findings_dropped": len(dropped),
        "dropped": dropped,
        "details": details,
    }
    if dropped:
        text = _remove_dropped_findings(text, dropped)
    return text, result


_EVIDENCE_BLOCK_START_RE = re.compile(r"^ {2,}>\s*```")
_EVIDENCE_BLOCKQUOTE_RE = re.compile(r"^ {2,}>")


def strip_evidence_blocks(text: str) -> str:
    lines = text.split("\n")
    kept: list[str] = []
    in_evidence = False
    for line in lines:
        is_fence = _EVIDENCE_BLOCK_START_RE.match(line)
        if not in_evidence and is_fence:
            in_evidence = True
            continue
        if in_evidence:
            in_evidence = not is_fence
            continue
        if _EVIDENCE_BLOCKQUOTE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


# ── Stable IDs for carry-forward ─────────────────────────────────────────────

def compute_stable_id(path: str, desc: str) -> str:
    key = f"{path.strip().lower()}:{desc[:80].strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:8]


_ANNOTATE_FINDING_RE = re.compile(
    r"^(- (?:\[ \] )?\*\*\[[A-Z]\d+\]\*\*)\s+"
)


def _extract_finding_path(line: str, after: str) -> str:
    """The file a finding line names, or "" when it names none.

    `_extract_path` answers first because it is the same reading the rest of
    the parser gives a location, and it is the only rung that recognises a
    plain-backtick file with no `:<line>` after it — the shape a review writes
    whenever the finding is about a file rather than a line of one. Reading
    that as no path at all left the finding with no stable ID either, so
    neither carry-forward nor the tree could account for it.

    The two rungs below it are what this function used to be, kept because
    they read locations `_extract_path` declines: a bold span that is a label
    rather than a filename, and a bare `path:12` in no span at all. Identity
    is the point here — a finding whose location is `**Documentation**` still
    has to hash to the same thing across reviews to be carried forward.
    """
    path, _, _ = _extract_path(after)
    if path:
        return path
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


def _parse_ledger(review_text: str) -> list[LedgerEntry]:
    """The entries of the review's prior-findings ledger, in order."""
    section = _extract_section(review_text, SECTION_PRIOR_FINDINGS)
    entries = (_parse_ledger_line(raw) for raw in section.split("\n"))
    return [entry for entry in entries if entry]


def _prior_finding(block: list[str]) -> PriorFinding:
    """One prior finding, read from its own line down to whatever ends it."""
    text: list[str] = []
    for raw in block:
        stripped = raw.strip()
        if text and (_is_next_finding_or_section(stripped) or _is_section_boundary(stripped)):
            break
        text.append(stripped)
    line = text[0]
    m = _ANNOTATE_FINDING_RE.match(line)
    label_m = BOLD_FINDING_ID_RE.search(line)
    return PriorFinding(
        ref=FindingRef(
            label_m.group(1) if label_m else "",
            _extract_finding_path(line, line[m.end():]),
        ),
        stable_id=_finding_stable_id(line, m),
        text="\n".join(text).strip(),
    )


def _parse_prior_findings(prior_text: str) -> list[PriorFinding]:
    """Every finding the prior review reported, each with the text reporting it."""
    lines = prior_text.split("\n")
    starts = [i for i, raw in enumerate(lines) if _ANNOTATE_FINDING_RE.match(raw.strip())]
    ends = [*starts[1:], len(lines)]
    return [_prior_finding(lines[start:end]) for start, end in zip(starts, ends)]


def strip_sections(text: str, headers: Iterable[str]) -> str:
    """Drop whole `## <header>` sections, headings included."""
    excluded = {h.lower() for h in headers}
    kept: list[str] = []
    dropping = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            dropping = stripped[3:].strip().lower() in excluded
        if not dropping:
            kept.append(line)
    return "\n".join(kept)


def strip_stable_ids(text: str) -> str:
    return re.sub(r" <!-- sid:\w+ -->", "", text)


# ── Mechanical verdict and review assembly ──────────────────────────────────

_VERDICT_LABELS = [(s.key, s.label) for s in SEVERITIES]

_MECHANICAL_NOTE = "(mechanically merged, not synthesized)"


def _verdict_from_counts(counts: dict[str, int]) -> ReviewVerdict:
    return ReviewVerdict.from_counts(
        counts.get(SEVERITY_MUST, 0), counts.get(SEVERITY_SHOULD, 0),
    )


def _count_parts(counts: dict[str, int]) -> list[str]:
    return [f"{counts[key]} {label}" for key, label in _VERDICT_LABELS if counts.get(key)]


def mechanical_verdict(counts: dict[str, int]) -> str:
    parts = _count_parts(counts)
    if not parts:
        return f"{ReviewVerdict.APPROVE.prose} — no findings {_MECHANICAL_NOTE}.\n"

    verdict = _verdict_from_counts(counts)
    suffix = " only" if verdict is ReviewVerdict.APPROVE else ""
    return f"{verdict.prose} — {', '.join(parts)}{suffix} {_MECHANICAL_NOTE}.\n"


# ── Reconciling prose against dropped findings ───────────────────────────────

# The synthesis agent writes the Summary and the Verdict before evidence
# verification runs, so both describe findings the drop may since have removed.
# Regenerating them would cost the agent's qualitative assessment, which is the
# part of a review a reader cannot get from counts. So the prose stays and the
# review says what left it, next to the prose that is now wrong.

_DROP_NOTE_MARKER = "<!-- verification-drops -->"


def _section_bounds(text: str, header: str) -> tuple[int, int] | None:
    """Character span of a `## <header>` section's body, heading excluded."""
    m = re.search(rf"^## {re.escape(header)}\s*$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.MULTILINE)
    return start, start + nxt.start() if nxt else len(text)


def _drop_note(details: list[dict], dropped: list[str]) -> str:
    """A blockquote naming every dropped finding and why it went.

    Identified by severity and path rather than by ID: `renumber_findings` runs
    after the drop, so the IDs the verification recorded no longer point at the
    findings that survived.
    """
    dropped_set = set(dropped)
    lines = [
        f"> - {severity_by_key(d['severity']).section} — `{d['path']}`: {_drop_reason(d)}"
        for d in details
        if d["id"] in dropped_set
    ]
    n = len(lines)
    header = f"> **Evidence verification removed {n} finding{plural(n)}:**"
    return "\n".join([_DROP_NOTE_MARKER, header, *lines])


def _insert_drop_note(text: str, note: str) -> str:
    bounds = _section_bounds(text, "Summary")
    if bounds is None:
        # No Summary to correct — the mechanical paths build theirs from this
        # text afterwards, so the note goes above the findings it explains and
        # ends up directly beneath the summary they generate.
        first = re.search(r"^## ", text, re.MULTILINE)
        if first is None:
            return f"{text.rstrip()}\n\n{note}\n"
        return f"{text[:first.start()]}{note}\n\n{text[first.start():]}"

    start, end = bounds
    body = text[start:end].rstrip()
    return f"{text[:start]}{body}\n\n{note}\n\n{text[end:].lstrip(chr(10))}"


def _revise_verdict(text: str, counts: dict[str, int], n_dropped: int) -> str:
    """Lower a verdict the surviving findings no longer support.

    Dropping only ever removes findings, so a stale verdict can only overstate
    — the revision lowers, never raises, and leaves an unranked verdict alone.
    """
    bounds = _section_bounds(text, "Verdict")
    if bounds is None:
        return text

    start, end = bounds
    body = text[start:end]
    stated = ReviewVerdict.stated_in(body)
    supported = _verdict_from_counts(counts)
    if not stated or not stated.outranks(supported):
        return text

    parts = _count_parts(counts)
    remaining = ", ".join(parts) if parts else "no findings"
    revised = (
        f"{supported.prose} — {remaining} after evidence verification removed "
        f"{n_dropped} finding{plural(n_dropped)}.\n"
    )
    return f"{text[:start]}\n{revised}\n{text[end:].lstrip(chr(10))}"


def reconcile_dropped_findings(text: str, verification: dict) -> str:
    """Make a review's prose account for the findings verification removed.

    Runs after renumbering, so the counts it reads are the ones the finished
    file reports.
    """
    dropped = verification.get("dropped") or []
    if not dropped or _DROP_NOTE_MARKER in text:
        return text

    text = _revise_verdict(text, _count_findings(text), len(dropped))
    return _insert_drop_note(text, _drop_note(verification.get("details") or [], dropped))


def post_process_findings(review_file: str, wt_path: str = "") -> dict | None:
    path = Path(review_file)
    # Guard covers all sub-steps (verify, strip, renumber, write) — callers
    # receive None rather than a partial result when the file is missing.
    if not path.exists():
        return None
    text = path.read_text()
    verification: dict | None = None
    if wt_path:
        text, verification = verify_findings(text, wt_path)
        dropped = verification["dropped"]
        if dropped:
            log.info(f"Dropped {len(dropped)} unverified findings: {', '.join(dropped)}")
    text = strip_evidence_blocks(text)
    text = strip_stable_ids(text)
    # Before renumbering: the ledger's IDs number the prior review, so leaving
    # them in would both mislead a reader and skew the renumbering of the
    # findings that are actually in this review.
    text = strip_sections(text, [SECTION_PRIOR_FINDINGS])
    text = renumber_findings(text)
    # After renumbering: the reconciliation reads the counts the finished file
    # reports, and must not describe findings by IDs renumbering has reassigned.
    if verification:
        text = reconcile_dropped_findings(text, verification)
    path.write_text(text)
    return verification


def build_mechanical_review(
    merged_content: str,
    *,
    title: str,
    meta_header: str,
    group_count: int,
    summary_note: str,
    include_verdict: bool = True,
    file_count: int = 0,
    failures_section: str = "",
) -> str:
    counts = _count_findings(merged_content)
    total = sum(counts.values())
    count_summary = f"{total} finding{plural(total)}" if total else "No findings"

    if file_count:
        scope = f"across {file_count} file{plural(file_count)} in {group_count} groups"
    else:
        scope = f"across {group_count} groups"

    failures_block = f"{failures_section}\n" if failures_section else ""

    summary = (
        f"{title}\n{meta_header}\n"
        f"{failures_block}"
        f"## Summary\n"
        f"{count_summary} {scope}. "
        f"{summary_note}\n\n"
        f"{merged_content}\n"
    )
    if not include_verdict:
        return summary
    return f"{summary}\n## Verdict\n{mechanical_verdict(counts)}"
