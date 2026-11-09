"""The claims ledger: the check, the fingerprint, the record entries, and
the should-fix findings derived from contradicted or unsupported claims.

This module owns whether a run has a claim source, the input fingerprint, the
run-context check composed with focus coverage, turning a writer's reports
into record entries after the check, the degraded-check warning, and
`settle`. Renderers take claims off the record and never import it.

The record is tri-state. `None` is not assessed — a writer that did not run
and whose claims could not be carried, or a legacy `review.json`. A check
that degraded still starts as `None`; `carried` then re-settles the prior
claims when the fingerprint matches. `()` is assessed with nothing
checkable, or no claim source. Entries are the assessed ledger.

Writers are the single agent and synthesis only. Scout, holistic and group get
no claims fields. Agentless paths that never re-settle claims — clean,
all-groups-failed, `--repair` rebuild — record `None`. So does any path whose
claim inputs changed, or that has no prior claims to carry. When a description
or ticket existed, that renders "*Not assessed this round.*" Settled,
`--no-synthesis`, a budget cut, mechanical fallback, and a degraded writer
check re-settle the prior claims when the fingerprint still matches, so those
rounds keep the ledger rather than dropping it.

The check rides ingest `check=` with one repair turn. Caps (12 claims, 512
bytes per `text` / `evidence` / `snippet`) live inside the check, so an
over-cap list degrades rather than rejects. Leftover claims errors after the
repair set `claims` to `None` and never cost the review. A first load that
fails `problems()` or schema never runs the claims check, so the repair prompt
does not mention claims and the second load degrades without that turn having
named them.

`settle` verifies claims, then derives, then applies live answers, then
appends, then links each source claim by position. A dangling link (names no
visible finding) is treated as no link, so that claim derives. A linked claim
derives nothing, so settle is idempotent on a record it already linked. A
true no-op round never derives twice because that path never calls settle.
It runs at both writer sites before disprove, so the record disprove reads
already holds those findings, and from `carried` on every path through
`_reconcile_and_verify`. On the degraded single-agent path `carried` runs
after disprove, so those re-derived claim findings skip the gate that round.
Disprove clears the link when it falsifies the finding. The evidence gate in
post_process_record can drop, and disprove can falsify, a WRITER finding a
claim links to; the link is then cleared and nothing re-derives that round.
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

Answers live in `ReviewMeta.claim_answers`, keyed by `ClaimEntry.key` (source
plus collapsed lower-cased text). `store.write_review` folds them from the
record's derived findings on every write. An answer holds while its claim
key is unchanged and, when anchored, its file has not changed since the
commit the answer was given at (OD-3); `live_answers` is that filter.
Unanchored answers never expire until the wording changes.

`settle` is the only reader of those answers (D4). Sticky skips neither mark
nor skip derived findings (D5), so the ledger is the only carry for them. A
run without `--prior-review` applies none.

A round that publishes without the writer re-settles the prior claims when
the fingerprint still matches (`carried`, D10): settled, `--no-synthesis`,
budget-cut, synthesis-fallback, and a degraded writer check. Clean and
all-groups-failed do not; they keep claims `None`, and answers still persist
in the sidecar.

The writer is forced when claims cannot be carried, an open claim finding is
unanchored, or a prior derived finding — including answered and falsified —
is anchored on a file in this round's change set (D11). An open claim
finding the writer is not forced to re-judge never reaches a clean review.

D19 still owns writer-finding declines: an ingest-time belief check on a
decline an agent restated. Derived findings are never shown to an agent, so
there is nothing for D19 to believe. Writer declines do not share the ledger
(D6). A claim linked to a prior writer finding that was fixed or settled
gets its link cleared and may derive OPEN on a non-writer round.

A derived finding answered before claim keys included the source re-raises
OPEN once on the next writer round: those records keep the old `stable_id`
and their sidecars carry no `claim_answers`.

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
import review.convergence
import review.focus
import review.merge
import review.verify
from review.artifacts import ReviewArtifact
from review.claim_types import CITE_VERDICTS, ClaimEntry, ClaimReport, ClaimVerdict
from review.grammar import strip_line_suffix
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review.record import Disposition, RecordFinding, ReviewRecord
from review.store import StoredReview
from review.types import ClaimAnswer, FindingOrigin, ReviewJob

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


def inputs_unchanged(job: ReviewJob) -> bool:
    """Whether this run's description and ticket match the prior sidecar's."""
    return job.prior is not None and job.prior.meta.claims_input_sha == input_sha(job)


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


def check_warning(errors: Sequence[IngestError], job: ReviewJob) -> str:
    """Warning after a writer's second failed check, with a claims clause.

    `errors` are the ingest errors left after the repair turn. When any is a
    claims kind, the warning adds that prior claims are carried, or that
    claims are published as not assessed when they cannot be.
    """
    more = ()
    if any(e.kind in CLAIM_KINDS for e in errors):
        clause = (
            "prior claims are carried"
            if _carryable_claims(job) is not None
            else "claims are published as not assessed"
        )
        more = (clause,)
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
            stable_id=claim.key,
            checkbox=checkbox,
            origin=FindingOrigin.CLAIM,
        ))
    return tuple(out)


def _expired(path: str, changed: frozenset[str] | None) -> bool:
    """Whether an anchored path is in this round's change set."""
    return bool(path) and not review.convergence.untouched(path, changed)


