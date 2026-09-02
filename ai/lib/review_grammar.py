"""The one reading of a finding line: its ID, its location, its identity.

Every regex that parses a finding declaration lives here, and so does the
identity two findings are compared on. Before this module there were eleven
such regexes in five files, and they disagreed: dedup read a path only when it
was bold, while carry-forward read all four shapes and hashed them together, so
a finding written with plain backticks was carried forward and deduplicated
against nothing.

One owner does not mean one pattern. `VERIFY_FINDING_RE` bounds a spaced
filename with the closing delimiter where `_FIRST_FILE_RE` uses a lookahead,
and the two have to keep reading the same line the same way for different
purposes. Both live here so a change to one is made next to the other.

A pattern here is also not always a whole reader. `LINE_SUFFIX` is the `:12` or
`:12-18` several of them match inside a span they capture, and
`strip_line_suffix` is how such a reader takes it back off — each used to
truncate at the last colon instead, which read `ns:module.py` as `ns` and
verified a finding against a file that does not exist. `SEVERITY_KEY` and the
`<!-- sid: -->` marker are the other two: `sid_marker` writes a marker,
`strip_sid_markers` takes it back off and `SID_MARKER_RE` reads the identity
out of one, so a marker a writer emits is one every reader here skips over.

The identity itself is `FindingIdentity`, and the `DedupKey` its `dedup_key`
returns is a pair with names rather than two loose strings, because which half
is the location is not something a call site should have to infer from
position.

What a document is assembled from is `review_document`'s; what a finding means
once parsed is `review_types`'.
"""

# doc-group: findings

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from review_types import (
    SEVERITIES, Finding, FindingLocation, FindingRef, LedgerEntry,
    PriorDisposition, severity_by_key,
)

# ── What a declaration is spelled with ───────────────────────────────────────

# The one character class a finding ID's severity key is read through, derived
# from the severities `review_types` declares rather than spelled out beside
# them. Every reader here matched a key its own way — `[MSNI]` in four,
# `[A-Z]` in two, `\w+` in one — so a key outside the set was a finding ID to
# three of them and not to the rest.
#
# The narrow reading is the right one: `SEVERITIES` says what a severity key
# can be, and a bracketed token whose key is outside it is not a finding ID.
SEVERITY_KEY = f"[{''.join(severity.key for severity in SEVERITIES)}]"

# The severity's human label, as a posted comment carries it after the ID. Read
# off `SEVERITIES` for the same reason `SEVERITY_KEY` is: this is the half of
# the posted spelling the readers below have to recognise, and a label added to
# `review_types` that they do not know is a comment none of them match.
_SEVERITY_LABEL = "|".join(re.escape(severity.label) for severity in SEVERITIES)

# The label as the readers see it, between the ID's `]` and the closing `**`.
# Optional, because a finding wears one tag in the review file and another once
# posted, and a reader of a finding ID has to accept both — that is what the
# posted spelling having no owner cost: `format_inline_comment` wrote the label
# and every reader required the `**` adjacent to the `]`, so a posted comment
# matched nothing and the thread-state annotation downstream never ran.
_POSTED_LABEL = rf"(?:\s+\[(?:{_SEVERITY_LABEL})\])?"

# The marker a carried-forward finding keeps between its ID and its location,
# holding the `FindingIdentity.stable_id` that lets a later review recognise it.
# The literal is written once here and reached through the pieces below: a
# marker written in a shape the readers do not skip is a finding whose location
# parses as the marker itself, and one a stripper misses ships to the reader.
_SID_OPEN = "<!-- sid:"
_SID_CLOSE = " -->"

# The marker with its ID captured, for a reader that wants the identity back.
SID_MARKER_RE = re.compile(rf"{_SID_OPEN}(\w+){_SID_CLOSE}")

# The marker as every reader of a declaration head sees it: optional, and
# taking the whitespace after it, because what follows is the location.
_SID_PREFIX = rf"(?:{_SID_OPEN}\w+{_SID_CLOSE}\s+)?"

# The marker with the space that precedes it. `sid_marker` writes that space and
# `strip_sid_markers` takes it back off, so a round trip leaves the line exactly
# as it was rather than a space wider each time.
_SID_SPACED_RE = re.compile(rf" {_SID_OPEN}\w+{_SID_CLOSE}")


