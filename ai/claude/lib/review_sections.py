"""Config-driven section registry with auto-discovery for review posting.

Defines section configs declaratively and extracts them from review markdown.
Replaces per-section parameter threading across the posting pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from review_common import SEVERITIES
from review_findings import _extract_section

POSITION_BEFORE = "before_findings"
POSITION_AFTER = "after_findings"

_SEVERITY_HEADERS: set[str] = set()
for _s in SEVERITIES:
    _SEVERITY_HEADERS.add(_s.section.lower())
    for _a in _s.aliases:
        _SEVERITY_HEADERS.add(_a.lower())


@dataclass(frozen=True)
class SectionConfig:
    key: str
    header: str
    position: str
    heading: str = ""
    strip_action: bool = False
    trailing_separator: bool = False
    subsection_of: str = ""


KNOWN_SECTIONS: list[SectionConfig] = [
    SectionConfig("summary", "Summary", POSITION_BEFORE,
                  heading="## Summary", trailing_separator=True),
    SectionConfig("verdict", "Verdict", POSITION_BEFORE,
                  heading="### Verdict", strip_action=True, subsection_of="summary"),
    SectionConfig("static_analysis", "Static Analysis", POSITION_AFTER),
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

        seen_headers: set[str] = set()
        for m in _SECTION_HEADER_RE.finditer(text):
            seen_headers.add(m.group(1).strip().lower())

        for header_lower in seen_headers:
            if header_lower in _SEVERITY_HEADERS:
                continue

            cfg = _KNOWN_BY_HEADER.get(header_lower)
            if cfg is None:
                original = next(
                    m.group(1).strip()
                    for m in _SECTION_HEADER_RE.finditer(text)
                    if m.group(1).strip().lower() == header_lower
                )
                cfg = SectionConfig(
                    key=_slugify(original),
                    header=original,
                    position=POSITION_AFTER,
                    heading=f"## {original}",
                )

            content = _extract_section(text, cfg.header)
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
