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

import json
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from gh import client as gh_client
from git import client as git_client
from git import topology as git_topology

SKIP_CONCLUSIONS = frozenset(("skipped",))
FAILURE_CONCLUSIONS = frozenset(
    ("failure", "timed_out", "action_required", "stale", "startup_failure"),
)


@dataclass(frozen=True)
class RunRow:
    """One workflow run as `gh run list` reports it, before its jobs are read.

    Carried whole rather than reduced to an id because the id alone cannot
    stand in for the run: `number` is what the dashboard calls it, and a run
    reported without one is `CI Run #0`. Everything here comes off the row the
    discovery call already returned, so widening it costs nothing.
    """

    run_id: int
    number: int = 0
    head_sha: str = ""
    status: str = ""
    conclusion: str = ""
    workflow: str = ""
    # Re-running a workflow reuses the run id and increments this, so the id
    # alone does not identify a result. Anything holding a run's payload has
    # to key on both or it will serve the previous attempt's verdict for a run
    # that has since been re-run and failed.
    attempt: int = 1

    @property
    def identity(self) -> tuple[int, int]:
        """What actually names one run result, as opposed to one run."""
        return (self.run_id, self.attempt)

    def as_payload(self, jobs: tuple[dict, ...]) -> dict:
        """The run payload `gh run view` would have served, built from the row.

        Only sound for a run the rollup proved settled and green: `jobs` is
        then the whole of its job list, and the two fields the row does not
        carry are the two that green already answers. A run in any other state
        has to be fetched — see `CommitChecks.green_run_ids`.
        """
        return {
            "databaseId": self.run_id, "number": self.number,
            "headSha": self.head_sha, "status": self.status or "completed",
            "conclusion": self.conclusion or "success", "jobs": list(jobs),
        }


@dataclass(frozen=True)
class RunDiscovery:
    """The runs found for a commit, and whether the looking itself worked.

    `failed` is the distinction that keeps an empty list honest. `gh run list`
    answers with nothing both when a commit has no workflow runs and when the
    call did not succeed, and those mean opposite things: the first is a fact
    about the commit, the second is an absence of facts. Collapsed into one
    empty list, a failed listing reads as a commit with nothing to report,
    which is the shape of every bug this module has had.
    """

    rows: tuple[RunRow, ...] = ()
    failed: bool = False


def fetch_latest_runs(repo: str, branch: str, head_sha: str = "") -> RunDiscovery:
    """Workflow runs for one commit on `branch`, one per workflow.

    `head_sha` names the commit. Without one the newest row's SHA stands in,
    which is a guess: `gh run list` is ordered by start time, so the newest row
    belongs to whatever was pushed last rather than to the commit the caller is
    asking about. Every caller that can name the commit passes it — the guess
    survives only for a run with no worktree and no PR to resolve one from.

    When a workflow is re-run, both the original and re-run share the same
    SHA.  gh run list returns newest first, so we deduplicate by workflow
    name to keep only the most recent run of each workflow.

    A run's conclusion decides which row may *claim* a workflow name, never
    whether the run is fetched. A skipped run ran nothing and is dropped. A
    cancelled run is returned — cancelling a run does not un-fail the jobs that
    had already failed in it, and dropping the run took those failures with it —
    but it claims no workflow name, so it can never shadow an older real run of
    the same workflow that would otherwise have been the one reported.
    """
    runs = gh_client.json_out(
        "run", "list", "--repo", repo, "--branch", branch,
        "--limit", "20",
        "--json", "databaseId,headSha,workflowName,conclusion,status,number,attempt",
        default=None,
    )
    if runs is None:
        return RunDiscovery(failed=True)
    if not runs:
        return RunDiscovery()
    latest_sha = head_sha or runs[0]["headSha"]
    seen_workflows: set[str] = set()
    seen_cancelled: set[str] = set()
    rows: list[RunRow] = []
    for r in runs:
        if r["headSha"] != latest_sha:
            continue
        if r.get("conclusion") in SKIP_CONCLUSIONS:
            continue
        wf = r.get("workflowName", "")
        # Which set a run dedupes against is the whole of the claim rule. A
        # cancelled run answers to its own, so it never occupies the name a real
        # run of that workflow would claim and can never shadow one — while
        # still collapsing its own repeats, since gh run list returns newest
        # first and an older cancelled attempt carries nothing the newest lacks.
        claimed = seen_cancelled if r.get("conclusion") == "cancelled" else seen_workflows
        if wf in claimed:
            continue
        claimed.add(wf)
        rows.append(RunRow(
            run_id=r["databaseId"], number=r.get("number") or 0,
            head_sha=r["headSha"], status=r.get("status") or "",
            conclusion=r.get("conclusion") or "", workflow=wf,
            attempt=r.get("attempt") or 1,
        ))
    return RunDiscovery(rows=tuple(rows))


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


