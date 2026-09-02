"""What a prompt says about the review that came before it.

A re-review is shown the prior review scoped to the files it is reviewing,
stripped of the sections that were bookkeeping rather than findings, annotated
with what the author said in reply to each finding, and followed by the
instruction that asks for a disposition for every finding it carries.

Scoping the prior review to a group's files cuts it a finding at a time, and
where a finding stops is `review_spans`'s `finding_spans` — the same measure
the gates that trim a finished review use. A section that measured it here
would quote an agent evidence belonging to a finding it was not shown.

`_LEDGER_INSTRUCTION` is the one place the ledger's shape is written down for
an agent; both sections here interpolate it rather than restating it, and
`review_reconcile` is what reads the ledger back afterwards.

Everything else a prompt says about the PR is `review_prompt_sections`'s, and
which sections a phase asks for is `review_prompt`'s.
"""

# doc-group: pipeline

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from review_document import (
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS, strip_sections,
)
from review_grammar import BOLD_FINDING_ID_RE, SCOPED_FINDING_RE
from review_merge import annotate_prior_with_stable_ids
from review_reply_threads import ReplyThreads
from review_spans import finding_spans
from review_types import PriorDisposition, PriorFinding, ReplyState


def _in_scope(line: str, filter_set: set[str]) -> bool:
    """Whether the finding line names a file the scoped review is about."""
    m = SCOPED_FINDING_RE.match(line)
    return bool(m) and m.group(1) in filter_set


@dataclass(frozen=True)
class _ScopedSection:
    """One `## ` heading of the prior review and the in-scope findings under it."""

    header: str
    lines: list[str] = field(default_factory=list)


def _collect_scoped_sections(
    prior_text: str, filter_set: set[str],
) -> list[_ScopedSection]:
    """Each `## ` section of `prior_text` and the in-scope findings under it.

    `SCOPED_FINDING_RE` picks the findings; `finding_spans` says how much of the
    text each one brings with it, so a finding kept for its path keeps the
    evidence quoted under it and nothing quoted under its neighbour. Sections
    come back in document order, empty ones included — the caller decides
    whether a heading with no findings left is worth printing.
    """
    lines = prior_text.split("\n")
    headers = [i for i, line in enumerate(lines) if line.strip().startswith("## ")]
    sections = [_ScopedSection(lines[i]) for i in headers]
    for span in finding_spans(prior_text):
        owner = bisect.bisect_right(headers, span.start) - 1
        if owner < 0 or not _in_scope(span.line, filter_set):
            continue
        sections[owner].lines.extend(span.text_of(prior_text).split("\n"))
    return sections


def _scope_prior_review(prior_text: str, file_filter: list[str]) -> str:
    filter_set = set(file_filter)
    sections = _collect_scoped_sections(prior_text, filter_set)

    parts: list[str] = []
    for section in sections:
        if section.lines:
            parts.append(section.header)
            parts.extend(section.lines)
    return "\n".join(parts).strip()


# Coverage bookkeeping and machine-generated output, not prior reviewer claims.
# The scoped path drops both already (their lines carry no finding ID), so
# stripping here keeps the unscoped prompts consistent with it.
_PRIOR_EXCLUDED_SECTIONS = {
    SECTION_FILE_TRIAGE.lower(),
    SECTION_PRIOR_FINDINGS.lower(),
    SECTION_STATIC_ANALYSIS.lower(),
}


def _strip_internal_sections(prior_text: str) -> str:
    return strip_sections(prior_text, _PRIOR_EXCLUDED_SECTIONS).strip()


_STATE_LABELS = {
    ReplyState.CONTESTED: "[CONTESTED]",
    ReplyState.ACKNOWLEDGED: "[ACKNOWLEDGED]",
    ReplyState.RESOLVED: "[RESOLVED]",
    ReplyState.REPLIED: "[REPLIED]",
}


def _annotate_with_thread_state(review_text: str, reply_threads: ReplyThreads | None) -> str:
    threads = reply_threads.threads if reply_threads else []
    if not threads:
        return review_text
    id_to_state = {}
    for t in threads:
        fid = t.get("finding_id", "")
        if fid and t["state"] in _STATE_LABELS:
            id_to_state[fid] = _STATE_LABELS[t["state"]]
    if not id_to_state:
        return review_text
    lines = review_text.split("\n")
    result = []
    for line in lines:
        m = BOLD_FINDING_ID_RE.search(line)
        if m and m.group(1) in id_to_state:
            label = id_to_state[m.group(1)]
            line = f"{line}  {label}"
        result.append(line)
    return "\n".join(result)


