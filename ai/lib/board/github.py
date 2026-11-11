"""GitHub REST listing and GraphQL status fetches for the PR board.

Listing is one paginated REST call per slug. Status is aliased GraphQL
``pullRequest`` fields, fifty numbers per request, with ``owner`` and ``name``
as variables. A pool of four runs those batches; a failed batch is partial
and its numbers stay unhydrated.
"""

# doc-group: board

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Sequence

import gh.client
from board.model import CiStatus, LatestReview, Mergeable, PrState, PullFacts, StatusFacts


PULLS_JQ = (
    "[.[] | {number, title, user: {login: .user.login}, "
    "head: {ref: .head.ref, sha: .head.sha}, "
    "base: {ref: .base.ref}, draft, html_url, "
    "labels: [(.labels // [])[] | {name}], created_at, updated_at}]"
)

GRAPHQL_BATCH = 50
POOL_WORKERS = 4

_STATUS_FIELDS = (
    "number title state isDraft url headRefName headRefOid baseRefName "
    "author { login } "
    "labels(first: 20) { nodes { name } } "
    "createdAt updatedAt mergedAt reviewDecision mergeable "
    "additions deletions changedFiles "
    "mergeQueueEntry { id } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } "
    "latestReviews(first: 20) { nodes { author { login } state } } "
    "reviewRequests(first: 10) { nodes { requestedReviewer { __typename ... on User { login } } } }"
)


@dataclass(frozen=True)
class StatusFetch:
    """Mapped GraphQL status facts for one slug, plus whether a batch failed."""

    by_number: dict[int, StatusFacts]
    partial: bool = False


def batches(numbers: Sequence[int] | None = None, size: int = GRAPHQL_BATCH) -> list[list[int]]:
    src = list(numbers or ())
    if not src:
        return []
    return [list(src[i:i + size]) for i in range(0, len(src), size)]


def status_query(numbers: Sequence[int]) -> str:
    """Aliased ``pullRequest`` selection; ``$owner`` / ``$name`` are variables."""
    aliases = " ".join(
        f"pr{i}: pullRequest(number: {num}) {{ {_STATUS_FIELDS} }}"
        for i, num in enumerate(_int_nums(numbers))
    )
    return (
        "query($owner: String!, $name: String!) { "
        f"repository(owner: $owner, name: $name) {{ {aliases} }} }}"
    )


def map_pull(pr: dict, repo: str = "") -> PullFacts:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    user = pr.get("user") or {}
    labels = tuple(
        name
        for raw in (pr.get("labels") or [])
        if (name := (raw or {}).get("name"))
    )
    return PullFacts(
        repo=repo,
        number=int(pr.get("number") or 0),
        title=pr.get("title") or "",
        author=user.get("login") or "",
        branch=head.get("ref") or "",
        base_branch=base.get("ref") or "",
        url=pr.get("html_url") or "",
        head_sha=head.get("sha") or "",
        labels=labels,
        created_at=pr.get("created_at") or "",
        updated_at=pr.get("updated_at") or "",
        is_draft=bool(pr.get("draft")),
        state=PrState.OPEN,
    )


def map_status(pr: dict) -> StatusFacts:
    return StatusFacts(
        additions=_as_int(pr.get("additions")),
        deletions=_as_int(pr.get("deletions")),
        changed_files=_as_int(pr.get("changedFiles")),
        in_merge_queue=bool(pr.get("mergeQueueEntry")),
        mergeable=_mergeable_of(pr.get("mergeable")),
        ci_status=_ci_status_of(pr),
        ci_unknown=False,
        review_decision=pr.get("reviewDecision"),
        latest_reviews=_latest_reviews_of(pr),
        requested_reviewers=_requested_logins(pr),
        merged_at=_optional_str(pr.get("mergedAt")),
        state=_pr_state_of(pr.get("state")),
    )


def map_graphql(payload: object, numbers: Sequence[int] | None = None) -> dict[int, StatusFacts]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    repo = data.get("repository")
    if not isinstance(repo, dict):
        return {}
    out: dict[int, StatusFacts] = {}
    nums = list(numbers or ())
    for i, num in enumerate(nums):
        node = repo.get(f"pr{i}")
        if not isinstance(node, dict):
            continue
        out[num] = map_status(node)
    return out


