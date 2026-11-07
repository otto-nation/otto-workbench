"""The claims ledger: the check, the fingerprint, the record entries, and
the should-fix findings derived from contradicted or unsupported claims.

This module owns whether a run has a claim source, the input fingerprint, the
run-context check composed with focus coverage, turning a writer's reports
into record entries after the check, the degraded-check warning, and
`settle`. Renderers take claims off the record and never import it.

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

`settle` verifies claims, then derives, then appends, then links each source
claim by position. A dangling link (names no visible finding) is treated as
no link, so that claim derives. A linked claim derives nothing, so settle is
idempotent on a record it already linked. A no-op carried round never
derives twice because that path never calls settle. It runs at both writer
sites before disprove, so the record disprove reads already holds those
findings. Disprove clears the link when it falsifies the finding. The
evidence gate in post_process_record can drop, and disprove can falsify, a
WRITER finding a claim links to; the link is then cleared and nothing
re-derives that round.
Verification uses the same evidence matcher findings use: a supported or
contradicted claim whose snippet is not in its file becomes unverifiable,
with a note, and derives nothing.

Derived findings are should-fix, never must-fix: a false statement about the
change hits maintainability, reliability, or developer experience, not
correctness, security, or data integrity (D1). The body is three lines: the
collapsed claim text under `Claim contradicted:` or `Claim unsupported:`,
the evidence prose, and `Source: description` or `Source: ticket`. The last
line is always that `Source:` line so a derived body never ends in a
non-defect phrase (D11).

A prior round's derived findings (`origin` other than writer) are never
shown in the prior prompt and never accounted for. The writer re-judges
the claims and settle re-derives. When the new finding's stable_id matches
a prior derived finding that was DECLINED or SKIPPED, it carries that
disposition and reason. A prior derived finding that was FALSIFIED is not
re-derived for that stable_id: the claim stays contradicted or unsupported
with no finding. A true no-op round carries them with their claims. An
OPEN prior derived finding stops a round being treated as clean or settled,
so the writer re-judges (`reconcile.open_derived_findings`,
`steps._writer_must_rejudge`).

The fingerprint hashes `pr.body` plus a projection of the issue: title and
description or body only. Tracker fields that churn (state, labels,
attachments) do not move it. It is stamped only when claims were assessed,
so a prior round of `None` cannot no-op past an unassessed ledger. A true
no-op carries prior claims: the gate now proves the description and ticket
are the same and that the author committed nothing, so the prior verdicts
still stand. Focus coverage is not carried on a no-op because areas can
change on that same round. The GitHub issue fetch is title and body only
— comments are not the ticket's claims and would move the fingerprint on
every reply.
"""

# doc-group: pipeline

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace

from core.phases import Mode
import review.budget
import review.focus
import review.merge
import review.verify
from review.artifacts import ReviewArtifact
from review.claim_types import CITE_VERDICTS, ClaimEntry, ClaimReport, ClaimVerdict
from review.grammar import FindingIdentity, strip_line_suffix
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review.record import Disposition, RecordFinding, ReviewRecord
from review.types import FindingOrigin, ReviewJob

CLAIM_KINDS = frozenset({
    IngestErrorKind.MISSING_CLAIMS,
    IngestErrorKind.CLAIM_COUNT,
    IngestErrorKind.CLAIM_SIZE,
    IngestErrorKind.BAD_CLAIM,
})

_SIZE_FIELDS = ("text", "evidence", "snippet")

_DERIVED_HEAD = {
    ClaimVerdict.CONTRADICTED: "Claim contradicted",
    ClaimVerdict.UNSUPPORTED: "Claim unsupported",
}


def _error(kind: IngestErrorKind, at: str, detail: str) -> IngestError:
    return IngestError(kind, 0, detail, at=at)


def has_claim_source(job: ReviewJob) -> bool:
    return bool(job.pr.body.strip() or job.issue_context.strip())


def _issue_claim_text(issue_context: str) -> str:
    """Title plus description/body; state, labels and attachments do not count."""
    try:
        data = json.loads(issue_context)
    except json.JSONDecodeError:
        return issue_context
    if not isinstance(data, dict):
        return issue_context
    title = data.get("title") if isinstance(data.get("title"), str) else ""
    body = data.get("description")
    if not isinstance(body, str):
        body = data.get("body") if isinstance(data.get("body"), str) else ""
    return f"{title}\0{body}"


def input_sha(job: ReviewJob) -> str:
    issue = _issue_claim_text(job.issue_context)
    if not job.pr.body and not issue:
        return ""
    return hashlib.sha256((job.pr.body + "\0" + issue).encode()).hexdigest()


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
    if claim.verdict in CITE_VERDICTS and (not claim.path or not claim.snippet.strip()):
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


def without_vanished_links(record: ReviewRecord) -> ReviewRecord:
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


def _derives_finding(claim: ClaimEntry) -> bool:
    return claim.verdict in _DERIVED_HEAD and not claim.finding