def live_answers(
    job: ReviewJob, answers: dict[str, ClaimAnswer],
) -> dict[str, ClaimAnswer]:
    """Answers that still hold for this run (OD-3)."""
    if not answers:
        return {}
    # ceiling: an unanchored FALSIFIED answer holds until the claim's wording
    # changes. Upgrade trigger: once a review is seen suppressing an
    # unsupported claim the code has since stopped supporting.
    changed = review.convergence.change_set(job)
    head = job.pr.head_sha
    return {
        key: answer
        for key, answer in answers.items()
        if answer.head == head or not _expired(answer.path, changed)
    }


# Answered last round: the writer does not have to re-judge these derived findings.
_ANSWERED_DERIVED = (Disposition.FIXED, Disposition.DECLINED, Disposition.SKIPPED)


def open_derived_findings(prior: StoredReview | None) -> tuple[RecordFinding, ...]:
    """Derived findings the prior still held open.

    Openness is a visible derived finding whose disposition is not FIXED,
    DECLINED or SKIPPED. UNREACHED is still open (the fix pass never reached
    it). DECLINED and SKIPPED were answered last round; FALSIFIED is not
    visible. Derived findings are never a prior finding to account for;
    whether an open one needs the writer is `writer_must_rejudge`.
    """
    if prior is None:
        return ()
    return tuple(
        f for f in prior.record.visible
        if f.derived and f.disposition not in _ANSWERED_DERIVED
    )


def writer_must_rejudge(job: ReviewJob) -> bool:
    """True when claims cannot be carried, an open claim finding is
    unanchored, a FIXED or open derived finding is anchored on a file
    in this round's change set, or live answers would drop an entry.

    Answered claims (DECLINED, SKIPPED, FALSIFIED) expire through
    `live_answers` reading the sidecar, not the prior record — a clean
    round leaves no derived findings on disk. Known limit: a claim
    linked to a prior writer finding that was fixed or settled gets
    its link cleared and may derive OPEN on a non-writer round.
    """
    if job.prior is None:
        return False
    open_derived = open_derived_findings(job.prior)
    if open_derived and _carryable_claims(job) is None:
        return True
    changed = review.convergence.change_set(job)
    if any(not review.convergence.untouched(f.path, changed) for f in open_derived):
        return True
    answers = job.prior.meta.claim_answers
    if answers.keys() - live_answers(job, answers).keys():
        return True
    return any(
        f.derived and f.disposition is Disposition.FIXED and _expired(f.path, changed)
        for f in job.prior.record.findings
    )


