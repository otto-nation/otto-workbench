"""Classify a PR into a standup bucket from GitHub list/search fields."""

# doc-group: pr-state

from __future__ import annotations

from typing import Any

WAITING = "waiting_on_review"
APPROVED = "approved_not_merged"
BLOCKED = "ci_or_conflicts"
DRAFT = "still_draft"
LANDED = "landed"
BUCKETS = (LANDED, WAITING, APPROVED, BLOCKED, DRAFT)

_FAIL = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED"})
_PENDING = frozenset({"PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "?"})


def ci_fail_names(rollup: object) -> list[str]:
    """Check names whose conclusion is a failure. Empty when CI is green or absent."""
    names: list[str] = []
    for check in _checks(rollup):
        if _conclusion(check) not in _FAIL:
            continue
        name = check.get("name")
        if isinstance(name, str) and name:
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
    repo = ""
    repository = pr.get("repository") or {}
    if isinstance(repository, dict):
        repo = str(repository.get("nameWithOwner") or "")
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


def _checks(rollup: object) -> list[dict[str, Any]]:
    if not isinstance(rollup, list):
        return []
    return [c for c in rollup if isinstance(c, dict)]


def _conclusion(check: dict[str, Any]) -> str:
    value = check.get("conclusion") or check.get("state") or "?"
    return str(value)