def sid_marker(stable_id: str) -> str:
    """The stable-ID marker for `stable_id`, including the space before it.

    What a writer appends to the head of a finding line. The space is part of
    what this returns because it is part of what `strip_sid_markers` removes.
    """
    return f" {_SID_OPEN}{stable_id}{_SID_CLOSE}"


def has_sid_marker(text: str) -> bool:
    """Whether `text` already carries a stable-ID marker.

    An opening test rather than a whole-marker match: a writer asking this
    wants to know whether to write another, and a half-written marker is still
    one it must not double.
    """
    return _SID_OPEN in text


def strip_sid_markers(text: str) -> str:
    """`text` with every stable-ID marker gone, the space before it included.

    The markers are a handle on a finding's identity rather than part of what it
    says, so a document on its way to a reader loses them. The one exception is
    a posted inline comment, which keeps its marker so a later round can find
    the thread again — GitHub renders an HTML comment as nothing, and the reader
    sees the same words either way. `review_dedup` strips it back off before
    scoring, since the fresh finding it is compared against carries none.
    """
    return _SID_SPACED_RE.sub("", text)


# ── The head of a declaration ────────────────────────────────────────────────

# The head of a finding declaration: a list item opening with the bold ID,
# carrying the fix pass's checkbox and a resolved finding's strikethrough when
# it has them, and the stable-ID marker a carried finding keeps. What follows
# the match is the location and the body.
FINDING_ID_RE = re.compile(
    r"^- (?:\[([ x])\] )?"
    r"(?:~~)?"
    rf"\*\*\[({SEVERITY_KEY})(\d+)\](?:\*\*)?"
    r"\s+"
    rf"{_SID_PREFIX}"
)

# A finding's ID wherever it appears, in either spelling: a review file's
# declaration or reference, and a posted comment's labelled tag.
BOLD_FINDING_ID_RE = re.compile(rf"\*\*\[({SEVERITY_KEY}\d+)\]{_POSTED_LABEL}\*\*")

# A declaration the review struck through, which is how it says the finding was
# resolved. It ends the body above it and opens nothing.
STRIKETHROUGH_RE = re.compile(r"^- ~~\*\*\[")

# The head as the stable-ID annotator requires it, with the head itself
# captured so a `<!-- sid: -->` marker can be written after it. Stricter than
# `FINDING_ID_RE` on purpose: the closing `**` is required and a checked or
# struck-through finding is passed over, because carry-forward is about
# findings still open. The marker is deliberately outside the match — the
# annotator asks whether the line already carries one before it writes another.
ANNOTATE_FINDING_RE = re.compile(
    rf"^(- (?:\[ \] )?\*\*\[{SEVERITY_KEY}\d+\]\*\*)\s+"
)


def finding_tag(posted_id: str) -> str:
    """The bold tag a review file's finding opens with.

    The plainer of the two spellings, and the one every reader above was
    written for.
    """
    return f"**[{posted_id}]**"


def posted_finding_tag(posted_id: str, severity: str) -> str:
    """The bold tag a posted comment's finding opens with, severity label included.

    The other spelling, and the reason it is written here rather than where it
    is posted from: it was spelled at two sites in `review_format` and read by
    nothing that understood it, so the label the writer added was the label
    every reader tripped over. `BOLD_FINDING_ID_RE` and `BODY_FINDING_RE` accept
    what this emits, and the label comes from the same `SEVERITIES` those
    patterns are built from, so a new severity reaches the writer and the
    readers together.
    """
    return f"**[{posted_id}] [{severity_by_key(severity).label}]**"


# ── The location a declaration names ─────────────────────────────────────────

_PATH_SECTION_RE = re.compile(
    r"\*\*`(.+?)`\*\*"
    r"|"
    r"\*\*(.+?)\*\*"
    r"|"
    r"`(.+?)`"
)
# What a path may hold, now that `_PATH_SECTION_RE` has already delimited the
# span. The class says what a path is not: whitespace, the `:` that introduces
# the line suffix, the `*` and backtick that close the span, and the em dash
# that separates a location from its body. Everything else is an ordinary
# filename character — non-ASCII included, because `src/café.py` names a file
# the same way `src/cafe.py` does.
_PATH_CHAR = r"[^\s:*`—]"
_SEGMENT_CHAR = r"[^\s/:*`—]"

