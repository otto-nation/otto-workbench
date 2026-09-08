"""One `RunState` out of however many workflow runs a commit set off.

GitHub reports a push as several independent runs, each with its own id,
conclusion and job list, and nothing downstream of here wants to know that: a
branch is passing or it is not. `merge_runs` folds them into one payload and
`parse_run` turns that into the `RunState` the report, the dashboard and the fix
pass all read.

Deciding what a failed job was complaining about is `pr.ci_annotations`'s job,
called from here once per failed job and in parallel.
"""

# doc-group: publishing

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from gh import run_reads
from gh.run_reads import FAILURE_CONCLUSIONS
from pr import ci_annotations
from pr import ci_failures as ci


def parse_run(repo: str, run_data: dict) -> ci.RunState:
    """Parse gh run data into a RunState with classified failures."""
    failed_jobs = [j for j in run_data.get("jobs", []) if j.get("conclusion") in FAILURE_CONCLUSIONS]
    failures: dict[str, ci.FailureGroup] = {}

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(
            lambda j: ci_annotations.fetch_job_failure(repo, j, run_data), failed_jobs,
        ))

    for r in results:
        if not r:
            continue
        job_key = r.job_name.lower().replace(" ", "-").replace("/", "-")
        prior_items = failures[job_key].items if job_key in failures else ()
        failures[job_key] = ci.FailureGroup(
            job=r.job_name, kind=r.kind, items=prior_items + tuple(r.items),
            failed_step=r.failed_step,
        )

    return ci.RunState(
        run_id=run_data["databaseId"],
        run_number=run_data.get("number", 0),
        head_sha=run_data.get("headSha", ""),
        status=run_data.get("status", ""),
        conclusion=run_data.get("conclusion", ""),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        failures=failures,
    )


def merge_runs(run_data_list: list[dict]) -> dict | None:
    """Merge multiple workflow run dicts into a single combined run.

    The first run becomes the primary; its conclusion is overridden to
    "failure" if any run has a real failure conclusion.  Jobs from all
    runs are collected into the primary's "jobs" list.
    """
    if not run_data_list:
        return None
    primary = run_data_list[0]
    all_jobs = []
    any_incomplete = False
    for rd in run_data_list:
        if rd.get("conclusion") in FAILURE_CONCLUSIONS:
            primary["conclusion"] = "failure"
        if rd.get("status") not in (None, "completed"):
            any_incomplete = True
        rid = rd.get("_run_id")
        for job in rd.get("jobs", []):
            job["_source_run_id"] = rid
        all_jobs.extend(rd.get("jobs", []))
    primary["jobs"] = all_jobs
    if any_incomplete:
        primary["status"] = "in_progress"
        if primary.get("conclusion") not in FAILURE_CONCLUSIONS:
            primary["conclusion"] = ""
    return primary


@dataclass(frozen=True)
class MergedRun:
    """The workflow runs behind one push, and the single payload they fold into.

    A caller that wants the branch's state reads `merged`; one that reports on
    each run it fetched reads `payloads`, which are as GitHub served them.
    """

    payloads: list[dict]
    merged: dict


def fetch_merged(repo: str, run_ids: list[int]) -> MergedRun | None:
    """Fetch each run's payload in parallel and fold them into one.

    `None` when GitHub served none of them — there is nothing to merge and
    nothing to report on.
    """
    with ThreadPoolExecutor(max_workers=5) as pool:
        fetched = list(pool.map(lambda rid: (rid, run_reads.fetch_run_data(repo, rid)), run_ids))
    payloads = [{**data, "_run_id": rid} for rid, data in fetched if data is not None]
    if not payloads:
        return None

    # merge_runs writes the combined conclusion, status and job list onto the
    # first payload it is given, so it gets a copy of that one: `payloads` is
    # what was fetched, not what the merge made of it.
    merged = merge_runs([dict(payloads[0]), *payloads[1:]])
    return MergedRun(payloads=payloads, merged=merged)


@dataclass(frozen=True)
class JobCounts:
    """How a merged run's jobs are distributed across the states CI reports.

    `failed` counts a subset of `completed` — a job that failed has finished —
    so the three states a job can be in at one moment are `completed`,
    `running` and `queued`, and only those three sum to the job total.
    """
    completed: int
    failed: int
    running: int
    queued: int

    @property
    def total(self) -> int:
        return self.completed + self.running + self.queued

    @property
    def finished(self) -> bool:
        """Whether nothing is left to wait for — no job running and none queued."""
        return not self.running and not self.queued


def count_job_states(merged: dict) -> JobCounts:
    """Count a merged run's jobs by state."""
    completed = failed = running = queued = 0
    for job in merged.get("jobs", []):
        status = job.get("status", "")
        if status == "in_progress":
            running += 1
            continue
        if status != "completed":
            queued += 1
            continue
        completed += 1
        if job.get("conclusion") in FAILURE_CONCLUSIONS:
            failed += 1
    return JobCounts(completed=completed, failed=failed, running=running, queued=queued)
