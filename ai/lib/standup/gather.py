"""Fetch the current user's PRs in one GitHub org and bucket them."""

# doc-group: pr-state

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import gh.client

from standup.buckets import (
    APPROVED,
    BLOCKED,
    BUCKETS,
    DRAFT,
    LANDED,
    WAITING,
    item_from_merged,
    item_from_open,
)

_OPEN_JSON = (
    "number,title,isDraft,mergeable,reviewDecision,url,additions,deletions,"
    "statusCheckRollup"
)
_SEARCH_OPEN_JSON = "number,repository"
_SEARCH_MERGED_JSON = "number,title,url,repository,additions,closedAt"


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
    parser = argparse.ArgumentParser(
        description="JSON inventory of the current user's PRs in one GitHub org",
    )
    parser.add_argument("--org", help="GitHub org (default: owner of the cwd repo)")
    parser.add_argument("--days", type=int, default=7, help="merged-since window")
    parser.add_argument("--cwd", default=None, help="repo used to resolve --org")
    args = parser.parse_args(argv)
    org = resolve_org(args.cwd, args.org)
    if not org:
        print("standup: could not resolve GitHub org from cwd; pass --org", file=sys.stderr)
        return 2
    payload = gather(org, resolve_author(), args.days)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


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
    by_repo: dict[str, list[int]] = defaultdict(list)
    if isinstance(hits, list):
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            repo = _repo_slug(hit)
            number = hit.get("number")
            if repo and isinstance(number, int):
                by_repo[repo].append(number)
    rows: list[dict[str, Any]] = []
    wanted = {WAITING, APPROVED, BLOCKED, DRAFT}
    for repo, numbers in sorted(by_repo.items()):
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
            continue
        want = set(numbers)
        for pr in listed:
            if not isinstance(pr, dict) or pr.get("number") not in want:
                continue
            row = item_from_open(pr, repo)
            if row["bucket"] in wanted:
                rows.append(row)
    return rows


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
    rows: list[dict[str, Any]] = []
    if not isinstance(hits, list):
        return rows
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        closed = hit.get("closedAt") or ""
        if not _on_or_after(str(closed), since):
            continue
        rows.append(item_from_merged(hit))
    return rows


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


if __name__ == "__main__":
    raise SystemExit(main())
