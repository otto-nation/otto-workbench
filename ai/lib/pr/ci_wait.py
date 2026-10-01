"""Polling a run that is still going, and reporting failures as they land.

`--wait` exists so a fix pass can start on the first failure rather than on the
last one: the loop below emits a partial report the moment a job fails, and
keeps going until every job has finished or the caller's timeout runs out.
What it hands back is the last poll's merged payload, which the caller turns
into the same report a single-shot run produces.
"""

# doc-group: publishing

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

import core.report
import gh.run_reads
import pr.ci_report
import pr.ci_runs


# How many extra polls a read that did not complete may buy. Enough for a
# flaky call to come good, few enough that a permanent cause — a token with
# no scope to read checks — costs a couple of intervals rather than the
# caller's whole timeout.
_MAX_INCOMPLETE_RETRIES = 2


def _retry_incomplete(passes: int, elapsed: float, timeout: int) -> bool:
    """Whether an incomplete read is worth another pass, in budget and in tries."""
    return passes < _MAX_INCOMPLETE_RETRIES and elapsed < timeout


def _nothing_came_back(discovery: gh.run_reads.RunDiscovery) -> str:
    """Why this poll has nothing to report on, in words the caller can print."""
    return ("the workflow run list could not be read" if discovery.failed
            else "no run data came back")


@dataclass(frozen=True)
class PollResult:
    """What a finished or timed-out poll leaves for the final report."""

    run_ids: list[int]
    merged: dict
    counts: pr.ci_runs.JobCounts


def emit_partial(repo: str, merged: dict, new_failed_jobs: list[dict],
                 reported_job_ids: set[int], trail) -> None:
    """Report the jobs that have failed since the last poll, and mark them reported.

    Only the new jobs are parsed: a partial report says what just broke, and a
    reader accumulates them. `reported_job_ids` is what stops the next poll
    saying it again, so it is updated here rather than by the loop.
    """
    partial_merged = dict(merged)
    partial_merged["jobs"] = new_failed_jobs
    partial_run = pr.ci_runs.parse_run(repo, partial_merged)

    counts = pr.ci_runs.count_job_states(merged)

    failures = pr.ci_report.serialize_failures(partial_run.failures)
    if not failures:
        return

    partial_report = {
        "completed": counts.completed,
        "total": counts.total,
        "failures": failures,
        "progression": {},
    }
    core.report.emit_stream_json(partial_report, "partial")
    trail.info("partial_report", f"{len(failures)} new failure(s)")
    reported_job_ids.update(j.get("databaseId", 0) for j in new_failed_jobs)


def _print_status(counts: pr.ci_runs.JobCounts) -> None:
    """One line per poll on stderr, so someone watching sees it move."""
    parts = [f"{counts.completed}/{counts.total} jobs complete"]
    if counts.running:
        parts.append(f"{counts.running} running")
    if counts.queued:
        parts.append(f"{counts.queued} queued")
    if counts.failed:
        parts.append(f"{counts.failed} failed")
    print(f"  {', '.join(parts)}", file=sys.stderr, flush=True)


def poll_until_complete(
    repo: str, branch: str, *, run_id: int | None,
    timeout: int, interval: int, trail, head_sha: str = "",
) -> PollResult:
    """Poll until every job has finished, or until `timeout` seconds have passed.

    The runs are re-resolved on every pass unless `run_id` pins one: a push
    can set off a workflow the first poll did not see. Raises
    `ci_runs.RunUnavailable` when there is nothing to poll at all.

    Payloads of runs that have finished are carried between polls. A poll
    re-reads what is still moving; re-reading what has already concluded is a
    call per run per poll spent on an answer that cannot have changed.
    """
    reported_job_ids: set[int] = set()
    settled = pr.ci_runs.PollCache()
    start_time = time.monotonic()
    incomplete_passes = 0

    while True:
        elapsed = time.monotonic() - start_time

        discovery = (gh.run_reads.RunDiscovery(rows=(gh.run_reads.RunRow(run_id=run_id),))
                     if run_id else gh.run_reads.fetch_latest_runs(repo, branch, head_sha))
        run_ids = [row.run_id for row in discovery.rows]

        # `head_sha` is the branch's current head, not the pinned run's commit
        # once `run_id` names one — see `cli.ci_check._run_ci`'s identical guard.
        # Passing it through would let the rollup answer for whatever the branch
        # head has moved on to and splice that commit's external checks into a
        # report about a different, pinned run.
        rollup_head_sha = "" if run_id else head_sha

        fetched = pr.ci_runs.fetch_merged(
            repo, discovery, head_sha=rollup_head_sha, cache=settled,
        )
        if fetched is None and not discovery.failed and not discovery.rows:
            # A commit with no checks is not going to grow any. Said apart
            # from a listing that failed, which is the condition this loop
            # exists to outlast.
            trail.warn("no_runs", "no checks found")
            raise pr.ci_runs.RunUnavailable(f"No checks found for branch '{branch}'")
        if fetched is None and not _retry_incomplete(incomplete_passes, elapsed, timeout):
            reason = _nothing_came_back(discovery)
            trail.error("fetch_run_data", reason)
            raise pr.ci_runs.RunUnavailable(f"Gave up polling: {reason}")
        if fetched is None:
            incomplete_passes += 1
            trail.warn("incomplete_poll", f"{_nothing_came_back(discovery)} — retrying")
            time.sleep(interval)
            continue

        merged = fetched.merged
        new_failed_jobs = [
            j for j in pr.ci_runs.failed_jobs(merged)
            if j.get("databaseId", 0) not in reported_job_ids
        ]
        if new_failed_jobs:
            emit_partial(repo, merged, new_failed_jobs, reported_job_ids, trail)

        counts = pr.ci_runs.count_job_states(merged)
        _print_status(counts)

        # Everything that can be read has settled. If something could not be
        # read, spend a little of the remaining budget re-reading it before
        # reporting an incomplete picture: every other transient condition in
        # this loop is retried, and the cause is usually one flaky call.
        # Bounded, so a cause that is not transient — a token that cannot see
        # checks at all — costs a couple of polls rather than the timeout.
        unread = merged.get("_unread", ())
        settled_now = merged.get("status") == "completed" or counts.finished
        retrying = unread and _retry_incomplete(incomplete_passes, elapsed, timeout)
        if settled_now and not retrying:
            return PollResult(run_ids=run_ids, merged=merged, counts=counts)
        if settled_now:
            incomplete_passes += 1
            trail.warn("incomplete_poll", f"{len(unread)} unread — retrying")
            time.sleep(interval)
            continue

        if elapsed >= timeout:
            print(f"  Timeout after {int(elapsed)}s — emitting partial results",
                  file=sys.stderr, flush=True)
            trail.warn("wait_timeout", f"timed out after {int(elapsed)}s")
            return PollResult(run_ids=run_ids, merged=merged, counts=counts)

        time.sleep(interval)
