"""Assemble board rows from the merged cache, reviews listing and busy set.

Ports the row assembly of `_lib/board_data.js`. Each row merges the listing
and status cache entries, marks the viewer's own PRs, and attaches the lanes
and issues from `board.columns` plus any owed or busy state.
"""

# doc-group: board

from __future__ import annotations

import batch.store
import board.cache
import board.columns
import board.presentation
import board.reviews
import core.serde
from batch.store import RepoRef
from board.model import (
    CiStatus,
    LatestReview,
    Mergeable,
    PrState,
    ReviewHealth,
    Row,
)
from board.reviews import JoinResult


def from_cache(
    repo_key: str,
    slug: str,
    host: str,
    listing,
    viewer: str,
    *,
    only: list[int] | None = None,
    mine_only: bool = False,
) -> list[dict]:
    """Document rows for one checkout, joined from the board cache."""
    merged = board.cache.merged(repo_key)
    if only is not None:
        want = set(only)
        merged = {number: row for number, row in merged.items() if number in want}
    prs = [_join_pr(row, slug) for row in merged.values()]
    joined = {
        item.number: item
        for item in board.reviews.join(prs, listing, slug, host, viewer)
    }
    busy = batch.store.busy_prs(RepoRef(key=repo_key, label=slug))
    out: list[dict] = []
    for number, raw in merged.items():
        row = _row_from_merged(
            raw,
            joined.get(number) or _empty_join(number, raw),
            busy=number in busy,
            viewer=viewer,
            slug=slug,
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
    payload["presentation"] = core.serde.to_dict(board.presentation.present(row))
    return payload
