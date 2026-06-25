"""Garbage collection for review artifacts.

Extracted from claude-review so both claude-review (startup prune) and
pr gc (broad cleanup) can share the logic.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import log
from review_common import (
    FILENAME_META,
    FILENAME_PIPELINE_STATE,
    REVIEW_EXT,
    REVIEWS_DIR,
    read_review_meta,
)

GC_STALE_DAYS = 7
PRUNE_MAX_FILES = 10


def gc_dir_is_all_stale(d: Path, stale_days: int = GC_STALE_DAYS) -> bool:
    """Return True if every file in *d* is older than *stale_days*."""
    try:
        files = [f for f in d.rglob("*") if f.is_file()]
    except OSError:
        return False
    if not files:
        return True
    now = datetime.now().timestamp()
    return all((now - f.stat().st_mtime) / 86400 > stale_days for f in files)


def gc_clean_intermediates(review_dir: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove stale intermediate files from a completed review directory."""
    count = 0
    patterns = [
        "group-*.md", "group-*.jsonl",
        "holistic.md", "holistic.jsonl",
        "synthesis.jsonl",
    ]
    files = [f for p in patterns for f in review_dir.glob(p) if f.is_file()]
    now = datetime.now().timestamp()
    for f in files:
        age_days = (now - f.stat().st_mtime) / 86400
        if age_days > stale_days:
            f.unlink(missing_ok=True)
            count += 1
    return count


def gc_reviews(reviews_dir: Path | None = None) -> int:
    """Remove orphaned review dirs and stale intermediates. Returns items cleaned."""
    reviews_dir = reviews_dir or REVIEWS_DIR
    if not reviews_dir.is_dir():
        return 0

    cleaned = 0
    for review_dir in reviews_dir.iterdir():
        if not review_dir.is_dir():
            continue

        has_review = (review_dir / f"review{REVIEW_EXT}").is_file()

        if not has_review and gc_dir_is_all_stale(review_dir):
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"GC: removed orphaned {review_dir.name}")
            cleaned += 1
            continue

        has_pipeline = (review_dir / FILENAME_PIPELINE_STATE).is_file()
        if has_review and not has_pipeline:
            cleaned += gc_clean_intermediates(review_dir)

    return cleaned


def prune_merged_reviews(reviews_dir: Path | None = None, max_files: int = PRUNE_MAX_FILES) -> int:
    """Remove review directories for merged/closed PRs. Returns count pruned."""
    reviews_dir = reviews_dir or REVIEWS_DIR
    if not reviews_dir.is_dir():
        return 0

    pruned = 0
    checked = 0

    for meta_file in reviews_dir.glob(f"*/{FILENAME_META}"):
        if checked >= max_files:
            break

        meta = read_review_meta(meta_file.parent)
        if not meta.repo or not meta.pr_number:
            continue

        checked += 1

        try:
            r = subprocess.run(
                ["gh", "pr", "view", str(meta.pr_number), "--repo", meta.repo,
                 "--json", "state", "--jq", ".state"],
                capture_output=True, text=True,
            )
            state = r.stdout.strip()
        except Exception:
            state = ""

        if state in ("MERGED", "CLOSED"):
            review_dir = meta_file.parent
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"Pruned {meta.repo}#{meta.pr_number} ({state})")
            pruned += 1

    return pruned
