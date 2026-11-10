"""Rendering a review record — `review.md` and the PR comment body."""

# doc-group: findings

from __future__ import annotations

from collections.abc import Sequence

from core.markdown import escape_cell
from review.behaviour_types import BehaviourEntry
from review.claim_types import ClaimEntry
from review.document import (
    SECTION_CLAIMS, SECTION_FAILURES, SECTION_FILE_TRIAGE, SECTION_FOCUS,
    SECTION_PRIOR_FINDINGS, SECTION_QUESTIONS, SECTION_STATIC_ANALYSIS,
    SECTION_SUMMARY, SECTION_TEST_EVIDENCE, SECTION_VERIFIED,
    ReviewHeader, review_title,
)
from review.grammar import finding_tag, location_ref, posted_finding_tag, sid_marker
from review.record import (
    AgentFailure, Disposition, FocusArea, FocusSource, LedgerLine, ProseSection,
    Question, RecordFinding, ReviewRecord, TriageEntry, VerifiedNote,
)
from review.types import SEVERITIES, Finding, ReviewMeta, severity_by_key
from review.verdict import verdict_line

# The line every rendered review.md carries. Agents write JSON, so nothing
# parses review.md; the banner tells a human the file is generated and that
# editing it is lost.
BANNER = "<!-- rendered from review.json — edits here are overwritten; change the record -->"

# Quoted in the Agent Failures section when a failed phase can be retried.
RECOVER_HINT = "Run `pr review --recover`"


def evidence_block(code: str) -> str:
    """Raw evidence as the indented, quoted fence a markdown finding carries it in."""
    return "\n".join(f"  > {line}".rstrip() for line in ["```", *code.split("\n"), "```"])


def ledger_line(entry: LedgerLine) -> str:
    """One `## Prior findings` list item."""
    where = f" `{entry.path}`" if entry.path else ""
    note = f" — {entry.note}" if entry.note else ""
    return f"- {finding_tag(entry.id)}{where} — {entry.disposition.value}{note}"


def triage_line(entry: TriageEntry) -> str:
    """One File Triage list item."""
    if not entry.path:
        return f"- {entry.note}"
    return f"- `{entry.path}` — {entry.note}" if entry.note else f"- `{entry.path}`"


def finding_line(f: RecordFinding, *, sids: bool = False, evidence: bool = False) -> str:
    """The finding as its review.md list item, continuation lines included.

    The location is rendered from the fields. A finding with no path has none,
    and its body says what it is about. `evidence` appends the quoted code
    after the body; only prompt text asks for it, and review.md never carries
    evidence (D9).
    """
    box = ""
    if f.checkbox:
        box = "[x] " if f.disposition is Disposition.FIXED else "[ ] "
    marker = sid_marker(f.sid) if sids and f.sid else ""
    where = f"{location_ref(f.path, f.line, f.end_line)} — " if f.path else ""
    first, sep, rest = f.body.partition("\n")
    head = f"- {box}{finding_tag(f.id)}{marker} {where}{first}".rstrip()
    if f.annotation:
        head = f"{head} {f.annotation}"
    line = head + (sep + rest if sep else "")
    return f"{line}\n{evidence_block(f.evidence)}" if evidence and f.evidence else line


def _continued(text: str) -> str:
    """A list item's later lines must be indented, or markdown ends the item."""
    first, *rest = text.split("\n")
    return "\n".join([first, *("  " + line if line else "" for line in rest)])


def _located(path: str | None, line: int | None, body: str) -> str:
    where = f"{location_ref(path, line, None)} — " if path else ""
    return f"{where}{_continued(body)}"


def question_line(q: Question) -> str:
    """One question, the same line in review.md and the comment. `[Q1]` is no severity key, so no finding reader matches it."""
    return f"- {finding_tag(q.id)} {_located(q.path, q.line, q.body)}"


def verified_line(v: VerifiedNote) -> str:
    """One verified note, the same line in review.md and the comment."""
    return f"- {_located(v.path, v.line, v.body)}"


def _coverage(area: FocusArea) -> str:
    return area.coverage if area.coverage else "*Not assessed this round.*"


