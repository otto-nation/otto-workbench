"""`review.md` — the artifact the review subsystem exists to produce.

A review document is a title, a block of HTML-comment metadata recording what
produced it, and a body. This module owns all three: the keys the header may
state, the sentence the title is, and where one ends and the next begins. A
writer that assembles those itself is stating the document's format a second
time, and the second statement is the one that drifts.

Three things write a header and they do not write the same one. The pipeline
renders the full block; `review-rebuild` renders the subset a `meta.json` can
attest to; and on the synthesis and single-agent paths the review agent writes
its own, following prose in its template. `ReviewHeader.parse` therefore reads
keys wherever they appear and in whatever order, and `set_status` edits the line
it is asked about rather than re-rendering the block — a field this module was
never told about survives an edit instead of being dropped by it.

`ReviewDocument` is for a document being *built*: it renders the canonical form
this module defines. Editing one that is already on disk is a different job and
stays a text edit, because a header read back off disk states only what its
writer chose to state — re-rendering it would add this module's defaults as
claims the original never made. `set_status` and `set_section` are what such an
edit asks instead, and `section_span` is what they are built on: the offsets of
the section being rewritten, leaving every byte outside them alone. A pipeline
that appends a section after the review is written — the Agent Failures table,
the static-analysis report — states which section it is and what goes in it,
and this module decides where that lands.

Reading a document is the same one owner from the other side. What a review
says — which sections it carries, which findings it declares, how many of each
severity, and the call it reached — is answered off the parsed document rather
than by a regex each caller brings, so two readers of one review cannot report
different things about it.

A finding declaration is part of that format, so the grammar of one lives here
too: the ID at the head of a list item, the location after it, the body after
that, and the annotations a later pass writes onto it — declined, skipped.
`parse_finding_line` is for a caller holding a single line that is not in a
findings section — the prior-findings ledger is the one — and every other
reader asks `ReviewDocument.findings`.

Where that body stops is the same one owner. `ends_finding_body` is the answer
and `finding_spans` is the traversal built on it, so a reader walking a review
a finding at a time gets the same line ranges wherever it walks from.
`drop_findings` is what an editing caller asks instead: the gates that trim a
finished review remove spans this module measured rather than lines each of
them recognised, because two gates that disagreed about where a body ended cut
one review two different ways — one of them swallowing the resolved finding
below the one it was told to drop.

Counting them is that same parse, not a second grammar over the same text:
`open_counts` tallies `open_findings`, so which findings a review is reported
to have and how many it is reported to have are one answer. A tally written as
its own regex is how a review came to report four findings it had none of —
the ledger's lines look like declarations from anywhere but inside the parse.

`build_mechanical_body` is the document this module writes rather than reads:
the whole body a review has when no synthesis agent produced one. It belongs
beside the format it renders for the same reason the readers do — a summary,
findings and a verdict assembled anywhere else would be the canonical form
stated twice.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from agent_types import Mode
from pr_domains import ReviewStatus, ReviewVerdict
from review_types import (
    SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD, Finding, ReviewMeta, ReviewType,
    meta_enum,
)
from text import plural

# The headers a review is written with. `Summary` and `Verdict` are the two this
# module reads by name; the rest are here because they name sections of the same
# document, and a header spelled in the module that reads it and again in the one
# that writes it is two owners of one string. Any other header is the caller's
# own vocabulary, passed to `section` as a string.
SECTION_SUMMARY = "Summary"
SECTION_VERDICT = "Verdict"
SECTION_FILE_TRIAGE = "File Triage"
SECTION_STATIC_ANALYSIS = "Static Analysis"

# A re-review's ledger: one line per prior finding, saying whether the change
# resolved it. Reconciliation reads it to tell a finding the re-review dropped
# on purpose from one it lost track of; it is stripped before the review is
# posted, since its finding IDs number the prior review, not this one.
SECTION_PRIOR_FINDINGS = "Prior findings"


class MetaKey(StrEnum):
    """A key in a review document's metadata header.

    The values are the strings on disk. They stay as they are so a review
    written by an earlier version still parses, and so the templates that ask
    the review agent for `<!-- date: ... -->` keep naming a key this reads.
    """

    DATE = "date"
    HEAD_SHA = "head_sha"
    REVIEW_TYPE = "review_type"
    PRIOR_SHA = "prior_sha"
    PRIOR_DATE = "prior_date"
    DELTA_FILES = "delta_files"
    SKIPPED_GROUPS = "skipped_groups"
    STATUS = "status"
    GENERATOR = "generator"


_LINE_RE = re.compile(r"<!--\s*([a-z_]+):\s*(.*?)\s*-->")
_STATUS_RE = re.compile(rf"<!--\s*{MetaKey.STATUS}:[^>]*-->")


def _line(key: MetaKey, value: object) -> str:
    return f"<!-- {key}: {value} -->"


def _int(value: str | None) -> int | None:
    """`value` read as an int, or None when it is absent or not a number."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ReviewHeader:
    """What a review document's metadata header states.

    `skipped_groups` and `total_groups` are one line on disk — `2/7` — and are
    written only when the caller has a group count to report.
    """

    date: str = ""
    head_sha: str = ""
    review_type: ReviewType = ReviewType.FULL
    prior_sha: str = ""
    prior_date: str = ""
    delta_files: int | None = None
    skipped_groups: int = 0
    total_groups: int = 0
    status: ReviewStatus | None = None
    generator_version: str = ""

    def render(self) -> str:
        """The header as it goes at the top of the document, newline-terminated.

        A field left at its default is omitted rather than written empty, so a
        full review carries no incremental keys and a run still in flight
        carries no status. `review_type` is the exception every writer states:
        an absent one reads back as `full`, which is a claim about the review
        rather than a gap in the record, so it is always written.
        """
        ratio = f"{self.skipped_groups}/{self.total_groups}" if self.total_groups else None
        lines: list[tuple[MetaKey, object | None]] = [
            (MetaKey.DATE, self.date or None),
            (MetaKey.HEAD_SHA, self.head_sha or None),
            (MetaKey.REVIEW_TYPE, self.review_type),
            (MetaKey.PRIOR_SHA, self.prior_sha or None),
            (MetaKey.PRIOR_DATE, self.prior_date or None),
            (MetaKey.DELTA_FILES, self.delta_files),
            (MetaKey.SKIPPED_GROUPS, ratio),
            (MetaKey.STATUS, self.status.value if self.status else None),
            (MetaKey.GENERATOR, self.generator_version or None),
        ]
        return "".join(f"{_line(k, v)}\n" for k, v in lines if v is not None)

    @classmethod
    def from_meta(cls, meta: ReviewMeta, **overrides) -> ReviewHeader:
        """The header `meta` attests to, with `overrides` written over the top.

        The sidecar and the document header record the same review, so what the
        two share is read from the sidecar rather than assembled a second time
        from whatever the caller happens to be holding. A writer that reaches
        past this is stating the review's attribution twice, and the second
        statement is the one that drifts.

        Left to the caller is everything `meta.json` does not know: the date the
        document is being written, the prior review's own date — which only that
        document records — the group ratio, and how the run ended.
        """
        incremental = meta.review_type == ReviewType.INCREMENTAL
        return replace(cls(
            head_sha=meta.head_sha,
            review_type=meta.review_type or ReviewType.FULL,
            prior_sha=meta.prior_sha,
            # A count only an incremental review has: on a full one the sidecar
            # records no delta, and a `0` here would read as a delta of nothing
            # rather than as the absence of one.
            delta_files=len(meta.delta_files) if incremental else None,
            generator_version=meta.generator_version,
        ), **overrides)

    @classmethod
    def parse(cls, text: str) -> ReviewHeader:
        """The header `text` states, with every key it omits left at its default.

        Keys are read wherever they appear and in whichever order, because the
        review agent writes its own header from a template's prose rather than
        from `render`. The first occurrence of a key wins.

        A value that will not convert reads as absent. A header is a record of
        a run that already happened; one garbled field is not a reason to
        discard what the rest of it says.
        """
        found: dict[str, str] = {}
        for match in _LINE_RE.finditer(text):
            found.setdefault(match.group(1), match.group(2))
        skipped, _, total = found.get(MetaKey.SKIPPED_GROUPS, "").partition("/")
        return cls(
            date=found.get(MetaKey.DATE, ""),
            head_sha=found.get(MetaKey.HEAD_SHA, ""),
            review_type=meta_enum(ReviewType, found.get(MetaKey.REVIEW_TYPE)) or ReviewType.FULL,
            prior_sha=found.get(MetaKey.PRIOR_SHA, ""),
            prior_date=found.get(MetaKey.PRIOR_DATE, ""),
            delta_files=_int(found.get(MetaKey.DELTA_FILES)),
            skipped_groups=_int(skipped) or 0,
            total_groups=_int(total) or 0,
            status=meta_enum(ReviewStatus, found.get(MetaKey.STATUS)),
            generator_version=found.get(MetaKey.GENERATOR, ""),
        )


