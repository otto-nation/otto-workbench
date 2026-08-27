"""Config-driven section registry with auto-discovery for review posting.

Defines section configs declaratively and extracts them from review markdown.
Replaces per-section parameter threading across the posting pipeline.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from review_common import SECTION_FILE_TRIAGE, SECTION_STATIC_ANALYSIS
from review_document import SECTION_SUMMARY, SECTION_VERDICT, ReviewDocument
from review_types import SEVERITIES

POSITION_BEFORE = "before_findings"
POSITION_AFTER = "after_findings"

_SEVERITY_HEADERS: set[str] = set()
for _s in SEVERITIES:
    _SEVERITY_HEADERS.add(_s.section.lower())
    for _a in _s.aliases:
        _SEVERITY_HEADERS.add(_a.lower())

# Sections written to the local review file for coverage tracking only — they
# document which files the reviewer looked at, which is noise on the PR.
_INTERNAL_HEADERS: set[str] = {SECTION_FILE_TRIAGE.lower()}


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
    SectionConfig("verdict", SECTION_VERDICT, POSITION_BEFORE,
                  heading=f"### {SECTION_VERDICT}", strip_action=True, subsection_of="summary"),
    SectionConfig("static_analysis", SECTION_STATIC_ANALYSIS, POSITION_AFTER),
]

_KNOWN_BY_HEADER: dict[str, SectionConfig] = {
    cfg.header.lower(): cfg for cfg in KNOWN_SECTIONS
}

_SECTION_HEADER_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


def _slugify(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")


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
    def from_text(cls, text: str) -> ReviewSections:
        entries: dict[str, str] = {}
        configs: dict[str, SectionConfig] = {}
        order: list[str] = []

        doc = ReviewDocument.parse(text)
        seen: dict[str, str] = {}
        for m in _SECTION_HEADER_RE.finditer(doc.body):
            raw = m.group(1).strip()
            seen.setdefault(raw.lower(), raw)

        for header_lower, original in seen.items():
            if header_lower in _SEVERITY_HEADERS or header_lower in _INTERNAL_HEADERS:
                continue

            cfg = _KNOWN_BY_HEADER.get(header_lower)
            if cfg is None:
                cfg = SectionConfig(
                    key=_slugify(original),
                    header=original,
                    position=POSITION_AFTER,
                    heading=f"## {original}",
                )

            content = doc.section(cfg.header)
            if not content:
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
