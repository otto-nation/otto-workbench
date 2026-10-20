"""What a prompt says about the review that came before it.

A re-review is shown the prior review scoped to the files it is reviewing,
stripped of the sections that were bookkeeping rather than findings, annotated
with what the author said in reply to each finding, and followed by the
instruction that asks for a disposition for every finding it carries.

Scoping the prior review to a group's files keeps findings that name a line of
one of those files. A finding without a line is file-level and is not in any
group's slice.

`_LEDGER_INSTRUCTION` is the one place the ledger's shape is written down for
an agent; both sections here interpolate it rather than restating it, and
`review.reconcile` is what reads the ledger back afterwards.

Everything else a prompt says about the PR is `review.prompt_sections`'s, and
which sections a phase asks for is `review.prompt`'s.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import replace

from review.document import (
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS,
)
from review.grammar import BOLD_FINDING_ID_RE, SID_MARKER_RE
from review.record import ReviewRecord
from review.render import render_body, render_findings, render_review
from review.reply_threads import ReplyThreads
from review.store import StoredReview
from review.types import (
    DISPOSITION_TAIL_PROSE, PriorDisposition, PriorFinding, ReplyState,
)
from core.text import join_or


def _with_sids(record: ReviewRecord) -> ReviewRecord:
    """Stamp each finding with the sid a later round recognises it by."""
    return replace(
        record,
        findings=tuple(replace(f, sid=f.sid or f.stable_id) for f in record.findings),
    )


def _scoped(record: ReviewRecord, files: list[str]) -> ReviewRecord:
    """The prior record cut to findings that name a line of one of `files`."""
    keep = set(files)
    return replace(
        record,
        findings=tuple(
            f for f in record.findings if f.line is not None and f.path in keep
        ),
        severity_sections=(),
    )


# Coverage bookkeeping and machine-generated output, not prior reviewer claims.
_PRIOR_EXCLUDED_SECTIONS = frozenset({
    SECTION_FILE_TRIAGE,
    SECTION_PRIOR_FINDINGS,
    SECTION_STATIC_ANALYSIS,
})


def _prior_text(prior: StoredReview, file_filter: list[str] | None) -> str:
    """What a re-review is shown of its predecessor; carried findings keep their sid."""
    record = _with_sids(prior.record)
    if file_filter:
        return render_findings(_scoped(record, file_filter), sids=True)
    body = render_body(record, sids=True, omit=_PRIOR_EXCLUDED_SECTIONS).strip()
    if not body:
        return ""
    return render_review(
        record, prior.meta, sids=True, omit=_PRIOR_EXCLUDED_SECTIONS, banner=False,
    ).strip()


_STATE_LABELS = {
    ReplyState.CONTESTED: "[CONTESTED]",
    ReplyState.ACKNOWLEDGED: "[ACKNOWLEDGED]",
    ReplyState.RESOLVED: "[RESOLVED]",
    ReplyState.REPLIED: "[REPLIED]",
}


def _state_of(line: str, by_sid: dict[str, str], by_id: dict[str, str]) -> str:
    """The label the thread on `line`'s finding earns, or empty for no thread.

    The stable ID answers first. A thread's `[M1]` is the number its comment was
    posted under, which `renumber_for_posting` assigned by diff position, so it
    names a different finding in the review file whenever anything above it
    moved. The number is the fallback for a comment posted before the marker
    existed, where a wrong-but-adjacent label beats none at all.
    """
    sid = SID_MARKER_RE.search(line)
    if sid and sid.group(1) in by_sid:
        return by_sid[sid.group(1)]
    m = BOLD_FINDING_ID_RE.search(line)
    return by_id.get(m.group(1), "") if m else ""


def _annotate_with_thread_state(review_text: str, reply_threads: ReplyThreads | None) -> str:
    threads = reply_threads.threads if reply_threads else []
    by_sid: dict[str, str] = {}
    by_id: dict[str, str] = {}
    for t in threads:
        if t["state"] not in _STATE_LABELS:
            continue
        label = _STATE_LABELS[t["state"]]
        if t.get("stable_id"):
            by_sid[t["stable_id"]] = label
        elif t.get("finding_id"):
            by_id[t["finding_id"]] = label
    if not by_sid and not by_id:
        return review_text
    result = []
    for line in review_text.split("\n"):
        label = _state_of(line, by_sid, by_id)
        result.append(f"{line}  {label}" if label else line)
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
# `review.grammar.parse_ledger_line` to hold the two together.
#
# Which breaks may follow the verdict is named rather than listed as glyphs: a
# model reads "a dash" better than it reads three dash characters it has to tell
# apart. The names are the parser's own — `DISPOSITION_TAIL_PROSE` maps every
# accepted character to the words for it — so the sentence cannot describe a
# break the parser rejects, or omit one it accepts, which hand-typing it did
# twice.
_TAIL_BREAK_NAMES = list(dict.fromkeys(DISPOSITION_TAIL_PROSE.values()))
_TAIL_BREAKS = join_or(_TAIL_BREAK_NAMES)
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
line or be followed by whatever introduces that explanation:
{_TAIL_BREAKS} around the detail.
A verdict qualified in the same breath ("{PriorDisposition.FIXED}, but only on
the happy path") is not read as a verdict at all.
This section is bookkeeping — it is stripped before the review is published, and
a prior finding missing from it is reported as unaccounted for."""


def _build_prior_section(
    prior: StoredReview | None,
    context: str = "",
    file_filter: list[str] | None = None,
    reply_threads: ReplyThreads | None = None,
) -> str:
    if prior is None:
        return ""
    review_text = _prior_text(prior, file_filter)
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
