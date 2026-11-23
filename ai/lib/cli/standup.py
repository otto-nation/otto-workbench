"""JSON inventory of the current user's PRs in one GitHub org."""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import gh.client

WAITING = "waiting_on_review"
APPROVED = "approved_not_merged"
BLOCKED = "ci_or_conflicts"
DRAFT = "still_draft"
LANDED = "landed"
BUCKETS = (LANDED, WAITING, APPROVED, BLOCKED, DRAFT)

_FAIL = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED"})
_PENDING = frozenset({"PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "?"})
_OPEN_JSON = (
    "number,title,isDraft,mergeable,reviewDecision,url,additions,deletions,"
    "statusCheckRollup"
)
_SEARCH_OPEN_JSON = "number,repository"
_SEARCH_MERGED_JSON = "number,title,url,repository,additions,closedAt"


def build_parser() -> argparse.ArgumentParser:
    """The standup parser, for `main` and for rendering usage."""
    parser = argparse.ArgumentParser(
        prog="standup",
        description="JSON inventory of the current user's PRs in one GitHub org",
    )
    parser.add_argument("--org", help="GitHub org (default: owner of the cwd repo)")
    parser.add_argument("--days", type=int, default=7, help="merged-since window")
    parser.add_argument("--cwd", default=None, help="repo used to resolve --org")
    return parser


def resolve_org(cwd: str | None, org: str | None) -> str | None:
    """Owner login for *org*, or the cwd repo's owner."""
    if org:
        return org
    view = gh.client.json_out("repo", "view", "--json", "owner", cwd=cwd, default=None)
    if not isinstance(view, dict):
        return None
    owner = view.get("owner") or {}
    login = owner.get("login") if isinstance(owner, dict) else None
    return login if isinstance(login, str) and login else None


def resolve_author() -> str:
    """gh login, or ``@me`` when the token cannot read /user."""
    return gh.client.login() or "@me"


def ci_fail_names(rollup: object) -> list[str]:
    """Check names whose conclusion is a failure. Empty when CI is green or absent."""
    names: list[str] = []
    for check in _checks(rollup):
        name = _fail_name(check)
        if name:
            names.append(name)
    return names


def ci_pending(rollup: object) -> bool:
    """True when any check is still running or unreported."""
    return any(_conclusion(check) in _PENDING for check in _checks(rollup))


def bucket_open(pr: dict[str, Any]) -> str:
    """Which standup section an open PR belongs in."""
    if pr.get("isDraft"):
        return DRAFT
    if pr.get("reviewDecision") == "APPROVED":
        return APPROVED
    mergeable = pr.get("mergeable") or ""
    if mergeable in {"CONFLICTING", "UNKNOWN", "DIRTY"}:
        return BLOCKED
    rollup = pr.get("statusCheckRollup")
    if ci_fail_names(rollup) or ci_pending(rollup):
        return BLOCKED
    return WAITING


def item_from_open(pr: dict[str, Any], repo: str) -> dict[str, Any]:
    """JSON row for an open PR."""
    rollup = pr.get("statusCheckRollup")
    return {
        "number": pr.get("number"),
        "title": pr.get("title") or "",
        "url": pr.get("url") or "",
        "repo": repo,
        "additions": pr.get("additions") or 0,
        "deletions": pr.get("deletions") or 0,
        "isDraft": bool(pr.get("isDraft")),
        "mergeable": pr.get("mergeable") or "",
        "reviewDecision": pr.get("reviewDecision") or "",
        "fail": ci_fail_names(rollup),
        "pending": ci_pending(rollup),
        "bucket": bucket_open(pr),
    }


def item_from_merged(pr: dict[str, Any]) -> dict[str, Any]:
    """JSON row for a recently merged PR (search payload)."""
    repository = pr.get("repository") or {}
    repo = _merged_repo(repository)
    return {
        "number": pr.get("number"),
        "title": pr.get("title") or "",
        "url": pr.get("url") or "",
        "repo": repo,
        "additions": pr.get("additions") or 0,
        "deletions": pr.get("deletions"),
        "closedAt": pr.get("closedAt") or "",
        "bucket": LANDED,
    }


def gather(org: str, author: str, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Open PRs plus merges in the last *days*, bucketed for the standup skill."""
    clock = now or datetime.now(timezone.utc)
    since = clock - timedelta(days=days)
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in BUCKETS}
    for row in _open_items(org, author):
        buckets[row["bucket"]].append(row)
    for row in _merged_items(org, author, since):
        buckets[LANDED].append(row)
    return {
        "org": org,
        "author": author,
        "days": days,
        "buckets": buckets,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    org = resolve_org(args.cwd, args.org)
    if not org:
        print("standup: could not resolve GitHub org from cwd; pass --org", file=sys.stderr)
        return 2
    payload = gather(org, resolve_author(), args.days)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _fail_name(check: dict[str, Any]) -> str | None:
    if _conclusion(check) not in _FAIL:
        return None
    name = check.get("name")
    if isinstance(name, str) and name:
        return name
    return None


def _checks(rollup: object) -> list[dict[str, Any]]:
    if not isinstance(rollup, list):
        return []
    return [c for c in rollup if isinstance(c, dict)]


def _conclusion(check: dict[str, Any]) -> str:
    value = check.get("conclusion") or check.get("state") or "?"
    return str(value)


def _merged_repo(repository: object) -> str:
    if not isinstance(repository, dict):
        return ""
    return str(repository.get("nameWithOwner") or "")


def _open_items(org: str, author: str) -> list[dict[str, Any]]:
    hits = gh.client.json_out(
        "search", "prs",
        "--owner", org,
        "--author", author,
        "--state", "open",
        "--limit", "100",
        "--json", _SEARCH_OPEN_JSON,
        default=[],
    )
    rows: list[dict[str, Any]] = []
    for repo, numbers in sorted(_group_open_hits(hits).items()):
        rows.extend(_list_open_repo(repo, author, set(numbers)))
    return rows


def _group_open_hits(hits: object) -> dict[str, list[int]]:
    by_repo: dict[str, list[int]] = defaultdict(list)
    if not isinstance(hits, list):
        return by_repo
    for hit in hits:
        parsed = _hit_repo_number(hit)
        if parsed is None:
            continue
        repo, number = parsed
        by_repo[repo].append(number)
    return by_repo


def _hit_repo_number(hit: object) -> tuple[str, int] | None:
    if not isinstance(hit, dict):
        return None
    repo = _repo_slug(hit)
    number = hit.get("number")
    if not repo or not isinstance(number, int):
        return None
    return repo, number


def _list_open_repo(repo: str, author: str, want: set[int]) -> list[dict[str, Any]]:
    listed = gh.client.json_out(
        "pr", "list",
        "--repo", repo,
        "--author", author,
        "--state", "open",
        "--limit", "100",
        "--json", _OPEN_JSON,
        default=[],
    )
    if not isinstance(listed, list):
        return []
    rows: list[dict[str, Any]] = []
    for pr in listed:
        row = _open_row(pr, repo, want)
        if row is not None:
            rows.append(row)
    return rows


def _open_row(pr: object, repo: str, want: set[int]) -> dict[str, Any] | None:
    if not isinstance(pr, dict) or pr.get("number") not in want:
        return None
    row = item_from_open(pr, repo)
    if row["bucket"] not in {WAITING, APPROVED, BLOCKED, DRAFT}:
        return None
    return row


def _merged_items(org: str, author: str, since: datetime) -> list[dict[str, Any]]:
    hits = gh.client.json_out(
        "search", "prs",
        "--owner", org,
        "--author", author,
        "--merged",
        "--limit", "50",
        "--json", _SEARCH_MERGED_JSON,
        default=[],
    )
    if not isinstance(hits, list):
        return []
    rows: list[dict[str, Any]] = []
    for hit in hits:
        row = _merged_row(hit, since)
        if row is not None:
            rows.append(row)
    return rows


def _merged_row(hit: object, since: datetime) -> dict[str, Any] | None:
    if not isinstance(hit, dict):
        return None
    closed = hit.get("closedAt") or ""
    if not _on_or_after(str(closed), since):
        return None
    return item_from_merged(hit)


def _repo_slug(hit: dict[str, Any]) -> str:
    repository = hit.get("repository") or {}
    if not isinstance(repository, dict):
        return ""
    slug = repository.get("nameWithOwner") or ""
    return slug if isinstance(slug, str) else ""


def _on_or_after(closed_at: str, since: datetime) -> bool:
    if not closed_at:
        return False
    try:
        stamp = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= since
