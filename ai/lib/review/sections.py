"""Config-driven section registry with auto-discovery for review posting.

Defines section configs declaratively and extracts them from the review record.
Replaces per-section parameter threading across the posting pipeline.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.text import slugify
from review.document import (
    SECTION_FAILURES, SECTION_FILE_TRIAGE, SECTION_FOCUS, SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS, SECTION_SUMMARY, SECTION_VERDICT,
)
from review.record import ProseSection, ReviewRecord
from review.render import failures_body, focus_comment
from review.types import SEVERITIES

POSITION_BEFORE = "before_findings"
POSITION_AFTER = "after_findings"

_SEVERITY_HEADERS: set[str] = set()
for _s in SEVERITIES:
    _SEVERITY_HEADERS.add(_s.section.lower())
    for _a in _s.aliases:
        _SEVERITY_HEADERS.add(_a.lower())

# Sections written to the local review file as bookkeeping rather than as
# reviewer claims about the code. They are excluded here because this is the
# last point before the body is posted, and neither reaches it the same way:
# File Triage is never stripped upstream, and the prior-findings ledger is
# stripped only on the pipeline that runs `post_process_record` (see its
# docstring for why the ledger's IDs cannot be posted beside this review's).
_INTERNAL_HEADERS: set[str] = {
    SECTION_FILE_TRIAGE.lower(),
    SECTION_PRIOR_FINDINGS.lower(),
}


@dataclass(frozen=True)
class SectionConfig:
    key: str
    header: str
    position: Literal["before_findings", "after_findings"]
    heading: str = ""
    strip_action: bool = False
    trailing_separator: bool = False
    subsection_of: str = ""


KNOWN_SECTIONS: list[SectionConfig] = [
    SectionConfig("summary", SECTION_SUMMARY, POSITION_BEFORE,
                  heading=f"## {SECTION_SUMMARY}", trailing_separator=True),
    SectionConfig("focus", SECTION_FOCUS, POSITION_BEFORE,
                  heading=f"### {SECTION_FOCUS}", subsection_of="summary"),
    SectionConfig("verdict", SECTION_VERDICT, POSITION_BEFORE,
                  heading=f"### {SECTION_VERDICT}", strip_action=True, subsection_of="summary"),
    SectionConfig("static_analysis", SECTION_STATIC_ANALYSIS, POSITION_AFTER),
]

_KNOWN_BY_HEADER: dict[str, SectionConfig] = {
    cfg.header.lower(): cfg for cfg in KNOWN_SECTIONS
}


def _config_for(heading: str) -> SectionConfig | None:
    header_lower = heading.strip().lower()
    if header_lower in _SEVERITY_HEADERS or header_lower in _INTERNAL_HEADERS:
        return None
    cfg = _KNOWN_BY_HEADER.get(header_lower)
    if cfg is not None:
        return cfg
    original = heading.strip()
    return SectionConfig(
        key=slugify(original, sep="_"),
        header=original,
        position=POSITION_AFTER,
        heading=f"## {original}",
    )


class ReviewSections:
    def __init__(
        self,
        entries: dict[str, str] | None = None,
        configs: dict[str, SectionConfig] | None = None,
        order: list[str] | None = None,
    ):
        self._entries: dict[str, str] = entries or {}
        self._configs: dict[str, SectionConfig] = configs or {}
        self._order: list[str] = order or []

    @classmethod
    def from_record(cls, record: ReviewRecord) -> ReviewSections:
        sections: list[ProseSection] = [
            ProseSection(SECTION_SUMMARY, record.summary),
            ProseSection(
                SECTION_FOCUS, focus_comment(record.focus) if record.focus else "",
            ),
            ProseSection(
                SECTION_FAILURES,
                failures_body(record.failures) if record.failures else "",
            ),
            ProseSection(SECTION_VERDICT, record.verdict),
            ProseSection(SECTION_STATIC_ANALYSIS, record.static_analysis),
            *record.extra,
        ]
        return cls._from_pairs(sections)

    @classmethod
    def _from_pairs(cls, sections: list[ProseSection]) -> ReviewSections:
        entries: dict[str, str] = {}
        configs: dict[str, SectionConfig] = {}
        order: list[str] = []
        for section in sections:
            cfg = _config_for(section.heading)
            content = section.body.strip()
            if cfg is None or not content:
                continue
            entries[cfg.key] = content
            configs[cfg.key] = cfg
            if cfg.key not in order:
                order.append(cfg.key)
        known_order = [c.key for c in KNOWN_SECTIONS]
        order.sort(key=lambda k: (
            known_order.index(k) if k in known_order else len(known_order),
            k,
        ))
        return cls(entries=entries, configs=configs, order=order)

    def get(self, key: str) -> str:
        return self._entries.get(key, "")

    def before_findings(self) -> list[tuple[SectionConfig, str]]:
        return [
            (self._configs[k], self._entries[k])
            for k in self._order
            if k in self._configs and self._configs[k].position == POSITION_BEFORE
        ]

    def after_findings(self) -> list[tuple[SectionConfig, str]]:
        return [
            (self._configs[k], self._entries[k])
            for k in self._order
            if k in self._configs and self._configs[k].position == POSITION_AFTER
        ]
