"""Garbage collection for review artifacts.

Used by pr gc for explicit cleanup of stale and merged reviews.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import log
from pr_state import ReviewStatus
from review_common import (
    FILENAME_META,
    FILENAME_PIPELINE_STATE,
    REVIEW_EXT,
    REVIEWS_DIR,
    Phase,
    read_pipeline_status,
    read_review_meta,
)

GC_STALE_DAYS = 7
GC_FAILED_STALE_DAYS = 30
PRUNE_MAX_FILES = 10


def _dir_is_all_stale(d: Path, stale_days: int = GC_STALE_DAYS) -> bool:
    """Return True if every file in *d* is older than *stale_days*."""
    try:
        files = [f for f in d.rglob("*") if f.is_file()]
    except OSError:
        return False
    if not files:
        return True
    now = datetime.now().timestamp()
    return all((now - f.stat().st_mtime) / 86400 > stale_days for f in files)


def _clean_intermediates(review_dir: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove stale intermediate files from a completed review directory."""
    count = 0
    # Output artifacts (group-*.md, holistic.md) follow a naming convention of
    # their own and stay literal here; session logs are named after the
    # phase, so that half is derived from Phase.log_filename rather than
    # hand-copied — a phase added there is collected here for free.
    log_globs = [p.log_filename.format("*") for p in Phase if p.log_filename]
    patterns = ["group-*.md", "holistic.md", *log_globs]
    files = [f for p in patterns for f in review_dir.glob(p) if f.is_file()]
    now = datetime.now().timestamp()
    for f in files:
        age_days = (now - f.stat().st_mtime) / 86400
        if age_days > stale_days:
            f.unlink(missing_ok=True)
            count += 1
    return count


def _is_migration_input(f: Path, reviews_dir: Path) -> bool:
    """Whether the flat-layout migration would still fold this loose file into a dir.

    The migration keys off a flat `<name>.md` and moves its suffixed siblings along
    with it. A `.md` is always its own input; anything else is only claimable while
    that `.md` is still there. Once it is gone the sibling is stranded, not pending.
    """
    if f.suffix == REVIEW_EXT:
        return True
    stem = f.name.split(".", 1)[0]
    return (reviews_dir / f"{stem}{REVIEW_EXT}").is_file()


def _collect_strays(reviews_dir: Path, stale_days: int = GC_STALE_DAYS) -> int:
    """Remove stale loose files at the reviews root. Returns the count removed.

    Reviews live in per-review directories, so a loose file is either an unclaimable
    leftover of the flat layout or an agent's scratch file. Staleness is the guard:
    a run still in flight must not have its working files pulled out from under it.
    """
    now = datetime.now().timestamp()
    cleaned = 0
    for f in reviews_dir.iterdir():
        if not f.is_file() or _is_migration_input(f, reviews_dir):
            continue
        if (now - f.stat().st_mtime) / 86400 <= stale_days:
            continue
        f.unlink(missing_ok=True)
        log.info(f"GC: removed stray {f.name}")
        cleaned += 1
    return cleaned


def gc_reviews(reviews_dir: Path | None = None) -> int:
    """Remove orphaned review dirs and stale intermediates. Returns items cleaned."""
    reviews_dir = reviews_dir or REVIEWS_DIR
    if not reviews_dir.is_dir():
        return 0

    cleaned = _collect_strays(reviews_dir)
    for review_dir in reviews_dir.iterdir():
        if not review_dir.is_dir():
            continue

        has_review = (review_dir / f"review{REVIEW_EXT}").is_file()

        if not has_review and _dir_is_all_stale(review_dir):
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"GC: removed orphaned {review_dir.name}")
            cleaned += 1
            continue

        has_pipeline = (review_dir / FILENAME_PIPELINE_STATE).is_file()
        if has_review and not has_pipeline:
            cleaned += _clean_intermediates(review_dir)

    return cleaned


def _has_pipeline_failure(review_dir: Path) -> bool:
    return read_pipeline_status(review_dir) == ReviewStatus.ERROR.value


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

        review_dir = meta_file.parent
        stale_days = GC_FAILED_STALE_DAYS if _has_pipeline_failure(review_dir) else GC_STALE_DAYS
        if not _dir_is_all_stale(review_dir, stale_days):
            continue

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
            shutil.rmtree(review_dir, ignore_errors=True)
            log.info(f"Pruned {meta.repo}#{meta.pr_number} ({state})")
            pruned += 1

    return pruned
