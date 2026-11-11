"""Assemble the PR board document from listing, status and owed phases.

`build` always returns a document. Per-checkout failures live on
``repos[].error``; ``--if-changed`` matching the current stamp returns the
unchanged envelope. The stamp hashes canonical JSON of the document without
``stamp`` and every ``fetched_at``.
"""

# doc-group: board

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import batch.plan
import board.cache
import board.columns
import board.github
import board.owed
import board.reviews
import board.rows
import core.serde
import core.workbench_paths
import git.client
import pr.target
import review.listing
from batch.model import Step
from board.github import POOL_WORKERS
from board.model import (
    SCHEMA_VERSIONS,
    Phase,
    PrState,
    PullFacts,
    RepoError,
    RepoPhase,
    RepoStatus,
    StatusFacts,
)


@dataclass(frozen=True)
class Checkout:
    """A checkout that resolved to a GitHub repo."""

    path: str
    slug: str
    host: str
    key: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp(doc: dict) -> str:
    """First 16 hex of sha256 over canonical JSON, ignoring stamp and fetched_at."""
    payload = json.dumps(_scrub(doc), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if key not in {"stamp", "fetched_at"}
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def build(
    phase: Phase | str,
    checkouts: Sequence[str],
    prs: Sequence[int] | None = None,
    if_changed: str | None = None,
    *,
    offline: bool = False,
) -> dict:
    """The board document for *phase* over *checkouts*.

    *prs* limits a status fetch to those numbers and marks the document a
    patch (``complete`` false). Owed is always a patch. *if_changed* equal to
    the current stamp returns the unchanged envelope. *offline* reads listing
    rows from cache and skips GitHub.
    """
    wanted = phase if isinstance(phase, Phase) else Phase(phase)
    viewer = board.cache.viewer(refresh=not offline)
    now = _now()
    listing = review.listing.rows()
    numbers = _int_prs(prs)
    repos, rows, complete = _assemble(
        wanted, list(checkouts), numbers, listing, viewer, now, offline=offline,
    )
    doc = {
        "schema_version": SCHEMA_VERSIONS[-1],
        "phase": wanted.value,
        "complete": complete,
        "viewer": viewer,
        "reviews_root": str(core.workbench_paths.reviews_dir()),
        "columns": [core.serde.to_dict(col) for col in board.columns.columns()],
        "repos": [core.serde.to_dict(repo) for repo in repos],
        "rows": rows,
    }
    current = stamp(doc)
    if if_changed == current:
        return {
            "schema_version": doc["schema_version"],
            "phase": doc["phase"],
            "stamp": current,
            "unchanged": True,
        }
    doc["stamp"] = current
    return doc


def _int_prs(prs: Sequence[int] | None) -> list[int] | None:
    if not prs:
        return None
    return [n for n in prs if type(n) is int]


_LISTING_REUSE_S = 60


def _assemble(
    phase: Phase,
    checkouts: list[str],
    prs: list[int] | None,
    listing,
    viewer: str,
    now: str,
    *,
    offline: bool = False,
) -> tuple[list[RepoStatus], list[dict], bool]:
    resolved: list[tuple[str, Checkout | RepoError]] = [
        (path, _resolve(path)) for path in checkouts
    ]
    if offline:
        return _offline_phase(resolved, listing, viewer, now)
    if phase is Phase.LISTING:
        return _listing_phase(resolved, listing, viewer, now)
    if phase is Phase.STATUS:
        return _status_phase(resolved, prs, listing, viewer, now)
    return _owed_phase(resolved, listing, viewer, now)


def _offline_repo_phase(repo_key: str) -> RepoPhase:
    cached = board.cache.read_all(repo_key)
    if Phase.STATUS in cached:
        return RepoPhase.LOADED
    if Phase.LISTING in cached:
        return RepoPhase.HYDRATING
    return RepoPhase.QUEUED


def _offline_phase(
    resolved: list[tuple[str, Checkout | RepoError]],
    listing,
    viewer: str,
    now: str,
) -> tuple[list[RepoStatus], list[dict], bool]:
    repos: list[RepoStatus] = []
    rows: list[dict] = []
    for path, found in resolved:
        if isinstance(found, RepoError):
            repos.append(_failed(path, found))
            continue
        repo_phase = _offline_repo_phase(found.key)
        repos.append(_repo(found, repo_phase, now))
        if repo_phase is RepoPhase.QUEUED:
            continue
        rows.extend(_cached_rows(found, listing, viewer))
    return repos, rows, True


def _resolve(path: str) -> Checkout | RepoError:
    if not Path(path).is_dir():
        return RepoError(kind="no-checkout", message=f"--checkout {path} does not exist")
    if not git.client.ok("rev-parse", "--git-dir", cwd=path):
        return RepoError(
            kind="not-git",
            message=f"--checkout {path} is not a git repository",
        )
    if not git.client.ok("remote", "get-url", "origin", cwd=path):
        return RepoError(kind="no-origin", message=f"--checkout {path} has no origin remote")
    identity = pr.target.repo_identity_from_origin(path)
    if identity is None or "/" not in identity.label:
        return RepoError(kind="not-github", message="origin is not a GitHub remote")
    return Checkout(path=path, slug=identity.label, host=identity.host, key=identity.key)


def _listing_phase(
    resolved: list[tuple[str, Checkout | RepoError]],
    listing,
    viewer: str,
    now: str,
) -> tuple[list[RepoStatus], list[dict], bool]:
    repos: list[RepoStatus] = []
    rows: list[dict] = []
    for path, found in resolved:
        if isinstance(found, RepoError):
            repos.append(_failed(path, found))
            continue
        listed = board.github.list_pulls(found.slug)
        if listed.pulls is None:
            repos.append(_failed(path, board.github.pull_list_error(listed), found=found))
            continue
        board.cache.write(found.key, Phase.LISTING, {
            "fetched_at": now,
            "rows": [core.serde.to_dict(pull) for pull in listed.pulls],
        })
        repos.append(_repo(found, RepoPhase.HYDRATING, now))
        rows.extend(_cached_rows(found, listing, viewer))
    return repos, rows, True


def _status_phase(
    resolved: list[tuple[str, Checkout | RepoError]],
    prs: list[int] | None,
    listing,
    viewer: str,
    now: str,
) -> tuple[list[RepoStatus], list[dict], bool]:
    repos: list[RepoStatus] = []
    rows: list[dict] = []
    complete = prs is None
    with ThreadPoolExecutor(max_workers=POOL_WORKERS) as pool:
        for path, found in resolved:
            repo, repo_rows = _status_one(path, found, prs, listing, viewer, now, pool)
            repos.append(repo)
            rows.extend(repo_rows)
    return repos, rows, complete


def _status_one(
    path: str,
    found: Checkout | RepoError,
    prs: list[int] | None,
    listing,
    viewer: str,
    now: str,
    pool: ThreadPoolExecutor,
) -> tuple[RepoStatus, list[dict]]:
    if isinstance(found, RepoError):
        return _failed(path, found), []
    return _status_repo(found, prs, listing, viewer, now, pool)


def _status_repo(
    found: Checkout,
    prs: list[int] | None,
    listing,
    viewer: str,
    now: str,
    pool: ThreadPoolExecutor,
) -> tuple[RepoStatus, list[dict]]:
    pulls = _fresh_listing_pulls(found)
    fetched_listing = pulls is None
    if fetched_listing:
        listed = board.github.list_pulls(found.slug)
        if listed.pulls is None:
            return _failed(
                found.path, board.github.pull_list_error(listed), found=found,
            ), []
        pulls = listed.pulls
        _write_listing_cache(found.key, pulls, now, replace=True)
    if prs is not None:
        want = set(prs)
        pulls = [pull for pull in pulls if pull.number in want]
        landed_nums: list[int] = []
    else:
        landed_nums = board.reviews.landed_candidates(
            pulls, listing, found.slug, found.host,
        )
    numbers = [pull.number for pull in pulls]
    for num in landed_nums:
        if num not in numbers:
            numbers.append(num)
    fetched = board.github.fetch_status(found.slug, numbers, executor=pool)
    status_rows = _status_cache_rows(pulls, fetched.by_number, listing, found, landed_nums)
    _write_status_cache(found.key, status_rows, now, replace=prs is None)
    kept_landed = _kept_landed(landed_nums, fetched.by_number, listing, found)
    _merge_landed_into_listing(found, kept_landed, now)
    repo = _repo(found, RepoPhase.LOADED, now, partial=fetched.partial)
    return repo, _cached_rows(found, listing, viewer, only=prs)


def _fresh_listing_pulls(found: Checkout) -> list[PullFacts] | None:
    payload = board.cache.read(found.key, Phase.LISTING)
    if payload is None:
        return None
    age = _iso_age_s(payload.get("fetched_at"))
    if age is None or age >= _LISTING_REUSE_S:
        return None
    return _pulls_from_rows(payload.get("rows") or [], found.slug)


def _iso_age_s(fetched_at: object) -> float | None:
    if not isinstance(fetched_at, str) or not fetched_at:
        return None
    try:
        when = datetime.strptime(fetched_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def _pulls_from_rows(rows: object, slug: str) -> list[PullFacts]:
    if not isinstance(rows, list):
        return []
    out: list[PullFacts] = []
    for row in rows:
        if not isinstance(row, dict) or "number" not in row:
            continue
        data = {**row, "repo": row.get("repo") or slug}
        try:
            out.append(core.serde.from_dict(PullFacts, data))
        except (TypeError, ValueError):
            continue
    return out


def _status_cache_rows(
    pulls: list[PullFacts],
    by_number: dict[int, StatusFacts],
    listing,
    found: Checkout,
    landed_nums: list[int],
) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    for pull in pulls:
        facts = by_number.get(pull.number)
        if facts is None:
            continue
        rows.append(_status_row(pull.number, pull.head_sha, facts, pull))
        seen.add(pull.number)
    for num in landed_nums:
        if num in seen:
            continue
        facts = by_number.get(num)
        if facts is None:
            continue
        pull = _landed_pull(num, facts, listing, found)
        rows.append(_status_row(num, pull.head_sha, facts, pull))
    return rows


def _status_row(number: int, head_sha: str, facts: StatusFacts, pull: PullFacts) -> dict:
    row = core.serde.to_dict(facts)
    row["number"] = number
    row["head_sha"] = head_sha
    row.update({
        "title": pull.title,
        "author": pull.author,
        "branch": pull.branch,
        "base_branch": pull.base_branch,
        "url": pull.url,
        "labels": list(pull.labels),
        "created_at": pull.created_at,
        "updated_at": pull.updated_at,
        "is_draft": pull.is_draft,
        "state": (facts.state or pull.state).value,
        "merged_at": facts.merged_at if facts.merged_at is not None else pull.merged_at,
        "repo": pull.repo,
    })
    return row


def _write_listing_cache(
    repo_key: str,
    pulls: list[PullFacts],
    now: str,
    *,
    replace: bool,
) -> None:
    rows = [core.serde.to_dict(pull) for pull in pulls]
    if replace:
        board.cache.write(repo_key, Phase.LISTING, {"fetched_at": now, "rows": rows})
        return
    previous = board.cache.read(repo_key, Phase.LISTING) or {}
    by_number = {
        row["number"]: row
        for row in previous.get("rows") or []
        if isinstance(row, dict) and "number" in row
    }
    for row in rows:
        by_number[row["number"]] = row
    board.cache.write(repo_key, Phase.LISTING, {
        "fetched_at": now,
        "rows": list(by_number.values()),
    })


def _write_status_cache(repo_key: str, rows: list[dict], now: str, *, replace: bool) -> None:
    if replace:
        board.cache.write(repo_key, Phase.STATUS, {"fetched_at": now, "rows": rows})
        return
    previous = board.cache.read(repo_key, Phase.STATUS) or {}
    by_number = {
        row["number"]: row
        for row in previous.get("rows") or []
        if isinstance(row, dict) and "number" in row
    }
    for row in rows:
        by_number[row["number"]] = row
    board.cache.write(repo_key, Phase.STATUS, {
        "fetched_at": now,
        "rows": list(by_number.values()),
    })


def _kept_landed(
    landed_nums: list[int],
    by_number: dict[int, StatusFacts],
    listing,
    found: Checkout,
) -> list[PullFacts]:
    entries = []
    for num in landed_nums:
        facts = by_number.get(num)
        if facts is None:
            continue
        entries.append({"number": num, "merged_at": facts.merged_at, "facts": facts})
    kept = board.reviews.keep_landed(entries)
    return [_landed_pull(entry["number"], entry["facts"], listing, found) for entry in kept]


def _landed_pull(number: int, facts: StatusFacts, listing, found: Checkout) -> PullFacts:
    match = _listing_match(listing, found, number)
    return PullFacts(
        repo=found.slug,
        number=number,
        title=f"#{number}",
        author="",
        branch=_attr(match, "head_ref") or "",
        url="",
        head_sha=_attr(match, "head_sha") or "",
        merged_at=facts.merged_at,
        is_draft=False,
        state=facts.state or PrState.MERGED,
    )


def _listing_match(listing, found: Checkout, number: int):
    match = None
    best_at = ""
    rows = listing if isinstance(listing, (list, tuple)) else ()
    for row in rows:
        if not board.reviews.matches_repo(row, found.slug, found.host):
            continue
        if _attr(row, "pr_number") != number:
            continue
        at = _attr(row, "reviewed_at") or ""
        if match is None or at > best_at:
            match = row
            best_at = at
    return match


def _attr(obj, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj[name] if name in obj else default
    return getattr(obj, name, default)


def _merge_landed_into_listing(found: Checkout, landed: list[PullFacts], now: str) -> None:
    if not landed:
        return
    previous = board.cache.read(found.key, Phase.LISTING) or {}
    rows = [
        row for row in previous.get("rows") or []
        if isinstance(row, dict) and "number" in row
    ]
    have = {row["number"] for row in rows}
    for pull in landed:
        if pull.number in have:
            continue
        rows.append(core.serde.to_dict(pull))
        have.add(pull.number)
    board.cache.write(found.key, Phase.LISTING, {"fetched_at": now, "rows": rows})


def _owed_phase(
    resolved: list[tuple[str, Checkout | RepoError]],
    listing,
    viewer: str,
    now: str,
) -> tuple[list[RepoStatus], list[dict], bool]:
    repos: list[RepoStatus] = []
    rows: list[dict] = []
    ok = [found for _, found in resolved if isinstance(found, Checkout)]
    plan = None
    plan_error: RepoError | None = None
    if ok:
        try:
            plan = batch.plan.build_plan(
                [found.path for found in ok],
                frozenset({Step.COMMENTS}),
            )
        except batch.plan.PlanError as exc:
            plan_error = RepoError(kind="gh-failed", message=str(exc))
    for path, found in resolved:
        if isinstance(found, RepoError):
            repos.append(_failed(path, found))
            continue
        if plan_error is not None:
            repos.append(_failed(path, plan_error, found=found))
            continue
        mapped = board.owed.owed_from(plan, found.slug)
        board.cache.write(found.key, Phase.OWED, {
            "fetched_at": now,
            "rows": [
                {"number": number, "reason": entry.reason, "head_sha": entry.head_sha}
                for number, entry in mapped.items()
            ],
        })
        repos.append(_repo(found, RepoPhase.LOADED, now))
        rows.extend(_cached_rows(found, listing, viewer, mine_only=True))
    return repos, rows, False


def _cached_rows(
    found: Checkout,
    listing,
    viewer: str,
    *,
    only: list[int] | None = None,
    mine_only: bool = False,
) -> list[dict]:
    return board.rows.from_cache(
        found.key,
        found.slug,
        found.host,
        listing,
        viewer,
        only=only,
        mine_only=mine_only,
    )


def _repo(
    found: Checkout,
    phase: RepoPhase,
    now: str,
    *,
    partial: bool = False,
    error: RepoError | None = None,
) -> RepoStatus:
    return RepoStatus(
        slug=found.slug,
        host=found.host,
        checkout=found.path,
        phase=phase,
        error=error,
        partial=partial,
        fetched_at=now,
    )


def _failed(path: str, error: RepoError, found: Checkout | None = None) -> RepoStatus:
    return RepoStatus(
        slug=found.slug if found is not None else "",
        host=found.host if found is not None else "",
        checkout=found.path if found is not None else path,
        phase=RepoPhase.FAILED,
        error=error,
    )


