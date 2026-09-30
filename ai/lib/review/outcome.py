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
    SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, SECTION_STATIC_ANALYSIS,
    SECTION_SUMMARY, SECTION_VERDICT,
    ReviewDocument, ReviewHeader, review_title, set_title, stamp_header,
    strip_sections,
)
from review.paths import write_review_meta
from review.prompt_sections import _is_incremental
from review.reconcile import record_prior_findings
from review.state import PipelineState, pipeline_status, set_failures_section
from review.types import ReviewJob, ReviewMeta, ReviewType
from review.verdict import (
    CLEAN_SUMMARY, CLEAN_VERDICT, FALLBACK_SUMMARY, NO_CHANGES_SUMMARY,
    PARTIAL_VERDICT, build_mechanical_body, states_verdict,
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
        host=job.host,
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


def _header(
    job: ReviewJob, meta: ReviewMeta,
    skipped_groups: int = 0, total_groups: int = 0,
    status: ReviewStatus | None = None,
) -> ReviewHeader:
    """What this run states about itself, wherever the body came from.

    The one place the pipeline decides a review's header, so the two kinds of
    path cannot state it differently: `_document` renders this above a body it
    built, and `_post_process_review` stamps this over a body the review agent
    wrote. Before they shared it the agent path carried whatever the prompt
    template had told the agent to type — which named no `review_type`, so
    every agent-written incremental review read back as a full one.
    """
    header = ReviewHeader.from_meta(
        meta,
        date=date.today().isoformat(),
        status=status,
    )
    if meta.review_type != ReviewType.INCREMENTAL:
        return header
    # The prior review's own header is where its date comes from — a re-review
    # states what it is a delta against, and only that document knows.
    prior = ReviewDocument.parse(job.prior_review) if job.prior_review else None
    return replace(
        header,
        prior_date=(prior.header.date if prior else "") or "unknown",
        skipped_groups=skipped_groups,
        total_groups=total_groups,
    )


def _document(
    job: ReviewJob, body: str,
    skipped_groups: int = 0, total_groups: int = 0,
    status: ReviewStatus | None = None,
) -> ReviewDocument:
    """`body` framed as this run's review document.

    Title and header both come from `_job_meta`, so a document this writes
    cannot disagree with the sidecar written beside it.
    """
    meta = _job_meta(job)
    return ReviewDocument(
        title=review_title(meta),
        header=_header(job, meta, skipped_groups, total_groups, status),
        body=body,
    )


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

    # What reported, not what was dispatched. A group that produced nothing
    # contributed no findings, so counting it in the scope tells the reader a
    # surface was examined when it was not — and that sentence sits directly
    # above the findings the missing group never added to. The verdict goes
    # the same way: "Approve — clean review" over unread source is the one
    # line a reader acts on without opening the failures table below it.
    failed = len(pipeline_state.groups_failed) if pipeline_state else 0
    partial = failed > 0 and failed < group_count
    # `None` says "every dispatched group reported", and only a run where none
    # failed may say it. The earlier test was `partial`, which is false both
    # when nothing failed and when *everything* did — so a run whose every
    # group died claimed the full scope: "No findings across 13 files in 3
    # groups", over 13 files nothing read. An empty tally has two causes and
    # the summary derives from the tally alone, so the count cannot tell them
    # apart; the scope is the only place the difference can be stated.
    reported = group_count - failed
    body = build_mechanical_body(
        merged_content,
        group_count=group_count,
        summary_note=FALLBACK_SUMMARY,
        include_verdict=states_verdict(job.mode),
        verdict=PARTIAL_VERDICT if partial else "",
        file_count=job.pr.changed_files,
        groups_reported=None if failed == 0 else reported,
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


def _reconcile_and_verify(job: ReviewJob) -> None:
    # Reconciliation reads the ledger, which post-processing then strips.
    record_prior_findings(job.review_file, job.prior_review, job.wt_path)
    job.verification = post_process_findings(job.review_file, job.wt_path)


# What a prior review states about the run that produced it rather than about
# the code: its own framing, and the bookkeeping its groups wrote. A body
# reusing its findings states all of this itself, so carrying these over would
# be the new document making the old document's claims twice.
_SUPERSEDED_SECTIONS = frozenset({
    SECTION_SUMMARY.lower(),
    SECTION_VERDICT.lower(),
    SECTION_FILE_TRIAGE.lower(),
    SECTION_PRIOR_FINDINGS.lower(),
    SECTION_STATIC_ANALYSIS.lower(),
})


def _carried_findings(prior_review: str) -> str:
    """`prior_review`'s findings, ready to be some other document's body.

    The findings alone: the title and metadata header go with `ReviewDocument`'s
    own parse, and every section stating something about the prior *run* is
    dropped. What is left is the claims about the code, which nothing has
    addressed and which therefore still stand.

    Not `prompt_prior._strip_internal_sections`, which keeps `## Summary` and
    `## Verdict` because a prompt wants a re-review to see the call its
    predecessor reached. Embedding those in a new body gives the document two
    of each, and both `section_span` and `ReviewDocument.verdict` read the
    first — so the review reports the prior run's verdict while carrying a
    fresh one below it, which inverts the answer when the prior run approved
    and the carried findings do not.
    """
    if not prior_review:
        return ""
    return strip_sections(
        ReviewDocument.parse(prior_review).body, _SUPERSEDED_SECTIONS,
    ).strip()


def _post_process_review(
    job: ReviewJob, skipped_groups: int = 0, total_groups: int = 0,
) -> None:
    """The review file finished, for the paths where an agent wrote all of it.

    A single-agent review and a completed synthesis are the two paths that
    reach a review file without `_document` rendering its frame, so they are
    the two where the title and header on disk are whatever the agent typed
    from its template. Stamping the run's own header over them makes every
    path record what `_job_meta` says, which is what the sidecar beside it
    already carries.

    This used to stamp the head SHA alone, on the reasoning that it was the
    one key the agent could not know. It is not — the agent cannot know any of
    them. What it wrote for the rest was its template's placeholder text: a
    literal `YYYY-MM-DD` for the date on the single-agent path, and on both
    paths no `review_type`, no `prior_sha` and no `base_ref` at all. The
    missing `review_type` is the one with teeth, because it reads back as
    `full`: `review.collect` asks the prior review's header what the last run
    covered, so an incremental review recorded as full is a re-review that
    re-reads the whole PR.

    The group ratio is the caller's because only the synthesis path has one.
    It used to be recorded only when synthesis *failed*, since the mechanical
    fallback rendered a header and the agent did not — so the coverage of a
    run that worked was the coverage nobody wrote down.

    Last, after reconciliation and verification, because both rewrite the file:
    the stamp is only authoritative if nothing writes over it afterwards.
    """
    _reconcile_and_verify(job)
    path = Path(job.review_file)
    if not path.exists():
        return
    meta = _job_meta(job)
    content = set_title(path.read_text(), review_title(meta))
    header = _header(job, meta, skipped_groups=skipped_groups, total_groups=total_groups)
    path.write_text(stamp_header(content, header))


def _post_processed_body(job: ReviewJob, body: str) -> str:
    """`body` written to the review file, post-processed, and read back.

    Evidence verification and renumbering work on the file rather than on a
    string, so a body has to reach disk before the document framing it can be
    built out of what survived.
    """
    Path(job.review_file).write_text(body)
    # Not `_post_process_review`: `body` is a bare body that `_document` has yet
    # to render a header onto, and a head SHA stamped into it now would sit
    # below that header as a second marker rather than being the one it states.
    _reconcile_and_verify(job)
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


def write_unchanged_review(job: ReviewJob) -> None:
    """The review a re-review ships when the author has committed nothing.

    Merging the base into a branch moves its HEAD without adding to it, so a
    re-review can have a prior review, a new commit to point at, and nothing to
    read. Every phase would skip its own way to that conclusion — the scan is
    skipped on any incremental run, every group carries forward for want of a
    delta file — but synthesis would still spend an agent call restating the
    prior review's findings, and the disprove gate would weigh them again.

    The prior review's findings are carried forward whole rather than
    re-derived: nothing addressed them, so they stand exactly as they were.
    Only the internal sections go, since a coverage table describing the prior
    run's groups would be read as this one's.

    The verdict follows those carried findings rather than approving. A prior
    review that asked for changes is still asking; the author has not answered
    it yet.

    Written through `_document` like every other agentless path, so the header
    states this run's head SHA — which is the point of doing it at all. The
    next re-review measures its delta from here, so the merge that prompted
    this run is behind it rather than being walked again.
    """
    body = build_mechanical_body(
        _carried_findings(job.prior_review),
        group_count=0,
        summary_note=NO_CHANGES_SUMMARY,
        include_verdict=states_verdict(job.mode),
        file_count=job.pr.changed_files,
    )
    _document(job, body).write(job.review_file)
    _write_review_sidecar(job)
