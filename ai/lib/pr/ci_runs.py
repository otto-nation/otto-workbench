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
from dataclasses import dataclass, field
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


def _apply_external(merged: dict, external: tuple[dict, ...]) -> dict:
    """Fold the checks no Actions run accounts for into the merged verdict.

    They join the job list rather than riding alongside it, so that every
    reader downstream — the failure scan, the job counts, the report — keeps
    asking one question of one list. A second list would be a second answer to
    "is this commit failing", which is the shape of the defect this exists to
    close.

    The rules are `merge_runs`'s own, applied to the same payload: a failure
    stands whatever else is pending, and an unfinished check clears a
    conclusion that has no failed job behind it.
    """
    if not external:
        return merged
    merged["jobs"] = [*merged.get("jobs", []), *external]
    if any(job.get("conclusion") in FAILURE_CONCLUSIONS for job in external):
        merged["conclusion"] = "failure"
        return merged
    if any(job.get("status") != "completed" for job in external):
        merged["status"] = "in_progress"
        if not _claims_failure(merged):
            merged["conclusion"] = ""
    return merged


@dataclass
class PollCache:
    """What one poll may hand to the next, for answers that cannot have changed.

    A single owner for both, because they are one question asked of two
    sources and they are always carried together. Two loose dicts keyed
    differently — run id to payload, commit to rollup — is an argument pair a
    call site can cross over, and the older of them was called just `cache`,
    which stopped being a name once there were two.

    Only a wait loop has a next poll. A single-shot run passes nothing and
    every read is made fresh.
    """

    runs: dict[int, dict] = field(default_factory=dict)
    checks: dict[str, run_reads.CommitChecks] = field(default_factory=dict)


def _hold_finished(cache: dict[int, dict], payloads: list[dict], served: dict) -> None:
    """Keep the payloads of runs that have concluded, for the next poll to reuse.

    A run GitHub has concluded does not change again, and a re-run arrives as a
    new id rather than as a new state under the old one — so a poll that
    re-reads a finished run is asking for an answer it already has.

    Only runs that were actually fetched are held. A green run's payload was
    built from the rollup, which the next poll rebuilds for nothing.
    """
    for payload in payloads:
        rid = payload["_run_id"]
        if rid in served and payload.get("status") == "completed":
            cache[rid] = payload


def _commit_checks(
    repo: str, rows: list[run_reads.RunRow], sha: str,
) -> run_reads.CommitChecks:
    """The commit's full check list, asked for at a commit GitHub has heard of.

    `sha` is the caller's idea of the branch head, which on a worktree with
    unpushed commits is a commit the API has never seen — it answers nothing,
    and reading that as "no checks" is the false green one layer down. The runs
    themselves name a commit GitHub definitely ran, so an unanswered rollup is
    retried there before the answer is believed.
    """
    checks = run_reads.fetch_commit_checks(repo, sha)
    if checks.answered or not rows:
        return checks
    ran_sha = rows[0].head_sha
    if not ran_sha or ran_sha == sha:
        return checks
    return run_reads.fetch_commit_checks(repo, ran_sha)


def _checks_settled(checks: run_reads.CommitChecks) -> bool:
    """Whether a later poll could not learn anything this rollup does not say now.

    An unanswered rollup might still be answered next time, and an external
    check that is not yet `completed` might still conclude — either one is
    worth asking again. Only a rollup that answered and has nothing left
    in flight is safe to hand back unasked on a later poll.
    """
    return checks.answered and all(
        job.get("status") == "completed" for job in checks.external
    )


