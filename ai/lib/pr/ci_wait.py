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

from core import report as core_report
from gh import run_reads
from pr import ci_report
from pr import ci_runs


@dataclass(frozen=True)
class PollResult:
    """What a finished or timed-out poll leaves for the final report."""

    run_ids: list[int]
    merged: dict
    counts: ci_runs.JobCounts


def emit_partial(repo: str, merged: dict, new_failed_jobs: list[dict],
                 reported_job_ids: set[int], trail) -> None:
    """Report the jobs that have failed since the last poll, and mark them reported.

    Only the new jobs are parsed: a partial report says what just broke, and a
    reader accumulates them. `reported_job_ids` is what stops the next poll
    saying it again, so it is updated here rather than by the loop.
    """
    partial_merged = dict(merged)
    partial_merged["jobs"] = new_failed_jobs
    partial_run = ci_runs.parse_run(repo, partial_merged)

    counts = ci_runs.count_job_states(merged)

    failures = ci_report.serialize_failures(partial_run.failures)
    if not failures:
        return

    partial_report = {
        "completed": counts.completed,
        "total": counts.total,
        "failures": failures,
        "progression": {},
    }
    core_report.emit_stream_json(partial_report, "partial")
    trail.info("partial_report", f"{len(failures)} new failure(s)")
    reported_job_ids.update(j.get("databaseId", 0) for j in new_failed_jobs)


def _print_status(counts: ci_runs.JobCounts) -> None:
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
    timeout: int, interval: int, trail,
) -> PollResult:
    """Poll until every job has finished, or until `timeout` seconds have passed.

    The run ids are re-resolved on every pass unless `run_id` pins one: a push
    can set off a workflow the first poll did not see. Raises
    `ci_runs.RunUnavailable` when there is nothing to poll at all.
    """
    reported_job_ids: set[int] = set()
    start_time = time.monotonic()

    while True:
        elapsed = time.monotonic() - start_time

        run_ids = [run_id] if run_id else run_reads.fetch_latest_run_ids(repo, branch)
        if not run_ids:
            trail.warn("no_runs", "no workflow runs found")
            raise ci_runs.RunUnavailable(f"No workflow runs found for branch '{branch}'")

        fetched = ci_runs.fetch_merged(repo, run_ids)
        if fetched is None:
            trail.error("fetch_run_data", "failed to fetch run data")
            raise ci_runs.RunUnavailable("Failed to fetch run data")

        merged = fetched.merged
        new_failed_jobs = [
            j for j in merged.get("jobs", [])
            if j.get("conclusion") in run_reads.FAILURE_CONCLUSIONS
            and j.get("databaseId", 0) not in reported_job_ids
        ]
        if new_failed_jobs:
            emit_partial(repo, merged, new_failed_jobs, reported_job_ids, trail)

        counts = ci_runs.count_job_states(merged)
        _print_status(counts)

        if merged.get("status") == "completed" or counts.finished:
            return PollResult(run_ids=run_ids, merged=merged, counts=counts)

        if elapsed >= timeout:
            print(f"  Timeout after {int(elapsed)}s — emitting partial results",
                  file=sys.stderr, flush=True)
            trail.warn("wait_timeout", f"timed out after {int(elapsed)}s")
            return PollResult(run_ids=run_ids, merged=merged, counts=counts)

        time.sleep(interval)
