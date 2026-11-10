"""What a set of counts means, and the review a run without an agent writes.

The policy layer over `review.document`: the verdict is derived from the
findings, status and mode, how that reads in prose, whether the run states a
verdict at all, and the body a mechanically merged review carries when no
synthesis agent produced one.

Kept apart from the document itself because the document is a shape and this is
a judgement — a stored review reads the same way whatever this module decides.

A review where no group reported — every agent failed, so its header says
`status: error` — records no verdict: its empty tally means nothing was read,
not that nothing was found. It states `NO_RUN_VERDICT` above its Agent Failures
table, and `pr review` exits 1 once it has shown and recorded it. A partial run
records only a blocking verdict (none in place of Approve) and exits 0, since
the surviving findings are real.
"""

# doc-group: pipeline

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from core.phases import Mode
from pr.domains import ReviewStatus, ReviewVerdict
from review.document import ReviewDocument
from review.record import Disposition, ReviewRecord
from review.types import ReviewMeta, SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD
from core.text import plural


def open_counts(doc: ReviewDocument | None) -> dict[str, int]:
    """How many outstanding findings of each severity `doc` declares, zeroed when absent.

    The one place a review that was never written is read as one that found
    nothing. A caller reporting counts has no separate answer for absent — a
    listing prints zeroes either way — so the substitution is made once here
    rather than restated at every reader. `resolve_review_verdict` is the
    reader that does have a separate answer, and takes the same nullable
    document to give it.
    """
    if doc is None:
        return {s.key: 0 for s in SEVERITIES}
    return doc.open_counts


