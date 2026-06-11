"""Deduplication of findings against already-posted PR comments.

Fetches existing bot comments (inline and review-body), compares via
Jaccard similarity, and filters out duplicates before posting.
"""

from __future__ import annotations

import json
import re

import review_github

from review_format import CLASS_SKIPPED
from review_findings import Finding


# ── Constants ───────────────────────────────────────────────────────────────

DEDUP_THRESHOLD = 0.6


# ── Similarity ──────────────────────────────────────────────────────────────

def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ── Body finding extraction ────────────────────────────────────────────────

_BODY_FINDING_RE = re.compile(
    r"^- \*\*\[[MSNI]\d+\]\*\*\s+"
    r"(?:\*\*`?([^`*\s]+?)`?\*\*|`([^`\s]+?)`)"
    r"(?::\d+(?:[-–]\d+)?)?"
    r"\s*—\s*(.*)",
    re.MULTILINE,
)


def _extract_body_findings(body: str) -> list[dict]:
    results = []
    for m in _BODY_FINDING_RE.finditer(body):
        raw_path = (m.group(1) or m.group(2) or "")
        path = raw_path.rsplit(":", 1)[0] if ":" in raw_path else raw_path
        results.append({"path": path, "body": m.group(3)})
    return results


# ── Bot comment collection ──────────────────────────────────────────────────

def _collect_inline_comments(repo: str, pr: str, bot_user: str) -> list[dict]:
    all_comments = review_github._fetch_json_list(f"repos/{repo}/pulls/{pr}/comments")
    return [
        {"path": c.get("path", ""), "body": c.get("body", "")}
        for c in all_comments
        if c.get("user", {}).get("login") == bot_user
    ]


def _collect_review_findings(repo: str, pr: str, bot_user: str) -> list[dict]:
    all_reviews = review_github._fetch_json_list(f"repos/{repo}/pulls/{pr}/reviews")
    bot_bodies = [
        r.get("body", "") for r in all_reviews
        if r.get("user", {}).get("login") == bot_user
    ]
    entries: list[dict] = []
    for body in bot_bodies:
        if body:
            entries.extend(_extract_body_findings(body))
    return entries


def _fetch_bot_comments(repo: str, pr: str) -> list[dict]:
    code, user_out = review_github._gh_api("user")
    if code != 0:
        return []
    try:
        bot_user = json.loads(user_out).get("login", "")
    except (json.JSONDecodeError, TypeError):
        return []
    if not bot_user:
        return []

    entries = _collect_inline_comments(repo, pr, bot_user)
    entries.extend(_collect_review_findings(repo, pr, bot_user))
    return entries


# ── Dedup ───────────────────────────────────────────────────────────────────

def dedup_against_posted(
    findings: list[Finding], repo: str, pr: str,
) -> tuple[list[Finding], list[Finding]]:
    existing = _fetch_bot_comments(repo, pr)
    if not existing:
        return findings, []

    posted_entries = [
        (c["path"], _word_set(c["body"]))
        for c in existing
    ]

    kept, deduped = [], []
    for f in findings:
        f_words = _word_set(f.body)
        is_dup = any(
            f.path and posted_path and
            f.path == posted_path and
            _jaccard(f_words, posted_words) >= DEDUP_THRESHOLD
            for posted_path, posted_words in posted_entries
        )
        if is_dup:
            f.classification = CLASS_SKIPPED
            f.skip_reason = "duplicate of existing comment"
            deduped.append(f)
        else:
            kept.append(f)

    return kept, deduped
