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

from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import agent.usage
import core.log
import core.trail
import git.client
import review.behaviours
import review.claims
from core.phases import Mode
from pr.domains import ReviewStatus
from review.convergence import (
    apply_sticky_skips, change_set, held_nits, new_nit_ids, prior_ids,
)
from review.document import covers
from review.focus import with_run_focus
from review.paths import read_review_meta
from review.prompt_sections import _is_incremental
from review.reconcile import record_prior_findings
from review.record import ReviewRecord
from review.state import PipelineState, apply_failures, pipeline_status
from review.worktree_delta import uncommitted_files
from review.store import StoredReview, read_review, write_review
from review.types import ReviewJob, ReviewMeta, ReviewType
from review.verdict import (
    CLEAN_SUMMARY, FALLBACK_SUMMARY, NO_CHANGES_SUMMARY, SETTLED_SUMMARY,
    build_mechanical_record,
)
from review.verify import post_process_record, verified_behaviours


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

    Built from the job, except `static_declined`, `sticky_skips` and
    `claim_answers`. Those accumulate across rounds — they carry every
    earlier decline and skip mark — so they are read from the sidecar
    already on disk. Rebuilding them from the job alone erased every
    earlier decline before `_inject_static_analysis_section` could read
    it. `covered_sha` is not carried: a new review does not inherit a
    prior pass's coverage.
    """
    carried = read_review_meta(Path(job.artifact_dir))
    meta = replace(_job_meta(job), static_declined=dict(carried.static_declined),
                   sticky_skips=dict(carried.sticky_skips),
                   claim_answers=review.claims.live_answers(job, carried.claim_answers),
                   covered_sha="")
    if meta.review_type != ReviewType.INCREMENTAL:
        return replace(meta, date=date.today().isoformat(), status=status)
    return replace(
        meta, date=date.today().isoformat(), status=status,
        prior_date=_prior_date(job) or "unknown",
        skipped_groups=skipped_groups, total_groups=total_groups,
    )


def _publish(job: ReviewJob, record: ReviewRecord, *, skipped_groups: int = 0,
             total_groups: int = 0, status: ReviewStatus | None = None) -> None:
    """Write `record` and this run's sidecar, and render review.md from both.

    Every review leaves with the run's areas, covered or not. Cited tests
    are verified here: a miss or an unsafe `test_cited` is cleared with a
    fixed note.
    """
    record = with_run_focus(record, job.focus)
    if record.claims is None and not review.claims.has_claim_source(job):
        record = replace(record, claims=())
    if record.behaviours is None and not review.behaviours.has_behaviour_source(job):
        record = replace(record, behaviours=())
    record = replace(record, behaviours=verified_behaviours(
        record.behaviours, job.wt_path, [f["path"] for f in job.pr.files]))
    meta = _review_meta(job, skipped_groups=skipped_groups, total_groups=total_groups,
                        status=status)
    sticky = apply_sticky_skips(record, meta.sticky_skips, job.wt_path)
    write_review(Path(job.review_file), sticky.record, replace(
        meta, sticky_skips=sticky.marks,
        held_nits=held_nits(job, incremental=_is_incremental(job)),
        head_moved_to=_head_moved_to(job),
        dirty_files=_dirty_files(job),
        claims_input_sha=review.claims.input_sha(job) if record.claims is not None else ""))


@dataclass(frozen=True)
class _Session:
    """One kind of agent session the `Models:` line counts."""

    phase: str  # the label `review.phases.PhaseRunner.invocation` writes
    model: str  # the served model, else the requested one
    thinking: str  # the thinking level; "" when the row has none


def models_used(job: ReviewJob) -> str:
    """The agents this run's review ran, as its `Models:` line states them; "" when the ledger names none.

    Read from the usage ledger, which every agent call writes: the rows under
    this command's trail root for this repo, branch and PR since the run started.
    Called once the review's phases are done and before the fix pass, so no
    fix row is among them. The served model is shown, else the requested one;
    a row naming neither is left out. Identical sessions collapse in the order
    they first ran: `group (×5) m (low)`. `(×N)` counts attempts; a served-thinking
    row and an unmeasured one stay split.
    """
    # The labels `review.phases.PhaseRunner.invocation` writes, and the root
    # `agent.backend._record` stamps on every row.
    # A self-review has no PR, so its rows carry no `pr`: "" -> None matches them.
    this_run = (core.trail.inherited_root(), job.repo or None, job.pr.head or None,
                str(job.pr_number) or None)
    counts: dict[_Session, int] = {}
    # Telemetry must not fail the review: a ledger line that is not an object,
    # or bytes that are not UTF-8, would otherwise raise here after the agents
    # have already spent.
    try:
        records = agent.usage.read_ledger(since=datetime.fromisoformat(job.started_at))
    except Exception:
        core.log.warn("usage ledger unreadable — models line omitted")
        return ""
    for rec in records:
        if (rec.get("invocation"), rec.get("repo"), rec.get("branch"),
                rec.get("pr")) != this_run:
            continue
        model = agent.usage.sole_model(rec.get("cost_by_model") or {}) or rec.get("model") or ""
        if model:
            session = _Session(rec.get("phase") or "", model, rec.get("thinking") or "")
            counts[session] = counts.get(session, 0) + 1
    return " · ".join(_models_entry(s, n) for s, n in counts.items())


def _models_entry(session: _Session, count: int) -> str:
    times = f" (×{count})" if count > 1 else ""
    level = f" ({session.thinking})" if session.thinking else ""
    return f"{session.phase}{times} {session.model}{level}".lstrip()


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
    return bool(stored and stored.record.summary)


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
    # above the findings the missing group never added to.
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
    # empty tally; the derived line withholds Approve on an error or partial run.
    nothing_ran = bool(pipeline_state and pipeline_state.all_groups_failed)
    record = build_mechanical_record(
        merged, group_count=group_count, summary_note=FALLBACK_SUMMARY,
        file_count=job.pr.changed_files,
        groups_reported=0 if nothing_ran else (None if failed == 0 else reported),
    )
    if pipeline_state:
        record = apply_failures(record, pipeline_state)
    return record


def _no_synthesis_record(job: ReviewJob, merged: ReviewRecord, group_count: int,
                         summary_note: str) -> ReviewRecord:
    """`merged` under a Summary saying synthesis did not run, and why.

    The two paths that reach the review file with the group output and no agent
    — `--no-synthesis` and the budget cut-off — write through here so that both
    carry the section every other path writes. Without it `is_complete_review`
    reads the document as unfinished and a run resumed at the disprove gate
    re-enters synthesis to rewrite a review it already has.

    The derived line still applies, matching what `pr status` already showed:
    open must-fix and should-fix findings block, and nothing open approves.
    """
    return build_mechanical_record(merged, group_count=group_count, summary_note=summary_note,
                                   file_count=job.pr.changed_files)


def _reconcile_and_verify(job: ReviewJob, record: ReviewRecord) -> ReviewRecord:
    # Before reconciliation and verification, which treat the carried findings
    # like this round's.
    record = review.claims.carried(job, record)
    # Before post-processing clears the sids: a carried finding is known by
    # its sid, and only this record still has them (D13).
    job.new_nits = new_nit_ids(record, prior_ids(job)) if _is_incremental(job) else frozenset()
    # Reconciliation reads the ledger, which post-processing then clears.
    record_prior_findings(job.review_file, job.prior, record, job.wt_path,
                          head_sha=job.pr.head_sha, changed=change_set(job))
    done = post_process_record(record, job.wt_path, [f["path"] for f in job.pr.files])
    job.verification = done.verification
    return done.record


def _carried_record(prior: StoredReview) -> ReviewRecord:
    """The prior round's findings about the code, without its claims about its own run.

    The claims ledger is carried because the no-op gate proved their inputs
    unchanged. Derived findings ride with the claims they came from.
    Behaviours stay as the prior round left them, so a legacy `None` stays
    not assessed until a writer round runs.
    """
    return replace(prior.record, summary="", triage=None, ledger=(),
                   static_analysis="", focus=())


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

    The derived verdict follows those carried findings rather than approving.
    A prior review that asked for changes is still asking; the author has not
    answered it yet.

    Written through `_publish` like every other agentless path, so the header
    states this run's head SHA — which is the point of doing it at all. The
    next re-review measures its delta from here, so the merge that prompted
    this run is behind it rather than being walked again.
    """
    record = build_mechanical_record(
        _carried_record(job.prior), group_count=0, summary_note=NO_CHANGES_SUMMARY,
        file_count=job.pr.changed_files,
    )
    _publish(job, record)


def _write_settled_review(job: ReviewJob, group_count: int, merged: ReviewRecord,
                          skipped_groups: int = 0) -> None:
    """The review an incremental round ships when it found nothing new to say.

    Synthesis writes up findings; with no new ones and every prior finding
    settled by the ledger, a carry, a prior decline or the tree, it would only
    restate the merge at the cost of an agent call. The derived verdict is
    stated from the carried findings, as `write_unchanged_review` does: a round
    that adds nothing does not withdraw what the last one asked for.
    """
    record = build_mechanical_record(
        _reconcile_and_verify(job, merged), group_count=group_count,
        summary_note=SETTLED_SUMMARY, file_count=job.pr.changed_files,
    )
    _publish(job, record, skipped_groups=skipped_groups, total_groups=group_count)
