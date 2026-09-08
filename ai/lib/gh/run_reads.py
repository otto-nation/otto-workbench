"""What GitHub says about a workflow run, before anything interprets it.

Every read here returns the payload `gh` handed back — a run dict, a list of
annotation dicts, log text — with no failure classification and no `RunState`.
Turning those into something a fix pass can act on belongs to `pr.ci_annotations`
and `pr.ci_runs`, which sit a layer up and call through here for their inputs.

The transport is not here either: `gh.client` owns running gh, its timeout tiers
and its rate-limit ladder. This module owns which questions CI asks about a run
and nothing about how the asking is done, the same division `gh.pr_reads` keeps
for a PR.

`SKIP_CONCLUSIONS` and `FAILURE_CONCLUSIONS` live here because they are the
vocabulary of the payload rather than of any one reading of it — both layer-4
modules classify against the same words, and a run GitHub calls `stale` is a
failure to each of them or to neither.
"""

# doc-group: publishing

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from gh import client as gh_client

SKIP_CONCLUSIONS = frozenset(("skipped", "cancelled"))
FAILURE_CONCLUSIONS = frozenset(
    ("failure", "timed_out", "action_required", "stale", "startup_failure"),
)


def fetch_latest_run_ids(repo: str, branch: str) -> list[int]:
    """Workflow run IDs for the latest commit on `branch`, one per workflow.

    Filters out skipped and cancelled runs before deduplication.
    When a workflow is re-run, both the original and re-run share the same
    SHA.  gh run list returns newest first, so we deduplicate by workflow
    name to keep only the most recent run of each workflow.
    """
    runs = gh_client.json_out(
        "run", "list", "--repo", repo, "--branch", branch,
        "--limit", "20", "--json", "databaseId,headSha,workflowName,conclusion",
        default=[],
    )
    if not runs:
        return []
    latest_sha = runs[0]["headSha"]
    seen_workflows: set[str] = set()
    ids: list[int] = []
    for r in runs:
        if r["headSha"] != latest_sha:
            continue
        if r.get("conclusion") in SKIP_CONCLUSIONS:
            continue
        wf = r.get("workflowName", "")
        if wf in seen_workflows:
            continue
        seen_workflows.add(wf)
        ids.append(r["databaseId"])
    return ids


def fetch_run_data(repo: str, run_id: int) -> dict | None:
    """Run metadata and job results for one run, or None when gh could not read it."""
    return gh_client.json_out(
        "run", "view", str(run_id), "--repo", repo,
        "--json", "databaseId,number,headSha,status,conclusion,jobs",
    )


def fetch_annotations(repo: str, job_id: int) -> list[dict]:
    """Annotations for a check run (job), as GitHub returned them."""
    return gh_client.api_json(
        f"repos/{repo}/check-runs/{job_id}/annotations", paginate=True, default=[],
    )


def fetch_job_logs(repo: str, job_id: int) -> str:
    """Logs for a single job via API. Smaller and faster than per-run.

    Escape sequences are allowed through because a job that coloured its output
    has them on every line, and gh refuses the whole response over them — which
    reads here as a job with no logs and silently demotes every such job to the
    per-run fallback. `ci_failures` strips them.
    """
    r = gh_client.api(
        f"repos/{repo}/actions/jobs/{job_id}/logs", allow_escape_sequences=True,
    )
    return r.stdout if r.ok else ""


def fetch_failed_logs(repo: str, run_id: int) -> str:
    """Raw log text for the run's failed steps."""
    r = gh_client.run("run", "view", str(run_id), "--repo", repo, "--log-failed")
    return r.stdout if r.ok else ""


@contextmanager
def download_artifact(repo: str, run_id: int, artifact_name: str) -> Iterator[str | None]:
    """Yield a directory holding `artifact_name`, or None when there is no such artifact.

    The directory is temporary and is removed on the way out, so the caller
    reads what it needs inside the block rather than keeping the path.
    """
    tmpdir = tempfile.mkdtemp(prefix="ci-artifact-")
    try:
        if not gh_client.ok(
            "run", "download", str(run_id), "--repo", repo,
            "--name", artifact_name, "--dir", tmpdir,
        ):
            yield None
            return
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def commits_behind_main(repo: str, branch: str) -> int:
    """Commits on origin/main not reachable from `branch`."""
    r = gh_client.api(f"repos/{repo}/compare/{branch}...main", jq=".ahead_by")
    val = r.stdout.strip()
    return int(val) if r.ok and val.isdigit() else 0
