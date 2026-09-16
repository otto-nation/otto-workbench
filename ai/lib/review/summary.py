"""The machine-readable summary of a finished review, and its human rendering.

`claude-review` prints a `REVIEW_SUMMARY:{json}` line that `pr` and the review
listing parse back, so this is the one place the summary's shape is decided.
It is the only reader that needs both halves of a review at once — the findings
document (counts, verdict) and the pipeline state (status, failure detail) —
which is why it sits above both rather than inside either.

The emitted shape is `ReviewSummaryReport`. In-process readers take the
dataclass; the dict exists only at the `json.dumps` call in `json_summary`.
The human renderer prints the same type.
"""

# doc-group: findings

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agent.usage import format_tokens, parse_session_log
from core import log
from pr.domains import ReviewVerdict
from review.paths import (
    aggregate_session_usage,
    read_review_meta,
)
from review.document import ReviewDocument
from review.state import (
    build_failure_detail, build_recoverable, read_pipeline_status,
    read_pipeline_warnings,
)
from review.types import SEVERITIES, ReviewMeta
from review.verdict import open_counts, resolve_review_verdict


def _empty_findings() -> dict[str, int]:
    return {**{s.json_key: 0 for s in SEVERITIES}, "total": 0}


@dataclass(frozen=True)
class ReviewSummaryReport:
    """One finished review, as the summary reports it.

    `to_json` is the dict `REVIEW_SUMMARY:` serialises. Field names and values
    match that dict byte-for-byte; a new field here is a change to the wire.
    """

    repo: str = ""
    pr_number: int | None = None
    head_sha: str | None = None
    head_ref: str | None = None
    base_ref: str | None = None
    review_type: str | None = None
    review_file: str = ""
    review_content: str | None = None
    findings: dict[str, int] = field(default_factory=_empty_findings)
    verdict: str = ""
    status: str = ""
    failure_detail: str = ""
    recoverable: bool | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0

    def to_json(self) -> dict:
        """The summary as the dict written to stdout."""
        return {
            "repo": self.repo,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "review_type": None if self.review_type is None else str(self.review_type),
            "review_file": self.review_file,
            "review_content": self.review_content,
            "findings": self.findings,
            "verdict": self.verdict,
            "status": self.status,
            "failure_detail": self.failure_detail,
            "recoverable": self.recoverable,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "duration_ms": self.duration_ms,
        }


def _read_review(path: Path | None) -> str | None:
    """The review's text, or None when there is no readable file at `path`."""
    if not path or not path.is_file():
        return None
    try:
        return path.read_text()
    except OSError:
        return None


def build_review_summary(repo: str, pr_number: str, review_file: str) -> ReviewSummaryReport:
    """Build a review summary for a review."""
    review_path = Path(review_file) if review_file else None
    # The document the summary reports on and the text it carries verbatim are
    # one file, read once: `review_content` is what was on disk, not what
    # re-rendering the parse would produce.
    review_content = _read_review(review_path)
    doc = ReviewDocument.parse(review_content) if review_content is not None else None

    by_key = open_counts(doc)
    counts = {s.json_key: by_key[s.key] for s in SEVERITIES}
    total = sum(by_key.values())

    review_dir = Path(review_file).parent if review_file else None
    meta = read_review_meta(review_dir) if review_dir else ReviewMeta()

    resolved = resolve_review_verdict(doc, mode=meta.mode)
    verdict = resolved.value if resolved else ""

    usage = aggregate_session_usage(review_dir)

    status = read_pipeline_status(review_dir)
    failure_detail = build_failure_detail(review_dir)
    recoverable = build_recoverable(review_dir)

    return ReviewSummaryReport(
        repo=repo,
        pr_number=int(pr_number) if pr_number else None,
        head_sha=meta.head_sha or None,
        head_ref=meta.head_ref or None,
        base_ref=meta.base_ref or None,
        review_type=meta.review_type,
        review_file=review_file,
        review_content=review_content,
        findings={**counts, "total": total},
        verdict=verdict,
        status=status,
        failure_detail=failure_detail,
        recoverable=recoverable,
        cost_usd=usage.cost,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        duration_ms=usage.duration_ms,
    )


def json_summary(repo: str, pr_number: str, review_file: str) -> str:
    """Build a REVIEW_SUMMARY:{json} string for a review."""
    data = build_review_summary(repo, pr_number, review_file).to_json()
    return f"REVIEW_SUMMARY:{json.dumps(data)}"


def format_usage(*log_paths: str, wall_clock_ms: int | None = None) -> str:
    logs = [p for p in log_paths if p and os.path.isfile(p)]
    if not logs:
        return ""

    usages = [parse_session_log(p) for p in logs]
    cost = sum(u.cost for u in usages)
    total = sum(u.total_tokens for u in usages)
    cached = sum(u.cache_read_tokens for u in usages)
    duration_ms = wall_clock_ms if wall_clock_ms is not None else sum(u.duration_ms for u in usages)

    if cost == 0.0 and total == 0 and duration_ms == 0:
        return ""

    tokens_part = f"{format_tokens(total)} tokens"
    if cached > 0:
        tokens_part += f" ({format_tokens(cached)} cached)"

    duration_s = duration_ms // 1000
    mins, secs = divmod(duration_s, 60)
    duration_fmt = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    cost_fmt = f"${cost:.2f}"

    return f"  Usage:           {cost_fmt} · {tokens_part} · {duration_fmt}"


def format_findings_line(report: ReviewSummaryReport) -> str:
    parts = [
        f"{report.findings.get(sev.json_key, 0)} {sev.section.lower()}"
        for sev in SEVERITIES if report.findings.get(sev.json_key, 0) > 0
    ]
    return ", ".join(parts)


def format_verdict(report: ReviewSummaryReport) -> str:
    if not report.verdict:
        return ""
    try:
        return ReviewVerdict(report.verdict).prose
    except ValueError:
        return report.verdict


def print_summary(
    report: ReviewSummaryReport,
    session_log: str,
    pr_url: str = "",
    post_session_log: str = "",
    branch_name: str = "",
    wall_clock_ms: int | None = None,
) -> None:
    log.blank()
    log.info("─── Summary ───")

    findings = format_findings_line(report)
    if findings:
        log.dim(f"Findings:        {findings}")

    verdict = format_verdict(report)
    if verdict:
        log.dim(f"Verdict:         {verdict}")

    review_dir = Path(report.review_file).parent if report.review_file else None
    warnings = read_pipeline_warnings(review_dir)
    if warnings:
        log.warn(f"Incomplete:      {', '.join(warnings)}")

    log.dim(f"Review:          {report.review_file}")
    log.dim(f"Session log:     {session_log}")
    if post_session_log and os.path.isfile(post_session_log):
        log.dim(f"Post session:    {post_session_log}")
    if pr_url:
        log.dim(f"PR:              {pr_url}")
    elif branch_name:
        log.dim(f"Branch:          {branch_name}")
    usage_line = format_usage(session_log, post_session_log, wall_clock_ms=wall_clock_ms)
    if usage_line:
        log.dim(usage_line.strip())
    log.blank()
