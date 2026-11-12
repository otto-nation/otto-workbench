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

Rows carry `post` (the latest run's post outcome, including dismissed) and
`history` (archived runs, newest first, structured where the run archived its
record). The document carries `reviews_root`.

The document carries `stamp`; `--if-changed STAMP` with the stamp of an earlier
document answers `{"unchanged": true}` and no `reviews` when nothing under the
reviews root changed; a consumer that never passes it is unaffected; both are
optional so the schema does not bump.

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

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import core.serde
import core.workbench_paths
from core.serde import load_file as serde_load_file
from pr.state import PostTracking
from pr.target import display_repo
from review.paths import (
    ARCHIVE_META_EXT, ARCHIVE_POST_EXT, ARCHIVE_RECORD_EXT, ARCHIVE_REVIEW_EXT,
    FILENAME_POST_SESSION, ReviewEntry, ReviewEntryKind, aggregate_session_usage,
    archived_file, archived_session_usage, archived_stamps, archives_dir,
    iter_review_entries, read_review_meta,
)
from review.document import ReviewDocument
from review.record import load_record
from review.state import build_failure_detail, read_pipeline_status
from review.store import StoredReview
from review.types import SEVERITIES
from review.usage_cache import FILENAME_USAGE_CACHE, TEMP_PREFIX, UsageCache
from review.verdict import open_counts, resolve_review_verdict

# Row-schema versions this build serves, oldest first. See "Version policy" in
# docs/ai-automation.md: a new optional field does not bump it, a removed or
# renamed field or a changed type does, and enforcement comes from this tuple
# being allowed to shrink. Nothing hand-stamps a version into the document —
# the caller declares which it speaks and gets an error if we no longer serve
# it, so the field cannot go stale the way a producer-stamped one does.
SCHEMA_VERSIONS = (1,)


@dataclass(frozen=True)
class RunPost:
    """What a run's `post.jsonl` says happened to it on GitHub, or that it was dismissed.

    `event` is a `PostEvent` value. A dismissed run posted nothing, and says so
    here so a consumer can tell "answered" from "not yet looked at".
    """

    event: str
    posted_at: str
    review_id: int
    sha_drifted: bool


@dataclass(frozen=True)
class ReviewRun:
    """One earlier run of a review, from its `archives/` files.

    `structured` is False for a run archived before records were: only its
    rendered markdown and logs survive, so its verdict, findings and SHA are
    empty rather than scraped back out of the markdown.
    """

    stamp: str
    review_file: str
    structured: bool
    head_sha: str
    reviewed_at: str
    verdict: str
    findings: dict[str, int]
    status: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    duration_ms: int
    post: RunPost | None
    # The agents the run used, as its `Models:` line states them; "" when the
    # run recorded none. Optional, so the schema does not bump.
    models: str = ""


@dataclass(frozen=True)
class ReviewRow:
    """One review, as the listing reports it.

    Deliberately not `build_review_summary`'s return value. The two share every
    underlying reader — `ReviewDocument`, `resolve_review_verdict`,
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
    # The fix commit this review's pass left unpushed, or "" when it owes
    # nothing. A new optional field, which the version policy above says does
    # not bump the schema. Only the unpushed case is reported: a pushed commit
    # is on the branch for anyone to find, while a held one is knowable from
    # nowhere else once the terminal that printed its resume line is gone.
    unpushed_fix_commit: str = ""
    # The latest commit this review answers "is a re-run needed" for
    # (`ReviewMeta.reviewed_through`). A new optional field, so the schema
    # does not bump. The listing itself does not decide staleness; a consumer
    # that does reads this rather than `head_sha`.
    reviewed_through: str = ""
    # The forge the review was run against, empty for public GitHub. Another
    # optional field, so the schema does not bump. `repo` stays the bare slug
    # every consumer already keys on — this is beside it, for a consumer that
    # wants to *show* which instance a row came from, and `display_repo` is
    # what turns the pair into that string.
    host: str = ""
    # What the latest run's post.jsonl records, None when it was never posted
    # or dismissed. Optional, so the schema does not bump.
    post: RunPost | None = None
    # The runs `archives/` keeps for this review, newest first
    # (`review.archive_keep`). Optional, so the schema does not bump.
    history: list[ReviewRun] = field(default_factory=list)
    # The agents the latest run used (`ReviewMeta.models`); "" when none was
    # recorded. Optional, so the schema does not bump.
    models: str = ""


def _findings(doc: ReviewDocument | None) -> dict[str, int]:
    by_key = open_counts(doc)
    return {**{s.json_key: by_key[s.key] for s in SEVERITIES}, "total": sum(by_key.values())}


def _post_from(path: Path) -> RunPost | None:
    tracking = serde_load_file(PostTracking, path)
    if tracking is None:
        return None
    return RunPost(event=tracking.status, posted_at=tracking.posted_at,
                   review_id=tracking.review_id, sha_drifted=tracking.sha_drifted)


def _run_for(review_dir: Path, stamp: str, cache: UsageCache) -> ReviewRun:
    """One archived run, read the way `row_for` reads the live one."""
    archive = archives_dir(review_dir)
    record = load_record(archive, f"{stamp}{ARCHIVE_RECORD_EXT}")
    meta = read_review_meta(archive, f"{stamp}{ARCHIVE_META_EXT}")
    doc = ReviewDocument.from_stored(StoredReview(record, meta)) if record else None
    verdict = resolve_review_verdict(record, meta) if record else None
    usage = archived_session_usage(review_dir, stamp, cache)
    return ReviewRun(
        stamp=stamp,
        review_file=str(archived_file(review_dir, stamp, ARCHIVE_REVIEW_EXT)),
        structured=record is not None,
        head_sha=meta.head_sha,
        reviewed_at=meta.reviewed_at,
        verdict=verdict.value if verdict else "",
        findings=_findings(doc),
        status=meta.status.value if meta.status else "",
        cost_usd=usage.cost,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        duration_ms=usage.duration_ms,
        post=_post_from(archived_file(review_dir, stamp, ARCHIVE_POST_EXT)),
        models=meta.models,
    )


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

    doc = ReviewDocument.read(review_file)
    verdict = resolve_review_verdict(doc.record, doc.meta) if doc else None
    cache = UsageCache(entry.path)
    usage = aggregate_session_usage(entry.path, cache)
    history = [_run_for(entry.path, stamp, cache) for stamp in archived_stamps(entry.path)]
    cache.save()

    return ReviewRow(
        repo=meta.repo,
        host=meta.host,
        pr_number=meta.pr_number,
        review_file=str(review_file),
        head_sha=meta.head_sha,
        reviewed_through=meta.reviewed_through,
        head_ref=meta.head_ref,
        base_ref=meta.base_ref,
        review_type=meta.review_type.value if meta.review_type else "",
        mode=meta.mode.value if meta.mode else "",
        reviewed_at=entry.reviewed_at,
        started_at=meta.started_at,
        findings=_findings(doc),
        verdict=verdict.value if verdict else "",
        status=read_pipeline_status(entry.path),
        failure_detail=build_failure_detail(entry.path),
        unpushed_fix_commit=meta.unpushed_fix_commit,
        cost_usd=usage.cost,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        duration_ms=usage.duration_ms,
        post=_post_from(entry.path / FILENAME_POST_SESSION),
        history=history,
        models=meta.models,
    )


# ceiling: each row still loads every live and archived record and sidecar;
# session-log usage is cached (review.usage_cache). Upgrade trigger: if
# `pr review --list` takes over 3 s on a real reviews root once the cache is
# warm, cache each finished row by its source files' mtimes.
def rows(*, keep: Callable[[ReviewEntry], bool] | None = None) -> list[ReviewRow]:
    """Every review at the reviews root, in the walk's order.

    *keep*, when given, is applied to each review entry before the row is
    built; an entry it rejects is omitted and never parsed. The default is
    every review, which is what `pr review --list` asks for.

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
        and (keep is None or keep(entry))
    ]