def commits_behind_main(repo: str, branch: str, cwd: str | None = None) -> int:
    """Commits on the default branch not reachable from `branch`.

    "main" in the name is the mainline rather than the literal branch: the
    trunk is resolved per repo, so a repo whose default branch is `master` is
    counted rather than being asked for a comparison that 404s. That 404 read
    back as zero, which is the shape of the bug it hides — a branch reported
    current against a trunk it had never been compared to, and a rebase that
    therefore never fired.

    Counted locally where there is a worktree, because the REST comparison is
    an API call on every invocation to learn something git already knows. The
    local count is only trusted when both remote-tracking refs resolve: an
    unresolvable ref counts as zero in git's own vocabulary, and zero here
    means "current with the trunk", which is the one wrong answer that is
    silently acted on.
    """
    default = git_topology.default_branch(cwd) if cwd else "main"
    # The trunk is not behind itself, and asking GitHub to compare it to itself
    # is a call whose answer is always zero. Without a worktree the trunk
    # cannot be resolved, so the two names it is usually spelled are both
    # treated as one rather than spending that call to find out.
    if branch == default or (not cwd and branch in ("main", "master")):
        return 0
    if cwd:
        local = _local_commits_behind(cwd, branch, default)
        if local is not None:
            return local
    r = gh_client.api(f"repos/{repo}/compare/{branch}...{default}", jq=".ahead_by")
    val = r.stdout.strip()
    return int(val) if r.ok and val.isdigit() else 0


