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


def _parse_header(stripped: str, pending_lead: Lead | None, leads: list[Lead]) -> tuple[str, Lead | None]:
    if pending_lead:
        leads.append(pending_lead)
    header = stripped[3:].strip().lower()
    if "investigation" in header or "lead" in header:
        return "leads", None
    if "no scrutiny" in header or "no-scrutiny" in header:
        return "no_scrutiny", None
    return "", None


def _apply_lead_detail(pending_lead: Lead, stripped: str) -> None:
    lower = stripped.lower()
    if lower.startswith("severity hint:"):
        pending_lead.severity_hint = stripped.split(":", 1)[1].strip().rstrip(".")
    elif lower.startswith("why:"):
        pending_lead.why = stripped.split(":", 1)[1].strip()
    elif not pending_lead.why:
        pending_lead.why = stripped
    else:
        pending_lead.why += " " + stripped


def _process_leads_line(stripped: str, pending_lead: Lead | None, leads: list[Lead]) -> Lead | None:
    m = _LEAD_RE.match(stripped)
    if m:
        if pending_lead:
            leads.append(pending_lead)
        path, line_num = _parse_path_line(m.group(1))
        return Lead(file=path, line=line_num, concern=m.group(2).strip(), severity_hint="", why="")
    if pending_lead and stripped:
        _apply_lead_detail(pending_lead, stripped)
    return pending_lead


def _process_no_scrutiny_line(stripped: str, no_scrutiny: list[str]) -> None:
    m = _NO_SCRUTINY_RE.match(stripped)
    if m:
        no_scrutiny.append(m.group(1))


def parse_scout_output(text: str) -> tuple[list[Lead], list[str]]:
    leads: list[Lead] = []
    no_scrutiny: list[str] = []
    section = ""
    pending_lead: Lead | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            section, pending_lead = _parse_header(stripped, pending_lead, leads)
            continue
        if section == "leads":
            pending_lead = _process_leads_line(stripped, pending_lead, leads)
        elif section == "no_scrutiny":
            _process_no_scrutiny_line(stripped, no_scrutiny)

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
            parts.extend(_format_lead_lines(lead))
        parts.append("")

    if filtered_no_scrutiny:
        parts.append("### Files that do not need scrutiny")
        parts.append("")
        for path in filtered_no_scrutiny:
            parts.append(f"- `{path}`")
        parts.append("")

    return "\n".join(parts)


def _format_lead_lines(lead: Lead) -> list[str]:
    loc = f"{lead.file}:{lead.line}" if lead.line else lead.file
    lines = [f"- **`{loc}`** — {lead.concern}"]
    detail_parts: list[str] = []
    if lead.severity_hint:
        detail_parts.append(f"Severity hint: {lead.severity_hint}")
    if lead.why:
        detail_parts.append(lead.why)
    if detail_parts:
        lines.append(f"  {'. '.join(detail_parts)}")
    return lines


_SCOUT_SENTINEL = "### Investigation leads from scout scan"


def is_scout_output(holistic_content: str) -> bool:
    return _SCOUT_SENTINEL in holistic_content or "## Investigation Leads" in holistic_content
