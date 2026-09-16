"""The only writer of the review domain.

`pr review` and `claude-review` both finish a review and both used to stamp
`ReviewSummary` themselves — two mappings, two persist paths, and a PR review
wrote the domain twice. This module is the one writer: it derives the domain
fields from the typed report and persists them on the target the context names.
"""

# doc-group: pr-state

from __future__ import annotations

from typing import Protocol

from core import log
from core.trail import Trail
from pr import context as pr_context
from pr import domains as pr_domains
from pr import state as pr_state


class ReviewReport(Protocol):
    """The fields sync_review_domain reads off a finished review.

    Satisfied by ``review.summary.ReviewSummaryReport``. Declared here so this
    module does not import layer 6.
    """

    review_file: str
    review_type: str | None
    head_sha: str | None
    findings: dict[str, int]
    verdict: str
    status: str
    failure_detail: str
    recoverable: bool | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


def sync_review_domain(
    ctx: pr_context.ResolvedContext,
    report: ReviewReport,
    *,
    trail: Trail | None = None,
) -> pr_domains.ReviewSummary | None:
    """Write the review domain from a finished review's typed report.

    Accepts a pr.context.ResolvedContext and a report that carries the review's
    outcome, and returns the ReviewSummary that was persisted — or None when
    the write failed.

    Fields are derived from `report` rather than from a serialized copy of it:
    the counts drop the total and the zeroes, a missing type falls back to what
    the domain already held (then to full), a null head_sha becomes empty, and
    total_tokens is the sum of the report's token fields. A failed write is
    logged and swallowed: the review itself succeeded, and state.json is a cache.
    """
    try:
        state = pr_state.load_or_init(
            target_dir=ctx.target_dir,
            repo=ctx.repo,
            branch=ctx.branch,
            pr_number=ctx.pr_number,
            head_sha=ctx.head_sha,
            worktree_root=str(ctx.worktree_root) if ctx.worktree_root else "",
        )
        domain = state.review
        finding_counts = {
            key: count for key, count in report.findings.items()
            if key != "total" and isinstance(count, int) and count > 0
        }
        domain.review_file = report.review_file
        # "full" duplicates review.types.ReviewType.FULL.value — this module is
        # layer 4 and review is layer 6, so it cannot import the enum to read
        # the value off it instead.
        domain.review_type = report.review_type or domain.review_type or "full"
        domain.head_sha = report.head_sha or ""
        domain.finding_counts = finding_counts
        domain.verdict = report.verdict
        domain.status = report.status or pr_domains.ReviewStatus.COMPLETED.value
        domain.failure_detail = report.failure_detail
        domain.recoverable = report.recoverable
        domain.cost_usd = report.cost_usd
        domain.total_tokens = (
            report.input_tokens + report.output_tokens
            + report.cache_read_tokens + report.cache_write_tokens
        )
        domain.updated_at = pr_state.now_iso()
        pr_state.save_state(ctx.target_dir, state)
        return domain
    except Exception as exc:
        # state.json is a cache and the review itself succeeded, so a failed
        # write must not abort the run — but it must not vanish either, which
        # is why both channels report it, as ci-check and review-threads do.
        if trail is not None:
            trail.error("state_update", f"state update failed: {exc}")
        log.error(f"review state update failed: {exc}")
        return None
