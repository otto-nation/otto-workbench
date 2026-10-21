"""The posted-comment wire format, and the identity two findings are compared on.

A finding posted to GitHub carries its tag (`finding_tag`, `posted_finding_tag`)
and a `<!-- sid: -->` marker (`sid_marker`). `reply_threads` reads them back off
the PR (`SID_MARKER_RE`, `BOLD_FINDING_ID_RE`), and `review.dedup` reads a
posted body (`BODY_FINDING_RE`, `strip_sid_markers`, `strip_line_suffix`).
`location_ref` is how both `review.md` and the comment spell a location.

`FindingIdentity` is what makes two findings the same one: built from a
finding's fields (`of_fields`), it owns the `DedupKey` merge deduplicates on and
the stable ID a sid marker carries. The stable ID hashes the path and the
body's first eighty characters, so an anchored finding keeps the sid its
rendered line hashed to before agents wrote JSON.

Agent output is not parsed here — agents write JSON (`review.artifacts`).
"""

# doc-group: findings

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from review.types import (
    SEVERITIES, severity_by_key,
)

# ── What a declaration is spelled with ───────────────────────────────────────

# The one character class a finding ID's severity key is read through, derived
# from the severities `review.types` declares rather than spelled out beside
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
# `review.types` that they do not know is a comment none of them match.
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


def strip_sid_markers(text: str) -> str:
    """`text` with every stable-ID marker gone, the space before it included.

    The markers are a handle on a finding's identity rather than part of what it
    says, so a document on its way to a reader loses them. The one exception is
    a posted inline comment, which keeps its marker so a later round can find
    the thread again — GitHub renders an HTML comment as nothing, and the reader
    sees the same words either way. `review.dedup` strips it back off before
    scoring, since the fresh finding it is compared against carries none.
    """
    return _SID_SPACED_RE.sub("", text)


# ── The head of a declaration ────────────────────────────────────────────────

# A finding's ID wherever it appears, in either spelling: a review file's
# declaration or reference, and a posted comment's labelled tag.
BOLD_FINDING_ID_RE = re.compile(rf"\*\*\[({SEVERITY_KEY}\d+)\]{_POSTED_LABEL}\*\*")


def finding_tag(posted_id: str) -> str:
    """The bold tag a review file's finding opens with.

    The plainer of the two spellings, and the one every reader above was
    written for.
    """
    return f"**[{posted_id}]**"


def posted_finding_tag(posted_id: str, severity: str) -> str:
    """The bold tag a posted comment's finding opens with, severity label included.

    The other spelling, and the reason it is written here rather than where it
    is posted from: it was spelled at two sites in `review.format` and read by
    nothing that understood it, so the label the writer added was the label
    every reader tripped over. `BOLD_FINDING_ID_RE` and `BODY_FINDING_RE` accept
    what this emits, and the label comes from the same `SEVERITIES` those
    patterns are built from, so a new severity reaches the writer and the
    readers together.
    """
    return f"**[{posted_id}] [{severity_by_key(severity).label}]**"


def location_ref(path: str, line: int | None, end_line: int | None) -> str:
    """A finding's location as one backtick span: `path`, `path:12` or `path:12-18`.

    The one spelling both views use: `review.md` writes it before a finding's
    body and the posted comment writes it before a body-routed finding.
    """
    ref = path
    if line is not None:
        ref += f":{line}"
        if end_line is not None:
            ref += f"-{end_line}"
    return f"`{ref}`"


# ── The location a declaration names ─────────────────────────────────────────

# The `:12` or `:12-18` a location may carry after its filename. A fragment
# rather than a reader: `BODY_FINDING_RE` bounds it inside a posted declaration,
# and `strip_line_suffix` removes what this matches.
# A list is one suffix, not several: `:64,82` names two lines of one file, and
# a reader that takes only the first leaves `,82` on the path.
_LINE_SPAN = r"\d+(?:[-–]\d+)?"
_LINE_SUFFIX_BODY = rf":{_LINE_SPAN}(?:\s*,\s*{_LINE_SPAN})*"
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


# The caveat a landed-but-unverifiable fix carries, in the same shape. Anchored
# to the tail for the same reason: matched anywhere, a finding whose prose
# quotes the annotation would read as already carrying one.
UNVERIFIED_TAIL_RE = re.compile(r"\*\(unverified(?:\s*[—–-]+\s*(.+?))?\)\*\s*$")


# ── Readers with bounds of their own ─────────────────────────────────────────

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


# ── The identity two findings are compared on ────────────────────────────────

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
    def of_fields(cls, path: str, line: int | None, body: str) -> "FindingIdentity":
        """The identity of a finding held as fields rather than as a line.

        `desc` is what `of` captured after a declaration's dash: the body's first
        line, leading space dropped, cut to eighty characters. Anchored findings
        therefore keep the stable ID their rendered line hashed to, which is
        the sid a posted comment already carries. A finding with no file hashes
        with an empty path.
        """
        first = body.split("\n", 1)[0].lstrip()
        return cls(path, line, first[:80].strip())

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
