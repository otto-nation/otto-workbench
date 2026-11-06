"""The claims ledger: the check, the fingerprint and the record entries.

This module owns whether a run has a claim source, the input fingerprint, the
run-context check composed with focus coverage, turning a writer's reports
into record entries after the check, and the degraded-check warning.
Renderers take claims off the record and never import it.

The record is tri-state. `None` is not assessed — a writer that did not run, a
check that degraded, or a legacy `review.json`. `()` is assessed with nothing
checkable, or no claim source. Entries are the assessed ledger.

Writers are the single agent and synthesis only. Scout, holistic and group get
no claims fields. Agentless paths — clean, settled, `--no-synthesis`,
low-effort, a budget cut, all-groups-failed, mechanical fallback, `--repair`
rebuild — record `None`. When a description or ticket existed, that renders
"*Not assessed this round.*" A multi-phase run that never reaches synthesis
therefore shows Not assessed; that is the honest result.

The check rides ingest `check=` with one repair turn. Caps (12 claims, 512
bytes per `text` / `evidence` / `snippet`) live inside the check, so an
over-cap list degrades rather than rejects. Leftover claims errors after the
repair set `claims` to `None` and never cost the review. A first load that
fails `problems()` or schema never runs the claims check, so the repair prompt
does not mention claims and the second load degrades without that turn having
named them.

`verify.post_process_record` forces a supported or contradicted claim whose
snippet is not in its file to unverifiable, with a note, using the same
evidence matcher findings use.

The fingerprint hashes `pr.body` plus the rendered, truncated issue section.
It is stamped only when claims were assessed, so a prior round of `None`
cannot no-op past an unassessed ledger. A true no-op carries prior claims:
the gate now proves description, ticket and code unchanged, so the prior
verdicts still stand. Focus coverage is not carried on a no-op because areas
can change on that same round. The GitHub issue fetch is title and body only
— comments are not the ticket's claims and would move the fingerprint on
every reply.
"""

# doc-group: pipeline

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace

import review.budget
import review.focus
import review.prompt_sections
from review.artifacts import ReviewArtifact
from review.claim_types import ClaimEntry, ClaimReport, ClaimVerdict
from review.grammar import strip_line_suffix
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review.types import ReviewJob

CLAIM_KINDS = frozenset({
    IngestErrorKind.MISSING_CLAIMS,
    IngestErrorKind.CLAIM_COUNT,
    IngestErrorKind.CLAIM_SIZE,
    IngestErrorKind.BAD_CLAIM,
})

_CITE = frozenset({ClaimVerdict.SUPPORTED, ClaimVerdict.CONTRADICTED})
_SIZE_FIELDS = ("text", "evidence", "snippet")


def _error(kind: IngestErrorKind, at: str, detail: str) -> IngestError:
    return IngestError(kind, 0, detail, at=at)


def has_claim_source(job) -> bool:
    return bool(job.pr.body.strip() or job.issue_context.strip())


def input_sha(job) -> str:
    section = review.prompt_sections._build_issue_section(
        job.issue_link, job.issue_context,
    )
    if not job.pr.body and not section:
        return ""
    payload = job.pr.body + "\0" + section
    return hashlib.sha256(payload.encode()).hexdigest()


def claims_problems(
    claims: tuple[ClaimReport, ...] | None,
    finding_ids: Collection[str],
    *,
    has_claim_source: bool,
) -> tuple[IngestError, ...]:
    if not has_claim_source:
        return ()
    if claims is None:
        return (_error(
            IngestErrorKind.MISSING_CLAIMS, "$.claims",
            "write claims for the description or ticket",
        ),)
    out: list[IngestError] = []
    if len(claims) > review.budget.MAX_CLAIMS:
        out.append(_error(
            IngestErrorKind.CLAIM_COUNT, "$.claims",
            f"{len(claims)} claims; at most {review.budget.MAX_CLAIMS}",
        ))
    for i, claim in enumerate(claims):
        out.extend(_claim_problems(claim, i, finding_ids))
    return tuple(out)