def set_status(content: str, status: ReviewStatus) -> str:
    """`content` with its header stating `status`, given one if it stated none.

    An edit rather than a re-render, because the header on disk may be the
    review agent's: rendering a fresh block over it would drop whichever keys
    the agent wrote and this caller does not hold.

    An existing status is replaced rather than left alone — `completed` is
    written before the disprove gate has had its say and has to become
    `partial` when the gate fails. A header with no status line takes one above
    its generator line, and a document with neither takes one above its first
    section heading.
    """
    line = _line(MetaKey.STATUS, status.value)
    if _STATUS_RE.search(content):
        return _STATUS_RE.sub(line, content, count=1)
    generator = f"<!-- {MetaKey.GENERATOR}:"
    if generator in content:
        return content.replace(generator, f"{line}\n{generator}", 1)
    return content.replace("## ", f"{line}\n\n## ", 1)


@dataclass(frozen=True)
class SectionSpan:
    """Where a section sits in the text it was found in.

    Offsets into that text, in the order they appear: the heading, the body it
    introduces, and the byte after the body. `text[span.start:span.end]` is the
    section's contents and `text[span.heading_start:span.end]` is the whole
    section, heading included — which is the slice an edit that removes or
    replaces one has to name.
    """

    heading_start: int
    start: int
    end: int

    def body_of(self, text: str) -> str:
        """The section's contents, verbatim — leading and trailing blank lines
        included, since an edit rewriting the section has to know what it is
        replacing."""
        return text[self.start:self.end]


