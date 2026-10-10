"""Pre-flight: which of my open PRs need which batch steps.

One GraphQL search covers every repo and asks only for fields whose cost does
not scale with comment volume — no nested `comments` connection — so a plan
over dozens of PRs costs a point or two of the hourly GraphQL budget.
"""

# doc-group: batch

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import core.log
import gh.client
import git.client
import git.topology
import pr.context
import pr.settlement
import pr.state
import pr.target
import rebase.inspect
import rebase.need
import review.document
import review.paths
from batch.model import Step
from pr.comments_fix import CloseoutDebt

PLAN_SCHEMA_VERSION = 1

_PR_FIELDS = """
  number title isDraft headRefName headRefOid mergeStateStatus
  baseRefName isCrossRepository
  repository { nameWithOwner }
  reviewThreads(first: 100) { nodes { id isResolved } }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
"""

# ceiling: search first:100 and reviewThreads first:100 are unpaginated; upgrade
# to pagination if a user has >100 open PRs in the scope or a PR has >100 threads.
_SEARCH = """
query($q: String!) {
  viewer { login }
  search(query: $q, type: ISSUE, first: 100) { nodes { ... on PullRequest { %s } } }
}
""" % _PR_FIELDS

_ONE = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { state %s } }
}
""" % _PR_FIELDS

_REBASE_NEEDED = {"BEHIND": "behind its base", "DIRTY": "has merge conflicts"}

# Where the plan fetches every branch of a repo, one namespace per plan. Never
# refs/remotes/origin/*: the rebase lease trusts that ref as last seen before
# its own fetch, and a plan-time fetch there would let a colleague's push
# become the tip the rebase remembers.
NAMESPACE_ROOT = "refs/pr-batch"

# Rollup states a CI step waits on rather than fixes straight away.
CI_RUNNING = frozenset({"PENDING", "EXPECTED"})
_CI_FAILED = frozenset({"FAILURE", "ERROR"})


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class StepNeed:
    needed: bool
    reason: str


# Rebase and CI are out of scope for a head that lives in someone else's fork.
FORK_NEED = StepNeed(False, "fork head — out of scope")


@dataclass(frozen=True)
class PlanRow:
    repo: str
    repo_dir: str
    pr: int
    title: str
    branch: str
    head_sha: str
    is_draft: bool
    needs: dict[Step, StepNeed] = field(default_factory=dict)
    # HEAD of the branch's local worktree, or "" when none is checked out.
    # Unpushed commits live here, so a self-review is judged against it.
    local_head: str = ""
    # The PR's base branch, whether its head lives in a fork, the head commit's
    # check rollup state, and the namespace this row's refs were fetched into.
    base_ref: str = ""
    is_fork: bool = False
    ci_state: str = ""
    ref_namespace: str = ""

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.pr}"


@dataclass(frozen=True)
class Plan:
    viewer: str
    rows: list[PlanRow]
    schema_version: int = PLAN_SCHEMA_VERSION
    ref_namespace: str = ""
    ref_dirs: list[str] = field(default_factory=list)


def search_query(repos: list[str]) -> str:
    return " ".join(["is:pr is:open author:@me archived:false", *(f"repo:{r}" for r in repos)])


def rebase_need(merge_state: str) -> StepNeed:
    """GitHub's merge state — the fallback when the branch's refs cannot be read."""
    if merge_state in _REBASE_NEEDED:
        return StepNeed(True, _REBASE_NEEDED[merge_state])
    if merge_state == "UNKNOWN":
        return StepNeed(True, "mergeability unknown — the rebase step decides")
    return StepNeed(False, f"up to date ({merge_state.lower()})")


def tree_rebase_need(repo_dir: str, branch: str, base: str, namespace: str,
                     merge_state: str) -> StepNeed:
    """Rebase need counted from refs, falling back to *merge_state*.

    The head is the local branch when one exists — it carries any unpushed
    work — and the namespaced copy of the remote branch otherwise.
    """
    if namespace and base:
        local = f"refs/heads/{branch}"
        head = local if rebase.inspect.ref_exists(repo_dir, local) else f"{namespace}/{branch}"
        found = rebase.need.need(repo_dir, head, f"{namespace}/{base}", base_label=base)
        if found.resolved:
            return StepNeed(found.needed, found.reason)
    return rebase_need(merge_state)


def rollup_state(node: dict) -> str:
    """The head commit's `statusCheckRollup.state`, or "" when it reports none."""
    nodes = (node.get("commits") or {}).get("nodes") or []
    commit = (nodes[-1] or {}).get("commit") if nodes else None
    return ((commit or {}).get("statusCheckRollup") or {}).get("state") or ""