def focus_line(area: FocusArea) -> str:
    """One `## Focus` list item for review.md, with rationale and paths when set."""
    asked = " *(requested)*" if area.source is FocusSource.USER else ""
    head = f"- **[{area.id}] {area.title}**{asked} — {_continued(_coverage(area))}"
    extra: list[str] = []
    if area.rationale:
        extra.append("  " + _continued(area.rationale))
    if area.paths:
        extra.append("  Paths: " + ", ".join(f"`{p}`" for p in area.paths))
    return "\n".join([head, *extra])


def claim_line(c: ClaimEntry) -> str:
    """One `## Claims` list item for review.md. The snippet is never rendered."""
    head = f"- **[{c.id}] {c.verdict.value}** *({c.source.value})* — {_continued(c.text)}"
    extra: list[str] = ["  " + _continued(c.evidence)]
    if c.path:
        extra.append(f"  Code: `{c.path}`")
    if c.finding:
        extra.append("  Finding: " + finding_tag(c.finding))
    return "\n".join([head, *extra])


def claims_body(claims: tuple[ClaimEntry, ...] | None) -> str:
    """The Claims section body: unassessed prose, or one line per entry."""
    if claims is None:
        return "*Not assessed this round.*"
    return "\n".join(claim_line(c) for c in claims)


def claims_comment(claims: tuple[ClaimEntry, ...] | None) -> str:
    """The GitHub-comment Claims lines: verdict, source, text; no C ids."""
    if claims is None:
        return "*Not assessed this round.*"
    lines: list[str] = []
    for c in claims:
        lines.append(f"- **{c.verdict.value}** *({c.source.value})* — {_continued(c.text)}")
        lines.append("  " + _continued(c.evidence))
    return "\n".join(lines)


def _behaviour_citation(b: BehaviourEntry) -> str:
    cited = f"test cited: `{b.test_cited}`" if b.test_cited else "no test found"
    note = f" *({b.note})*" if b.note else ""
    return f"{_continued(b.text)} — {cited}{note}"


def behaviour_line(b: BehaviourEntry) -> str:
    """One `## Test evidence` list item for review.md."""
    return f"- **[{b.id}]** {_behaviour_citation(b)}"


def behaviours_body(behaviours: tuple[BehaviourEntry, ...] | None) -> str:
    """The Test evidence section body: unassessed prose, or one line per entry."""
    if behaviours is None:
        return "*Not assessed this round.*"
    return "\n".join(behaviour_line(b) for b in behaviours)


def behaviours_comment(behaviours: tuple[BehaviourEntry, ...] | None) -> str:
    """The GitHub-comment Test evidence lines: text and citation; no B ids."""
    if behaviours is None:
        return "*Not assessed this round.*"
    return "\n".join(f"- {_behaviour_citation(b)}" for b in behaviours)


def focus_comment(areas: Sequence[FocusArea]) -> str:
    """The GitHub-comment Focus lines: title, requested mark, coverage."""
    lines = []
    for area in areas:
        asked = " *(requested)*" if area.source is FocusSource.USER else ""
        lines.append(f"- **{area.title}**{asked} — {_continued(_coverage(area))}")
    return "\n".join(lines)


def failures_body(failures: tuple[AgentFailure, ...]) -> str:
    """The Agent Failures table, and the recover hint when a retry could help."""
    lines = ["| Agent | Reason | Status |", "|-------|--------|--------|"]
    # A reason is free text — an RPC error, a quoted stderr line — and one
    # literal pipe in it shifts every later cell in the row.
    lines += [f"| {f.agent} | {escape_cell(f.reason)} | {f.status} |" for f in failures]
    if any(f.recoverable for f in failures):
        lines += ["", f"{RECOVER_HINT} to retry failed agents."]
    return "\n".join(lines)


def _comment_finding_line(f: Finding, *, label: bool = True) -> str:
    """One posted body finding. `review.dedup` reads this shape back off earlier posts."""
    tag = posted_finding_tag(f.posted_id, f.severity) if label else finding_tag(f.posted_id)
    where = f" {location_ref(f.path, f.line, f.end_line)} —" if f.path else ""
    return f"- {tag}{where} {f.body}"