# The disposition ledger every re-review must emit. Reconciliation matches a
# prior finding on its ID or its path — the two parts an agent restates
# verbatim — so the instruction asks for exactly those, and never for the
# internal sid marker, which nothing downstream requires the agent to echo. The
# verdict words come from the enum the ledger is parsed with, so asking for
# a word the parser does not know is not expressible here.
#
# Where the verdict sits in the line is not so protected: an example the parser
# rejects is expressible, and stays invisible until a re-review's bookkeeping
# is lost. So the instruction says where the verdict goes as well as what it
# is, and `TestLedgerInstructionParses` reads every example back through
# `review_grammar.parse_ledger_line` to hold the two together.
_LEDGER_INSTRUCTION = f"""
End your output with a `## {SECTION_PRIOR_FINDINGS}` section listing EVERY prior
finding above, one line each, copying its ID and path exactly as written there:
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.FIXED}` when the change resolves it
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.STILL_OPEN}` when it does not, and
  carry the finding forward into the severity sections as well
- `- **[M1]** \\`path/to/file.py\\` — {PriorDisposition.DECLINED}` when it was considered and
  rejected on the merits — a documented tradeoff (a `ceiling:` marker, a commit
  message or a prior reply explaining the choice), or something the prior review
  itself already recorded as declined. Carry it forward too, but annotated
  `*(declined — one-line reason)*` so it is not raised or auto-fixed again. A
  declined finding stays declined: never downgrade one to {PriorDisposition.STILL_OPEN}
Write the verdict word first, before any explanation of it, and let it end the
line or be followed by a dash, a colon or a full stop. A verdict qualified in
the same breath ("{PriorDisposition.FIXED}, but only on the happy path") is not
read as a verdict at all.
This section is bookkeeping — it is stripped before the review is published, and
a prior finding missing from it is reported as unaccounted for."""


def _build_prior_section(
    prior_review: str,
    context: str = "",
    file_filter: list[str] | None = None,
    reply_threads: ReplyThreads | None = None,
) -> str:
    if not prior_review:
        return ""
    review_text = _strip_internal_sections(annotate_prior_with_stable_ids(prior_review))
    if file_filter:
        review_text = _scope_prior_review(review_text, file_filter)
    if not review_text:
        return ""
    # A frozen dataclass is truthy even with an empty `threads` list, unlike the
    # dict this replaced — so the emptiness has to be asked of the field itself.
    if reply_threads and reply_threads.threads:
        review_text = _annotate_with_thread_state(review_text, reply_threads)
    return f"""
## Prior review
{context}
{_LEDGER_INSTRUCTION}

<prior_review>
{review_text}
</prior_review>"""


# What synthesis is told about the prior findings the group agents passed over.
# It asks for a decision, not for a restatement: a finding whose subject is
# still in the tree may well be right to decline, and the outcome this exists to
# prevent is the third one — the document saying nothing about it at all, which
# the next round cannot tell apart from the finding never having existed.
_UNACCOUNTED_CTX = """These prior findings reached no disposition: the group
agents did not mention them, and nothing in the tree confirmed the issue was
resolved. Decide each one here, on the text below and the merged findings
above. Omitting one is not a third option — a prior finding this document
does not mention is reported as unaccounted for, and comes back unsettled
next round. Your output holds one `## Prior findings` section total: if the
merged findings above already carry one, add these findings' verdicts as
lines to that same section instead of writing a second one; only write it
fresh below if none exists yet."""


def _build_unaccounted_section(findings: list[PriorFinding]) -> str:
    """The prior findings synthesis must dispose of, with the text to judge them on.

    The findings come before `_LEDGER_INSTRUCTION` rather than after it, which
    is the reverse of `_build_prior_section`: the instruction asks for a line
    per finding above it, and here the only findings synthesis has been shown
    are these. Restating the verdict forms in this section's own words would
    make it the second place the ledger's shape is written down.
    """
    if not findings:
        return ""
    reported = "\n\n".join(finding.text.strip() for finding in findings)
    return f"""
## Prior findings awaiting a disposition
{_UNACCOUNTED_CTX}

<unaccounted_findings>
{reported}
</unaccounted_findings>
{_LEDGER_INSTRUCTION}"""