def ci_need(state: str) -> StepNeed:
    if state in _CI_FAILED:
        return StepNeed(True, f"checks {state.lower()}")
    if state in CI_RUNNING:
        return StepNeed(True, "checks running")
    if state == "SUCCESS":
        return StepNeed(False, "checks green")
    # Not "green": a workflow waiting on approval reports no rollup at all.
    return StepNeed(False, "no checks reported")


def new_namespace() -> str:
    return f"{NAMESPACE_ROOT}/{secrets.token_hex(4)}"


def fetch_namespace(repo_dir: str, namespace: str) -> bool:
    """Fetch every branch of origin into *namespace*, leaving origin's refs alone."""
    # An empty --refmap is what stops git's opportunistic update: with a
    # command-line refspec it still applies remote.origin.fetch and would move
    # refs/remotes/origin/* alongside the namespace.
    r = git.client.run("fetch", "--no-tags", "--quiet", "--refmap=", "origin",
                       f"+refs/heads/*:{namespace}/*", cwd=repo_dir)
    return r.ok


def drop_refs(repo_dirs: list[str], namespace: str) -> None:
    """Delete *namespace*'s refs in each repo; a no-op for anything outside NAMESPACE_ROOT."""
    if not namespace.startswith(f"{NAMESPACE_ROOT}/"):
        return
    for repo_dir in sorted(set(repo_dirs)):
        _drop_repo_refs(repo_dir, namespace)


def _drop_repo_refs(repo_dir: str, namespace: str) -> None:
    # A checkout removed since the plan has no refs left to drop, and git
    # cannot even start with it as the working directory.
    if not Path(repo_dir).is_dir():
        return
    for ref in git.client.lines("for-each-ref", "--format=%(refname)", namespace,
                                cwd=repo_dir):
        r = git.client.run("update-ref", "-d", ref, cwd=repo_dir)
        if not r.ok:
            core.log.warn(f"could not delete {ref} in {repo_dir}: {r.stderr.strip()}")


def comments_need(threads: list[dict], settled: set[str]) -> StepNeed:
    owed = [t for t in threads if not t.get("isResolved") and t.get("id") not in settled]
    if not owed:
        return StepNeed(False, "no unresolved threads")
    return StepNeed(True, f"{len(owed)} unresolved thread{'' if len(owed) == 1 else 's'}")


def review_need(review_file: Path, head_sha: str, *, local_head: str = "") -> StepNeed:
    """Whether the self-review covers the commit a review would publish.

    That commit is the local worktree's HEAD when there is one — it carries any
    unpushed fix commits — and GitHub's head otherwise, where nothing local exists.
    """
    if not review_file.is_file():
        return StepNeed(True, "no self-review yet")
    subject = local_head or head_sha
    reviewed = review.document.ReviewHeader.parse(review_file.read_text()).head_sha
    if reviewed and subject.startswith(reviewed):
        return StepNeed(False, "self-review is current")
    of = f"self-review is of {git.client.abbrev(reviewed) or 'an unknown head'}"
    if local_head and local_head != head_sha:
        return StepNeed(True, f"{of}; local HEAD {git.client.abbrev(local_head)} "
                              f"differs from GitHub's {git.client.abbrev(head_sha)}")
    return StepNeed(True, of)


def _pr_state(repo_dir: str, branch: str) -> pr.state.PRState | None:
    """The `pr` state saved for *branch* of *repo_dir*'s repo, or None when there is none."""
    # A checkout that is gone has no state rather than an error: a finished
    # run's report reads every item, including ones whose checkout was removed,
    # and git cannot be run in a directory that does not exist.
    if not Path(repo_dir).is_dir():
        return None
    key = pr.target.repo_key_from_origin(repo_dir)
    return pr.state.load_state(pr.target.target_dir(key, branch)) if key else None


def settled_ids(repo_dir: str, branch: str) -> set[str]:
    state = _pr_state(repo_dir, branch)
    if state is None:
        return set()
    return {i.id for i in state.fix.fix.items if i.outcome in pr.settlement.SETTLE_OUTCOMES}


def closeout_debt(repo_dir: str, branch: str) -> CloseoutDebt:
    """What `pr comments` recorded as owed to *branch*'s PR and never delivered.

    Read from saved state only, like `settled_ids`: no fetch. Publish pays it
    with `CloseoutDebt.command`; a finished run's report prints that command.
    """
    state = _pr_state(repo_dir, branch)
    return state.fix.closeout_debt() if state is not None else CloseoutDebt()


def _review_file(repo: str, branch: str) -> Path:
    return review.paths.self_review_file_path(repo, branch)


