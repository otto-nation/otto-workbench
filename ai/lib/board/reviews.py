"""Join `pr review` listing rows onto GitHub PR records.

Ports `_lib/reviews.js`. Listing rows may be `review.listing.ReviewRow`
objects or the dicts that document emits; field names are snake_case.
"""

# doc-group: board

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from board.model import ReviewHealth, ReviewRunRef


POST_STATE_BY_EVENT = {
    "approve": "APPROVED",
    "requested_changes": "CHANGES_REQUESTED",
    "comment": "COMMENTED",
}

LANDED_CAP = 100
LANDED_DAYS = 30


@dataclass(frozen=True)
class JoinResult:
    number: int
    head_sha: str
    review_runs: tuple[ReviewRunRef, ...]
    my_review_state: str | None
    is_requested_reviewer: bool
    has_new_changes_since_review: bool
    review_health: ReviewHealth


def _field(obj, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj[name] if name in obj else default
    return getattr(obj, name, default)


def _rows_of(listing) -> list:
    if isinstance(listing, (list, tuple)):
        return list(listing)
    reviews = _field(listing, "reviews")
    if isinstance(reviews, (list, tuple)):
        return list(reviews)
    return []


def _host_ok(row, host: str) -> bool:
    row_host = _field(row, "host")
    return row_host is None or row_host == "" or row_host == host


def matches_repo(row, repo: str, host: str) -> bool:
    return (
        _field(row, "repo") == repo
        and _field(row, "mode") == "pr"
        and isinstance(_field(row, "pr_number"), int)
        and not isinstance(_field(row, "pr_number"), bool)
        and _host_ok(row, host)
    )


def posted(run) -> bool:
    return run is not None and _field(run, "post") is not None


def _run_ref(run) -> ReviewRunRef:
    post = _field(run, "post")
    if post is None:
        posted_event = None
    else:
        posted_event = _field(post, "event") or ""
    return ReviewRunRef(
        posted_event=posted_event,
        head_sha=_field(run, "head_sha") or "",
        status=_field(run, "status") or "",
        reviewed_at=_field(run, "reviewed_at") or "",
        review_file=_field(run, "review_file") or "",
        verdict=_field(run, "verdict") or "",
        findings=_field(run, "findings"),
    )


def runs_for(row) -> tuple[ReviewRunRef, ...]:
    history = _field(row, "history") or ()
    return tuple(_run_ref(run) for run in (row, *history))


def _pick_row(rows):
    best = None
    best_at = ""
    for row in rows:
        at = _field(row, "reviewed_at") or ""
        if best is None or at > best_at:
            best = row
            best_at = at
    return best


def runs_for_pr(listing, repo: str, host: str, number: int) -> tuple[ReviewRunRef, ...]:
    matched = [
        row for row in _rows_of(listing)
        if matches_repo(row, repo, host) and _field(row, "pr_number") == number
    ]
    row = _pick_row(matched)
    return runs_for(row) if row is not None else ()


def health(run) -> ReviewHealth:
    status = run.status if isinstance(run, ReviewRunRef) else _field(run, "status")
    if status == "partial":
        return ReviewHealth.PARTIAL
    if status == "error":
        return ReviewHealth.ERROR
    return ReviewHealth.OK


def my_review_state(pr, runs, me: str | None) -> str | None:
    mine = (me or "").casefold()
    reviews = _field(pr, "latest_reviews") or ()
    for review in reversed(list(reviews)):
        login = (_field(review, "login") or "").casefold()
        if login != mine:
            continue
        state = _field(review, "state")
        if state in {"DISMISSED", "PENDING"}:
            continue
        return state
    for run in runs:
        event = run.posted_event if isinstance(run, ReviewRunRef) else _field(run, "posted_event")
        mapped = POST_STATE_BY_EVENT.get(event)
        if event is not None and mapped:
            return mapped
    return None


def join(prs, listing, repo: str, host: str, me: str | None) -> list[JoinResult]:
    rows = _rows_of(listing)
    mine = (me or "").casefold()
    out: list[JoinResult] = []
    for pr in prs:
        number = _field(pr, "number")
        head_sha = _field(pr, "head_sha") or ""
        match = _pick_row(
            row for row in rows
            if matches_repo(row, repo, host) and _field(row, "pr_number") == number
        )
        review_runs = runs_for(match) if match is not None else ()
        state = my_review_state(pr, review_runs, me)
        reviewers = _field(pr, "requested_reviewers") or ()
        is_requested = (
            any(str(login).casefold() == mine for login in reviewers)
            and state is None
        )
        has_new = bool(review_runs) and review_runs[0].head_sha != head_sha
        out.append(JoinResult(
            number=number,
            head_sha=head_sha,
            review_runs=review_runs,
            my_review_state=state,
            is_requested_reviewer=is_requested,
            has_new_changes_since_review=has_new,
            review_health=health(review_runs[0]) if review_runs else ReviewHealth.OK,
        ))
    return out


def landed_candidates(prs, listing, repo: str, host: str) -> list[int]:
    present = {_field(pr, "number") for pr in prs}
    best: dict[int, object] = {}
    for row in _rows_of(listing):
        if not matches_repo(row, repo, host):
            continue
        number = _field(row, "pr_number")
        if number in present:
            continue
        prev = best.get(number)
        if prev is None or (_field(row, "reviewed_at") or "") > (_field(prev, "reviewed_at") or ""):
            best[number] = row
    ordered = sorted(
        best.values(),
        key=lambda row: _field(row, "reviewed_at") or "",
        reverse=True,
    )
    return [_field(row, "pr_number") for row in ordered[:LANDED_CAP]]


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def keep_landed(entries, now: datetime | None = None) -> list:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    earliest = now - timedelta(days=LANDED_DAYS)
    latest = now + timedelta(days=1)
    kept = []
    for entry in entries:
        merged_at = _field(entry, "merged_at")
        if merged_at is None or merged_at == "":
            continue
        if isinstance(merged_at, datetime):
            stamp = merged_at if merged_at.tzinfo else merged_at.replace(tzinfo=timezone.utc)
        else:
            stamp = _parse_iso(str(merged_at))
        if stamp is None:
            continue
        if earliest <= stamp <= latest:
            kept.append(entry)
    return kept