def stamp(root: Path | None = None) -> str:
    """Fingerprint of every regular file under the reviews root.

    Each file contributes its relative POSIX path, size and ``st_mtime_ns``.
    ``usage-cache.json`` and the cache's temp files (names starting
    ``.usage-cache.json.``) are skipped: the listing writes those itself, so
    counting them would make every listing look like a change. A deletion
    changes the stamp because the file set is hashed. A missing root hashes
    nothing rather than raising.
    """
    if root is None:
        root = core.workbench_paths.reviews_dir()
    lines = [
        line
        for dirpath, _dirnames, filenames in os.walk(root)
        for name in filenames
        if (line := _stamp_line(root, Path(dirpath, name))) is not None
    ]
    payload = "\n".join(sorted(lines))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _stamp_line(root: Path, path: Path) -> str | None:
    """*path*'s relative path, size and mtime as one stamp line, or None for a file it skips."""
    if path.name == FILENAME_USAGE_CACHE or path.name.startswith(TEMP_PREFIX):
        return None
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return f"{path.relative_to(root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}"


def document(schema_version: int, if_changed: str | None = None) -> dict:
    """The listing as the versioned document a consumer parses.

    The version is echoed back rather than restated from a constant: it is the
    one the caller declared and this build agreed to serve, so a document can
    never claim a version its reader did not ask for.
    """
    # Taken before the rows are read, so a change during the read shows on the
    # next poll instead of being masked.
    current = stamp()
    envelope = {
        "schema_version": schema_version,
        # Where the walk ran, so a consumer can watch it without deriving the
        # path. Optional, so the schema does not bump.
        "reviews_root": str(core.workbench_paths.reviews_dir()),
        "stamp": current,
    }
    if if_changed == current:
        return {**envelope, "unchanged": True}
    return {**envelope, "reviews": [core.serde.to_dict(row) for row in rows()]}


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
            # Host-qualified, because the listing is where two instances'
            # reviews sit in one table and the slug alone cannot tell them
            # apart. A public-GitHub row renders exactly as it did.
            display_repo(row.repo, row.host) if row.repo else "(unattributed)",
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
