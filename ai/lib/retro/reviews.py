"""Turning locally-saved reviews into the same comment shape GitHub PRs produce.

Walks the reviews root `review_paths` already tracks and reads each review's
findings into the retro's per-repo, per-PR comment structure, so `retro_report`
renders a local self-review indistinguishably from a GitHub one. Deciding
which rule a finding is nearest to is `retro_rules`'; matching the comment to
the exact bullet on the page is `retro_report`'s.
"""

# doc-group: platform

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core import log
from review.document import ReviewDocument
from review.paths import ReviewEntry, ReviewEntryKind, iter_review_entries
from review.types import Finding
from retro.report import best_matching_bullet
from retro.rules import find_nearest_rule


# ── Constants ────────────────────────────────────────────────────────────────

# Beside reviews/ under the state root rather than under --home: both are
# generated data, and retro-complete.sh reads them through the state root too.
# Its half of this name is RETRO_CONSUMED_REVIEWS_FILE in lib/constants.sh;
# tests/workbench_roots.bats holds the two together.
CONSUMED_REVIEWS_NAME = "retro-consumed-reviews.txt"

# The repository a review written before `meta.json` recorded one is for. The
# sidecar is the answer everywhere it exists — see `_review_repo`.
REVIEW_TITLE_RE = re.compile(
    r"^#\s+(?:Self-)?Review:\s+(\S+)\s*(?:—|#)",
)


# ── Local review scanning ────────────────────────────────────────────────────

def _review_repo(entry: ReviewEntry, doc: ReviewDocument) -> str:
    """The repository `entry`'s review is for.

    `meta.json` is the answer wherever there is one — the walk that classified
    the entry already read it, and it is what every other reader of the tree
    goes by. A review written before the sidecar recorded a repo has only its
    title, and one whose title states none has only its directory name, which
    is the short repo name with the PR number or the branch appended.
    """
    if entry.meta.repo:
        return entry.meta.repo
    m = REVIEW_TITLE_RE.search(doc.title)
    if m:
        return m.group(1)
    dir_name = entry.path.name
    parts = dir_name.split("-self-", 1)
    if len(parts) == 2:
        return parts[0]
    parts = dir_name.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return dir_name


@dataclass(frozen=True)
class RuleMatch:
    """A finding rendered as a retro comment, and whether a rule claimed it.

    ``matched`` is the question the caller actually asks, so it is named for
    the answer rather than for the tally it feeds — an unmatched finding is
    what the retro counts, but "did a rule match" is what the comment knows.
    """

    comment: dict
    matched: bool


def _finding_to_comment(
    finding: Finding, source: str, review_file: Path,
    rules: list[dict], rule_match_counts: dict[str, dict],
) -> RuleMatch:
    comment = {
        "author": source,
        "body": f"[{finding.severity}] {finding.body}",
        "path": finding.path or None,
        "line": finding.line,
        "url": "",
        "direction": "received",
        "source_file": str(review_file),
    }
    nearest = find_nearest_rule(comment["body"], rules)
    if nearest:
        comment["nearest_rule"] = {
            "filename": nearest["filename"],
            "match_snippet": best_matching_bullet(comment["body"], nearest),
        }
        rule_match_counts[nearest["filename"]]["matched"] += 1
        return RuleMatch(comment, matched=True)
    comment["nearest_rule"] = None
    return RuleMatch(comment, matched=False)


@dataclass(frozen=True)
class ScannedReview:
    """One local review's findings, in the shape the retro groups them by.

    ``pr_entry`` is a whole review rendered as the single pseudo-PR the retro
    files it under; ``unmatched`` is how many of its findings matched no rule.
    """

    repo_key: str
    pr_entry: dict
    unmatched: int


def _scan_review_entry(
    entry: ReviewEntry, rules: list[dict],
    rule_match_counts: dict[str, dict],
) -> ScannedReview | None:
    review_file = entry.review_file
    # A REVIEW-kind entry had its review file when the walk classified it, so a
    # document that comes back None means the file went away or turned
    # unreadable in between — a concurrent `pr gc` sweep, most likely. Skip the
    # review rather than abandoning the scan over one directory.
    doc = ReviewDocument.read(review_file)
    if doc is None:
        return None
    findings = doc.findings
    if not findings:
        return None

    dir_name = entry.path.name
    repo_key = _review_repo(entry, doc)
    source = "self-review" if "-self-" in dir_name else "claude-review"

    unmatched = 0
    comments: list[dict] = []
    for f in findings:
        match = _finding_to_comment(
            f, source, review_file, rules, rule_match_counts,
        )
        if not match.matched:
            unmatched += 1
        comments.append(match.comment)

    pr_entry = {
        "number": f"local:{dir_name}",
        "title": f"Local review ({source})",
        "author": source,
        "merged_at": "",
        "comments": comments,
    }
    return ScannedReview(repo_key=repo_key, pr_entry=pr_entry, unmatched=unmatched)


@dataclass(frozen=True)
class LocalReviewScan:
    """What scanning the local reviews directory for findings turned up.

    ``repos`` groups the scanned reviews by repository, in the same shape a
    GitHub fetch produces so the two lists merge without translation.
    ``unmatched`` is how many of those findings matched no rule, and
    ``consumed`` names the review directories the scan read — the retro
    records these as spent so a later run does not re-report them.
    """

    repos: list[dict]
    unmatched: int
    consumed: list[str]


def scan_local_reviews(
    reviews_dir: Path, rules: list[dict], rule_match_counts: dict[str, dict],
) -> LocalReviewScan:
    """Scan every local review under `reviews_dir` into a `LocalReviewScan`."""
    local_repos: dict[str, list[dict]] = {}
    consumed_dirs: list[str] = []
    unmatched = 0

    for entry in iter_review_entries(reviews_dir):
        if entry.kind is not ReviewEntryKind.REVIEW:
            continue
        # `is None`, not truthiness — a `ScannedReview` is always truthy, so a
        # falsy test here would read as a skip that can never fire.
        scanned = _scan_review_entry(entry, rules, rule_match_counts)
        if scanned is None:
            continue
        unmatched += scanned.unmatched
        local_repos.setdefault(scanned.repo_key, []).append(scanned.pr_entry)
        consumed_dirs.append(entry.path.name)

    result_list = [
        {"github": f"local/{repo_key}", "prs": prs}
        for repo_key, prs in local_repos.items()
    ]

    log.info(f"Found {sum(len(r['prs']) for r in result_list)} local review(s)")
    return LocalReviewScan(repos=result_list, unmatched=unmatched, consumed=consumed_dirs)
