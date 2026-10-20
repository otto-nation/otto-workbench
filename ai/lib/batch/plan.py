"""Pre-flight: which of my open PRs need which batch steps.

One GraphQL search covers every repo. The only nested connections are the ones
the comments need requires — review threads, and the newest reviews and
PR comments, each bounded and never paged — so a plan over dozens of PRs costs a
few points of the hourly GraphQL budget. Thread comments, the connection whose
cost would scale with discussion length, are never asked for.

The plan fetches only the origin branches rows need into a private
`refs/pr-batch/<hex>` namespace, claimed under the batch state root before
the fetch so a killed plan does not leak refs. `pr gc` drops a namespace
that has no live claim and no run referencing it.
"""

# doc-group: batch

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import core.log
import core.serde
import core.session_lock
import gh.client
import gh.pr_pages
import git.client
import git.topology
import pr.ci_need
import pr.context
import pr.owed_feedback
import pr.settlement
import pr.state
import pr.target
import rebase.inspect
import rebase.need
import review.document
import review.paths
from batch.model import Step
from gh.pr_data import PRData
from pr.comments_fix import CloseoutDebt

PLAN_SCHEMA_VERSION = 1

# The comments need reads the newest reviews and PR comments, at the page sizes
# `pr comments`' own read uses. GitHub prices a nested connection by the count of
# its parents, not its `last:`, so the size is free: each one adds about a point
# to a search over 100 PRs, whatever its limit.
# ceiling: a PR whose only unanswered feedback is older than its newest 100
# reviews (or PR comments) plans no comments step; page them, as `pr comments`
# does, once a PR carries that much top-level traffic.
# The newest comments on each review thread, which is all `settlement.settled_ids`
# reads: whether anyone spoke after a settlement was recorded. Unlike the two
# connections above this one is nested under the threads, so its parents are
# every thread on every PR in the search and it costs about a point per hundred
# threads rather than one in total.
# ceiling: a reply older than a thread's newest five comments, posted after the
# thread was settled, leaves the PR planned with no comments step; raise this
# once a thread is seen to carry that much traffic between two plans.
_THREAD_COMMENTS = 5

_PR_FIELDS = """
  number title isDraft headRefName headRefOid mergeStateStatus
  baseRefName isCrossRepository
  repository { nameWithOwner }
  reviewThreads(first: 100) {
    nodes { id isResolved comments(last: %(thread_comments)d) {
      nodes { createdAt lastEditedAt author { login } } } }
  }
  reviews(last: %(reviews)d) {
    nodes { databaseId state body minimizedReason lastEditedAt author { login } }
  }
  comments(last: %(comments)d) { nodes { databaseId lastEditedAt author { login __typename } } }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
""" % {"reviews": gh.pr_pages.GQL_REVIEWS_LIMIT,
       "comments": gh.pr_pages.GQL_ISSUE_COMMENTS_LIMIT,
       "thread_comments": _THREAD_COMMENTS}

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
  viewer { login }
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { state %s } }
}
""" % _PR_FIELDS

_REBASE_NEEDED = {"BEHIND": "behind its base", "DIRTY": "has merge conflicts"}

# Where the plan fetches every branch of a repo, one namespace per plan. Never
# refs/remotes/origin/*: the rebase lease trusts that ref as last seen before
# its own fetch, and a plan-time fetch there would let a colleague's push
# become the tip the rebase remembers.
NAMESPACE_ROOT = "refs/pr-batch"


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
    # What pr's saved state says is owed (CloseoutDebt.describe()), "" when nothing is.
    closeout: str = ""

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


def _pid_exists(pid: int) -> bool:
    """Whether the kernel still has *pid*; 0 and negative never name a claim's writer."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


@dataclass(frozen=True)
class NamespaceClaim:
    """Ownership of one `refs/pr-batch/<hex>` namespace while a plan is in flight."""

    namespace: str = ""
    repo_dirs: list[str] = field(default_factory=list)
    pid: int = 0
    # When *pid* started (`core.session_lock.process_start`), so a pid the
    # kernel recycles onto an unrelated process does not keep the claim live.
    started: str = ""
    created: str = ""

    @property
    def live(self) -> bool:
        """Whether the process that wrote this claim is still running.

        A pid that no longer exists is dead. `process_start` cannot say that by
        itself: it returns "" both for a dead pid and for a `ps` that failed. So
        existence is asked of the kernel first, and an unreadable start time on
        either side then errs toward live; only two readable, differing start
        times prove the pid was recycled.
        """
        if not _pid_exists(self.pid):
            return False
        now = core.session_lock.process_start(self.pid)
        return not (self.started and now) or now == self.started


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


