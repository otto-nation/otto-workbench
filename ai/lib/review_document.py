"""The metadata header a review document opens with.

`review.md` begins with a block of HTML comments recording what produced it —
the date, the head SHA, whether the review covers the whole branch or a delta,
and how the run that wrote it ended. Every one of those keys is spelled here
once, so the writers agree on the format by construction rather than by two
lists of format strings staying in step.

Three things write a header and they do not write the same one. The pipeline
renders the full block; `review-rebuild` renders the subset a `meta.json` can
attest to; and on the synthesis and single-agent paths the review agent writes
its own, following prose in its template. `parse` therefore reads keys wherever
they appear and in whatever order, and `set_status` edits the line it is asked
about rather than re-rendering the block — a field this module was never told
about survives an edit instead of being dropped by it.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pr_domains import ReviewStatus
from review_types import ReviewType, meta_enum


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
