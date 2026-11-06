"""Claims check and input fingerprint.

The wiring calls these; Task 10 completes the contract.
"""

# doc-group: pipeline

from __future__ import annotations

import hashlib
from collections.abc import Collection

import review.budget
import review.prompt_sections
from review.claim_types import ClaimReport, ClaimVerdict
from review.grammar import strip_line_suffix
from review.ingest import IngestError, IngestErrorKind

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
    if claim.verdict in _CITE and (not claim.path or not claim.snippet):
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