def _carryable_claims(job: ReviewJob) -> tuple[ClaimEntry, ...] | None:
    if not inputs_unchanged(job) or job.prior.record.claims is None:
        return None
    return job.prior.record.claims


def _identities(finding: RecordFinding) -> set[str]:
    return {token for token in (finding.stable_id, finding.sid) if token}


def _writer_id_in(record: ReviewRecord, prior: RecordFinding) -> str:
    ids = _identities(prior)
    for finding in record.visible:
        if finding.origin is not FindingOrigin.WRITER:
            continue
        if not (ids & _identities(finding)):
            continue
        return finding.id
    return ""


def _relinked(
    prior: ReviewRecord, record: ReviewRecord, claims: tuple[ClaimEntry, ...],
) -> tuple[ClaimEntry, ...]:
    by_id = {f.id: f for f in prior.findings}
    out: list[ClaimEntry] = []
    for claim in claims:
        linked = by_id.get(claim.finding) if claim.finding else None
        if linked is None or linked.origin is not FindingOrigin.WRITER:
            out.append(replace(claim, finding=""))
            continue
        out.append(replace(claim, finding=_writer_id_in(record, linked)))
    return tuple(out)


def carried(job: ReviewJob, record: ReviewRecord) -> ReviewRecord:
    """Re-settle prior claims onto a round that did not run the writer (D10).

    No-op when this record already assessed claims, or when the prior claims
    cannot be carried. Otherwise relinks writer findings by identity, then
    calls `settle` so verification, answers and derived findings apply here.
    """
    if record.claims is not None:
        return record
    claims = _carryable_claims(job)
    if claims is None:
        return record
    claims = _relinked(job.prior.record, record, claims)
    return settle(job, replace(record, claims=claims))


def settle(job: ReviewJob, record: ReviewRecord) -> ReviewRecord:
    """Verify claims, derive findings, append them, and link each source claim.

    Runs at both writer sites before disprove, and from `carried` on every
    path through `_reconcile_and_verify`. On the degraded single-agent path
    that is after disprove, so carried claim findings skip the gate there.
    A claim whose cited code did not verify becomes unverifiable and derives
    nothing. A finding link that names no visible finding is cleared first,
    so a dangling link is no link and the claim derives. Linking is by
    position, not `ClaimEntry.key`: the claims that consume an id are the
    post-append claims, zipped with the last len(derived) visible findings.
    append_findings adds newcomers at the end; _rewrite_record keeps claim
    order and length, so derived_at still lines up, and starting after the
    append keeps rewritten links and evidence. Identity is `ClaimEntry.key`:
    source plus collapsed lower-cased text, which is also the derived
    finding's `stable_id`.

    Live prior answers (`job.prior.meta.claim_answers`) set a derived
    finding's disposition: DECLINED or SKIPPED is copied with its reason;
    FALSIFIED is not re-derived, so the claim stays contradicted or
    unsupported with no finding. A run without a prior, or without a live
    answer for that claim key, leaves every derived finding OPEN. A prior
    record's derived dispositions are not read.
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
    answers = live_answers(job, job.prior.meta.claim_answers if job.prior else {})
    kept: list[RecordFinding] = []
    kept_at: list[int] = []
    for index, finding in zip(
        (i for i, c in enumerate(claims) if _derives_finding(c)),
        derived,
        strict=True,
    ):
        answer = answers.get(claims[index].key)
        if answer is not None and answer.disposition is Disposition.FALSIFIED:
            continue
        if answer is not None and answer.disposition in (
            Disposition.DECLINED, Disposition.SKIPPED,
        ):
            finding = replace(
                finding, disposition=answer.disposition, reason=answer.reason,
            )
        kept.append(finding)
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
            f"derived_at and tail disagree: {len(derived_at)} indexes, "
            f"{len(tail)} ids",
        )
    return replace(record, claims=tuple(linked))