# The `:12` or `:12-18` a location may carry after its filename. A fragment
# rather than a reader: `_FIRST_FILE_RE`, `VERIFY_FINDING_RE` and
# `BODY_FINDING_RE` each bound it differently, and they have to agree on what a
# line suffix is or a finding parses one way and verifies against the other.
# `strip_line_suffix` removes what this matches, so a reader that matched one
# without capturing it does not decide for itself what it was.
_LINE_SUFFIX_BODY = r":\d+(?:[-–]\d+)?"
LINE_SUFFIX = rf"(?:{_LINE_SUFFIX_BODY})?"
_LINE_SUFFIX_TAIL_RE = re.compile(rf"{_LINE_SUFFIX_BODY}$")


def strip_line_suffix(path: str) -> str:
    """`path` with a trailing `:12` or `:12-18` removed, and nothing else.

    For a reader whose pattern matched `LINE_SUFFIX` inside the span it
    captured, so the path it hands back still carries it.

    Only a line suffix comes off. Truncating at the last colon instead — which
    is what each such reader used to do for itself — turns `C:/src/x.py` into
    `C` and `ns:module.py` into `ns`, because neither reader's path class
    excludes a colon. That is the same one line read two ways this module
    exists to prevent, one layer below the location grammar.
    """
    return _LINE_SUFFIX_TAIL_RE.sub("", path)


# A filename holding spaces. It has no character class to stop it, so every
# use has to bound it: the extension ends it, and whatever follows has to be
# the end of the span it was found in.
SPACED_FILE = rf"{_PATH_CHAR}+(?: {_PATH_CHAR}+)+\.\w+"

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
_FIRST_FILE_RE = re.compile(
    r"("
    rf"{_PATH_CHAR}+\.\w+"
    r"|"
    rf"{SPACED_FILE}(?={LINE_SUFFIX}\s*$)"
    r"|"
    rf"{_SEGMENT_CHAR}+(?:/{_SEGMENT_CHAR}+)+"
    r")"
    r"(?::(\d+)(?:[-–](\d+))?)?"
)


def finding_location(after_id: str) -> FindingLocation:
    """The location a finding line names, having had its ID stripped.

    An unnamed `FindingLocation` when the line names none — `named` is the
    question to ask rather than the emptiness of `path`.

    The location has to open the remainder of the line, in a bold or backtick
    span: a path mentioned mid-sentence is the finding's prose rather than its
    location, and reading one as the other points the posted comment at a file
    the finding only mentions.
    """
    section_match = _PATH_SECTION_RE.match(after_id)
    if not section_match:
        return FindingLocation()
    section_text = section_match.group(1) or section_match.group(2) or section_match.group(3) or ""
    section_text = section_text.replace("\\_", "_")
    file_match = _FIRST_FILE_RE.match(section_text)
    if not file_match:
        return FindingLocation()
    return FindingLocation(
        path=file_match.group(1).strip(),
        line=int(file_match.group(2)) if file_match.group(2) else None,
        end_line=int(file_match.group(3)) if file_match.group(3) else None,
    )


# ── The body below the location ──────────────────────────────────────────────

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
_DECLINED_HEAD_RE = re.compile(rf"^{_DECLINED}", re.IGNORECASE)
_DECLINED_TAIL_RE = re.compile(rf"{_DECLINED}\s*$", re.IGNORECASE)


def _match_decline(body: str, line: str) -> re.Match[str] | None:
    """The decline annotation a finding line carries, if it carries one."""
    return _DECLINED_HEAD_RE.match(body) or _DECLINED_TAIL_RE.search(line)


def _extract_body_text(line: str) -> str:
    em_dash_pos = line.find("—")
    if em_dash_pos != -1:
        return line[em_dash_pos + 1:].strip()
    for sep in (" -- ", " - "):
        pos = line.find(sep)
        if pos != -1:
            return line[pos + len(sep):].strip()
    return ""


