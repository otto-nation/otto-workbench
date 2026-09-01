"""What a set of counts means, and the review a run without an agent writes.

The policy layer over `review_document`: how many open findings of each
severity add up to an approve or a request-for-changes, how that reads in
prose, and the body a mechanically merged review carries when no synthesis
agent produced one.

Kept apart from the document itself because the document is a shape and this is
a judgement — a review file parses the same way whatever this module decides.
"""

# doc-group: pipeline

from __future__ import annotations

from pr_domains import ReviewVerdict
from review_document import SECTION_SUMMARY, SECTION_VERDICT, ReviewDocument
from review_types import SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD
from text import plural


def open_counts(doc: ReviewDocument | None) -> dict[str, int]:
    """How many outstanding findings of each severity `doc` declares, zeroed when absent.

    The one place a review that was never written is read as one that found
    nothing. A caller reporting counts has no separate answer for absent — a
    listing prints zeroes either way — so the substitution is made once here
    rather than restated at every reader. `resolve_review_verdict` is the
    reader that does have a separate answer, and takes the same nullable
    document to give it.
    """
    return doc.open_counts if doc else ReviewDocument().open_counts


def verdict_from_counts(counts: dict[str, int]) -> ReviewVerdict:
    """The verdict a tally of open findings supports on its own.

    The counts alone, with no prose read: what `resolve_review_verdict`
    reconciles the agent's stated call against, what a mechanically merged
    review states outright for want of an agent, and what a drop leaves a
    stated verdict to be lowered to. A severity the tally omits counts as none
    of that severity, so a partial tally is read rather than refused.
    """
    return ReviewVerdict.from_counts(
        counts.get(SEVERITY_MUST, 0), counts.get(SEVERITY_SHOULD, 0),
    )


def counts_prose(counts: dict[str, int]) -> str:
    """A tally read out as prose — `2 must-fix, 1 nit` — or "" when it is empty.

    Severities in the order `SEVERITIES` declares them, and one the review has
    none of is left out rather than written as a zero. What a caller says
    instead of the empty string is its own: a verdict reads "no findings" where
    a count summary reads nothing at all.
    """
    return ", ".join(f"{counts[s.key]} {s.label}" for s in SEVERITIES if counts.get(s.key))


def resolve_review_verdict(
    doc: ReviewDocument | None, *, self_review: bool = False,
) -> ReviewVerdict | None:
    """The verdict to record and report for a finished review.

    The prose the synthesis agent wrote and the findings that survived
    verification are two readings of the same document, and this is the only
    place they are reconciled: the stronger call wins, so the prose can never
    under-report findings that block, and the counts can never quietly discard
    a stronger call the agent made. Disapprove is unranked and always stands —
    no count implies it and none refutes it.

    A review that was never written reaches no verdict at all, which is why
    this takes the document rather than a path: absent and empty are different
    answers and only the caller that went looking can tell them apart.
    """
    if doc is None:
        return None
    stated = doc.verdict
    if stated is ReviewVerdict.DISAPPROVE:
        return stated
    # A self-review is advisory — it has no PR to approve or block. Disapprove
    # is the exception above: it judges the approach, which holds without a PR.
    if self_review:
        return None
    derived = verdict_from_counts(doc.open_counts)
    return stated if stated and stated.outranks(derived) else derived


# Stamped into a mechanically written verdict so a reader — and the pipeline's
# own check for whether synthesis ran — can tell one from a verdict an agent
# reached.
MECHANICAL_NOTE = "(mechanically merged, not synthesized)"


def mechanical_verdict(counts: dict[str, int]) -> str:
    """The `## Verdict` body a tally supports, said without an agent.

    What the review states when synthesis did not run: the call
    `verdict_from_counts` derives, the tally read out, and `MECHANICAL_NOTE` so
    the absence of a synthesis is on the page rather than inferred from the
    prose being terse.
    """
    prose = counts_prose(counts)
    if not prose:
        return f"{ReviewVerdict.APPROVE.prose} — no findings {MECHANICAL_NOTE}.\n"
    verdict = verdict_from_counts(counts)
    suffix = " only" if verdict is ReviewVerdict.APPROVE else ""
    return f"{verdict.prose} — {prose}{suffix} {MECHANICAL_NOTE}.\n"


# The `## Verdict` a run that found nothing states. It carries no
# `MECHANICAL_NOTE`: the note exists to say a merge stood in for a synthesis
# that had findings to weigh, and a review with none had nothing to synthesize.
CLEAN_VERDICT = f"{ReviewVerdict.APPROVE.prose} — clean review.\n"


# What the Summary says when no synthesis agent wrote the review — the
# `summary_note` a caller of `build_mechanical_body` hands in. Each names why
# synthesis did not produce the document, because a reader who cannot tell a
# failed agent from one nobody asked to run reads the same review two ways.
FALLBACK_SUMMARY = "Synthesis agent failed — findings below are from individual group reviews."
SKIPPED_SUMMARY = "Synthesis skipped by --no-synthesis — findings below are from individual group reviews."
BUDGET_SUMMARY = (
    "Synthesis did not run — the cost budget was reached first. "
    "Findings below are from individual group reviews."
)
CLEAN_SUMMARY = "Synthesis did not run — no group reported a finding."


def build_mechanical_body(
    merged_content: str,
    *,
    group_count: int,
    summary_note: str,
    include_verdict: bool = True,
    verdict: str = "",
    file_count: int = 0,
) -> str:
    """A whole review body around findings no synthesis agent read.

    `merged_content` is the sections as merged — the file triage every group
    wrote and whatever findings they reported; `summary_note` is the caller's
    one sentence on why synthesis was skipped, which is the only part of the
    summary that is not derived from the findings themselves. `file_count` of 0
    leaves the file scope out rather than writing a zero.

    `include_verdict=False` writes no verdict section at all, for a path that
    reaches no verdict — the two that ship group output after the operator or
    the budget stopped the run. `verdict` overrides what a verdict section
    says, defaulting to the one `mechanical_verdict` derives from the tally.

    Every path that reaches the review file without a synthesis agent composes
    here, so which sections such a review carries has one answer. A caller that
    assembles its own body decides that question again, which is how a clean
    run came to drop the triage its groups had already merged.
    """
    counts = ReviewDocument(body=merged_content).open_counts
    total = sum(counts.values())
    count_summary = f"{total} finding{plural(total)}" if total else "No findings"
    if file_count:
        scope = f"across {file_count} file{plural(file_count)} in {group_count} groups"
    else:
        scope = f"across {group_count} groups"
    body = (
        f"## {SECTION_SUMMARY}\n"
        f"{count_summary} {scope}. "
        f"{summary_note}\n\n"
        f"{merged_content}\n"
    )
    if not include_verdict:
        return body
    return f"{body}\n## {SECTION_VERDICT}\n{verdict or mechanical_verdict(counts)}"
