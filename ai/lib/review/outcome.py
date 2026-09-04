"""What a run writes to the review file, and what it claims about itself.

Every path that reaches a review file without a synthesis agent comes through
here: the clean review, the mechanical fallback, the two skip paths. Each
carries a different summary and a different claim about whether a verdict was
reached, and getting those to agree is this module's whole job — a header that
says the run completed while the sidecar beside it says otherwise is a document
nobody can act on.

Deciding which of those paths a run takes is `review.steps`'; sequencing the
phases that lead there is `review.pipeline`'s.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from pr.domains import ReviewStatus
from review.document import (
    SECTION_SUMMARY, SECTION_VERDICT,
    ReviewDocument, ReviewHeader, review_title,
)
from review.paths import write_review_meta
from review.prompt_sections import _is_incremental
from review.reconcile import record_prior_findings
from review.state import PipelineState, pipeline_status, set_failures_section
from review.types import ReviewJob, ReviewMeta, ReviewType
from review.verdict import (
    CLEAN_SUMMARY, CLEAN_VERDICT, FALLBACK_SUMMARY,
    build_mechanical_body, states_verdict,
)
from review.verify import post_process_findings


def _job_meta(job: ReviewJob) -> ReviewMeta:
    """This run reduced to the record of what it is reviewing.

    The single place a live `ReviewJob` becomes the review's attribution. Both
    things that state it — the `meta.json` sidecar and the document's metadata
    header — are derived from here rather than from the job, so the header
    cannot claim one head SHA while the sidecar beside it claims another.

    `pr_number` is a string on the job because that is what an argument parser
    hands over, and an int here because that is what it means; a self-review,
    which has no PR, records none.
    """
    incremental = _is_incremental(job)
    pf = job.preflight
    return ReviewMeta(
        repo=job.repo,
        pr_number=int(job.pr_number) if str(job.pr_number).isdigit() else None,
        head_sha=job.pr.head_sha,
        head_ref=job.pr.head,
        base_ref=job.pr.base,
        title=job.pr.title,
        changed_files=job.pr.changed_files,
        generator_version=job.generator_version,
        review_type=ReviewType.of(incremental),
        mode=job.mode,
        prior_sha=pf.prior_head_sha if incremental else "",
        delta_files=tuple(pf.delta_files) if incremental else (),
        started_at=job.started_at,
    )


def _write_review_sidecar(job: ReviewJob):
    """Write the sidecar recording what this run is reviewing.

    Called from every branch that reaches a review file, so the only timestamp
    it can honestly stamp is the run's own start, which it carries rather than
    takes. That a review came of the run is a separate claim, made once at the
    end by `review.paths.stamp_reviewed` and only when the run got there.
    """
    write_review_meta(Path(job.artifact_dir), _job_meta(job))


def _document(
    job: ReviewJob, body: str,
    skipped_groups: int = 0, total_groups: int = 0,
    status: ReviewStatus | None = None,
) -> ReviewDocument:
    """`body` framed as this run's review document.

    The one place the pipeline states a review's title and header. Both come
    from `_job_meta`, so a document this writes cannot disagree with the sidecar
    written beside it.
    """
    meta = _job_meta(job)
    header = ReviewHeader.from_meta(
        meta,
        date=date.today().isoformat(),
        status=status,
    )
    if meta.review_type == ReviewType.INCREMENTAL:
        # The prior review's own header is where its date comes from — a
        # re-review states what it is a delta against, and only that document
        # knows.
        prior = ReviewDocument.parse(job.prior_review) if job.prior_review else None
        header = replace(
            header,
            prior_date=(prior.header.date if prior else "") or "unknown",
            skipped_groups=skipped_groups,
            total_groups=total_groups,
        )
    return ReviewDocument(title=review_title(meta), header=header, body=body)


def is_complete_review(review_file: str) -> bool:
    """Whether `review_file` already carries a section every write path here produces.

    A resumed run reads this to decide whether it can trust the file on disk:
    the two sections are the last thing any path writes, so their absence
    means whatever wrote the file died before finishing it.
    """
    if not Path(review_file).exists():
        return False
    content = Path(review_file).read_text()
    return f"## {SECTION_SUMMARY}" in content or f"## {SECTION_VERDICT}" in content


def _build_mechanical_fallback(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
    pipeline_state: "PipelineState | None" = None,
) -> ReviewDocument:
    review_dir = Path(job.review_file).parent
    status = pipeline_status(review_dir) if review_dir.exists() else ReviewStatus.ERROR

    body = build_mechanical_body(
        merged_content,
        group_count=group_count,
        summary_note=FALLBACK_SUMMARY,
        include_verdict=states_verdict(job.mode),
        file_count=job.pr.changed_files,
    )
    if pipeline_state:
        body = set_failures_section(body, pipeline_state)
    return _document(
        job, body, skipped_groups=skipped_groups, total_groups=group_count,
        status=status,
    )


def _no_synthesis_body(
    job: ReviewJob, merged_content: str, group_count: int, summary_note: str,
) -> str:
    """`merged_content` under a Summary saying synthesis did not run, and why.

    The two paths that reach the review file with the group output and no agent
    — `--no-synthesis` and the budget cut-off — write through here so that both
    carry the section every other path writes. Without it `is_complete_review`
    reads the document as unfinished and a run resumed at the disprove gate
    re-enters synthesis to rewrite a review it already has.

    No verdict: neither path reviewed anything the synthesis agent would have
    weighed, and a mechanical approve/request-changes from a run the operator
    stopped is a claim nobody made.
    """
    return build_mechanical_body(
        merged_content,
        group_count=group_count,
        summary_note=summary_note,
        include_verdict=False,
        file_count=job.pr.changed_files,
    )


def _post_process_review(job: ReviewJob) -> None:
    # Reconciliation reads the ledger, which post-processing then strips.
    record_prior_findings(job.review_file, job.prior_review, job.wt_path)
    job.verification = post_process_findings(job.review_file, job.wt_path)


def _post_processed_body(job: ReviewJob, body: str) -> str:
    """`body` written to the review file, post-processed, and read back.

    Evidence verification and renumbering work on the file rather than on a
    string, so a body has to reach disk before the document framing it can be
    built out of what survived.
    """
    Path(job.review_file).write_text(body)
    _post_process_review(job)
    return Path(job.review_file).read_text()


def _write_mechanical_fallback(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
):
    _build_mechanical_fallback(
        job, group_count, _post_processed_body(job, merged_content),
        skipped_groups=skipped_groups,
    ).write(job.review_file)


def _write_clean_review(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
):
    """The review a run that found nothing ships.

    Composed through `build_mechanical_body` like every other path that reaches
    the review file without a synthesis agent, so `merged_content` — which on a
    findings-free run is the file triage and nothing else — is on the page. It
    is the only evidence such a run leaves that the groups examined anything.
    """
    body = build_mechanical_body(
        merged_content,
        group_count=group_count,
        summary_note=CLEAN_SUMMARY,
        include_verdict=states_verdict(job.mode),
        verdict=CLEAN_VERDICT,
        file_count=job.pr.changed_files,
    )
    _document(
        job, body, skipped_groups=skipped_groups, total_groups=group_count,
    ).write(job.review_file)
    _write_review_sidecar(job)
