"""Pre-flight: which of my open PRs need which batch steps.

One GraphQL search covers every repo and asks only for fields whose cost does
not scale with comment volume — no nested `comments` connection — so a plan
over dozens of PRs costs a point or two of the hourly GraphQL budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import gh.client
import pr.context
import pr.settlement
import pr.state
import pr.target
import review.document
import review.paths
from batch.model import Step

PLAN_SCHEMA_VERSION = 1

_PR_FIELDS = """
  number title isDraft headRefName headRefOid mergeStateStatus
  repository { nameWithOwner }
  reviewThreads(first: 100) { nodes { id isResolved } }
"""

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


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class StepNeed:
    needed: bool
    reason: str


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

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.pr}"


@dataclass(frozen=True)
class Plan:
    viewer: str
    rows: list[PlanRow]
    schema_version: int = PLAN_SCHEMA_VERSION


def search_query(repos: list[str]) -> str:
    return " ".join(["is:pr is:open author:@me archived:false", *(f"repo:{r}" for r in repos)])


def rebase_need(merge_state: str) -> StepNeed:
    if merge_state in _REBASE_NEEDED:
        return StepNeed(True, _REBASE_NEEDED[merge_state])
    if merge_state == "UNKNOWN":
        return StepNeed(False, "GitHub is still computing mergeability")
    return StepNeed(False, f"up to date ({merge_state.lower()})")


def comments_need(threads: list[dict], settled: set[str]) -> StepNeed:
    owed = [t for t in threads if not t.get("isResolved") and t.get("id") not in settled]
    if not owed:
        return StepNeed(False, "no unresolved threads")
    return StepNeed(True, f"{len(owed)} unresolved thread{'' if len(owed) == 1 else 's'}")


def review_need(review_file: Path, head_sha: str) -> StepNeed:
    if not review_file.is_file():
        return StepNeed(True, "no self-review yet")
    reviewed = review.document.ReviewHeader.parse(review_file.read_text()).head_sha
    if reviewed and head_sha.startswith(reviewed):
        return StepNeed(False, "self-review is current")
    return StepNeed(True, f"self-review is of {reviewed[:7] or 'an unknown head'}")


def settled_ids(repo_dir: str, branch: str) -> set[str]:
    key = pr.target.repo_key_from_origin(repo_dir)
    state = pr.state.load_state(pr.target.target_dir(key, branch)) if key else None
    if state is None:
        return set()
    return {i.id for i in state.fix.fix.items if i.outcome in pr.settlement.SETTLE_OUTCOMES}


def _review_file(repo: str, branch: str) -> Path:
    return review.paths.self_review_file_path(repo, branch)


def _repo_slug(repo_dir: str) -> str:
    return pr.context.detect_repo(repo_dir)


def _row(node: dict, repo_dir: str, repo: str) -> PlanRow:
    branch, head = node["headRefName"], node["headRefOid"]
    threads = (node.get("reviewThreads") or {}).get("nodes") or []
    return PlanRow(
        repo=repo, repo_dir=repo_dir, pr=int(node["number"]), title=node.get("title", ""),
        branch=branch, head_sha=head, is_draft=bool(node.get("isDraft")),
        needs={
            Step.REBASE: rebase_need(node.get("mergeStateStatus") or "UNKNOWN"),
            Step.COMMENTS: comments_need(threads, settled_ids(repo_dir, branch)),
            Step.REVIEW: review_need(_review_file(repo, branch), head),
        },
    )


def rows_from_search(data: dict, repo_dirs: dict[str, str]) -> list[PlanRow]:
    by_fold = {slug.casefold(): slug for slug in repo_dirs}
    rows = []
    for n in (data.get("search") or {}).get("nodes") or []:
        name = (n.get("repository") or {}).get("nameWithOwner") or ""
        slug = by_fold.get(name.casefold())
        if slug is None:
            continue
        rows.append(_row(n, repo_dirs[slug], slug))
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
    by_slug = {_repo_slug(d): d for d in repo_dirs}
    data = _graphql(_SEARCH, {"q": search_query(sorted(by_slug))})
    return Plan(viewer=(data.get("viewer") or {}).get("login", ""),
                rows=rows_from_search(data, by_slug))


def replan_row(row: PlanRow) -> PlanRow | None:
    owner, name = row.repo.split("/", 1)
    node = (_graphql(_ONE, {"owner": owner, "name": name, "number": row.pr})
            .get("repository") or {}).get("pullRequest") or {}
    if node.get("state") != "OPEN":
        return None
    return _row(node, row.repo_dir, row.repo)
