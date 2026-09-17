"""Tests for the record authorising a retro to delete the reviews it consumed.

The property under test is that the permission to delete is bounded to the run
that earned it. A record is a file on disk between two processes, so anything
can pick it up: the incident that prompted these tests was a debug scan
overwriting the record with five review directories, four of them belonging to
branches the retro had nothing to do with. Nothing was deleted only because the
retro was never completed.

So each test here names one way the deletion can reach a review nobody meant to
delete — a foreign scan, an abandoned run, a review re-run since it was read, a
directory name that escapes the reviews root — and pins the refusal.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from retro.consumed import (  # noqa: E402
    ConsumedReview,
    ConsumeRecord,
    deletable,
    read_record,
    write_record,
)


def _review_dir(reviews: Path, name: str, reviewed_at: str = "") -> Path:
    """A review directory complete enough for the walk to classify it."""
    d = reviews / name
    d.mkdir(parents=True)
    (d / "review.md").write_text(f"# Self-Review: o/{name}\n")
    if reviewed_at:
        (d / "meta.json").write_text(f'{{"reviewed_at": "{reviewed_at}"}}')
    return d


# ── The record round-trips ───────────────────────────────────────────────────

def test_a_record_reads_back_as_it_was_written(tmp_path):
    path = tmp_path / "rec.json"
    record = ConsumeRecord(
        scan_id="abc123def456",
        scanned_at="2026-09-17T10:00:00Z",
        reviews=[ConsumedReview("repo-self-branch", "o/repo", "2026-09-17T09:00:00Z")],
    )
    write_record(record, path)

    assert read_record(path) == record


def test_a_missing_record_reads_as_none(tmp_path):
    assert read_record(tmp_path / "absent.json") is None


def test_an_unparseable_record_reads_as_none_rather_than_raising(tmp_path):
    """A damaged record must not fail the completion.

    It authorises deletion, so the safe reading of a file that cannot be
    understood is that it authorises none — not that the retro cannot finish.
    """
    path = tmp_path / "rec.json"
    path.write_text("{not json")

    assert read_record(path) is None


def test_a_record_is_written_even_when_it_consumed_nothing(tmp_path):
    """The empty record is the point, not an omission.

    A scan that reads no local reviews still has to displace the previous
    run's claim. Skipping the write on empty is what let an abandoned retro's
    list survive into a later run that scanned nothing of its own — the second
    path to the incident, and the one no amount of scan-ID checking closes.
    """
    path = tmp_path / "rec.json"
    write_record(ConsumeRecord(scan_id="old", reviews=[ConsumedReview("stale-review")]), path)

    write_record(ConsumeRecord(scan_id="new", reviews=[]), path)

    record = read_record(path)
    assert record.scan_id == "new"
    assert record.reviews == []


# ── What is safe to delete ───────────────────────────────────────────────────

def test_a_consumed_review_still_as_it_was_read_is_deletable(tmp_path):
    reviews = tmp_path / "reviews"
    _review_dir(reviews, "repo-self-branch", "2026-09-17T09:00:00+00:00")
    record = ConsumeRecord(
        scan_id="s",
        reviews=[ConsumedReview("repo-self-branch", "o/repo", "2026-09-17T09:00:00+00:00")],
    )

    targets, skipped = deletable(record, reviews)

    assert [t.name for t in targets] == ["repo-self-branch"]
    assert skipped == []


def test_a_review_re_run_since_the_scan_is_kept(tmp_path):
    """The directory name is the same review; the review is not.

    A re-review between Phase 1 and Phase 4 replaces the deliverable the retro
    analysed with one nobody has read. Deleting it discards a review on the
    strength of having read a different one.
    """
    reviews = tmp_path / "reviews"
    _review_dir(reviews, "repo-self-branch", "2026-09-17T18:00:00+00:00")
    record = ConsumeRecord(
        scan_id="s",
        reviews=[ConsumedReview("repo-self-branch", "o/repo", "2026-09-17T09:00:00+00:00")],
    )

    targets, skipped = deletable(record, reviews)

    assert targets == []
    assert any("re-reviewed" in s for s in skipped)


def test_a_review_already_gone_is_not_an_error(tmp_path):
    """Idempotence: a completion re-run after a partial crash finishes the job."""
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    record = ConsumeRecord(scan_id="s", reviews=[ConsumedReview("already-deleted")])

    targets, skipped = deletable(record, reviews)

    assert targets == []
    assert any("already gone" in s for s in skipped)


def test_a_name_that_escapes_the_reviews_root_is_refused(tmp_path):
    """This list feeds a recursive delete.

    Nothing legitimate writes a separator into a directory name, so a name
    carrying one is a bug — and the blast radius of that bug is every path the
    traversal can reach. It is contained here rather than trusted upstream.
    """
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    record = ConsumeRecord(
        scan_id="s",
        reviews=[
            ConsumedReview("../outside"),
            ConsumedReview(".."),
            ConsumedReview("nested/dir"),
            ConsumedReview(""),
        ],
    )

    targets, skipped = deletable(record, reviews)

    assert targets == []
    assert len(skipped) == 4
    assert outside.is_dir()


def test_a_review_with_no_recorded_timestamp_is_still_deletable(tmp_path):
    """A review predating the sidecar has no fingerprint to compare.

    Refusing it would leak every such review forever, since nothing else
    collects a pre-PR self-review. The name match is all there is, and it is
    what the old behaviour used for every entry.
    """
    reviews = tmp_path / "reviews"
    _review_dir(reviews, "old-review")
    record = ConsumeRecord(scan_id="s", reviews=[ConsumedReview("old-review", "", "")])

    targets, skipped = deletable(record, reviews)

    assert [t.name for t in targets] == ["old-review"]
    assert skipped == []