def section_span(text: str, header: str) -> SectionSpan | None:
    """Where `text`'s `## <header>` section sits, or None when it has none.

    The body opens at the end of the heading line and closes at the next `## `
    or the end of the text, so the slice `body_of` names is the section's
    contents with the heading excluded and the blank lines around it intact.

    The one owner of where a section begins and ends. A reader after the
    contents asks `ReviewDocument.section`; an edit rewriting one section of a
    document already on disk asks here or `set_section`, because parsing and
    re-rendering that document would restate a header its writer never wrote.
    Headers are matched case-insensitively: the review agent writes its own, and
    `## Must Fix` names the same section as `## Must fix`.
    """
    m = re.search(rf"^## {re.escape(header)}\s*$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.MULTILINE)
    return SectionSpan(m.start(), start, start + nxt.start() if nxt else len(text))


def set_section(content: str, header: str, body: str, *, before: str = "") -> str:
    """`content` with its `## <header>` section stating `body`, added if absent.

    `body` is the section's contents without its heading — this owns the
    heading, so a caller that renders one is spelling the document's format a
    second time. An empty `body` removes the section, which is how a rerun that
    fixed what failed drops the report of it.

    A section already there is replaced where it stands. One that is not goes
    above the `## <before>` section when the document has one, and at the end
    when it does not — a document missing the anchor still receives the section
    rather than silently discarding it, which is what a caller asking for a
    section to be set means either way.
    """
    body = body.strip()
    section = f"## {header}\n\n{body}\n\n" if body else ""
    span = section_span(content, header)
    if span:
        return content[:span.heading_start] + section + content[span.end:]
    if not section:
        return content
    anchor = section_span(content, before) if before else None
    if anchor:
        return content[:anchor.heading_start] + section + content[anchor.heading_start:]
    preceding = f"{content.rstrip()}\n\n" if content.strip() else ""
    return f"{preceding}{section.rstrip()}\n"


def strip_sections(text: str, headers: Iterable[str]) -> str:
    """`text` with each `## <header>` section in `headers` dropped, heading included.

    For a caller holding a document written for one purpose and reading it for
    another: the prompt that shows a re-review its predecessor drops the
    sections that are bookkeeping rather than reviewer claims, and the prior
    findings ledger is stripped before a review is published because its IDs
    number the previous review.

    Every occurrence of a header goes, not only the first — a document assembled
    from group outputs carries one `## File Triage` per group until the merge
    has run.
    """
    for header in headers:
        while (span := section_span(text, header)) is not None:
            text = text[:span.heading_start] + text[span.end:]
    return text


def review_title(meta: ReviewMeta) -> str:
    """The `# ...` line a review opens with, as the attribution in `meta` reads.

    Derived from the sidecar rather than from whatever the caller is holding, so
    the pipeline, the mechanical fallback and `review-rebuild` cannot name the
    same review three ways. A self-review has no PR to number and is titled by
    the branch it covers instead.

    A sidecar that numbers no PR is named by its repository alone — `#None` is
    a number the review does not have, and stating it is worse than saying
    nothing.
    """
    if meta.mode == Mode.SELF:
        return f"# Self-Review: {meta.repo} — {meta.head_ref or 'unknown'}"
    number = f"#{meta.pr_number}" if meta.pr_number is not None else ""
    title = f"# Review: {meta.repo}{number}"
    return f"{title} — {meta.title}" if meta.title else title


# ── Finding lines ────────────────────────────────────────────────────────────

# The head of a finding declaration: a list item opening with the bold ID,
# carrying the fix pass's checkbox and a resolved finding's strikethrough when
# it has them, and the stable-ID marker a carried finding keeps. What follows
# the match is the location and the body.
FINDING_ID_RE = re.compile(
    r"^- (?:\[([ x])\] )?"
    r"(?:~~)?"
    r"\*\*\[([MSNI])(\d+)\](?:\*\*)?"
    r"\s+"
    r"(?:<!-- sid:\w+ -->\s+)?"
)

# A finding's ID wherever it appears, declaration or reference.
BOLD_FINDING_ID_RE = re.compile(r"\*\*\[([MSNI]\d+)\]\*\*")

_STRIKETHROUGH_RE = re.compile(r"^- ~~\*\*\[")

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

# The `:12` or `:12-18` a location may carry after its filename. Public with
# `SPACED_FILE` below: the evidence-verification reader in `review_verify`
# reads the same location off the same line with a stricter shape, and the two
# have to agree on what a location is or a finding parses one way and verifies
# against the other.
LINE_SUFFIX = r"(?::\d+(?:[-–]\d+)?)?"

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

# The fix pass's record of a finding it read and left alone, in the same grammar
# as the decline annotation above and optional in the same way — a skip written
# without a reason is still a skip, and reading it as an ordinary open finding
# is what lets a sibling finding's edit report it as fixed.
#
# Anchored to head and tail for the reason the decline annotation is: matched
# anywhere, a finding whose prose quotes the annotation — the docs of the fix
# pass do, verbatim — would read as carrying it and never be touched again.
_SKIP = r"\*\(skipped(?:\s*[—–-]+\s*(.+?))?\)\*"
_SKIP_HEAD_RE = re.compile(rf"^{_SKIP}")
_SKIP_TAIL_RE = re.compile(rf"{_SKIP}\s*$")


def _severity_names() -> dict[str, str]:
    """Every heading a severity answers to, lowercased, mapped to its key."""
    names: dict[str, str] = {}
    for s in SEVERITIES:
        names[s.section.lower()] = s.key
        # replace("-", " ") mirrors _match_severity_header's input normalisation so
        # future hyphenated section names (e.g. "Should-fix") still resolve correctly.
        names[s.section.lower().replace("-", " ")] = s.key
        for alias in s.aliases:
            names[alias.lower()] = s.key
    return names


_SEVERITY_NAMES = _severity_names()


@dataclass(frozen=True)
class FindingLocation:
    """Where in the tree a finding line says its finding is.

    `line` and `end_line` are the range the location names — both absent when
    it names a file and no line of it.
    """

    path: str = ""
    line: int | None = None
    end_line: int | None = None

    @property
    def named(self) -> bool:
        """Whether the line named a location at all."""
        return bool(self.path)


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
    return Finding(
        id=f"{sev}{seq}", severity=sev, seq=seq,
        path=location.path, line=location.line, end_line=location.end_line,
        body=body,
        checked=(checkbox is not None and checkbox.lower() == "x"),
        declined=declined is not None,
        decline_reason=(declined.group(1) or "").strip() if declined else "",
    )


def is_skipped(finding: Finding) -> bool:
    """Whether a previous fix pass recorded that it left this finding alone.

    A checked finding is one the fix pass landed, so it is never skipped
    however its body reads — the annotation only speaks for an open one.
    """
    if finding.checked:
        return False
    return bool(_SKIP_HEAD_RE.match(finding.body) or _SKIP_TAIL_RE.search(finding.body))


def is_section_boundary(stripped: str) -> bool:
    """Whether the line ends whatever finding's body was being read.

    A sub-heading or a struck-through finding — the review resolved that one,
    and the lines below it belong to nothing.
    """
    return stripped.startswith("### ") or bool(_STRIKETHROUGH_RE.match(stripped))


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


def _match_severity_header(stripped: str) -> str | None:
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
        or _match_severity_header(stripped) is not None
    )


