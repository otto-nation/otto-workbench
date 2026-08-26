"""The machine-readable summary of a finished review.

`claude-review` prints a `REVIEW_SUMMARY:{json}` line that `pr` and the review
listing parse back, so this is the one place the summary's shape is decided.
It is the only reader that needs both halves of a review at once — the findings
document (counts, verdict) and the pipeline state (status, failure detail) —
which is why it sits above both rather than inside either.
"""

# doc-group: findings

from __future__ import annotations

import json
from pathlib import Path

from review_common import (
    SEVERITIES,
    Mode,
    ReviewMeta,
    aggregate_session_usage,
    count_severities,
    read_review_meta,
    resolve_review_verdict,
)
from review_state import build_failure_detail, read_pipeline_status


def build_review_summary(repo: str, pr_number: str, review_file: str) -> dict:
    """Build a review summary dict for a review."""
    review_path = Path(review_file) if review_file else None
    by_key = count_severities(review_path)
    counts = {s.json_key: by_key[s.key] for s in SEVERITIES}
    total = sum(by_key.values())

    review_dir = Path(review_file).parent if review_file else None
    meta = read_review_meta(review_dir) if review_dir else ReviewMeta()

    resolved = resolve_review_verdict(
        review_path, counts=by_key, self_review=meta.mode is Mode.SELF,
    )
    verdict = resolved.value if resolved else ""

    usage = aggregate_session_usage(review_dir)

    review_content = None
    if review_path and review_path.is_file():
        try:
            review_content = review_path.read_text()
        except OSError:
            pass

    status = read_pipeline_status(review_dir)
    failure_detail = build_failure_detail(review_dir)

    return {
        "repo": repo,
        "pr_number": int(pr_number) if pr_number else None,
        "head_sha": meta.head_sha or None,
        "head_ref": meta.head_ref or None,
        "base_ref": meta.base_ref or None,
        "review_type": meta.review_type,
        "review_file": review_file,
        "review_content": review_content,
        "findings": {**counts, "total": total},
        "verdict": verdict,
        "status": status,
        "failure_detail": failure_detail,
        "cost_usd": usage.cost,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "duration_ms": usage.duration_ms,
    }


def json_summary(repo: str, pr_number: str, review_file: str) -> str:
    """Build a REVIEW_SUMMARY:{json} string for a review."""
    data = build_review_summary(repo, pr_number, review_file)
    return f"REVIEW_SUMMARY:{json.dumps(data)}"
