"""The reviews listing that `pr review --list` serves.

The contract another repo reads review state through. It asks this CLI instead
of resolving `<state root>/reviews/<name>/review.md` from its own process and
scraping the prose, so what a consumer has to agree with us about collapses
from a state-root rung chain plus two directory-naming schemes plus the review
file's format down to this module's output.

Two properties are the reason the query beats the path derivation it replaces:

* **A wrong answer is loud.** Deriving a path and listing it cannot fail — a
  root nothing writes to reads exactly like a machine that has never run a
  review. Here the process is the error channel: a `pr` that is missing,
  crashes, or exits non-zero is "unknown", and an empty `reviews` array is the
  only thing that means "no reviews".
* **Nothing is re-derived.** Finding counts, verdict, pipeline status, cost and
  both timestamps are computed by the code that owns them and handed over
  whole, rather than parsed back out of a document written for a human.

A consumer asks for the row schema it speaks, and the CLI enforces it:

    pr review --list --schema-version 1

An unsupported value exits non-zero and names the versions this build serves. A
bare `pr review --list` writes a human table to stderr and nothing at all to
stdout — a consumer that forgets the handshake gets a `jq` parse failure rather
than a subtly-wrong document.

`stdout` carries one JSON object:

    {
      "schema_version": 1,
      "reviews": [
        {
          "repo": "otto-nation/otto-workbench",
          "pr_number": 761,
          "review_file": "/Users/…/reviews/otto-workbench-761/review.md",
          "head_sha": "4a33027c…",
          "head_ref": "isaac/761/…",
          "base_ref": "main",
          "review_type": "full",
          "mode": "pr",
          "reviewed_at": "2026-08-18T14:02:11+00:00",
          "started_at": "2026-08-18T13:47:03+00:00",
          "findings": {"must_fix": 0, "should_fix": 2, "nit": 1, "idiom": 0,
                       "total": 3},
          "verdict": "approve",
          "status": "complete",
          "failure_detail": "",
          "cost_usd": 4.12,
          "input_tokens": 0, "output_tokens": 0,
          "cache_read_tokens": 0, "cache_write_tokens": 0,
          "duration_ms": 0
        }
      ]
    }

A row reports its review's *path*, never its content: a consumer polling on an
interval would otherwise carry every review's full text on every tick. Finding
keys are the `SeverityConfig.json_key` vocabulary the rest of the codebase
already uses, so this document and `build_review_summary`'s cannot disagree
about what a severity is called. A review written before `meta.json` existed is
still listed, with an empty repo and a null PR number — unattributed is a fact
about that review, and dropping it would hide one the consumer can still open.

A missing reviews root is not an error; it is `{"reviews": []}` with exit 0.

**Version policy.** A new *optional* field does not bump `schema_version`. A
removed field, a renamed field, or a changed type adds a new version.
Enforcement comes from the supported set being allowed to *shrink* —
`--schema-version 1` keeps working until this build stops serving 1, and
`SCHEMA_VERSIONS` is the one place that says which those are. Nothing
hand-stamps a version into the document: the field echoes back what the caller
declared and this build agreed to serve, so it cannot go stale on its own.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass

import serde
from review_common import (
    Mode, ReviewEntry, ReviewEntryKind, SEVERITIES, aggregate_session_usage,
    build_failure_detail, count_severities, iter_review_entries,
    read_pipeline_status, resolve_review_verdict,
)

# Row-schema versions this build serves, oldest first. See "Version policy" in
# docs/ai-automation.md: a new optional field does not bump it, a removed or
# renamed field or a changed type does, and enforcement comes from this tuple
# being allowed to shrink. Nothing hand-stamps a version into the document —
# the caller declares which it speaks and gets an error if we no longer serve
# it, so the field cannot go stale the way a producer-stamped one does.
SCHEMA_VERSIONS = (1,)


@dataclass(frozen=True)
class ReviewRow:
    """One review, as the listing reports it.

    Deliberately not `build_review_summary`'s return value. The two share every
    underlying reader — `count_severities`, `resolve_review_verdict`,
    `read_pipeline_status`, `build_failure_detail`, `aggregate_session_usage` —
    so they cannot disagree about what they describe, but the summary carries
    `review_content` and no timestamps, and this carries the timestamps and no
    content. Sharing the readers rather than the function is what keeps them
    consistent without making either a subset of the other.

    `findings` is keyed by `SeverityConfig.json_key` rather than by fields of
    its own, so the severities a row reports are the severities the codebase
    has, not a copy of them that a fifth severity would leave behind.
    """

    repo: str
    pr_number: int | None
    review_file: str
    head_sha: str
    head_ref: str
    base_ref: str
    review_type: str
    mode: str
    reviewed_at: str
    started_at: str
    findings: dict[str, int]
    verdict: str
    status: str
    failure_detail: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    duration_ms: int


def row_for(entry: ReviewEntry) -> ReviewRow:
    """Build the row describing *entry*, which must hold a review.

    Attribution is the sidecar's, never the directory name's, so a review is
    reported for the repo it was run against even when it sits under a
    directory named for another one. A review predating the sidecar reports an
    empty repo and a null PR number: unattributed is a fact about that review,
    and dropping it would hide a review the consumer can still open.
    """
    review_file = entry.review_file
    meta = entry.meta

    by_key = count_severities(review_file)
    verdict = resolve_review_verdict(
        review_file, counts=by_key, self_review=meta.mode is Mode.SELF,
    )
    usage = aggregate_session_usage(entry.path)

    return ReviewRow(
        repo=meta.repo,
        pr_number=meta.pr_number,
        review_file=str(review_file),
        head_sha=meta.head_sha,
        head_ref=meta.head_ref,
        base_ref=meta.base_ref,
        review_type=meta.review_type.value if meta.review_type else "",
        mode=meta.mode.value if meta.mode else "",
        reviewed_at=entry.reviewed_at,
        started_at=meta.started_at,
        findings={
            **{s.json_key: by_key[s.key] for s in SEVERITIES},
            "total": sum(by_key.values()),
        },
        verdict=verdict.value if verdict else "",
        status=read_pipeline_status(entry.path),
        failure_detail=build_failure_detail(entry.path),
        cost_usd=usage.cost,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        duration_ms=usage.duration_ms,
    )


def rows() -> list[ReviewRow]:
    """Every review at the reviews root, in the walk's order.

    Orphaned directories and stray files are not reviews and are left to `pr
    gc`; a consumer asking what has been reviewed is not asking about a run
    that produced nothing. A reviews root that does not exist yields no rows
    rather than raising — a machine that has never run a review is not an
    error, and the process is the error channel for the ones that are.
    """
    return [
        row_for(entry)
        for entry in iter_review_entries()
        if entry.kind is ReviewEntryKind.REVIEW
    ]


def document(schema_version: int) -> dict:
    """The listing as the versioned document a consumer parses.

    The version is echoed back rather than restated from a constant: it is the
    one the caller declared and this build agreed to serve, so a document can
    never claim a version its reader did not ask for.
    """
    return {
        "schema_version": schema_version,
        "reviews": [serde.to_dict(row) for row in rows()],
    }


def render_table(listing: list[ReviewRow]) -> list[str]:
    """The listing as lines for a human, for a bare `--list` with no handshake.

    Deliberately not the document with the braces taken off: a person reading
    `pr review --list` wants to know which reviews exist and what they said,
    and the token counts a consumer polls for are noise on a terminal.
    """
    if not listing:
        return ["No reviews."]
    header = ("REPO", "PR", "VERDICT", "FINDINGS", "REVIEWED")
    body = [
        (
            row.repo or "(unattributed)",
            f"#{row.pr_number}" if row.pr_number else "-",
            row.verdict or "-",
            str(row.findings["total"]),
            row.reviewed_at or "-",
        )
        for row in listing
    ]
    widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(cells, widths)).rstrip()
        for cells in [header, *body]
    ]
