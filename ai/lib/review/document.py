"""The header vocabulary of a review, and the read view of a stored one.

This module owns the keys a review header may state, the sentence the title is,
and `ReviewDocument` as the read view of a stored review. `ReviewHeader.render`
and `ReviewHeader.from_meta` write the header from the sidecar; `covers` is
whether a header SHA covers a commit; `review_title` is the `# ...` line
derived from the same sidecar. `ReviewDocument` answers sections, findings,
open counts and the verdict off the record, never by parsing `review.md`.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from core.phases import Mode
from pr.domains import ReviewStatus, ReviewVerdict
from pr.target import display_repo
from review.record import ReviewRecord
from review.types import SEVERITIES, Finding, ReviewMeta, ReviewType


# The headers a review is written with. `Summary` and `Verdict` are the two this
# module reads by name; the rest are here because they name sections of the same
# document, and a header spelled in the module that reads it and again in the one
# that writes it is two owners of one string. Any other header is the caller's
# own vocabulary, passed to `section` as a string.
SECTION_SUMMARY = "Summary"
SECTION_VERDICT = "Verdict"
SECTION_FILE_TRIAGE = "File Triage"
SECTION_STATIC_ANALYSIS = "Static Analysis"
SECTION_FAILURES = "Agent Failures"

# A re-review's ledger: one line per prior finding, saying whether the change
# resolved it. Reconciliation reads it to tell a finding the re-review dropped
# on purpose from one it lost track of; it is stripped before the review is
# posted, since its finding IDs number the prior review, not this one.
SECTION_PRIOR_FINDINGS = "Prior findings"


class MetaKey(StrEnum):
    """A key in a review document's metadata header.

    The values are the strings on disk. They stay as they are so a review
    written by an earlier version still renders the same header, and so the
    templates that ask the review agent for `<!-- date: ... -->` keep naming
    a key this module owns.
    """

    DATE = "date"
    MODE = "mode"
    PR = "pr"
    HEAD_SHA = "head_sha"
    HEAD_REF = "head_ref"
    BASE_REF = "base_ref"
    REVIEW_TYPE = "review_type"
    PRIOR_SHA = "prior_sha"
    PRIOR_DATE = "prior_date"
    DELTA_FILES = "delta_files"
    SKIPPED_GROUPS = "skipped_groups"
    STATUS = "status"
    GENERATOR = "generator"


def _line(key: MetaKey, value: object) -> str:
    return f"<!-- {key}: {value} -->"


@dataclass(frozen=True)
class ReviewHeader:
    """What a review document's metadata header states.

    Enough for the document to be read on its own. A review file is opened by
    a session that has no sidecar in hand and no run to ask — the self-review
    protocol has an agent read the last review at session start — so what it
    covers has to be on the page: which branch, against which base, at which
    commit, and whether the run that wrote it got to the end.

    `skipped_groups` and `total_groups` are one line on disk — `2/7` — and are
    written only when the caller has a group count to report.

    What is deliberately absent is any tally of findings. The body declares
    them and `ReviewDocument.open_counts` counts that declaration; a count in
    the header would be a second statement of one fact, and the two disagree
    the moment a fix pass ticks a box without rewriting the header.
    """

    date: str = ""
    mode: Mode | None = None
    pr_number: int | None = None
    head_sha: str = ""
    head_ref: str = ""
    base_ref: str = ""
    review_type: ReviewType = ReviewType.FULL
    prior_sha: str = ""
    prior_date: str = ""
    delta_files: int | None = None
    skipped_groups: int = 0
    total_groups: int = 0
    status: ReviewStatus | None = None
    generator_version: str = ""

    def _markers(self) -> list[tuple[MetaKey, object | None]]:
        """Every key this header can state, paired with the value it states.

        A `None` is a key this header has nothing to say about. `render` drops
        those keys, so the block a fresh render produces carries only what this
        header attests to, in declaration order.
        """
        ratio = f"{self.skipped_groups}/{self.total_groups}" if self.total_groups else None
        return [
            (MetaKey.DATE, self.date or None),
            (MetaKey.MODE, self.mode.value if self.mode else None),
            (MetaKey.PR, self.pr_number),
            (MetaKey.HEAD_SHA, self.head_sha or None),
            (MetaKey.HEAD_REF, self.head_ref or None),
            (MetaKey.BASE_REF, self.base_ref or None),
            (MetaKey.REVIEW_TYPE, self.review_type),
            (MetaKey.PRIOR_SHA, self.prior_sha or None),
            (MetaKey.PRIOR_DATE, self.prior_date or None),
            (MetaKey.DELTA_FILES, self.delta_files),
            (MetaKey.SKIPPED_GROUPS, ratio),
            (MetaKey.STATUS, self.status.value if self.status else None),
            (MetaKey.GENERATOR, self.generator_version or None),
        ]

    def render(self) -> str:
        """The header as it goes at the top of the document, newline-terminated.

        A field left at its default is omitted rather than written empty, so a
        full review carries no incremental keys and a run still in flight
        carries no status. `review_type` is the exception every writer states:
        an absent one reads back as `full`, which is a claim about the review
        rather than a gap in the record, so it is always written.
        """
        return "".join(f"{_line(k, v)}\n" for k, v in self._markers() if v is not None)

    @classmethod
    def from_meta(cls, meta: ReviewMeta, **overrides) -> ReviewHeader:
        """The header `meta` attests to, with `overrides` written over the top.

        The sidecar and the document header record the same review, so what the
        two share is read from the sidecar rather than assembled a second time
        from whatever the caller happens to be holding. A writer that reaches
        past this is stating the review's attribution twice, and the second
        statement is the one that drifts.

        Overrides win over what `meta.json` records.
        """
        incremental = meta.review_type == ReviewType.INCREMENTAL
        return replace(cls(
            date=meta.date,
            mode=meta.mode,
            pr_number=meta.pr_number,
            head_sha=meta.head_sha,
            head_ref=meta.head_ref,
            base_ref=meta.base_ref,
            review_type=meta.review_type or ReviewType.FULL,
            prior_sha=meta.prior_sha,
            prior_date=meta.prior_date if incremental else "",
            # A count only an incremental review has: on a full one the sidecar
            # records no delta, and a `0` here would read as a delta of nothing
            # rather than as the absence of one.
            delta_files=len(meta.delta_files) if incremental else None,
            skipped_groups=meta.skipped_groups if incremental else 0,
            total_groups=meta.total_groups if incremental else 0,
            status=meta.status,
            generator_version=meta.generator_version,
        ), **overrides)


# The shortest header SHA read as an abbreviation of the subject rather than a
# coincidence. Seven is git's own floor in a small repository. A header the
# pipeline wrote holds the full SHA and matches exactly. A header an agent
# wrote from a template may hold an abbreviation, and a prefix shorter than
# this proves nothing: one hex character prefixes a sixteenth of all commits.
MIN_SHA_PREFIX = 7


def covers(reviewed_sha: str, sha: str) -> bool:
    """Whether a review whose header states *reviewed_sha* covers commit *sha*.

    True on an exact match, or when *reviewed_sha* is an abbreviation of *sha*
    at least `MIN_SHA_PREFIX` long. An empty *reviewed_sha* covers nothing.
    Every caller that asks whether a review is current — batch's plan, the
    review preflight and auto-recover, the delta collector, and the post's
    drift check — asks it here, so none can disagree.
    """
    if not reviewed_sha:
        return False
    if reviewed_sha == sha:
        return True
    return len(reviewed_sha) >= MIN_SHA_PREFIX and sha.startswith(reviewed_sha)


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
    # Host-qualified: two enterprise instances can serve the same slug, and a
    # title naming only the slug leaves the reader unable to tell which review
    # they are reading. Empty and github.com render exactly as before.
    shown = display_repo(meta.repo, meta.host)
    if meta.mode == Mode.SELF:
        return f"# Self-Review: {shown} — {meta.head_ref or 'unknown'}"
    number = f"#{meta.pr_number}" if meta.pr_number is not None else ""
    title = f"# Review: {shown}{number}"
    return f"{title} — {meta.title}" if meta.title else title


def _record_section(record: ReviewRecord, header: str) -> str:
    """A section's text read off the record's fields. Findings are read through
    `findings`, never as a section — a severity heading raises."""
    key = header.strip().lower()
    if key in {s.section.lower() for s in SEVERITIES}:
        raise ValueError(
            f"{header!r} is a severity heading; read it from .findings"
        )
    by_key = {
        SECTION_SUMMARY.lower(): record.summary,
        SECTION_VERDICT.lower(): record.verdict,
        SECTION_STATIC_ANALYSIS.lower(): record.static_analysis,
    }
    if key in by_key:
        return by_key[key].strip()
    if key == SECTION_PRIOR_FINDINGS.lower():
        # review.render imports this module; a top-level import here is circular.
        from review.render import ledger_line
        return "\n".join(ledger_line(e) for e in record.ledger)
    if key == SECTION_FILE_TRIAGE.lower():
        # review.render imports this module; a top-level import here is circular.
        from review.render import triage_line
        return "\n".join(triage_line(e) for e in record.triage or ()).strip()
    if key == SECTION_FAILURES.lower():
        if not record.failures:
            return ""
        # review.render imports this module; a top-level import here is circular.
        from review.render import failures_body
        return failures_body(record.failures)
    return next((e.body.strip() for e in record.extra if e.heading.lower() == key), "")


@dataclass(frozen=True)
class ReviewDocument:
    """The read view of a stored review: its title, its header, and its record.

    Sections, findings, open counts and the verdict are answered off the
    record. A document with no record is an empty view — zeroes, no findings,
    no verdict — which is what a review that was never written reads as.
    """

    title: str = ""
    header: ReviewHeader = field(default_factory=ReviewHeader)
    record: ReviewRecord | None = None
    meta: ReviewMeta | None = None

    @classmethod
    def from_stored(cls, stored) -> "ReviewDocument":
        """The read view of `stored` (a `review.store.StoredReview`)."""
        return cls(
            title=review_title(stored.meta), header=ReviewHeader.from_meta(stored.meta),
            record=stored.record, meta=stored.meta,
        )

    @classmethod
    def read(cls, path: str | Path | None) -> ReviewDocument | None:
        """The stored review beside `path`, or None when there is none or it is stale."""
        if not path:
            return None
        # Imported here: `review.store` renders through `review.render`, which
        # imports this module for the header and title.
        from review.store import read_review

        stored = read_review(Path(path))
        return cls.from_stored(stored) if stored else None

    def section(self, header: str) -> str:
        """The body of the `## <header>` section, stripped, or "" when absent.

        A severity heading raises ``ValueError``; those findings live on
        ``.findings``.
        """
        if self.record is None:
            return ""
        return _record_section(self.record, header)

    @property
    def findings(self) -> list[Finding]:
        """The findings the document declares, in the order it declares them.

        `open_findings` answers a narrower question over the same
        declarations: the ones still outstanding, so a finding the fix pass
        checked off is one of these and not one of those.
        """
        if self.record is None:
            return []
        return [f.view() for f in self.record.visible]

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
        """
        if self.record is not None:
            return self.record.open_counts
        return {s.key: 0 for s in SEVERITIES}

    @property
    def verdict(self) -> ReviewVerdict | None:
        """The verdict the `## Verdict` section states, if it states one."""
        if self.record is None:
            return None
        return ReviewVerdict.stated_in(self.record.verdict)