def _finalize_finding(finding: Finding, body_lines: list[str]):
    body = "\n".join(body_lines).strip()
    if not body:
        return
    body = re.sub(r"\n+###[^\n]*$", "", body, flags=re.DOTALL).strip()
    finding.body = body


class FindingScope(StrEnum):
    """What the heading above a finding declaration makes of it.

    `DECLARED` is a declaration under a severity heading — a finding the text
    reports as its own. `REPORTED` is one under a heading that names no
    severity: the `## Prior findings` ledger repeats the last review's findings
    there, and its IDs number that review rather than this one, so an edit that
    touched them would rewrite the record of a review it is not looking at.
    `UNHEADED` is a declaration with no heading above it at all, which is what
    a caller holding one severity's findings on their own hands in.
    """

    DECLARED = "declared"
    REPORTED = "reported"
    UNHEADED = "unheaded"


@dataclass(frozen=True)
class FindingSpan:
    """A finding declaration and the lines belonging to it.

    `line` is the declaration itself, stripped — what a caller with a narrower
    grammar than `FINDING_ID_RE` matches against to decide whether this is a
    finding it wants. `start` and `end` are line indices into the text the span
    was read from: the declaration's own line, and the line after the last one
    its body claims. `text_of` is the slice they name.

    The coordinates live here rather than on `Finding`, which the fix pass
    serializes to disk — a line number from one reading of one document is not
    something a stored finding should carry around.
    """

    finding: Finding
    line: str
    start: int
    end: int
    scope: FindingScope = FindingScope.UNHEADED

    @property
    def reported(self) -> bool:
        """Whether the span sits under a heading reporting on another review."""
        return self.scope is FindingScope.REPORTED

    def text_of(self, text: str) -> str:
        """The lines the span claims, verbatim.

        Trailing blank lines included, since a caller removing the span has to
        name every line the span owns or it leaves the gap behind.
        """
        return "\n".join(text.split("\n")[self.start:self.end])