def _by_place(f: Finding) -> tuple[bool, str, int]:
    return (not f.path, f.path or "", f.line or 0)


def _comment_prose(record: ReviewRecord, verdict: str) -> list[str]:
    """The Summary, closed by the verdict line, and its subsections."""
    summary = "\n\n".join(p for p in (record.summary.strip(), verdict) if p)
    lines = [f"## {SECTION_SUMMARY}", "", summary, ""] if summary else []
    for heading, body in (
        (SECTION_FOCUS, focus_comment(record.focus)),
        (SECTION_QUESTIONS, "\n".join(question_line(q) for q in record.questions)),
        (SECTION_CLAIMS, claims_comment(record.claims) if record.claims != () else ""),
        (SECTION_TEST_EVIDENCE, behaviours_comment(record.behaviours) if record.behaviours != () else ""),
    ):
        text = body.strip()
        if not text:
            continue
        lines += [f"### {heading}", "", text, ""]
    return [*lines, "---", ""] if summary else lines


def _comment_findings(findings: list[Finding], has_inline: bool) -> list[str]:
    declined = [f for f in findings if f.declined]
    by_sev: dict[str, list[Finding]] = {}
    by_file: dict[str, list[Finding]] = {}
    for f in findings:
        if f.declined:
            continue
        bucket = by_file if severity_by_key(f.severity).body_group == "by_file" else by_sev
        bucket.setdefault(f.severity, []).append(f)
    lines: list[str] = []
    if by_sev:
        lines += ["", "**Findings outside the diff:**", ""] if has_inline else [""]
        order = [s.key for s in SEVERITIES if s.body_group == "by_severity" and s.key in by_sev]
        headers = len(order) > 1 or bool(by_file)
        for sev in order:
            lines += [f"### {severity_by_key(sev).section}", ""] if headers else []
            lines += [_comment_finding_line(f) for f in by_sev[sev]] + [""]
    if by_file:
        lines.append("")
        for sev in (s.key for s in SEVERITIES if s.body_group == "by_file" and s.key in by_file):
            items = sorted(by_file[sev], key=_by_place)
            head = f"<summary>{severity_by_key(sev).section} ({len(items)})</summary>"
            lines += ["<details open>", head, "",
                      *(_comment_finding_line(f, label=False) for f in items), "", "</details>", ""]
    if declined:
        lines += ["", "**Declined — considered and not carried forward:**", "",
                  *(_comment_finding_line(f) for f in sorted(declined, key=_by_place)), ""]
    return lines


def _comment_tail(record: ReviewRecord) -> list[str]:
    static = record.static_analysis.strip()
    chunks: list[tuple[str, str]] = [("", static)] if static else []
    if record.failures:
        chunks.append((f"## {SECTION_FAILURES}", failures_body(record.failures)))
    chunks += [(f"## {s.heading}", s.body.strip()) for s in record.extra if s.body.strip()]
    return [p for h, b in chunks for p in (["", h, "", b] if h else ["", b])]


def _models_line(models: str) -> str:
    """The `Models:` line, with any whitespace (a newline in a model id) collapsed to single spaces."""
    return f"Models: {' '.join(models.split())}"


def comment_body(record: ReviewRecord, findings: list[Finding], *,
                 meta: ReviewMeta, has_inline: bool, severity_filter: set[str]) -> str:
    """The PR review comment's body: the record's prose around the body findings.

    `findings` are the ones `review.format` placed in the body, already
    renumbered for posting. A declined finding is stated in a block of its own.
    `meta` supplies the verdict line's mode and status and the `Models:` line.
    """
    labels = ", ".join(s.label for s in SEVERITIES if s.key in severity_filter)
    lines = _comment_prose(record, verdict_line(record, meta))
    if has_inline:
        lines.append(f"Have some comments marked as {labels}.")
    elif findings:
        lines.append(f"Review findings ({labels}):")
    body = "\n".join(
        lines + _comment_findings(findings, has_inline)
        + _verified_comment(record) + _comment_tail(record),
    ).rstrip("\n")
    # Only beside something else: a review with nothing to post still posts nothing.
    return f"{body}\n\n<sub>{_models_line(meta.models)}</sub>" if body and meta.models else body


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