def ci_step_need(state: str) -> StepNeed:
    """The CI step's need, from `pr.ci_need` — the one place the rollup is judged."""
    found = pr.ci_need.need(state)
    return StepNeed(found.needed, found.reason)


def new_namespace() -> str:
    return f"{NAMESPACE_ROOT}/{secrets.token_hex(4)}"


def _claims_dir() -> Path:
    # store.batch_root is the SSOT; imported here to avoid a module-level cycle
    # (store imports plan for drop_refs on terminal commit and reap).
    import batch.store
    return batch.store.batch_root() / "claims"


def _claim_path(namespace: str) -> Path | None:
    if not namespace.startswith(f"{NAMESPACE_ROOT}/"):
        return None
    token = namespace[len(NAMESPACE_ROOT) + 1:]
    if not token or token != Path(token).name:
        return None
    return _claims_dir() / f"{token}.json"


def write_claim(namespace: str, repo_dirs: list[str], *,
                pid: int | None = None, created: str | None = None) -> None:
    """Record that *pid* owns *namespace* in *repo_dirs*, before any fetch."""
    path = _claim_path(namespace)
    if path is None:
        return
    owner = os.getpid() if pid is None else pid
    claim = NamespaceClaim(
        namespace=namespace, repo_dirs=list(repo_dirs), pid=owner,
        started=core.session_lock.process_start(owner),
        created=created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    core.serde.write_json(path, core.serde.to_dict(claim))


def read_claim(namespace: str) -> NamespaceClaim | None:
    path = _claim_path(namespace)
    if path is None or not path.is_file():
        return None
    try:
        return core.serde.from_dict(NamespaceClaim, json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def load_claims() -> list[NamespaceClaim]:
    root = _claims_dir()
    if not root.is_dir():
        return []
    claims = []
    for path in sorted(root.glob("*.json")):
        try:
            claims.append(core.serde.from_dict(NamespaceClaim, json.loads(path.read_text())))
        except (OSError, ValueError, TypeError) as exc:
            core.log.warn(f"could not read namespace claim {path.name}: {exc}")
    return claims


def remove_claim(namespace: str) -> None:
    path = _claim_path(namespace)
    if path is not None:
        path.unlink(missing_ok=True)


def namespaces_in(repo_dir: str) -> list[str]:
    """The `refs/pr-batch/<hex>` prefixes that have refs in *repo_dir*."""
    if not Path(repo_dir).is_dir():
        return []
    found: dict[str, None] = {}
    prefix = f"{NAMESPACE_ROOT}/"
    for ref in git.client.lines("for-each-ref", "--format=%(refname)", NAMESPACE_ROOT,
                                cwd=repo_dir):
        if not ref.startswith(prefix):
            continue
        token = ref[len(prefix):].split("/", 1)[0]
        if token:
            found[f"{prefix}{token}"] = None
    return list(found)


def _fetch_refspecs(repo_dir: str, specs: list[str]) -> bool:
    # An empty --refmap is what stops git's opportunistic update: with a
    # command-line refspec it still applies remote.origin.fetch and would move
    # refs/remotes/origin/* alongside the namespace.
    r = git.client.run("fetch", "--no-tags", "--quiet", "--refmap=", "origin",
                       *specs, cwd=repo_dir)
    return r.ok


def fetch_namespace(repo_dir: str, namespace: str, branches: list[str]) -> bool:
    """Fetch *branches* of origin into *namespace*, leaving origin's refs alone."""
    wanted = sorted({b for b in branches if b})
    if not wanted:
        return True
    specs = [f"+refs/heads/{b}:{namespace}/{b}" for b in wanted]
    if _fetch_refspecs(repo_dir, specs):
        return True
    # An explicit refspec whose source ref is gone fails the whole fetch, so a
    # branch deleted on origin would drop every other ref this plan needs.
    # Retry each remaining refspec; keep whatever landed. A namespaced ref that
    # is still missing falls back to GitHub's merge state, as today. Listing
    # with ls-remote first would add a transfer to every plan (status's hot
    # path) and still race a delete between the list and the fetch, so the
    # retry is the robust option on its own.
    landed = [_fetch_refspecs(repo_dir, [spec]) for spec in specs]
    return any(landed)


def drop_refs(repo_dirs: list[str], namespace: str) -> bool:
    """Delete *namespace*'s refs in each repo; a no-op for anything outside NAMESPACE_ROOT.

    Returns False when any repo's refs would not delete (one warning per repo),
    True otherwise — including when there was nothing to drop. The claim is
    removed only after every repo dropped successfully.
    """
    if not namespace.startswith(f"{NAMESPACE_ROOT}/"):
        return True
    dirs = set(repo_dirs)
    claim = read_claim(namespace)
    if claim is not None:
        dirs.update(claim.repo_dirs)
    # A list, not a generator: all() would short-circuit and skip the remaining repos after one failure.
    results = [_drop_repo_refs(d, namespace) for d in sorted(dirs)]
    ok = all(results)
    if ok:
        remove_claim(namespace)
    return ok


def _held_ref_lock(repo_dir: str, namespace: str) -> str | None:
    """The packed-refs or loose-ref lock blocking a drop, if one exists."""
    packed = git.client.out("rev-parse", "--git-path", "packed-refs.lock", cwd=repo_dir)
    if packed:
        path = Path(packed) if os.path.isabs(packed) else Path(repo_dir) / packed
        if path.is_file():
            return str(path)
    ns_path = git.client.out("rev-parse", "--git-path", namespace, cwd=repo_dir)
    root = Path(ns_path) if os.path.isabs(ns_path) else Path(repo_dir) / ns_path
    if root.is_dir():
        locks = sorted(p for p in root.rglob("*.lock") if p.is_file())
        if locks:
            return str(locks[0])
    return None


def _warn_drop_failed(repo_dir: str, namespace: str, stderr: str) -> None:
    lock = _held_ref_lock(repo_dir, namespace)
    if lock is not None:
        core.log.warn(
            f"could not delete {namespace} in {repo_dir}: {lock} exists; "
            f"confirm no git process holds it before removing the lock")
        return
    detail = stderr.strip() or "git update-ref failed"
    core.log.warn(f"could not delete {namespace} in {repo_dir}: {detail}")


def _drop_repo_refs(repo_dir: str, namespace: str) -> bool:
    # A checkout removed since the plan has no refs left to drop, and git
    # cannot even start with it as the working directory.
    if not Path(repo_dir).is_dir():
        return True
    refs = git.client.lines("for-each-ref", "--format=%(refname)", namespace, cwd=repo_dir)
    if not refs:
        return True
    lines = ["start", *(f"delete {ref}" for ref in refs), "prepare", "commit"]
    r = git.client.run("update-ref", "--stdin", cwd=repo_dir,
                       input_text="\n".join(lines) + "\n")
    if r.ok:
        return True
    _warn_drop_failed(repo_dir, namespace, r.stderr)
    return False


def comments_need(node: dict, viewer: str, state: pr.state.PRState | None) -> StepNeed:
    """Whether `pr comments` would find feedback to answer on *node*'s PR.

    The answer is `pr.owed_feedback`'s, read over the plan query's subset of
    the PR — threads, the newest reviews and PR comments — so the planner and
    `pr comments` cannot disagree about what is owed.
    """
    data = PRData(
        viewer_login=viewer, head_sha=node.get("headRefOid", ""),
        head_ref=node.get("headRefName", ""), base_ref=node.get("baseRefName") or "",
        review_threads=(node.get("reviewThreads") or {}).get("nodes") or [],
        reviews=(node.get("reviews") or {}).get("nodes") or [],
        issue_comments=(node.get("comments") or {}).get("nodes") or [],
    )
    owed = pr.owed_feedback.owed_feedback(
        data, _settled_from(state, data.review_threads, viewer),
        state.comments if state is not None else None)
    if not owed.owed:
        return StepNeed(False, "no unresolved threads or unanswered comments")
    return StepNeed(True, owed.describe())


def review_need(review_file: Path, head_sha: str, *, local_head: str = "") -> StepNeed:
    """Whether the self-review covers the commit a review would publish.

    That commit is the local worktree's HEAD when there is one — it carries any
    unpushed fix commits — and GitHub's head otherwise, where nothing local exists.
    """
    if not review_file.is_file():
        return StepNeed(True, "no self-review yet")
    subject = local_head or head_sha
    reviewed = review.document.ReviewHeader.parse(review_file.read_text()).head_sha
    if review.document.covers(reviewed, subject):
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


def _settled_from(
    state: pr.state.PRState | None, threads: list[dict], viewer: str,
) -> set[str]:
    """The threads *state* settles, by the rule `pr comments` triages on."""
    if state is None:
        return set()
    comments = {
        t["id"]: (t.get("comments") or {}).get("nodes") or []
        for t in threads if t.get("id")
    }
    return pr.settlement.settled_ids(state.fix.fix, comments, viewer)


def closeout_debt(repo_dir: str, branch: str) -> CloseoutDebt:
    """What `pr comments` recorded as owed to *branch*'s PR and never delivered.

    Read from saved state only: no fetch. Publish pays it
    with `CloseoutDebt.command`; a finished run's report prints that command.
    """
    return _closeout_from(_pr_state(repo_dir, branch))


def _closeout_from(state: pr.state.PRState | None) -> CloseoutDebt:
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
         ref_namespace: str = "", viewer: str = "") -> PlanRow:
    branch, head = node["headRefName"], node["headRefOid"]
    base = node.get("baseRefName") or ""
    fork = bool(node.get("isCrossRepository"))
    ci_state = rollup_state(node)
    merge_state = node.get("mergeStateStatus") or "UNKNOWN"
    # One state load (one git call) per row, shared by the comments need and the closeout.
    state = _pr_state(repo_dir, branch)
    return PlanRow(
        repo=repo, repo_dir=repo_dir, pr=int(node["number"]), title=node.get("title", ""),
        branch=branch, head_sha=head, is_draft=bool(node.get("isDraft")),
        needs={
            Step.REBASE: FORK_NEED if fork else tree_rebase_need(
                repo_dir, branch, base, ref_namespace, merge_state),
            Step.CI: FORK_NEED if fork else ci_step_need(ci_state),
            Step.COMMENTS: comments_need(node, viewer, state),
            Step.REVIEW: review_need(_review_file(repo, branch), head, local_head=local_head),
        },
        local_head=local_head, base_ref=base, is_fork=fork, ci_state=ci_state,
        ref_namespace=ref_namespace,
        closeout=_closeout_from(state).describe(),
    )


def _fetch_branches(data: dict, repo_dirs: dict[str, str]) -> dict[str, list[str]]:
    """Per checkout, the origin branches tree_rebase_need will read.

    Base branches of every matched PR, plus same-repo heads. Fork heads live
    on another remote and are skipped, so a missing namespaced ref still falls
    back to GitHub's merge state as today.
    """
    by_fold = {slug.casefold(): slug for slug in repo_dirs}
    needed: dict[str, set[str]] = {d: set() for d in repo_dirs.values()}
    for n in (data.get("search") or {}).get("nodes") or []:
        name = (n.get("repository") or {}).get("nameWithOwner") or ""
        slug = by_fold.get(name.casefold())
        if slug is None:
            continue
        repo_dir = repo_dirs[slug]
        base = n.get("baseRefName") or ""
        if base:
            needed[repo_dir].add(base)
        head = n.get("headRefName") or ""
        if head and not n.get("isCrossRepository"):
            needed[repo_dir].add(head)
    return {d: sorted(b) for d, b in needed.items()}


def rows_from_search(data: dict, repo_dirs: dict[str, str],
                     namespaces: dict[str, str] | None = None) -> list[PlanRow]:
    by_fold = {slug.casefold(): slug for slug in repo_dirs}
    viewer = (data.get("viewer") or {}).get("login", "")
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
                         (namespaces or {}).get(repo_dir, ""), viewer))
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
    # Absolute, because a run persists these: a relative checkout names a
    # different directory, or none, to every later reader in another cwd.
    repo_dirs = [os.path.abspath(d) for d in repo_dirs]
    slugs = [_repo_slug(d) for d in repo_dirs]
    by_slug = dict(zip(slugs, repo_dirs))
    if len(by_slug) != len(repo_dirs):
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        raise PlanError("--checkout names the same repo more than once: " + ", ".join(dupes))
    data = _graphql(_SEARCH, {"q": search_query(sorted(by_slug))})
    needed = _fetch_branches(data, by_slug)
    namespace = new_namespace()
    try:
        # Claim before the fetch so a killed plan still names the namespace;
        # drop_refs removes the claim only after every repo dropped.
        write_claim(namespace, repo_dirs)
        fetched = [d for d in repo_dirs if fetch_namespace(d, namespace, needed.get(d, []))]
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
    data = _graphql(_ONE, {"owner": owner, "name": name, "number": row.pr})
    node = (data.get("repository") or {}).get("pullRequest") or {}
    if node.get("state") != "OPEN":
        return None
    return _row(node, row.repo_dir, row.repo, row.local_head, row.ref_namespace,
                (data.get("viewer") or {}).get("login", ""))