def _scope_after(stripped: str, scope: FindingScope) -> FindingScope:
    """The scope a heading line puts the walk into, or the one it was in."""
    if _match_severity_header(stripped) is not None:
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


def cut_spans(text: str, spans: Iterable[FindingSpan]) -> str:
    """`text` with the lines `spans` claim removed and every other byte intact.

    An edit rather than a re-render, so what is left is exactly what was there:
    a review a gate trimmed still reads as the document its author wrote.
    """
    removed: set[int] = set()
    for span in spans:
        removed.update(range(span.start, span.end))
    if not removed:
        return text
    return "\n".join(
        line for index, line in enumerate(text.split("\n")) if index not in removed
    )


def drop_findings(text: str, ids: Iterable[str]) -> str:
    """`text` with each finding `ids` names, and the body under it, removed.

    The one owner of what leaves a review when a gate drops a finding, so the
    evidence check and the disprove gate cannot take different amounts of it.
    A declaration under a heading naming no severity is left alone: the
    prior-findings ledger reports on the last review, and an ID that collides
    with a dropped finding numbers that review rather than this one.
    """
    wanted = set(ids)
    return cut_spans(text, [
        span for span in finding_spans(text)
        if span.finding.id in wanted and not span.reported
    ])


def _parse_findings(text: str) -> list[Finding]:
    return [
        span.finding for span in finding_spans(text)
        if span.scope is FindingScope.DECLARED
    ]