def _local_commits_behind(cwd: str, branch: str, default: str) -> int | None:
    """The count from remote-tracking refs, or None when git cannot answer it.

    None rather than zero so the caller falls back to the API instead of
    reporting a branch current with a trunk it could not read.
    """
    base, head = f"origin/{branch}", f"origin/{default}"
    if not all(git_client.ok("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=cwd)
               for ref in (base, head)):
        return None
    return git_client.commits_ahead(cwd, target_ref=base, rev=head)


# ── Every check on a commit, not only the Actions ones ──────────────────────

# A check run posted by this app is a job of an Actions workflow run, which
# `fetch_latest_runs` already discovers. Every other app is a check the
# Actions-only path cannot see at all.
_ACTIONS_APP = "github-actions"

_ROLLUP_PAGE = 100

# Pages are followed, because the page boundary is the defect this module was
# changed to fix wearing a different hat: a failing check at position 101 that
# nobody listed is a commit that reports green. The extra call only happens on
# a commit that has more checks than fit, and the cap is there so a pathological
# commit cannot spin — reaching it sets `truncated`, which withholds the green
# verdict rather than inventing one.
_ROLLUP_MAX_PAGES = 10

# What a finished check has to conclude for it to count as passing. A
# whitelist rather than a list of failures, because the failure list is the
# one that goes stale: `cancelled` was already missing from
# `FAILURE_CONCLUSIONS`, so a cancelled external check read as a pass, and
# anything GitHub adds to the enum later would too. Everything not named here
# is something a reader has to be told about.
GREEN_CONCLUSIONS = frozenset(("success", "neutral", "skipped"))

# GitHub spells a status context's state in its own vocabulary, which is not
# the one FAILURE_CONCLUSIONS is written in. `error` is the entry that matters:
# unmapped, an errored external check has a conclusion no failure check matches
# and the commit reads as passing.
_STATUS_STATES: dict[str, tuple[str, str]] = {
    "ERROR": ("completed", "failure"),
    "FAILURE": ("completed", "failure"),
    "SUCCESS": ("completed", "success"),
    "PENDING": ("in_progress", ""),
    "EXPECTED": ("queued", ""),
}

# CheckStatusState has more members than these two (QUEUED, PENDING, REQUESTED,
# WAITING), and all of them fall through the `.get(..., "queued")` default
# below deliberately: `count_job_states` only distinguishes completed, running
# and queued, and every status but COMPLETED/IN_PROGRESS belongs in the last
# bucket.
_CHECK_STATUSES: dict[str, str] = {"COMPLETED": "completed", "IN_PROGRESS": "in_progress"}

_ROLLUP_QUERY = """
query($owner:String!,$name:String!,$oid:GitObjectID!,$page:Int!,$after:String){
  repository(owner:$owner,name:$name){
    object(oid:$oid){ ... on Commit {
      statusCheckRollup { contexts(first:$page,after:$after){
        pageInfo { hasNextPage endCursor } nodes {
        __typename
        ... on CheckRun { name status conclusion databaseId detailsUrl title
          checkSuite { app { slug } workflowRun { databaseId } } }
        ... on StatusContext { context state description targetUrl }
      } } }
    } }
  }
}
"""


@dataclass(frozen=True)
class CommitChecks:
    """Every check GitHub reports against one commit, split by who has to read it.

    `external` holds the checks no Actions run accounts for — an app's check
    run, a status context — each shaped like a job so the merge downstream does
    not need a second kind of thing to fold, and each tagged `_check_source` so
    the one consumer that would reach for Actions-only data knows not to.

    `actions` is the same rollup's view of the Actions jobs, keyed by run. It
    is not a second source of truth about them: it is what lets a run already
    proven green be reported without spending a `gh run view` on it.

    `answered` is the distinction the whole type exists to keep. A commit whose
    workflow is held for approval has no rollup at all, and reading that as an
    empty check list is how a red commit reports green — the defect this module
    was changed to fix, reintroduced one layer down.
    """

    answered: bool = False
    truncated: bool = False
    # Whether a read failed, as opposed to a commit having nothing to report.
    # `answered` is false for both, and they mean opposite things: GitHub
    # returning no rollup is a fact about the commit — an approval-gated run
    # has none — while a call that errored is an absence of facts. Only the
    # second may withhold a verdict, or every held workflow would report as
    # unknown.
    unreadable: bool = False
    external: tuple[dict, ...] = ()
    actions: dict[int, tuple[dict, ...]] = field(default_factory=dict)

    def green_run_ids(self) -> frozenset[int]:
        """Runs the rollup proves need no job payload fetched for them.

        Empty whenever the rollup did not answer or did not fit on one page:
        both mean a run's checks may be unseen, and a run cannot be proven
        green by checks nobody read. A run absent from `actions` is likewise
        never green — that is what keeps a cancelled run, which the rollup
        drops while its failed jobs live on, from being skipped.
        """
        if not self.answered or self.truncated:
            return frozenset()
        return frozenset(
            run_id for run_id, rows in self.actions.items()
            if rows and all(row["conclusion"] in GREEN_CONCLUSIONS for row in rows)
        )


def _job_shape(name, status, conclusion, db_id=0, **extra) -> dict:
    """One check in the job shape `ci_runs` and `ci_annotations` already read."""
    return {
        "name": name, "databaseId": db_id, "status": status,
        "conclusion": conclusion, "steps": [], **extra,
    }


def _owning_run(node: dict) -> int | None:
    """The Actions run a rollup check run belongs to, or None when no run does.

    None is the whole point of the read: a check another app posted is the one
    the Actions-only discovery path cannot see.
    """
    suite = node.get("checkSuite") or {}
    if (suite.get("app") or {}).get("slug") != _ACTIONS_APP:
        return None
    return (suite.get("workflowRun") or {}).get("databaseId")


def _from_check_run(node: dict, run_id: int | None) -> dict:
    """A rollup check run in the job shape, tagged with where it came from."""
    job = _job_shape(
        node.get("name", "unknown"),
        _CHECK_STATUSES.get(node.get("status", ""), "queued"),
        (node.get("conclusion") or "").lower(),
        node.get("databaseId") or 0,
    )
    if run_id is None:
        job["_check_source"] = "check_run"
        job["_details_url"] = node.get("detailsUrl") or ""
        job["_summary"] = node.get("title") or ""
    return job


def _from_status_context(node: dict) -> dict:
    """A rollup status context in the job shape, with its state translated."""
    status, conclusion = _STATUS_STATES.get(node.get("state", ""), ("queued", ""))
    return _job_shape(
        node.get("context", "unknown"), status, conclusion,
        _check_source="status_context",
        _details_url=node.get("targetUrl") or "",
        _summary=node.get("description") or "",
    )


def _sort_page(nodes: list[dict], external: list[dict],
               actions: dict[int, list[dict]]) -> None:
    """Split one page of rollup nodes into the two lists `CommitChecks` keeps."""
    for node in nodes:
        if node.get("__typename") == "StatusContext":
            external.append(_from_status_context(node))
            continue
        run_id = _owning_run(node)
        job = _from_check_run(node, run_id)
        if run_id is None:
            external.append(job)
            continue
        actions.setdefault(run_id, []).append(job)


@dataclass(frozen=True)
class _Page:
    """One attempt at a page of the rollup, and which kind of nothing it got.

    `contexts` present is a page to read. Absent, `failed` is the whole of the
    difference between "this commit has no checks" and "nobody could find
    out", which the caller has to keep apart.
    """

    contexts: dict | None = None
    failed: bool = False


def _rollup_page(repo: str, sha: str, after: str | None) -> _Page:
    """One page of the commit's rollup, or which way it came back empty."""
    owner, _, name = repo.partition("/")
    r = gh_client.graphql(_ROLLUP_QUERY, variables={
        "owner": owner, "name": name, "oid": sha,
        "page": _ROLLUP_PAGE, "after": after,
    })
    if not r.ok:
        return _Page(failed=True)
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return _Page(failed=True)
    # A GraphQL error, or a null `data`, is the server declining to answer — a
    # token without the scope to read checks lands here, and reading that as
    # "no checks" would hide every external check in the repo behind a
    # permissions problem nobody is told about.
    if not isinstance(data, dict) or data.get("errors") or data.get("data") is None:
        return _Page(failed=True)
    obj = ((data["data"].get("repository") or {}).get("object")) or {}
    contexts = (obj.get("statusCheckRollup") or {}).get("contexts") or {}
    return _Page(contexts=contexts) if contexts.get("nodes") is not None else _Page()


def fetch_commit_checks(repo: str, sha: str) -> CommitChecks:
    """Every check GitHub reports against `sha`.

    One call for a commit whose checks fit on a page, which is all of them in
    practice; a commit with more is followed rather than cut off, because a
    check nobody listed is a check nobody reports.

    An unanswered rollup is reported as such rather than as an empty result:
    see `CommitChecks.answered`. A commit GitHub has never seen — a local HEAD
    that was never pushed — answers the same way, and the caller retries at a
    commit the runs themselves name.
    """
    external: list[dict] = []
    actions: dict[int, list[dict]] = {}
    after: str | None = None
    answered = complete = failed = False

    for _ in range(_ROLLUP_MAX_PAGES):
        page = _rollup_page(repo, sha, after)
        # A later page failing is not the same as there being no rollup: what
        # was already read stands, and the unread remainder leaves the result
        # incomplete rather than empty.
        if page.contexts is None:
            failed = page.failed
            break
        answered = True
        _sort_page(page.contexts["nodes"], external, actions)
        info = page.contexts.get("pageInfo") or {}
        complete = not info.get("hasNextPage")
        if complete:
            break
        after = info.get("endCursor")

    if not answered:
        return CommitChecks(unreadable=failed)
    # Anything short of a clean finish is truncated, which is what withholds a
    # green verdict the pages nobody read could have contradicted.
    return CommitChecks(
        answered=True, truncated=not complete, unreadable=failed,
        external=tuple(external),
        actions={rid: tuple(rows) for rid, rows in actions.items()},
    )
