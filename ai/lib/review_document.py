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
claims the original never made.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from agent_types import Mode
from pr_domains import ReviewStatus
from review_types import ReviewMeta, ReviewType, meta_enum


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


def review_title(meta: ReviewMeta) -> str:
    """The `# ...` line a review opens with, as the attribution in `meta` reads.

    Derived from the sidecar rather than from whatever the caller is holding, so
    the pipeline, the mechanical fallback and `review-rebuild` cannot name the
    same review three ways. A self-review has no PR to number and is titled by
    the branch it covers instead.
    """
    if meta.mode == Mode.SELF:
        return f"# Self-Review: {meta.repo} — {meta.head_ref or 'unknown'}"
    title = f"# Review: {meta.repo}#{meta.pr_number}"
    return f"{title} — {meta.title}" if meta.title else title


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


def _is_header_line(line: str) -> bool:
    stripped = line.strip()
    return not stripped or bool(_LINE_RE.fullmatch(stripped))
