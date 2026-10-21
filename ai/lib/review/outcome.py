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

import core.log
import git.client
from core.phases import Mode
from pr.domains import ReviewStatus
from review.collect import uncommitted_files
from review.convergence import apply_sticky_skips, change_set
from review.document import covers
from review.paths import read_review_meta
from review.prompt_sections import _is_incremental
from review.reconcile import record_prior_findings
from review.record import ReviewRecord
from review.state import PipelineState, apply_failures, pipeline_status
from review.store import StoredReview, read_review, write_review
from review.types import ReviewJob, ReviewMeta, ReviewType
from review.verdict import (
    CLEAN_SUMMARY, CLEAN_VERDICT, FALLBACK_SUMMARY, NO_CHANGES_SUMMARY,
    NO_RUN_VERDICT, PARTIAL_VERDICT, build_mechanical_record, states_verdict,
)
from review.verify import post_process_record


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


def _prior_date(job: ReviewJob) -> str:
    """The prior round's date, from its sidecar."""
    return job.prior.meta.date if job.prior else ""


def _review_meta(job: ReviewJob, *, skipped_groups: int = 0, total_groups: int = 0,
                 status: ReviewStatus | None = None) -> ReviewMeta:
    """This run's sidecar, header facts included — the one thing review.md's header renders from.

    Built from the job, except `static_declined` and `sticky_skips`, which accumulate across rounds
    (see `ReviewMeta.static_declined`), so they are carried from the sidecar
    already on disk. Rebuilding them from the job alone erased every earlier
    decline before `_inject_static_analysis_section` could read it.
    """
    carried = read_review_meta(Path(job.artifact_dir))
    meta = replace(_job_meta(job), static_declined=dict(carried.static_declined),
                   sticky_skips=dict(carried.sticky_skips))
    if meta.review_type != ReviewType.INCREMENTAL:
        return replace(meta, date=date.today().isoformat(), status=status)
    return replace(
        meta, date=date.today().isoformat(), status=status,
        prior_date=_prior_date(job) or "unknown",
        skipped_groups=skipped_groups, total_groups=total_groups,
    )


def _publish(job: ReviewJob, record: ReviewRecord, *, skipped_groups: int = 0,
             total_groups: int = 0, status: ReviewStatus | None = None) -> None:
    """Write `record` and this run's sidecar, and render review.md from both."""
    meta = _review_meta(job, skipped_groups=skipped_groups, total_groups=total_groups,
                        status=status)
    sticky = apply_sticky_skips(record, meta.sticky_skips, job.wt_path)
    write_review(Path(job.review_file), sticky.record, replace(
        meta, sticky_skips=sticky.marks, head_moved_to=_head_moved_to(job),
        dirty_files=_dirty_files(job)))


def _pinned_here(job: ReviewJob) -> str:
    """The pinned head, when this is a self-review whose worktree holds it, else "".

    Self mode only: there the pinned head was read from this same worktree, so
    the worktree can be asked what changed since. In PR mode the pin is
    GitHub's head and the local checkout is not the review's subject. A pinned
    SHA the worktree does not hold says nothing either way.
    """
    pinned = job.pr.head_sha
    if job.mode != Mode.SELF or not pinned or not Path(job.wt_path).is_dir():
        return ""
    held = git.client.out("cat-file", "-t", pinned, cwd=job.wt_path) == "commit"
    return pinned if held else ""


def _head_moved_to(job: ReviewJob) -> str:
    """The live HEAD when a commit landed after this self-review pinned its head, else ""."""
    pinned = _pinned_here(job)
    live = git.client.head_sha(cwd=job.wt_path) if pinned else ""
    if not live or covers(pinned, live):
        return ""
    core.log.warn(
        f"HEAD moved from {git.client.abbrev(pinned)} to {git.client.abbrev(live)} "
        f"during the run; this review covers {git.client.abbrev(pinned)} only")
    return live


def _dirty_files(job: ReviewJob) -> tuple[str, ...]:
    """The files the tree differs from the pinned head in, for the next round to re-read.

    What this self-review read uncommitted, and on a raced run also the raced
    commit's files and any mid-run edit (see `ReviewMeta.dirty_files`).
    """
    pinned = _pinned_here(job)
    return tuple(uncommitted_files(job.wt_path, pinned)) if pinned else ()


def is_complete_review(review_file: str) -> bool:
    """Whether a finished review is stored — the resumed run's test for trusting the disk."""
    stored = read_review(Path(review_file))
    return bool(stored and (stored.record.summary or stored.record.verdict))


def _fallback_status(job: ReviewJob) -> ReviewStatus:
    review_dir = Path(job.review_file).parent
    return pipeline_status(review_dir) if review_dir.exists() else ReviewStatus.ERROR