def _repo_slug(repo_dir: str) -> str:
    return pr.context.detect_repo(repo_dir)


def _local_heads(repo_dir: str) -> dict[str, str]:
    """Branch → HEAD for every worktree of *repo_dir* that has a branch checked out."""
    return {e.branch: git.client.head_sha(cwd=str(e.path))
            for e in git.topology.worktree_entries(repo_dir) if e.branch}


def _row(node: dict, repo_dir: str, repo: str, local_head: str = "",
         ref_namespace: str = "") -> PlanRow:
    branch, head = node["headRefName"], node["headRefOid"]
    base = node.get("baseRefName") or ""
    fork = bool(node.get("isCrossRepository"))
    ci_state = rollup_state(node)
    threads = (node.get("reviewThreads") or {}).get("nodes") or []
    merge_state = node.get("mergeStateStatus") or "UNKNOWN"
    return PlanRow(
        repo=repo, repo_dir=repo_dir, pr=int(node["number"]), title=node.get("title", ""),
        branch=branch, head_sha=head, is_draft=bool(node.get("isDraft")),
        needs={
            Step.REBASE: FORK_NEED if fork else tree_rebase_need(
                repo_dir, branch, base, ref_namespace, merge_state),
            Step.CI: FORK_NEED if fork else ci_need(ci_state),
            Step.COMMENTS: comments_need(threads, settled_ids(repo_dir, branch)),
            Step.REVIEW: review_need(_review_file(repo, branch), head, local_head=local_head),
        },
        local_head=local_head, base_ref=base, is_fork=fork, ci_state=ci_state,
        ref_namespace=ref_namespace,
    )


def rows_from_search(data: dict, repo_dirs: dict[str, str],
                     namespaces: dict[str, str] | None = None) -> list[PlanRow]:
    by_fold = {slug.casefold(): slug for slug in repo_dirs}
    heads: dict[str, dict[str, str]] = {}
    rows = []
    for n in (data.get("search") or {}).get("nodes") or []:
        name = (n.get("repository") or {}).get("nameWithOwner") or ""
        slug = by_fold.get(name.casefold())
        if slug is None:
            continue
        repo_dir = repo_dirs[slug]
        if repo_dir not in heads:
            heads[repo_dir] = _local_heads(repo_dir)
        rows.append(_row(n, repo_dir, slug, heads[repo_dir].get(n.get("headRefName", ""), ""),
                         (namespaces or {}).get(repo_dir, "")))
    return sorted(rows, key=lambda r: (r.repo, r.pr))


def _graphql(query: str, variables: dict) -> dict:
    r = gh.client.graphql(query, variables=variables)
    if not r.ok:
        raise PlanError(f"GitHub query failed: {r.stderr.strip() or r.stdout.strip()}")
    payload = None
    try:
        payload = json.loads(r.stdout)
        data = payload["data"]
        if not isinstance(data, dict):
            raise TypeError("data is not an object")
        return data
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        messages = []
        if isinstance(payload, dict):
            messages = [
                e.get("message", "")
                for e in (payload.get("errors") or [])
                if isinstance(e, dict)
            ]
        detail = "; ".join(m for m in messages if m) or str(exc)
        raise PlanError(f"GitHub query failed: {detail}") from exc


def build_plan(repo_dirs: list[str]) -> Plan:
    slugs = [_repo_slug(d) for d in repo_dirs]
    by_slug = dict(zip(slugs, repo_dirs))
    if len(by_slug) != len(repo_dirs):
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        raise PlanError("--checkout names the same repo more than once: " + ", ".join(dupes))
    namespace = new_namespace()
    try:
        fetched = [d for d in repo_dirs if fetch_namespace(d, namespace)]
        data = _graphql(_SEARCH, {"q": search_query(sorted(by_slug))})
        rows = rows_from_search(data, by_slug, {d: namespace for d in fetched})
    except BaseException:
        # No plan comes back to own the refs — Ctrl-C included — so drop them
        # here, in every repo: a fetch that failed part-way may still have
        # written some.
        drop_refs(repo_dirs, namespace)
        raise
    return Plan(viewer=(data.get("viewer") or {}).get("login", ""), rows=rows,
                ref_namespace=namespace, ref_dirs=fetched)


def replan_row(row: PlanRow) -> PlanRow | None:
    """Re-read one PR from GitHub, judging its review against ``row.local_head``."""
    owner, name = row.repo.split("/", 1)
    node = (_graphql(_ONE, {"owner": owner, "name": name, "number": row.pr})
            .get("repository") or {}).get("pullRequest") or {}
    if node.get("state") != "OPEN":
        return None
    return _row(node, row.repo_dir, row.repo, row.local_head, row.ref_namespace)