def derived_findings(
    claims: tuple[ClaimEntry, ...] | None, *, checkbox: bool,
) -> tuple[RecordFinding, ...]:
    """Should-fix findings for contradicted or unsupported claims with no link.

    A false statement about the change is should-fix: it hits maintainability,
    reliability, or developer experience, not correctness, security, or data
    integrity (D1). The code defect behind a false claim, if any, is a
    separate writer finding. A claim the writer already covered (`finding`
    set) derives nothing.

    Each body is three lines: the collapsed claim text under `Claim
    contradicted:` or `Claim unsupported:`, the evidence prose, and
    `Source: description` or `Source: ticket`. The last line is always that
    `Source:` line so a derived body never ends in a non-defect phrase (D11).
    """
    if not claims:
        return ()
    out: list[RecordFinding] = []
    for claim in claims:
        if not _derives_finding(claim):
            continue
        head = _DERIVED_HEAD[claim.verdict]
        text = " ".join(claim.text.split())
        body = f"{head}: {text}\n{claim.evidence}\nSource: {claim.source.value}"
        contradicted = claim.verdict is ClaimVerdict.CONTRADICTED
        path = claim.path if contradicted else ""
        evidence = claim.snippet if contradicted else ""
        out.append(RecordFinding(
            id=f"S{len(out) + 1}",
            severity="S",
            path=path,
            line=None,
            body=body,
            evidence=evidence,
            stable_id=FindingIdentity.of_fields(path, None, body).stable_id,
            checkbox=checkbox,
            origin=FindingOrigin.CLAIM,
        ))
    return tuple(out)


def _prior_derived(job: ReviewJob) -> dict[str, RecordFinding]:
    if job.prior is None:
        return {}
    return {
        f.stable_id: f
        for f in job.prior.record.findings
        if f.derived and f.stable_id
    }


def _carry_derived(
    finding: RecordFinding, prior: dict[str, RecordFinding],
) -> RecordFinding | None:
    """Copy a prior DECLINED/SKIPPED, or drop a FALSIFIED same stable_id.

    Empty *prior* (job.prior None) leaves the finding OPEN. A prior derived
    finding that was FALSIFIED is in findings but not visible; it is not
    re-derived.
    """
    old = prior.get(finding.stable_id)
    if old is None:
        return finding
    if old.disposition is Disposition.FALSIFIED:
        return None
    if old.disposition in (Disposition.DECLINED, Disposition.SKIPPED):
        return replace(finding, disposition=old.disposition, reason=old.reason)
    return finding


def settle(job: ReviewJob, record: ReviewRecord) -> ReviewRecord:
    """Verify claims, derive findings, append them, and link each source claim.

    Runs at both writer sites before disprove. A claim whose cited code did
    not verify becomes unverifiable and derives nothing. A finding link that
    names no visible finding is cleared first, so a dangling link is no link
    and the claim derives. Linking is by position, not stable_id: the claims
    that consume an id are the post-append claims, zipped with the last
    len(derived) visible findings. append_findings adds newcomers at the end;
    _rewrite_record keeps claim order and length, so derived_at still lines
    up, and starting after the append keeps rewritten links and evidence.
    stable_id is not unique per claim — two unsupported claims that share
    a first line hash alike.

    A derived finding whose stable_id matches a prior derived finding that
    was DECLINED or SKIPPED carries that disposition and reason. A prior
    derived finding that was FALSIFIED (in prior.record.findings, not
    visible) is not re-derived for that stable_id: the claim stays
    contradicted or unsupported with no finding. job.prior None leaves
    every derived finding OPEN.
    """
    if record.claims is None:
        return record
    claims = review.verify.verified_claims(
        record.claims, job.wt_path, [f["path"] for f in job.pr.files],
    )
    record = without_vanished_links(replace(record, claims=claims))
    claims = record.claims
    derived = derived_findings(claims, checkbox=job.mode is Mode.SELF)
    if not derived:
        return record
    prior = _prior_derived(job)
    kept: list[RecordFinding] = []
    kept_at: list[int] = []
    for index, finding in zip(
        (i for i, c in enumerate(claims) if _derives_finding(c)),
        derived,
        strict=True,
    ):
        carried = _carry_derived(finding, prior)
        if carried is None:
            continue
        kept.append(carried)
        kept_at.append(index)
    derived = tuple(kept)
    if not derived:
        return record
    derived_at = tuple(kept_at)
    record = review.merge.append_findings(record, derived)
    if record.claims is None or len(record.claims) != len(claims):
        raise RuntimeError(
            f"append_findings changed claim count from {len(claims)} to "
            f"{0 if record.claims is None else len(record.claims)}",
        )
    tail = record.visible[-len(derived):]
    origins = tuple(f.origin.name for f in tail)
    if len(tail) != len(derived) or any(
        f.origin is not FindingOrigin.CLAIM for f in tail
    ):
        raise RuntimeError(
            f"expected {len(derived)} CLAIM findings at the end of the "
            f"visible list; found {len(tail)} with origins {origins}",
        )
    ids = iter(f.id for f in tail)
    linked = list(record.claims)
    for i in derived_at:
        linked[i] = replace(linked[i], finding=next(ids))
    extra = next(ids, None)
    if extra is not None:
        raise RuntimeError(
            f"expected {len(derived)} CLAIM findings at the end of the "
            f"visible list; found {len(tail)} with origins {origins}",
        )
    return replace(record, claims=tuple(linked))