def parse_finding_line(stripped: str) -> Finding | None:
    """The finding `stripped` declares, or None when it declares none.

    For a caller holding one line that is not in a findings section — the
    prior-findings ledger writes finding lines under its own heading. A caller
    after the findings of a whole review asks `ReviewDocument.findings`, which
    is the reading that knows which headings declare findings.

    The `stable_id` is set here rather than derived from the finding returned,
    because `finding_spans` replaces `body` with the whole multi-line span and
    the identity hashes the declaration line's own wording.
    """
    id_match = FINDING_ID_RE.match(stripped)
    if not id_match:
        return None
    checkbox = id_match.group(1)
    sev = id_match.group(2)
    seq = int(id_match.group(3))
    after_id = stripped[id_match.end():]
    location = finding_location(after_id)
    body = _extract_body_text(stripped) if location.named else after_id.strip()
    declined = _match_decline(body, stripped)
    identity = FindingIdentity.of(stripped)
    return Finding(
        id=f"{sev}{seq}", severity=sev, seq=seq,
        path=location.path, line=location.line, end_line=location.end_line,
        body=body,
        stable_id=identity.stable_id if identity else "",
        checked=(checkbox is not None and checkbox.lower() == "x"),
        declined=declined is not None,
        decline_reason=(declined.group(1) or "").strip() if declined else "",
    )


def parse_ledger_line(raw: str) -> LedgerEntry | None:
    """The entry a ledger line carries, or None when it names no finding.

    The grammar of one ledger line, exactly as `parse_finding_line` is the
    grammar of one finding line — a ledger entry is a finding line whose body
    is read as a verdict instead of a description.
    """
    parsed = parse_finding_line(raw.strip())
    if not parsed:
        return None
    return LedgerEntry(
        ref=FindingRef(parsed.id, parsed.path),
        disposition=PriorDisposition.parse(parsed.body),
        text=raw,
    )


# ── Readers with bounds of their own ─────────────────────────────────────────

# This pattern selects: which findings the evidence gate checks, and the
# location it checks each one against. Where a finding's body ends is not its
# business — `finding_spans` measures that, so a line this pattern cannot read
# ends the span above it instead of joining that finding's evidence.
#
# The space-free class stays exactly as it was — anything the delimiters cannot
# hold, line suffix included, which `strip_line_suffix` takes off the captured
# path — and the spaced shape is beside it rather than replacing it. Every
# location that parsed before parses the same way, since a space-free span
# never reaches the second alternative at all.
#
# `SPACED_FILE` needs the same bound it has in `_FIRST_FILE_RE` above, where a
# lookahead makes the filename account for the whole span. Here the closing
# delimiter is that bound: the extension and its optional line suffix have to
# run right up to it, so "the fix lands in v2.0 of the tool" is still no path
# and a greedy space run cannot walk past the real filename.
VERIFY_FINDING_RE = re.compile(
    r"^- (?:\[ \] )?"
    rf"\*\*\[({SEVERITY_KEY})(\d+)\]\*\*"
    rf"\s+{_SID_PREFIX}"
    rf"(?:\*\*[`]?([^`*\s]+?|{SPACED_FILE}{LINE_SUFFIX})[`]?\*\*"
    rf"|[`]([^`\s]+?|{SPACED_FILE}{LINE_SUFFIX})[`])"
    rf"{LINE_SUFFIX}"
    r"\s*—\s*(.*)"
)

# A declaration whose location names a line of a file, with the path captured.
# What a scoped re-review filters the prior review's findings on: a finding
# about a file rather than a line of one names no line to keep it against, so
# the `:\d+` is required here where every other reader has it optional.
SCOPED_FINDING_RE = re.compile(
    r"- (?:\[[ x]\] )?"                       # optional checkbox
    rf"\*\*\[{SEVERITY_KEY}\d+\]\*\*"          # finding ID
    rf"\s+{_SID_PREFIX}"                       # optional stable ID
    r"(?:\*\*)?[`]?(\S+?)[`]?(?:\*\*)?:\d+"   # path with optional bold/backtick wrapping
)

# A declaration as it reads once posted, in the body of a review comment rather
# than in the review file: no checkbox, since the fix pass has not seen it, the
# severity label the posted spelling carries, and the path and the body captured
# so a finding about to be posted can be matched against one already there.
# Scanned with `finditer` over a whole comment body, which is why it is the one
# reader here carrying `re.MULTILINE`.
BODY_FINDING_RE = re.compile(
    rf"^- \*\*\[{SEVERITY_KEY}\d+\]{_POSTED_LABEL}\*\*\s+"
    r"(?:\*\*`?([^`*\s]+?)`?\*\*|`([^`\s]+?)`)"
    rf"{LINE_SUFFIX}"
    r"\s*—\s*(.*)",
    re.MULTILINE,
)

