"""Rendering a review record — `review.md` today, the GitHub comment from PR 2."""

# doc-group: findings

from __future__ import annotations

from core.markdown import escape_cell
from review.document import (
    SECTION_FAILURES, SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS, SECTION_SUMMARY, SECTION_VERDICT,
    ReviewHeader, review_title,
)
from review.grammar import finding_tag, sid_marker
from review.record import AgentFailure, Disposition, ProseSection, RecordFinding, ReviewRecord
from review.types import SEVERITIES, ReviewMeta

# The line every rendered review.md carries. Ingest refuses any text holding it,
# which is what "review.md is never parsed back" is enforced by.
BANNER = "<!-- rendered from review.json — edits here are overwritten; change the record -->"

# Quoted in the Agent Failures section when a failed phase can be retried.
RECOVER_HINT = "Run `pr review --recover`"


def finding_line(f: RecordFinding, *, sids: bool = False, evidence: bool = False) -> str:
    """The finding as its review.md list item, continuation lines included.

    `evidence` appends the quoted code after the body. Only prompt text asks
    for it; review.md never carries evidence (D9).
    """
    box = ""
    if f.checkbox:
        box = "[x] " if f.disposition is Disposition.FIXED else "[ ] "
    marker = sid_marker(f.sid) if sids and f.sid else ""
    first, sep, rest = f.body.partition("\n")
    head = f"- {box}{finding_tag(f.id)}{marker} {f.lead}{first}".rstrip()
    if f.annotation:
        head = f"{head} {f.annotation}"
    line = head + (sep + rest if sep else "")
    return f"{line}\n{f.evidence}" if evidence and f.evidence else line


def failures_body(failures: tuple[AgentFailure, ...]) -> str:
    """The Agent Failures table, and the recover hint when a retry could help."""
    lines = ["| Agent | Reason | Status |", "|-------|--------|--------|"]
    # A reason is free text — an RPC error, a quoted stderr line — and one
    # literal pipe in it shifts every later cell in the row.
    lines += [f"| {f.agent} | {escape_cell(f.reason)} | {f.status} |" for f in failures]
    if any(f.recoverable for f in failures):
        lines += ["", f"{RECOVER_HINT} to retry failed agents."]
    return "\n".join(lines)


def _severity_sections(record: ReviewRecord, sids: bool, evidence: bool) -> list[ProseSection]:
    out = []
    for severity in SEVERITIES:
        notes = [s.note for s in record.severity_sections if s.severity == severity.key]
        findings = [f for f in record.visible if f.severity == severity.key]
        if not notes and not findings:
            continue
        lines = [n for n in notes if n] + [finding_line(f, sids=sids, evidence=evidence) for f in findings]
        out.append(ProseSection(severity.section, "\n".join(lines)))
    return out


def _sections(record: ReviewRecord, sids: bool, evidence: bool) -> list[ProseSection]:
    """Every section the record holds, in spec §2's order."""
    sections: list[ProseSection] = []
    if record.triage is not None:
        sections.append(ProseSection(SECTION_FILE_TRIAGE, "\n".join(e.text for e in record.triage)))
    if record.failures:
        sections.append(ProseSection(SECTION_FAILURES, failures_body(record.failures)))
    if record.summary:
        sections.append(ProseSection(SECTION_SUMMARY, record.summary))
    sections += _severity_sections(record, sids, evidence)
    if record.ledger:
        sections.append(ProseSection(SECTION_PRIOR_FINDINGS, record.ledger))
    sections += list(record.extra)
    if record.static_analysis:
        sections.append(ProseSection(SECTION_STATIC_ANALYSIS, record.static_analysis))
    if record.verdict:
        sections.append(ProseSection(SECTION_VERDICT, record.verdict))
    return sections


def render_body(record: ReviewRecord, *, sids: bool = False, evidence: bool = False,
                omit: frozenset[str] = frozenset()) -> str:
    """The `## ` sections of `record`, without title or header. `omit` names headings."""
    return "\n".join(
        f"## {s.heading}\n\n{s.body.strip()}\n" if s.body.strip() else f"## {s.heading}\n"
        for s in _sections(record, sids, evidence) if s.heading not in omit
    )


def render_findings(record: ReviewRecord, *, sids: bool = False, evidence: bool = False) -> str:
    """Only the severity sections — what a re-review's scoped prior section shows."""
    return "\n".join(f"## {s.heading}\n{s.body}" for s in _severity_sections(record, sids, evidence)).strip()


def render_review(record: ReviewRecord, meta: ReviewMeta, *, sids: bool = False,
                  omit: frozenset[str] = frozenset(), banner: bool = True) -> str:
    """The whole review.md: title and header from `meta`, banner, then the body.

    Prompt text passes `banner=False` so an agent quoting a rendered review
    cannot echo the line ingest refuses as RENDERED_INPUT.
    """
    header = ReviewHeader.from_meta(meta).render()
    mark = f"{BANNER}\n" if banner else ""
    return f"{review_title(meta)}\n\n{header}{mark}\n{render_body(record, sids=sids, omit=omit)}"