def _build_mechanical_fallback(
    job: ReviewJob, group_count: int, merged: ReviewRecord,
    skipped_groups: int = 0, pipeline_state: PipelineState | None = None,
) -> ReviewRecord:
    # What reported, not what was dispatched. A group that produced nothing
    # contributed no findings, so counting it in the scope tells the reader a
    # surface was examined when it was not — and that sentence sits directly
    # above the findings the missing group never added to. The verdict goes
    # the same way: "Approve — clean review" over unread source is the one
    # line a reader acts on without opening the failures table below it.
    failed = len(pipeline_state.groups_failed) if pipeline_state else 0
    # `None` says "every dispatched group reported", and only a run where none
    # failed may say it. The earlier test was `partial`, which is false both
    # when nothing failed and when *everything* did — so a run whose every
    # group died claimed the full scope: "No findings across 13 files in 3
    # groups", over 13 files nothing read. An empty tally has two causes and
    # the summary derives from the tally alone, so the count cannot tell them
    # apart; the scope is the only place the difference can be stated.
    reported = group_count - failed
    # Every group gone — counted here or diagnosed by synthesis — leaves an
    # empty tally that `mechanical_verdict` would read as Approve.
    nothing_ran = bool(pipeline_state and pipeline_state.all_groups_failed)
    partial = bool(pipeline_state and pipeline_state.partial)
    verdict = NO_RUN_VERDICT if nothing_ran else (PARTIAL_VERDICT if partial else "")
    record = build_mechanical_record(
        merged, group_count=group_count, summary_note=FALLBACK_SUMMARY,
        include_verdict=states_verdict(job.mode), verdict=verdict,
        file_count=job.pr.changed_files,
        groups_reported=0 if nothing_ran else (None if failed == 0 else reported),
    )
    if pipeline_state:
        record = replace(record, failures=apply_failures(record, pipeline_state).failures)
    return record


def _no_synthesis_record(job: ReviewJob, merged: ReviewRecord, group_count: int,
                         summary_note: str) -> ReviewRecord:
    """`merged` under a Summary saying synthesis did not run, and why. No verdict.

    The two paths that reach the review file with the group output and no agent
    — `--no-synthesis` and the budget cut-off — write through here so that both
    carry the section every other path writes. Without it `is_complete_review`
    reads the document as unfinished and a run resumed at the disprove gate
    re-enters synthesis to rewrite a review it already has.

    No verdict: neither path reviewed anything the synthesis agent would have
    weighed, and a mechanical approve/request-changes from a run the operator
    stopped is a claim nobody made.
    """
    return build_mechanical_record(merged, group_count=group_count, summary_note=summary_note,
                                   include_verdict=False, file_count=job.pr.changed_files)


def _reconcile_and_verify(job: ReviewJob, record: ReviewRecord) -> ReviewRecord:
    # Reconciliation reads the ledger, which post-processing then clears.
    record_prior_findings(job.review_file, job.prior, record, job.wt_path,
                          head_sha=job.pr.head_sha, changed=change_set(job))
    done = post_process_record(record, job.wt_path, [f["path"] for f in job.pr.files])
    job.verification = done.verification
    return done.record


def _carried_record(prior: StoredReview) -> ReviewRecord:
    """The prior round's claims about the code, without its claims about its own run."""
    return replace(prior.record, summary="", verdict="", triage=None, ledger=(), static_analysis="")


def _post_process_review(job: ReviewJob, record: ReviewRecord,
                         skipped_groups: int = 0, total_groups: int = 0) -> None:
    """An agent-written review finished: reconciled, verified, and published.

    The header is this run's, from `_review_meta` — the agent's own header was
    never read (D6), so the stamp the text pipeline needed is gone with it.
    """
    _publish(job, _reconcile_and_verify(job, record),
             skipped_groups=skipped_groups, total_groups=total_groups)


def _write_mechanical_fallback(job: ReviewJob, group_count: int, merged: ReviewRecord,
                               skipped_groups: int = 0) -> None:
    # Today's `_write_mechanical_fallback` did not pass `pipeline_state`; Task
    # 10's `_inject_failures_and_status` adds the failures. Keep that: this
    # writer passes no state.
    record = _build_mechanical_fallback(job, group_count, _reconcile_and_verify(job, merged),
                                        skipped_groups=skipped_groups)
    _publish(job, record, skipped_groups=skipped_groups, total_groups=group_count,
             status=_fallback_status(job))


def _write_clean_review(job: ReviewJob, group_count: int, merged: ReviewRecord,
                        skipped_groups: int = 0) -> None:
    """The review a run that found nothing ships.

    Composed through `build_mechanical_record` like every other path that
    reaches the review file without a synthesis agent, so `merged` — which on a
    findings-free run is the file triage and nothing else — is on the page. It
    is the only evidence such a run leaves that the groups examined anything.
    """
    record = build_mechanical_record(
        merged, group_count=group_count, summary_note=CLEAN_SUMMARY,
        include_verdict=states_verdict(job.mode), verdict=CLEAN_VERDICT,
        file_count=job.pr.changed_files,
    )
    # Publishing clears the ledger everywhere else (`post_process_record`); this
    # writer never reaches it, and a stored ledger would number the prior review.
    _publish(job, replace(record, ledger=()), skipped_groups=skipped_groups, total_groups=group_count)


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

    Written through `_publish` like every other agentless path, so the header
    states this run's head SHA — which is the point of doing it at all. The
    next re-review measures its delta from here, so the merge that prompted
    this run is behind it rather than being walked again.
    """
    record = build_mechanical_record(
        _carried_record(job.prior), group_count=0, summary_note=NO_CHANGES_SUMMARY,
        include_verdict=states_verdict(job.mode), file_count=job.pr.changed_files,
    )
    _publish(job, record)
