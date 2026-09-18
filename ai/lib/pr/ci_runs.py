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


class RunUnavailable(Exception):
    """There was nothing to report on — no run on the branch, or no data for it.

    Raised where the run is resolved or fetched, by the single-shot path and the
    wait loop alike, and carries the message the caller prints. A timeout is not
    this: a poll that ran out of time still saw a run, and the caller reports on
    whatever of it had finished.
    """


def _dedupe_items(items: tuple[ci.FailureItem, ...]) -> tuple[ci.FailureItem, ...]:
    """Drop repeats of a failure one job reported in more than one run.

    A commit can have two runs of the same workflow — a cancelled one and the
    real one — and `merge_runs` concatenates their job lists, so the same job
    name arrives twice and its failures land in one group. Identity is the
    failure's id together with its text: the id alone is not enough, because two
    distinct annotations anchored on the same file and line share one.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[ci.FailureItem] = []
    for item in items:
        key = (item.id, item.annotation)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return tuple(kept)


def failed_jobs(run_data: dict) -> list[dict]:
    """The jobs in one run's payload that GitHub concluded in a failure state.

    One definition, read by the report and by the merge alike, so that what a
    merged conclusion asserts and what the report can name cannot come apart:
    a run's claim to have failed is the jobs this finds under it.
    """
    return [j for j in run_data.get("jobs", []) if j.get("conclusion") in FAILURE_CONCLUSIONS]


def _claims_failure(run_data: dict) -> bool:
    """Whether a run concluded in a failure state with a failed job to show for it.

    The invariant the merged conclusion rests on. A conclusion is a word GitHub
    writes on the run; a failed job is the thing a report can name and a fix
    pass can act on, so the word alone does not carry a verdict.
    """
    return run_data.get("conclusion") in FAILURE_CONCLUSIONS and bool(failed_jobs(run_data))


def parse_run(repo: str, run_data: dict) -> ci.RunState:
    """Parse gh run data into a RunState with classified failures."""
    failures: dict[str, ci.FailureGroup] = {}

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(
            lambda j: ci_annotations.fetch_job_failure(repo, j, run_data), failed_jobs(run_data),
        ))

    for r in results:
        if not r:
            continue
        job_key = r.job_name.lower().replace(" ", "-").replace("/", "-")
        prior_items = failures[job_key].items if job_key in failures else ()
        failures[job_key] = ci.FailureGroup(
            job=r.job_name, kind=r.kind,
            items=_dedupe_items(prior_items + tuple(r.items)),
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
    "failure" if any run failed a job.  Jobs from all runs are collected into
    the primary's "jobs" list.

    A failure conclusion alone does not earn the override — the run must have a
    failed job under it. GitHub concludes a run `action_required` when it is
    held awaiting manual approval, and `startup_failure` or `stale` when it
    never got going, and each of those runs carries no jobs at all. Overriding
    on the conclusion alone turned every one of them into a merged `failure`
    over an empty failure list: a red verdict naming nothing, which readiness
    reports as a blocker and no fix pass can clear. The run's own conclusion
    still stands where it leads the merge, so what GitHub said is reported —
    it is only the merged verdict that waits for evidence.
    """
    if not run_data_list:
        return None
    primary = run_data_list[0]
    all_jobs = []
    any_incomplete = False
    for rd in run_data_list:
        if _claims_failure(rd):
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
        # Asked of the merged payload, so the jobs weighed are every run's. A
        # conclusion is kept only where a failed job stands behind it; anything
        # else is cleared, because a run still going has not reached a verdict
        # and a word with no evidence under it is not one worth carrying.
        if not _claims_failure(primary):
            primary["conclusion"] = ""
    return primary


@dataclass(frozen=True)
class MergedRun:
    """The workflow runs behind one push, and the single payload they fold into.

    A caller that wants the branch's state reads `merged`; one that reports on
    each run it fetched reads `payloads`, whose top-level keys are as GitHub
    served them. The job dicts underneath are shared with `merged` and carry the
    `_source_run_id` the merge tags them with.
    """

    payloads: list[dict]
    merged: dict


def _primary_first(payloads: list[dict]) -> list[dict]:
    """Reorder so the run whose conclusion should stand for the commit leads.

    `merge_runs` keeps the leading payload's conclusion whenever no other run
    failed, which makes the choice of leader a verdict. A cancelled run that
    contributed no job is the one payload that must not lead: it is selected
    only so its failed jobs are not lost, it has none, and leading would leave
    the merged conclusion `cancelled` over a commit whose real runs all passed
    — which `CIDomain.readiness` reports as a `CI failing` blocker.

    Order is otherwise preserved, and a run with jobs leads normally however it
    concluded: the jobs are the evidence, and a run that has them has a claim.

    # ceiling: only `cancelled` is demoted, so a jobless run in another
    # non-success state still leads over a successful sibling and carries its
    # own word into the merged conclusion. Demoting those too would report the
    # commit green while its CI has not been permitted to start, which is the
    # worse error of the two. Upgrade trigger: a commit is observed carrying a
    # jobless `action_required`, `stale` or `startup_failure` run alongside a
    # run that passed — approval-gated workflows are currently the only run at
    # their own commit, which is what keeps this latent.
    """
    lead_index = next(
        (i for i, p in enumerate(payloads) if p.get("conclusion") != "cancelled" or p.get("jobs")),
        None,
    )
    if lead_index is None:
        return payloads
    rest = payloads[:lead_index] + payloads[lead_index + 1:]
    return [payloads[lead_index], *rest]


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
    # first payload it is given, so it gets a copy of that one: each payload's
    # own conclusion and status stay as they were fetched.
    ordered = _primary_first(payloads)
    merged = merge_runs([dict(ordered[0]), *ordered[1:]])
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