def _late_checks(
    repo: str, payloads: list[dict], cache: PollCache | None = None,
) -> run_reads.CommitChecks:
    """The rollup at the commit the runs named, for a caller that could not.

    A run pinned by id arrives with no commit attached, and the caller's own
    head is the wrong thing to substitute — for a historical run that is a
    different commit, whose checks belong to something else. So the question
    waits rather than being asked of the wrong subject: the payload GitHub
    served names the commit the run actually ran on.

    `cache` holds a settled rollup across polls, keyed by the commit it
    answered for. A pinned run's commit does not change between polls, so once
    its rollup has nothing left in flight (`_checks_settled`), a later poll
    that already has the Actions payload from `cache`/`held` would otherwise
    still re-issue this GraphQL query for an answer it already has. An
    unsettled rollup is never cached: the answer it gives next poll may differ
    from this one.
    """
    sha = (payloads[0].get("headSha") or "") if payloads else ""
    if not sha:
        return run_reads.CommitChecks()
    if cache is not None and sha in cache.checks:
        return cache.checks[sha]
    checks = run_reads.fetch_commit_checks(repo, sha)
    if cache is not None and _checks_settled(checks):
        cache.checks[sha] = checks
    return checks


def fetch_merged(
    repo: str, rows: list[run_reads.RunRow], *, head_sha: str = "",
    cache: PollCache | None = None,
) -> MergedRun | None:
    """Fold every check on the commit — Actions runs and otherwise — into one payload.

    A run the rollup proves settled and green is not fetched: its job payload
    would carry only the step list of jobs that did not fail, and the rollup
    already named every job under it. Anything else is fetched as before, which
    is what keeps a cancelled run — dropped from the rollup while its failed
    jobs live on — from being skipped on the strength of checks nobody saw.

    `None` when GitHub served nothing at all: no run payload and no external
    check. A commit whose only checks are external still reports, because a
    repo can have checks without having a workflow.

    `head_sha` may be empty, and a caller that cannot name the commit should
    leave it so rather than pass one it is unsure of: the runs are then read
    first and the rollup asked at the commit they name. That costs the chance
    to skip a green run's payload, which is the right trade against answering
    for the wrong commit.
    """
    # Whether the commit can be named up front is the whole branch: named, the
    # rollup is asked first and can spare a green run its payload; unnamed, the
    # runs are read first and asked about afterwards. One variable rather than
    # a field on the answer, so "was it asked" cannot drift from "what it said".
    sha = head_sha or (rows[0].head_sha if rows else "")
    checks = _commit_checks(repo, rows, sha) if sha else run_reads.CommitChecks()
    green = checks.green_run_ids()
    held = cache.runs if cache is not None else {}

    to_fetch = [row for row in rows if row.run_id not in green and row.run_id not in held]
    with ThreadPoolExecutor(max_workers=5) as pool:
        served = dict(pool.map(
            lambda row: (row.run_id, run_reads.fetch_run_data(repo, row.run_id)), to_fetch,
        ))

    payloads: list[dict] = []
    for row in rows:
        if row.run_id in green:
            data = row.as_payload(checks.actions.get(row.run_id, ()))
        else:
            data = served.get(row.run_id) or held.get(row.run_id)
        if data is not None:
            payloads.append({**data, "_run_id": row.run_id})

    if cache is not None:
        _hold_finished(cache.runs, payloads, served)

    if not sha:
        checks = _late_checks(repo, payloads, cache)

    if not payloads:
        if not checks.external:
            return None
        # No workflow ran, but something checked the commit. Anchoring on a
        # run that does not exist would be a lie about where the verdict came
        # from, so the payload claims no run and `_apply_external` writes the
        # conclusion from the checks themselves.
        payloads = [{"databaseId": 0, "number": 0, "headSha": sha,
                     "status": "completed", "conclusion": "success",
                     "jobs": [], "_run_id": 0}]

    # merge_runs writes the combined conclusion, status and job list onto the
    # first payload it is given, so it gets a copy of that one: each payload's
    # own conclusion and status stay as they were fetched.
    ordered = _primary_first(payloads)
    merged = _apply_external(merge_runs([dict(ordered[0]), *ordered[1:]]), checks.external)
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