def list_pulls(slug: str) -> list[PullFacts] | None:
    """Open PRs, or None when the fetch failed (distinct from no open PRs)."""
    # gh.client.api omits --method on GET, and -F/-f would switch gh to POST.
    # Query parameters keep this a GET, matching pr.stack's open-PR listing.
    result = gh.client.api(
        f"repos/{slug}/pulls?state=open&per_page=100",
        paginate=True,
        jq=PULLS_JQ,
    )
    if not result.ok:
        return None
    try:
        raw = _parse_pulls_stdout(result.stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return [map_pull(pr, slug) for pr in raw if isinstance(pr, dict)]


def fetch_status(
    slug: str,
    numbers: Sequence[int],
    *,
    executor: ThreadPoolExecutor | None = None,
) -> StatusFetch:
    chunks = batches(numbers)
    if not chunks:
        return StatusFetch(by_number={}, partial=False)
    slash = slug.find("/")
    if slash <= 0:
        owner, name = "", slug
    else:
        owner, name = slug[:slash], slug[slash + 1:]
    variables = {"owner": owner, "name": name}
    own_pool = executor is None
    pool = executor or ThreadPoolExecutor(max_workers=POOL_WORKERS)
    try:
        outcomes = list(pool.map(lambda chunk: _fetch_batch(chunk, variables), chunks))
    finally:
        if own_pool:
            pool.shutdown(wait=True)
    mapped: dict[int, StatusFacts] = {}
    partial = False
    for outcome in outcomes:
        if outcome is None:
            partial = True
            continue
        mapped.update(outcome)
    return StatusFetch(by_number=mapped, partial=partial)


def _fetch_batch(chunk: list[int], variables: dict[str, str]) -> dict[int, StatusFacts] | None:
    result = gh.client.graphql(status_query(chunk), variables=variables)
    if not result.ok:
        return None
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return map_graphql(payload, chunk)


def _int_nums(numbers: Sequence[int]) -> list[int]:
    return [n for n in numbers if type(n) is int]


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def _mergeable_of(value: object) -> Mergeable:
    if value == Mergeable.MERGEABLE or value == Mergeable.CONFLICTING:
        return Mergeable(value)
    return Mergeable.UNKNOWN


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _pr_state_of(value: object) -> PrState | None:
    if value in (PrState.OPEN, PrState.MERGED, PrState.CLOSED):
        return PrState(value)
    return None


def _ci_status_of(pr: dict) -> CiStatus:
    # ceiling: rollup only; add ui-code's check-runs fallback if a repo reports no rollup
    nodes = ((pr.get("commits") or {}).get("nodes") or [])
    commit = (nodes[0] or {}).get("commit") if nodes else None
    rollup = (commit or {}).get("statusCheckRollup") or {}
    state = rollup.get("state")
    if state in ("FAILURE", "ERROR"):
        return CiStatus.FAILING
    if state in ("PENDING", "EXPECTED"):
        return CiStatus.PENDING
    if state == "SUCCESS":
        return CiStatus.PASSING
    return CiStatus.NONE


def _requested_logins(pr: dict) -> tuple[str, ...]:
    logins = []
    for node in (pr.get("reviewRequests") or {}).get("nodes") or []:
        reviewer = (node or {}).get("requestedReviewer") or {}
        typename = reviewer.get("__typename")
        if typename == "Team":
            continue
        if typename and typename != "User":
            continue
        login = reviewer.get("login")
        if login:
            logins.append(login)
    return tuple(logins)


def _latest_reviews_of(pr: dict) -> tuple[LatestReview, ...]:
    out = []
    for node in (pr.get("latestReviews") or {}).get("nodes") or []:
        login = ((node or {}).get("author") or {}).get("login")
        if login:
            out.append(LatestReview(login=login, state=node.get("state") or ""))
    return tuple(out)


def _flatten_pull_pages(payload: object) -> list[Any]:
    if not isinstance(payload, list):
        return []
    if not payload:
        return []
    if isinstance(payload[0], list):
        return [item for page in payload if isinstance(page, list) for item in page]
    return payload


def _parse_pulls_stdout(stdout: str) -> list[Any]:
    text = stdout.strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    values: list[Any] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        value, end = decoder.raw_decode(text, idx)
        values.append(value)
        idx = end
    if len(values) == 1:
        return _flatten_pull_pages(values[0])
    out: list[Any] = []
    for value in values:
        out.extend(_flatten_pull_pages(value))
    return out
