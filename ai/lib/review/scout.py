"""Phase 1's context: the scout's leads or the holistic assessment, as prompt text.

Phase 1 runs one of two scans over the whole PR. Its agent writes a
`ScoutArtifact` or a `HolisticArtifact` (`review.artifacts`). The phases after
it never read that file: they read the block rendered here, which every group
prompt embeds and synthesis is shown. Each block opens with its own heading, so
a reader needs no sentinel to tell the two apart.
"""

# doc-group: pipeline

from __future__ import annotations

from review.artifacts import HolisticArtifact, Lead, ScoutArtifact
from review.grammar import location_ref


def _lead_lines(lead: Lead) -> list[str]:
    detail = [f"Severity hint: {lead.severity_hint.value}"]
    if lead.why.strip():
        detail.append(lead.why.strip())
    return [
        f"- **{location_ref(lead.path, lead.line, lead.end_line)}** — {lead.concern.strip()}",
        f"  {'. '.join(detail)}",
    ]


def scout_context(scan: ScoutArtifact) -> str:
    """The scout's scan as the block later phases read, or "" when it found nothing."""
    parts: list[str] = []
    if scan.leads:
        parts += [
            "### Investigation leads from scout scan", "",
            "Focus your review on these specific concerns. Verify each lead —",
            "confirm it is real or dismiss it with evidence.", "",
        ]
        for lead in scan.leads:
            parts += _lead_lines(lead)
        parts.append("")
    if scan.no_scrutiny:
        parts += ["### Files that do not need scrutiny", ""]
        parts += [f"- `{q.path}` — {q.reason.strip()}" for q in scan.no_scrutiny]
        parts.append("")
    if scan.direction_concern.strip():
        parts += ["### Direction concern", "", scan.direction_concern.strip(), ""]
    if not parts:
        return ""
    return "\n".join(["## Scout context", *parts]).rstrip() + "\n"


def holistic_context(scan: HolisticArtifact) -> str:
    """The holistic scan as the block later phases read."""
    parts = [
        "## Holistic context",
        "The following assessment was produced by scanning every file in this PR.",
        "Use it to inform your detailed review — especially the flags.", "",
        scan.assessment.strip(),
    ]
    for heading, items in (("Flags for detailed review", scan.flags),
                           ("Cross-module concerns", scan.cross_module)):
        if items:
            parts += ["", f"### {heading}", *(f"- {item}" for item in items)]
    if scan.direction_concern.strip():
        parts += ["", "### Direction concern", scan.direction_concern.strip()]
    return "\n".join(parts) + "\n"