@dataclass(frozen=True)
class ReviewDocument:
    """A review document being built: its title, its header, and its body.

    The body is whatever goes below the header, verbatim — findings, prose,
    sections this module has never heard of. Only the frame is typed, because
    the frame is the part every writer was spelling out for itself.
    """

    title: str = ""
    header: ReviewHeader = field(default_factory=ReviewHeader)
    body: str = ""

    def render(self) -> str:
        """The document as it goes on disk: title, header block, blank line, body.

        A document with no title opens with its header, which is what a
        mechanical merge written before synthesis has.
        """
        title = f"{self.title}\n" if self.title else ""
        return f"{title}{self.header.render()}\n{self.body}"

    def write(self, path: str | Path) -> None:
        """Render the document to `path`."""
        Path(path).write_text(self.render())

    @classmethod
    def parse(cls, text: str) -> ReviewDocument:
        """The title, header and body `text` is made of.

        The header is the run of metadata comments the document opens with,
        blank lines included; the body is everything from the first line that is
        neither. A metadata comment further down belongs to whatever section it
        sits in and stays in the body, so rendering what was parsed does not
        hoist it into the header.
        """
        lines = text.split("\n")
        start = 0
        title = ""
        if lines and lines[0].startswith("# "):
            title = lines[0].rstrip()
            start = 1
        end = start
        while end < len(lines) and _is_header_line(lines[end]):
            end += 1
        return cls(
            header=ReviewHeader.parse("\n".join(lines[start:end])),
            title=title,
            body="\n".join(lines[end:]),
        )

    @classmethod
    def read(cls, path: str | Path | None) -> ReviewDocument | None:
        """The document at `path`, or None when there is no readable one there.

        An absent review is not an empty one. A caller handed a document either
        way would report a review with no findings and nothing to say where in
        fact no review was ever written, so the two answers stay distinct and
        the caller decides what an absent one means.
        """
        if not path:
            return None
        file = Path(path)
        if not file.is_file():
            return None
        try:
            return cls.parse(file.read_text())
        except OSError:
            return None

    def section(self, header: str) -> str:
        """The body of the `## <header>` section, stripped, or "" when absent.

        Read off the body, so a metadata comment above it can never be mistaken
        for the section's contents.
        """
        span = section_span(self.body, header)
        return span.body_of(self.body).strip() if span else ""

    @property
    def findings(self) -> list[Finding]:
        """The findings the document declares, in the order it declares them.

        Read off the body, under the severity headings that declare findings —
        a finding line written anywhere else is prose about a finding rather
        than a declaration of one, and the prior-findings ledger is exactly
        that. A caller holding such a line asks `parse_finding_line`.

        `open_findings` answers a narrower question over the same
        declarations: the ones still outstanding, so a finding the fix pass
        checked off is one of these and not one of those.
        """
        return _parse_findings(self.body)

    @property
    def open_findings(self) -> list[Finding]:
        """The findings the document declares that are still outstanding.

        A finding is open until the fix pass ticks its box. That is wider than
        the work a fix pass has left: a declined finding is open — the review
        recorded a judgement about it rather than a fix — and `run_fix_pass`
        states that narrower predicate where it drives the pass, so neither
        reading has to answer for the other.
        """
        return [f for f in self.findings if not f.checked]

    @property
    def open_counts(self) -> dict[str, int]:
        """How many outstanding findings of each severity the document declares.

        Keyed by severity key and always complete, so a caller can index the
        result rather than guarding every key.

        Tallied over `open_findings`, so the parse decides what one is and no
        second grammar can disagree with it: a struck-through finding is one
        the review resolved, a checked one is a finding the fix pass fixed, and
        a finding line under any other heading — the prior-findings ledger is
        one — is prose about a finding rather than a declaration of one.
        """
        tally = Counter(f.severity for f in self.open_findings)
        return {s.key: tally[s.key] for s in SEVERITIES}

    @property
    def verdict(self) -> ReviewVerdict | None:
        """The verdict the `## Verdict` section states, if it states one."""
        return ReviewVerdict.stated_in(self.section(SECTION_VERDICT))


def open_counts(doc: ReviewDocument | None) -> dict[str, int]:
    """How many outstanding findings of each severity `doc` declares, zeroed when absent.

    The one place a review that was never written is read as one that found
    nothing. A caller reporting counts has no separate answer for absent — a
    listing prints zeroes either way — so the substitution is made once here
    rather than restated at every reader. `resolve_review_verdict` is the
    reader that does have a separate answer, and takes the same nullable
    document to give it.
    """
    return doc.open_counts if doc else ReviewDocument().open_counts


