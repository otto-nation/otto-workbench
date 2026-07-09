"""Lead scout: parse structured investigation leads from scout phase output.

The scout phase replaces the holistic phase's prose output with structured
leads that tell group reviewers exactly where to focus investigation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Lead:
    file: str
    line: int | None
    concern: str
    severity_hint: str
    why: str


_LEAD_RE = re.compile(
    r"^- \*\*`([^`]+)`\*\*\s*(?:—|--)\s*(.+)",
)

_NO_SCRUTINY_RE = re.compile(
    r"^- `([^`]+)`\s*(?:—|--)\s*(.+)",
)


def _parse_path_line(path_str: str) -> tuple[str, int | None]:
    if ":" in path_str:
        parts = path_str.rsplit(":", 1)
        try:
            return parts[0], int(parts[1].split("-")[0])
        except ValueError:
            return path_str, None
    return path_str, None


def parse_scout_output(text: str) -> tuple[list[Lead], list[str]]:
    leads: list[Lead] = []
    no_scrutiny: list[str] = []
    section = ""
    pending_lead: Lead | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if pending_lead:
                leads.append(pending_lead)
                pending_lead = None
            header = stripped[3:].strip().lower()
            if "investigation" in header or "lead" in header:
                section = "leads"
            elif "no scrutiny" in header or "no-scrutiny" in header:
                section = "no_scrutiny"
            else:
                section = ""
            continue

        if section == "leads":
            m = _LEAD_RE.match(stripped)
            if m:
                if pending_lead:
                    leads.append(pending_lead)
                path, line_num = _parse_path_line(m.group(1))
                pending_lead = Lead(
                    file=path, line=line_num,
                    concern=m.group(2).strip(),
                    severity_hint="", why="",
                )
            elif pending_lead and stripped:
                lower = stripped.lower()
                if lower.startswith("severity hint:"):
                    pending_lead.severity_hint = stripped.split(":", 1)[1].strip().rstrip(".")
                elif lower.startswith("why:"):
                    pending_lead.why = stripped.split(":", 1)[1].strip()
                elif not pending_lead.why:
                    pending_lead.why = stripped
                else:
                    pending_lead.why += " " + stripped

        elif section == "no_scrutiny":
            m = _NO_SCRUTINY_RE.match(stripped)
            if m:
                no_scrutiny.append(m.group(1))

    if pending_lead:
        leads.append(pending_lead)

    return leads, no_scrutiny


def filter_leads_for_group(
    leads: list[Lead], group_files: list[str],
) -> list[Lead]:
    file_set = set(group_files)
    return [lead for lead in leads if lead.file in file_set]


def format_leads_block(
    leads: list[Lead],
    no_scrutiny: list[str],
    group_files: list[str] | None = None,
) -> str:
    filtered_leads = (
        filter_leads_for_group(leads, group_files) if group_files else leads
    )
    filtered_no_scrutiny = (
        [f for f in no_scrutiny if f in set(group_files)]
        if group_files else no_scrutiny
    )

    if not filtered_leads and not filtered_no_scrutiny:
        return ""

    parts: list[str] = []
    if filtered_leads:
        parts.append("### Investigation leads from scout scan")
        parts.append("")
        parts.append("Focus your review on these specific concerns. Verify each lead —")
        parts.append("confirm it is real or dismiss it with evidence.")
        parts.append("")
        for lead in filtered_leads:
            loc = f"{lead.file}:{lead.line}" if lead.line else lead.file
            parts.append(f"- **`{loc}`** — {lead.concern}")
            detail_parts: list[str] = []
            if lead.severity_hint:
                detail_parts.append(f"Severity hint: {lead.severity_hint}")
            if lead.why:
                detail_parts.append(lead.why)
            if detail_parts:
                parts.append(f"  {'. '.join(detail_parts)}")
        parts.append("")

    if filtered_no_scrutiny:
        parts.append("### Files that do not need scrutiny")
        parts.append("")
        for path in filtered_no_scrutiny:
            parts.append(f"- `{path}`")
        parts.append("")

    return "\n".join(parts)


_SCOUT_SENTINEL = "### Investigation leads from scout scan"


def is_scout_output(holistic_content: str) -> bool:
    return _SCOUT_SENTINEL in holistic_content or "## Investigation Leads" in holistic_content