def _verified_comment(record: ReviewRecord) -> list[str]:
    if not record.verified:
        return []
    head = f"<details><summary>{SECTION_VERIFIED} ({len(record.verified)})</summary>"
    return ["", head, "", *(verified_line(v) for v in record.verified), "", "</details>"]


def _sections(record: ReviewRecord, sids: bool, evidence: bool,
              verdict: str = "") -> list[ProseSection]:
    """Every section the record holds, in spec §2's order, with today's Claims, Test evidence and harness sections slotted in: File Triage, Agent Failures, Summary, Focus, Questions, Claims, Test evidence, findings, Verified, Prior findings, extra, Static Analysis."""
    sections: list[ProseSection] = []
    if record.triage is not None:
        sections.append(ProseSection(SECTION_FILE_TRIAGE, "\n".join(triage_line(e) for e in record.triage)))
    if record.failures:
        sections.append(ProseSection(SECTION_FAILURES, failures_body(record.failures)))
    lead = "\n\n".join(p for p in (record.summary, verdict) if p)
    if lead:
        sections.append(ProseSection(SECTION_SUMMARY, lead))
    if record.focus:
        sections.append(ProseSection(
            SECTION_FOCUS, "\n".join(focus_line(a) for a in record.focus),
        ))
    if record.questions:
        sections.append(ProseSection(
            SECTION_QUESTIONS, "\n".join(question_line(q) for q in record.questions),
        ))
    if record.claims is None or record.claims:
        sections.append(ProseSection(SECTION_CLAIMS, claims_body(record.claims)))
    if record.behaviours is None or record.behaviours:
        sections.append(ProseSection(SECTION_TEST_EVIDENCE, behaviours_body(record.behaviours)))
    sections += _severity_sections(record, sids, evidence)
    if record.verified:
        sections.append(ProseSection(
            SECTION_VERIFIED, "\n".join(verified_line(v) for v in record.verified),
        ))
    if record.ledger:
        sections.append(ProseSection(SECTION_PRIOR_FINDINGS, "\n".join(ledger_line(e) for e in record.ledger)))
    sections += list(record.extra)
    if record.static_analysis:
        sections.append(ProseSection(SECTION_STATIC_ANALYSIS, record.static_analysis))
    return sections


def render_body(record: ReviewRecord, *, sids: bool = False, evidence: bool = False,
                omit: frozenset[str] = frozenset(), verdict: str = "") -> str:
    """The `## ` sections of `record`, without title or header. `omit` names headings.

    `verdict` is the generated line that closes the Summary, not a stored field.
    """
    return "\n".join(
        f"## {s.heading}\n\n{s.body.strip()}\n" if s.body.strip() else f"## {s.heading}\n"
        for s in _sections(record, sids, evidence, verdict) if s.heading not in omit
    )


def render_findings(record: ReviewRecord, *, sids: bool = False, evidence: bool = False) -> str:
    """Only the severity sections — what a re-review's scoped prior section shows."""
    return "\n".join(f"## {s.heading}\n{s.body}" for s in _severity_sections(record, sids, evidence)).strip()


def render_review(record: ReviewRecord, meta: ReviewMeta, *, sids: bool = False,
                  omit: frozenset[str] = frozenset(), banner: bool = True) -> str:
    """The whole review.md: title and header from `meta`, banner, then the body.

    Prompt text passes `banner=False` and gets no banner, no `Models:` line and
    no verdict line. Agents write JSON, so nothing parses review.md; the banner
    tells a human the file is generated and that editing it is lost.
    """
    header = ReviewHeader.from_meta(meta).render()
    mark = f"{BANNER}\n" if banner else ""
    # Prompt text leaves it out: which models ran is not a prior reviewer's claim.
    models = f"{_models_line(meta.models)}\n\n" if banner and meta.models else ""
    line = verdict_line(record, meta) if banner else ""
    return f"{review_title(meta)}\n\n{header}{mark}\n{models}{render_body(record, sids=sids, omit=omit, verdict=line)}"