def _claim_problems(
    claim: ClaimReport, index: int, finding_ids: Collection[str],
) -> list[IngestError]:
    at = f"$.claims[{index}]"
    out: list[IngestError] = []
    cap = review.budget.MAX_CLAIM_BYTES
    for name in _SIZE_FIELDS:
        size = len(getattr(claim, name).encode())
        if size > cap:
            out.append(_error(
                IngestErrorKind.CLAIM_SIZE, f"{at}.{name}",
                f"{size} bytes; a claim field is capped at {cap}",
            ))
    if not claim.text.strip():
        out.append(_error(IngestErrorKind.BAD_CLAIM, f"{at}.text", "text is empty"))
    if not claim.evidence.strip():
        out.append(_error(
            IngestErrorKind.BAD_CLAIM, f"{at}.evidence", "evidence is empty",
        ))
    if claim.verdict in _CITE and (not claim.path or not claim.snippet.strip()):
        out.append(_error(
            IngestErrorKind.BAD_CLAIM, at,
            "cite the path and a verbatim snippet the verdict rests on",
        ))
    if strip_line_suffix(claim.path) != claim.path:
        out.append(_error(
            IngestErrorKind.BAD_CLAIM, f"{at}.path",
            f"{claim.path!r} carries a line number; drop the :N suffix",
        ))
    if claim.finding and claim.finding not in finding_ids:
        out.append(_error(
            IngestErrorKind.BAD_CLAIM, f"{at}.finding",
            f"{claim.finding} names no finding",
        ))
    return out


def without_vanished_links(record):
    """Clear claim.finding when it names no visible finding.

    Disprove writes without renumbering, so a link to a falsified finding
    would otherwise keep an id that no longer renders.
    """
    if not record.claims:
        return record
    return replace(record, claims=tuple(
        replace(c, finding=c.finding if record.finding(c.finding) else "")
        for c in record.claims
    ))


def writer_check(
    job: ReviewJob,
) -> Callable[[ReviewArtifact], tuple[IngestError, ...]]:
    """Focus coverage then claims, as the writer's `check=` callable.

    `job` supplies the requested focus, whether derived areas are required,
    and whether a claim source exists. Caps and cite rules live in
    `claims_problems`, so an over-cap list degrades rather than rejects.
    """
    def check(artifact: ReviewArtifact) -> tuple[IngestError, ...]:
        return review.focus.coverage_problems(
            artifact, job.focus, derive=review.focus.needs_derived(job.focus),
        ) + claims_problems(
            artifact.claims, {f.id for f in artifact.findings},
            has_claim_source=has_claim_source(job),
        )
    return check


def claims_of(
    job: ReviewJob, outcome: ArtifactOutcome,
) -> tuple[ClaimEntry, ...] | None:
    """Record claims from `outcome`, or `None` when the check degraded.

    `job` decides whether a source exists (`()` with none). `outcome.errors`
    of a claims kind, or a missing `outcome.artifact.claims`, yield `None`.
    """
    if not has_claim_source(job):
        return ()
    if any(e.kind in CLAIM_KINDS for e in outcome.errors):
        return None
    if outcome.artifact.claims is None:
        return None
    return tuple(
        ClaimEntry(
            id=f"C{i}", source=c.source, text=c.text, verdict=c.verdict,
            evidence=c.evidence, path=c.path, snippet=c.snippet, finding=c.finding,
        )
        for i, c in enumerate(outcome.artifact.claims, start=1)
    )


def check_warning(errors: Sequence[IngestError]) -> str:
    """Warning after a writer's second failed check, with a claims clause.

    `errors` are the ingest errors left after the repair turn. When any is a
    claims kind, the warning adds that claims are published as not assessed.
    """
    more = (
        ("claims are published as not assessed",)
        if any(e.kind in CLAIM_KINDS for e in errors) else ()
    )
    return review.focus.check_warning(errors, more=more)