# A file-triage line: the file a group looked at, in backticks, and its note on
# what it found there. The note is what makes it a triage entry rather than a
# bare bullet, so the trailing space is part of the shape; the path is captured
# because two groups reporting on one file report it once.
TRIAGE_LINE_RE = re.compile(r"^- `([^`]+)`\s")


# ── The identity two findings are compared on ────────────────────────────────

_FINDING_PATH_RE = re.compile(
    rf"^- (?:\[ \] )?\*\*\[{SEVERITY_KEY}\d+\]\*\*"
    rf"\s+{_SID_PREFIX}"
    r"\*\*(?:`([^`]+)`|([^*]+))\*\*"
)
_FINDING_DESC_RE = re.compile(r"—\s*(.{0,80})")


def _fallback_location(line: str, after: str) -> tuple[str, int | None]:
    """The two readings `finding_location` declines: a bold label, and a bare `path:12`.

    A finding whose location is `**Documentation**` is not a file, but it still
    has to hash to the same thing across reviews to be carried forward.
    """
    path_m = _FINDING_PATH_RE.match(line)
    if path_m:
        raw = (path_m.group(1) or path_m.group(2) or "").replace("\\_", "_").strip()
        head, _, tail = raw.rpartition(":")
        return (head, int(tail)) if head and tail.isdigit() else (raw, None)
    bare = re.match(r"[`]?(\S+?)[`]?:(\d+)", after)
    return (bare.group(1), int(bare.group(2))) if bare else ("", None)


@dataclass(frozen=True)
class DedupKey:
    """What two findings have to share to be the same finding.

    `located` is the path with the line number back on it when the finding
    named one, `desc` is the finding's first words lowercased. Frozen so it can
    key the dict that finds the repeats.

    A pair rather than the two strings loose: a caller that unpacked them by
    position had to know which one was the location, and the reading that
    decides whether a finding ships twice is not one to leave to the order the
    fields happen to be in.
    """

    located: str
    desc: str


@dataclass(frozen=True)
class FindingIdentity:
    """What makes two findings the same one: where they point and what they say.

    `path` is stripped of any `:line` suffix and `line` is kept beside it,
    because the two readings this type replaces disagreed on that point.
    `stable_id` hashes the path alone, so a finding survives the code moving
    down the file; `dedup_key` puts the line back, so two findings about two
    lines of one file stay two findings.
    """

    path: str
    line: int | None
    desc: str

    @classmethod
    def of(cls, text: str) -> "FindingIdentity | None":
        """The identity of the finding `text` declares, or None if it declares none.

        `finding_location` answers first because it is the same reading the
        rest of the parser gives a location, and it is the only rung that
        recognises a plain-backtick file with no `:<line>` after it — the shape
        a review writes whenever the finding is about a file rather than a line
        of one. `_fallback_location` is what reads the two shapes it declines.
        """
        m = ANNOTATE_FINDING_RE.match(text)
        if not m:
            return None
        after = text[m.end():]
        location = finding_location(after)
        path, line = location.path, location.line
        if not path:
            path, line = _fallback_location(text, after)
        if not path:
            return None
        desc_m = _FINDING_DESC_RE.search(text)
        return cls(path, line, desc_m.group(1).strip() if desc_m else "")

    @property
    def dedup_key(self) -> DedupKey:
        """A `DedupKey` of where the finding points and what it says lowercased.

        `line is not None` rather than a truth test: line 0 is not a line a
        review writes, but reading it as no line at all would key such a
        finding on its bare path and merge it with a real one.

        A range keys on where it starts, since `end_line` is not part of the
        identity: `x.py:12-18` and `x.py:12-40` are two groups disagreeing
        about how far one problem reaches, not two problems.
        """
        located = f"{self.path}:{self.line}" if self.line is not None else self.path
        return DedupKey(located, self.desc.lower())

    @property
    def stable_id(self) -> str:
        """The eight hex digits a `<!-- sid: -->` marker carries."""
        key = f"{self.path.strip().lower()}:{self.desc[:80].strip().lower()}"
        return hashlib.sha256(key.encode()).hexdigest()[:8]