def verdict_from_counts(counts: dict[str, int]) -> ReviewVerdict:
    """The verdict a tally of open findings supports on its own.

    The counts alone, with no prose read: what `resolve_review_verdict`
    reconciles the agent's stated call against, what a mechanically merged
    review states outright for want of an agent, and what a drop leaves a
    stated verdict to be lowered to. A severity the tally omits counts as none
    of that severity, so a partial tally is read rather than refused.
    """
    return ReviewVerdict.from_counts(
        counts.get(SEVERITY_MUST, 0), counts.get(SEVERITY_SHOULD, 0),
    )


def counts_prose(counts: dict[str, int]) -> str:
    """A tally read out as prose — `2 must-fix, 1 nit` — or "" when it is empty.

    Severities in the order `SEVERITIES` declares them, and one the review has
    none of is left out rather than written as a zero. What a caller says
    instead of the empty string is its own: a verdict reads "no findings" where
    a count summary reads nothing at all.
    """
    return ", ".join(f"{counts[s.key]} {s.label}" for s in SEVERITIES if counts.get(s.key))


def resolve_review_verdict(
    doc: ReviewDocument | None, *, self_review: bool = False,
) -> ReviewVerdict | None:
    """The verdict to record and report for a finished review.

    The prose the synthesis agent wrote and the findings that survived
    verification are two readings of the same document, and this is the only
    place they are reconciled: the stronger call wins, so the prose can never
    under-report findings that block, and the counts can never quietly discard
    a stronger call the agent made. Disapprove is unranked and always stands —
    no count implies it and none refutes it.

    A review that was never written reaches no verdict at all, which is why
    this takes the document rather than a path: absent and empty are different
    answers and only the caller that went looking can tell them apart.
    """
    if doc is None:
        return None
    stated = doc.verdict
    if stated is ReviewVerdict.DISAPPROVE:
        return stated
    # A self-review is advisory — it has no PR to approve or block. Disapprove
    # is the exception above: it judges the approach, which holds without a PR.
    if self_review:
        return None
    derived = verdict_from_counts(doc.open_counts)
    return stated if stated and stated.outranks(derived) else derived


# Stamped into a mechanically written verdict so a reader — and the pipeline's
# own check for whether synthesis ran — can tell one from a verdict an agent
# reached.
MECHANICAL_NOTE = "(mechanically merged, not synthesized)"


def mechanical_verdict(counts: dict[str, int]) -> str:
    """The `## Verdict` body a tally supports, said without an agent.

    What the review states when synthesis did not run: the call
    `verdict_from_counts` derives, the tally read out, and `MECHANICAL_NOTE` so
    the absence of a synthesis is on the page rather than inferred from the
    prose being terse.
    """
    prose = counts_prose(counts)
    if not prose:
        return f"{ReviewVerdict.APPROVE.prose} — no findings {MECHANICAL_NOTE}.\n"
    verdict = verdict_from_counts(counts)
    suffix = " only" if verdict is ReviewVerdict.APPROVE else ""
    return f"{verdict.prose} — {prose}{suffix} {MECHANICAL_NOTE}.\n"


def build_mechanical_body(
    merged_content: str,
    *,
    group_count: int,
    summary_note: str,
    include_verdict: bool = True,
    file_count: int = 0,
) -> str:
    """A whole review body around findings no synthesis agent read.

    `merged_content` is the findings sections as merged; `summary_note` is the
    caller's one sentence on why synthesis was skipped, which is the only part
    of the summary that is not derived from the findings themselves.
    `file_count` of 0 leaves the file scope out rather than writing a zero.

    `include_verdict=False` is for a caller that writes the verdict itself —
    the rebuild reuses the verdict the review already reached.
    """
    counts = ReviewDocument(body=merged_content).open_counts
    total = sum(counts.values())
    count_summary = f"{total} finding{plural(total)}" if total else "No findings"
    if file_count:
        scope = f"across {file_count} file{plural(file_count)} in {group_count} groups"
    else:
        scope = f"across {group_count} groups"
    body = (
        f"## {SECTION_SUMMARY}\n"
        f"{count_summary} {scope}. "
        f"{summary_note}\n\n"
        f"{merged_content}\n"
    )
    if not include_verdict:
        return body
    return f"{body}\n## {SECTION_VERDICT}\n{mechanical_verdict(counts)}"


def _is_header_line(line: str) -> bool:
    stripped = line.strip()
    return not stripped or bool(_LINE_RE.fullmatch(stripped))
