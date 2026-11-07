"""Markdown and stdout projections of a session report dict.

Rendered from the JSON only. Filing commands go in a trailing
"File follow-up issues (only on request)" block, never on the stdout line.
"""

# doc-group: batch

from __future__ import annotations

import shlex


def render_markdown(report: dict) -> str:
    """`report.md` body for *report* (the dict `report.build` returns)."""
    run = report.get("run") or {}
    lines = [
        f"# pr batch report `{run.get('id', '')}`",
        "",
        f"status: {run.get('status', '')}",
        "",
    ]
    filing_block: list[str] = []
    for session in report.get("sessions") or []:
        lines.extend(_session_lines(session, filing_block))
    if filing_block:
        lines.extend(["## File follow-up issues (only on request)", ""])
        lines.extend(filing_block)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def stdout_lines(report: dict) -> str:
    """One line per PR: `<state> <pr> <reasons> -> <first non-filing command>`."""
    rows = []
    for session in report.get("sessions") or []:
        reasons = ",".join(f.get("reason", "") for f in session.get("findings") or [] if f.get("reason"))
        first = next((c for c in session.get("commands") or [] if not c.get("files_issue")), None)
        arrow = f" -> {shlex.join(first['argv'])}" if first and first.get("argv") else ""
        rows.append(f"{session.get('state', '')} {session.get('pr', '')} {reasons}{arrow}".rstrip())
    return "\n".join(rows)


def _session_lines(session: dict, filing_block: list[str]) -> list[str]:
    findings = session.get("findings") or []
    reasons = ", ".join(f.get("reason", "") for f in findings if f.get("reason"))
    lines = [
        f"## {session.get('pr', '')}",
        "",
        f"state: {session.get('state', '')} · severity: {session.get('severity', '')}",
        f"branch: {session.get('branch', '')}",
        f"findings: {reasons or '(none)'}",
        "",
    ]
    for note in session.get("notes") or []:
        lines.append(f"> {note}")
        lines.append("")
    work = [c for c in session.get("commands") or [] if not c.get("files_issue")]
    filing = [c for c in session.get("commands") or [] if c.get("files_issue")]
    for cmd in work:
        lines.append(_cmd_line(cmd))
    if work:
        lines.append("")
    for cmd in filing:
        filing_block.append(_cmd_line(cmd, prefix=f"{session.get('pr', '')}: "))
    return lines


def _cmd_line(cmd: dict, prefix: str = "") -> str:
    argv = cmd.get("argv") or []
    why = cmd.get("why") or ""
    joined = shlex.join(argv) if argv else ""
    return f"- `{prefix}{joined}` — {why}" if why else f"- `{prefix}{joined}`"
