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
import batch.store
import board.cache
import board.columns
import board.github
import board.owed
import board.reviews
import core.serde
import gh.client
import git.client
import pr.target
import review.listing
from batch.model import Step
from batch.store import RepoRef
from board.github import POOL_WORKERS
from board.model import (
    SCHEMA_VERSIONS,
    CiStatus,
    LatestReview,
    Mergeable,
    Phase,
    PrState,
    PullFacts,
    RepoError,
    RepoPhase,
    RepoStatus,
    ReviewHealth,
    Row,
    StatusFacts,
)
from board.reviews import JoinResult


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
) -> dict:
    """The board document for *phase* over *checkouts*.

    *prs* limits a status fetch to those numbers and marks the document a
    patch (``complete`` false). Owed is always a patch. *if_changed* equal to
    the current stamp returns the unchanged envelope.
    """
    wanted = phase if isinstance(phase, Phase) else Phase(phase)
    viewer = gh.client.login()
    now = _now()
    listing = review.listing.rows()
    numbers = _int_prs(prs)
    repos, rows, complete = _assemble(wanted, list(checkouts), numbers, listing, viewer, now)
    doc = {
        "schema_version": SCHEMA_VERSIONS[-1],
        "phase": wanted.value,
        "complete": complete,
        "viewer": viewer,
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


_LIST_FAILED = RepoError(kind="gh-failed", message="listing open pull requests failed")


def _assemble(
    phase: Phase,
    checkouts: list[str],
    prs: list[int] | None,
    listing,
    viewer: str,
    now: str,
) -> tuple[list[RepoStatus], list[dict], bool]:
    resolved: list[tuple[str, Checkout | RepoError]] = [
        (path, _resolve(path)) for path in checkouts
    ]
    if phase is Phase.LISTING:
        return _listing_phase(resolved, listing, viewer, now)
    if phase is Phase.STATUS:
        return _status_phase(resolved, prs, listing, viewer, now)
    return _owed_phase(resolved, listing, viewer, now)


def _resolve(path: str) -> Checkout | RepoError:
    if not Path(path).is_dir():
        return RepoError(kind="no-checkout", message=f"--checkout {path} does not exist")
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
        pulls = board.github.list_pulls(found.slug)
        if pulls is None:
            repos.append(_failed(path, _LIST_FAILED, found=found))
            continue
        board.cache.write(found.key, Phase.LISTING, {
            "fetched_at": now,
            "rows": [core.serde.to_dict(pull) for pull in pulls],
        })
        repos.append(_repo(found, RepoPhase.HYDRATING, now))
        rows.extend(_rows_from_cache(found, listing, viewer))
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
    pulls = board.github.list_pulls(found.slug)
    if pulls is None:
        return _failed(found.path, _LIST_FAILED, found=found), []
    if prs is not None:
        want = set(prs)
        pulls = [pull for pull in pulls if pull.number in want]
        landed_nums: list[int] = []
    else:
        landed_nums = board.reviews.landed_candidates(pulls, listing, found.slug, found.host)
    _write_listing_cache(found.key, pulls, now, replace=prs is None)
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
    return repo, _rows_from_cache(found, listing, viewer, only=prs)


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
        rows.extend(_rows_from_cache(found, listing, viewer, mine_only=True))
    return repos, rows, False


def _rows_from_cache(
    found: Checkout,
    listing,
    viewer: str,
    *,
    only: list[int] | None = None,
    mine_only: bool = False,
) -> list[dict]:
    merged = board.cache.merged(found.key)
    if only is not None:
        want = set(only)
        merged = {number: row for number, row in merged.items() if number in want}
    prs = [_join_pr(row, found.slug) for row in merged.values()]
    joined = {
        item.number: item
        for item in board.reviews.join(prs, listing, found.slug, found.host, viewer)
    }
    busy = batch.store.busy_prs(RepoRef(key=found.key, label=found.slug))
    out: list[dict] = []
    for number, raw in merged.items():
        row = _row_from_merged(
            raw,
            joined.get(number) or _empty_join(number, raw),
            busy=number in busy,
            viewer=viewer,
            slug=found.slug,
        )
        if mine_only and not row.is_mine:
            continue
        out.append(_emit_row(row))
    return out


def _join_pr(raw: dict, slug: str) -> dict:
    return {
        "number": raw["number"],
        "head_sha": raw.get("head_sha") or "",
        "latest_reviews": raw.get("latest_reviews") or (),
        "requested_reviewers": tuple(raw.get("requested_reviewers") or ()),
        "repo": raw.get("repo") or slug,
    }


def _empty_join(number: int, raw: dict) -> JoinResult:
    return JoinResult(
        number=number,
        head_sha=raw.get("head_sha") or "",
        review_runs=(),
        my_review_state=None,
        is_requested_reviewer=False,
        has_new_changes_since_review=False,
        review_health=ReviewHealth.OK,
    )


def _row_from_merged(
    raw: dict,
    join: JoinResult,
    *,
    busy: bool,
    viewer: str,
    slug: str,
) -> Row:
    author = raw.get("author") or ""
    is_mine = bool(viewer) and author.casefold() == viewer.casefold()
    owed_reason = raw.get("owed")
    owed_known = bool(raw.get("owed_known"))
    owed_sha = raw.get("owed_head_sha") or ""
    head_sha = raw.get("head_sha") or ""
    if owed_sha and head_sha and owed_sha != head_sha:
        owed_reason = None
        owed_known = False
    if not is_mine:
        owed_reason = None
        owed_known = False
    hydrated = bool(raw.get("hydrated"))
    if hydrated:
        mergeable = _enum(Mergeable, raw.get("mergeable"), Mergeable.UNKNOWN)
        ci_status = _enum(CiStatus, raw.get("ci_status"), CiStatus.NONE)
        ci_unknown = bool(raw.get("ci_unknown"))
    else:
        mergeable = Mergeable.UNKNOWN
        ci_status = CiStatus.NONE
        ci_unknown = True
    return Row(
        repo=raw.get("repo") or slug,
        number=int(raw["number"]),
        title=raw.get("title") or "",
        author=author,
        branch=raw.get("branch") or "",
        base_branch=raw.get("base_branch") or "",
        url=raw.get("url") or "",
        head_sha=head_sha,
        labels=tuple(raw.get("labels") or ()),
        created_at=raw.get("created_at") or "",
        updated_at=raw.get("updated_at") or "",
        merged_at=raw.get("merged_at"),
        additions=int(raw.get("additions") or 0),
        deletions=int(raw.get("deletions") or 0),
        changed_files=int(raw.get("changed_files") or 0),
        is_draft=bool(raw.get("is_draft")),
        state=_enum(PrState, raw.get("state"), PrState.OPEN),
        in_merge_queue=bool(raw.get("in_merge_queue")),
        mergeable=mergeable,
        ci_status=ci_status,
        ci_unknown=ci_unknown,
        hydrated=hydrated,
        review_decision=raw.get("review_decision"),
        latest_reviews=_latest_reviews(raw.get("latest_reviews")),
        requested_reviewers=tuple(raw.get("requested_reviewers") or ()),
        review_runs=join.review_runs,
        my_review_state=join.my_review_state,
        is_requested_reviewer=join.is_requested_reviewer,
        has_new_changes_since_review=join.has_new_changes_since_review,
        review_health=join.review_health,
        busy=busy,
        is_mine=is_mine,
        owed=owed_reason,
        owed_known=owed_known,
    )


def _latest_reviews(value: object) -> tuple[LatestReview, ...]:
    if not value:
        return ()
    out: list[LatestReview] = []
    for item in value:
        if isinstance(item, LatestReview):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        login = item.get("login") or ""
        if login:
            out.append(LatestReview(login=login, state=item.get("state") or ""))
    return tuple(out)


def _enum(cls, value, default):
    if isinstance(value, cls):
        return value
    try:
        return cls(value)
    except (TypeError, ValueError):
        return default


def _emit_row(row: Row) -> dict:
    payload = core.serde.to_dict(row)
    payload["lanes"] = core.serde.to_dict(board.columns.lanes(row))
    payload["issues"] = [core.serde.to_dict(issue) for issue in board.columns.issues(row)]
    return payload


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