def verdict_from_counts(counts: dict[str, int]) -> ReviewVerdict:
    """The verdict a tally of open findings supports on its own.

    The counts alone, with no prose read: what a mechanically merged
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


def states_verdict(mode: Mode | None) -> bool:
    """Whether a review run in `mode` reaches a verdict at all.

    False for a self-review, true for everything else — a review whose
    metadata never stated a mode included, since a missing mode is not a claim
    that the review had no PR. A self-review is advisory: there is nothing to
    approve or block, so the verdict it would otherwise state is a call on
    nothing. Every path that writes or reads a verdict asks here rather than
    testing the mode for itself.
    """
    return mode is not Mode.SELF


# What a partial run with nothing blocking open states. An incomplete run
# cannot vouch that nothing blocks; writers still store this until Task 4.
PARTIAL_VERDICT = (
    "Review incomplete: part of this review did not run, so it cannot vouch "
    "that nothing blocks. See Agent Failures for what was not assessed."
)

# What a run where no group reported states. Distinct from `PARTIAL_VERDICT`
# because "part of this review" understates a run with nothing in it.
NO_RUN_VERDICT = (
    "No part of this review ran. See Agent Failures for why."
)

# The sentence a partial run's blocking verdict ends with.
PARTIAL_NOTE = (
    "Review incomplete: see Agent Failures for what was not assessed."
)


def resolve_review_verdict(record: ReviewRecord, meta: ReviewMeta) -> ReviewVerdict | None:
    """The verdict a review states, from its findings, how its run ended and its mode.

    None for a self-review (advisory: nothing to approve or block) and for a run
    `meta.status` records as error (nothing ran, so its empty tally is not a clean
    one). Otherwise any open must-fix asks for changes and any open should-fix needs
    discussion, a partial run included: the blockers it found are real. Nothing open
    approves, except on a partial run, which states none — an incomplete run cannot
    vouch for the absence of findings. Open is `ReviewRecord.unfixed`: only a fixed
    finding closes. Nothing an agent wrote is read.
    """
    if meta.mode is Mode.SELF or meta.status is ReviewStatus.ERROR:
        return None
    counts = record.open_counts
    verdict = ReviewVerdict.from_counts(counts[SEVERITY_MUST], counts[SEVERITY_SHOULD])
    # A partial run may have left files unread: the blockers it found stand, but it
    # cannot vouch that nothing blocks.
    if verdict is ReviewVerdict.APPROVE and meta.status is ReviewStatus.PARTIAL:
        return None
    return verdict


def _rationale(record: ReviewRecord) -> str:
    blocking = [f for f in record.unfixed if f.severity in (SEVERITY_MUST, SEVERITY_SHOULD)]
    if not blocking:
        return "No must-fix or should-fix finding is open."
    declined = Counter(f.severity for f in blocking if f.disposition is Disposition.DECLINED)
    still = Counter(f.severity for f in blocking if f.disposition is not Disposition.DECLINED)
    parts = [f"{counts_prose(still)} open"] if still else []
    parts += [f"{counts_prose(declined)} declined, still counted"] if declined else []
    return "; ".join(parts) + "."


def verdict_line(record: ReviewRecord, meta: ReviewMeta) -> str:
    """The one line review.md and the comment state the verdict in; "" for a self-review.

    `**Verdict: Needs discussion.** 1 should-fix open.` — the rationale counts rather
    than naming IDs, because the comment renumbers findings by place. An error run,
    and a partial run with nothing blocking open, say why they state none; a partial
    run's blocking verdict says the review is incomplete.
    """
    if meta.mode is Mode.SELF:
        return ""
    if meta.status is ReviewStatus.ERROR:
        return f"**No verdict.** {NO_RUN_VERDICT}"
    verdict = resolve_review_verdict(record, meta)
    if verdict is None:
        return f"**No verdict.** {PARTIAL_VERDICT}"
    tail = f" {PARTIAL_NOTE}" if meta.status is ReviewStatus.PARTIAL else ""
    return f"**Verdict: {verdict.prose}.** {_rationale(record)}{tail}"


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
# `summary_note` a caller of `build_mechanical_record` hands in. Each names why
# synthesis did not produce the document, because a reader who cannot tell a
# failed agent from one nobody asked to run reads the same review two ways.
FALLBACK_SUMMARY = "Synthesis agent failed — findings below are from individual group reviews."
SKIPPED_SUMMARY = "Synthesis skipped by --no-synthesis — findings below are from individual group reviews."
BUDGET_SUMMARY = (
    "Synthesis did not run — the cost budget was reached first. "
    "Findings below are from individual group reviews."
)
CLEAN_SUMMARY = "Synthesis did not run — no group reported a finding."
NO_CHANGES_SUMMARY = (
    "No agent ran — the author has committed nothing since the prior review. "
    "Its findings are carried forward below unchanged."
)
SETTLED_SUMMARY = (
    "Synthesis did not run — no group reported a new finding, and every prior "
    "finding was settled without it."
)


def _summary_lead(counts: dict[str, int], *, group_count: int, summary_note: str,
                  file_count: int, groups_reported: int | None) -> str:
    """The one-sentence scope and reason a mechanical Summary opens with."""
    total = sum(counts.values())
    count_summary = f"{total} finding{plural(total)}" if total else "No findings"
    # Nothing reported is not the same as nothing found, and the tally cannot
    # tell them apart: both are zero. Saying "No findings" for a run where
    # every group died is the review claiming a clean read of source no agent
    # opened — the one sentence a reader acts on before reaching the failures
    # table underneath it.
    if groups_reported == 0 and group_count:
        count_summary = "No group reported"
    # A run that grouped nothing states no scope. `group_count` of 0 is the path
    # that never reached the group phase at all, where "across 120 files in 0
    # groups" would report a surface this run did not look at.
    groups_seen = group_count if groups_reported is None else groups_reported
    group_scope = (
        f"{groups_seen} groups" if groups_seen == group_count
        else f"{groups_seen} of {group_count} groups"
    )
    if not group_count:
        scope = ""
    elif file_count:
        scope = f" across {file_count} file{plural(file_count)} in {group_scope}"
    else:
        scope = f" across {group_scope}"
    return f"{count_summary}{scope}. {summary_note}"


def build_mechanical_record(
    merged: ReviewRecord, *, group_count: int, summary_note: str,
    include_verdict: bool = True, verdict: str = "", file_count: int = 0,
    groups_reported: int | None = None,
) -> ReviewRecord:
    """`merged` as a whole review no synthesis agent read.

    Text already on `merged.summary` (a verification drop note) follows the
    scope sentence, inside the Summary, which is where the text pipeline put it.
    """
    counts = merged.open_counts
    lead = _summary_lead(counts, group_count=group_count, summary_note=summary_note,
                         file_count=file_count, groups_reported=groups_reported)
    summary = f"{lead}\n\n{merged.summary}" if merged.summary else lead
    stated = (verdict or mechanical_verdict(counts)).strip() if include_verdict else ""
    return replace(merged, summary=summary, verdict=stated)
