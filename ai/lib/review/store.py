"""Where a review's record, sidecar and rendered view are written — together.

`write_review` is the one writer of `review.md`: it saves `review.json`, then
`meta.json`, then renders `review.md` from the two, each atomically. A caller
that changes a review loads it with `read_review`, transforms the record, and
writes it back — `update_review` is that loop. Nothing edits `review.md`.

A directory with no readable `review.json` is stale: `read_review` answers None
and every reader treats that as no review. The prior round's record is moved
to `prior.json` and its sidecar copied to `prior-meta.json` when a re-review
archives, so the re-review reads its predecessor as data rather than parsing
`prior.md`.
"""

# doc-group: pipeline

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import core.serde
from review.paths import (
    FILENAME_META, FILENAME_PRIOR_META, FILENAME_PRIOR_RECORD, FILENAME_RECORD,
    read_review_meta, write_review_meta,
)
from review.record import ReviewRecord, load_record, save_record
from review.render import render_review
from review.types import ReviewMeta


@dataclass(frozen=True)
class StoredReview:
    record: ReviewRecord
    meta: ReviewMeta


def _write_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        core.serde.replace_file(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_review(review_file: Path, record: ReviewRecord, meta: ReviewMeta) -> None:
    """Save `record` and `meta` beside `review_file`, and render `review_file` from them."""
    review_dir = review_file.parent
    review_dir.mkdir(parents=True, exist_ok=True)
    save_record(review_dir, record)
    write_review_meta(review_dir, meta)
    _write_text(review_file, render_review(record, meta))


def read_review(review_file: Path) -> StoredReview | None:
    """The review beside `review_file`, or None when the directory is stale."""
    record = load_record(review_file.parent)
    if record is None:
        return None
    return StoredReview(record, read_review_meta(review_file.parent))


def update_review(
    review_file: Path,
    transform: Callable[[ReviewRecord], ReviewRecord] = lambda r: r,
    **meta_changes,
) -> StoredReview | None:
    """Load, transform, write back. None, and nothing written, when stale."""
    stored = read_review(review_file)
    if stored is None:
        return None
    updated = StoredReview(transform(stored.record), replace(stored.meta, **meta_changes))
    write_review(review_file, updated.record, updated.meta)
    return updated


def archive_prior(review_dir: Path) -> None:
    """Move this round's record aside and copy its sidecar for the re-review about to replace them.

    A stale directory leaves no prior at all — a leftover copy from an older
    round would be read as this one's predecessor.
    """
    if load_record(review_dir) is None:
        clear_prior(review_dir)
        return
    core.serde.replace_file(review_dir / FILENAME_RECORD, review_dir / FILENAME_PRIOR_RECORD)
    if (review_dir / FILENAME_META).is_file():
        shutil.copy2(review_dir / FILENAME_META, review_dir / FILENAME_PRIOR_META)


def clear_prior(review_dir: Path) -> None:
    for name in (FILENAME_PRIOR_RECORD, FILENAME_PRIOR_META):
        (review_dir / name).unlink(missing_ok=True)


def read_prior(review_dir: Path) -> StoredReview | None:
    """The prior round's record and sidecar, or None when there is no usable prior."""
    record = load_record(review_dir, FILENAME_PRIOR_RECORD)
    if record is None:
        return None
    return StoredReview(record, read_review_meta(review_dir, FILENAME_PRIOR_META))
