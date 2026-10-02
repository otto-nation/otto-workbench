"""Tests for review.paths — review file paths, the shared walk of the reviews tree,
find_review_file, and review timestamps."""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from review.paths import (
    ReviewEntryKind, find_review_file,
    iter_review_entries, read_review_meta, review_file_path, stamp_reviewed,
)
import review.gc

# cr and reviews_dir are pytest fixtures: imported by name so tests here can request them.
from review_entry_support import cr, reviews_dir


# ── review_file_path ─────────────────────────────────────────────────────────


def test_review_file_basic(cr, reviews_dir):
    result = review_file_path("org/my-repo", "42")
    assert result == reviews_dir / "my-repo-42" / "review.md"


def test_review_file_repo_with_hyphens(cr, reviews_dir):
    result = review_file_path("org/my-cool-repo", "1")
    assert result == reviews_dir / "my-cool-repo-1" / "review.md"


def test_review_file_deep_nested_repo(cr, reviews_dir):
    result = review_file_path("deep/nested/repo", "7")
    assert result == reviews_dir / "repo-7" / "review.md"


# ── the shared walk of the reviews tree ──────────────────────────────────────

# 2021-06-01T00:00:00Z, as (atime, mtime) — old enough to be stale for every
# sweep, and a date a fallback timestamp can be asserted against.
OLD_MTIME = (1622505600, 1622505600)


def _seed_review(reviews_dir, name, repo="", pr_number="", **meta):
    """A review directory holding a deliverable and the meta.json attributing it.

    The directory name and the attribution are set separately on purpose: what
    a review is for comes from meta.json, and a test that spells both the same
    way cannot tell the two apart.
    """
    d = reviews_dir / name
    d.mkdir()
    (d / "review.md").write_text("## Summary\n")
    if repo or meta:
        (d / "meta.json").write_text(json.dumps(
            {"repo": repo, "pr_number": pr_number, **meta},
        ))
    return d


def test_walk_classifies_every_entry_at_the_root(cr, reviews_dir):
    _seed_review(reviews_dir, "widget-42", repo="acme/widget", pr_number="42")
    (reviews_dir / "widget-43").mkdir()
    (reviews_dir / "widget-43" / "pipeline.json").write_text("{}")
    (reviews_dir / "check_hunks.py").write_text("scratch")

    kinds = {e.path.name: e.kind for e in iter_review_entries(reviews_dir)}

    assert kinds == {
        "widget-42": ReviewEntryKind.REVIEW,
        "widget-43": ReviewEntryKind.ORPHAN,
        "check_hunks.py": ReviewEntryKind.STRAY,
    }


def test_walk_attributes_a_review_from_its_meta_not_its_name(cr, reviews_dir):
    _seed_review(reviews_dir, "some-other-name", repo="acme/widget", pr_number="42")

    entry = next(iter_review_entries(reviews_dir))

    assert entry.is_for("acme/widget", "42")
    assert not entry.is_for("acme/widget-ui", "42")


def test_walk_attributes_nothing_to_a_directory_with_no_meta(cr, reviews_dir):
    _seed_review(reviews_dir, "widget-42")

    assert not next(iter_review_entries(reviews_dir)).is_for("acme/widget", "42")


def test_a_stray_file_is_not_asked_for_a_deliverable(cr, reviews_dir):
    """`check_hunks.py/review.md` is a worse answer than a raise."""
    (reviews_dir / "check_hunks.py").write_text("scratch")

    entry = next(iter_review_entries(reviews_dir))

    with pytest.raises(ValueError):
        _ = entry.review_file


def test_walk_yields_nothing_for_a_root_that_does_not_exist(cr, tmp_path):
    """A machine that has never run a review is not an error."""
    assert list(iter_review_entries(tmp_path / "never-reviewed")) == []


def test_find_review_file_ignores_another_repos_same_prefixed_directory(cr, reviews_dir):
    """`widget-ui-42` starts with `widget`, and is not acme/widget's review."""
    _seed_review(reviews_dir, "widget-ui-42", repo="acme/widget-ui", pr_number="42")

    assert find_review_file("acme/widget", "42") is None


def test_find_review_file_reads_a_directory_its_name_does_not_advertise(cr, reviews_dir):
    """The prefix filter used to skip this directory before reading its meta."""
    d = _seed_review(reviews_dir, "reviewed-widget-42", repo="acme/widget", pr_number="42")

    assert find_review_file("acme/widget", "42") == d / "review.md"


def test_find_review_file_takes_the_canonical_path_first(cr, reviews_dir):
    canonical = _seed_review(reviews_dir, "widget-42", repo="acme/widget", pr_number="42")
    _seed_review(reviews_dir, "aardvark", repo="acme/widget", pr_number="42")

    assert find_review_file("acme/widget", "42") == canonical / "review.md"


def test_find_review_file_rejects_a_canonical_directory_another_repo_owns(cr, reviews_dir):
    """`acme/widget` and `other/widget` derive the same canonical directory."""
    _seed_review(reviews_dir, "widget-42", repo="other/widget", pr_number="42")

    assert find_review_file("acme/widget", "42") is None


def test_find_review_file_takes_a_canonical_directory_that_claims_no_repo(cr, reviews_dir):
    """A review from before meta.json carried attribution still resolves."""
    d = _seed_review(reviews_dir, "widget-42")

    assert find_review_file("acme/widget", "42") == d / "review.md"


def test_find_review_file_skips_a_directory_holding_no_review(cr, reviews_dir):
    d = reviews_dir / "elsewhere"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"repo": "acme/widget", "pr_number": "42"}))

    assert find_review_file("acme/widget", "42") is None


def test_gc_collects_strays_and_directories_in_one_pass(cr, reviews_dir):
    """Two sweeps became one walk — both still have to happen."""
    stray = reviews_dir / "check_hunks.py"
    stray.write_text("scratch")
    os.utime(str(stray), OLD_MTIME)
    orphan = reviews_dir / "widget-100"
    orphan.mkdir()
    (orphan / "pipeline.json").write_text("{}")
    os.utime(str(orphan / "pipeline.json"), OLD_MTIME)

    cleaned = review.gc.gc_reviews(reviews_dir)

    assert cleaned == 2
    assert not stray.exists()
    assert not orphan.exists()


# ── review timestamps ────────────────────────────────────────────────────────


def test_reviewed_at_falls_back_to_the_review_files_mtime(cr, reviews_dir):
    """A review written before the field existed still has to answer when."""
    d = _seed_review(reviews_dir, "widget-42", repo="acme/widget", pr_number="42")
    os.utime(str(d / "review.md"), OLD_MTIME)

    entry = next(iter_review_entries(reviews_dir))

    assert entry.meta.reviewed_at == ""
    assert entry.reviewed_at.startswith("2021-06-01T")


def test_reviewed_at_prefers_the_stamp_over_the_mtime(cr, reviews_dir):
    """The stamp survives a copy or a restore; the mtime is what those rewrite."""
    d = _seed_review(
        reviews_dir, "widget-42", repo="acme/widget", pr_number="42",
        reviewed_at="2026-08-18T14:02:11+00:00",
    )
    os.utime(str(d / "review.md"), OLD_MTIME)

    assert next(iter_review_entries(reviews_dir)).reviewed_at == "2026-08-18T14:02:11+00:00"


def test_reviewed_at_is_empty_when_there_is_no_review_to_date(cr, reviews_dir):
    (reviews_dir / "widget-42").mkdir()

    assert next(iter_review_entries(reviews_dir)).reviewed_at == ""


def test_started_at_is_reported_absent_rather_than_backfilled(cr, reviews_dir):
    _seed_review(reviews_dir, "widget-42", repo="acme/widget", pr_number="42")

    assert next(iter_review_entries(reviews_dir)).meta.started_at == ""


def test_stamp_reviewed_records_the_time_and_keeps_the_rest(cr, reviews_dir):
    d = _seed_review(reviews_dir, "widget-42", repo="acme/widget", pr_number="42")

    stamp_reviewed(d)

    meta = json.loads((d / "meta.json").read_text())
    assert meta["reviewed_at"]
    assert meta["repo"] == "acme/widget"
    assert next(iter_review_entries(reviews_dir)).reviewed_at == meta["reviewed_at"]


def test_stamp_reviewed_creates_nothing_without_a_sidecar(cr, reviews_dir):
    """No sidecar means no run recorded what it was reviewing — nothing to date."""
    d = reviews_dir / "widget-42"
    d.mkdir()

    stamp_reviewed(d)

    assert not (d / "meta.json").exists()


def test_stamp_reviewed_leaves_an_unreadable_sidecar_as_it_found_it(cr, reviews_dir):
    d = reviews_dir / "widget-42"
    d.mkdir()
    (d / "meta.json").write_text("{ truncated")

    stamp_reviewed(d)

    assert (d / "meta.json").read_text() == "{ truncated"


def test_stamp_reviewed_leaves_a_sidecar_that_is_not_an_object_alone(cr, reviews_dir):
    """A payload that parses but holds no record attributes nothing; writing a
    fresh sidecar over it would invent attribution the file never had."""
    d = reviews_dir / "widget-42"
    d.mkdir()
    (d / "meta.json").write_text("[]")

    stamp_reviewed(d)

    assert (d / "meta.json").read_text() == "[]"


def test_stamp_reviewed_keeps_the_delta_a_re_review_recorded(cr, reviews_dir):
    """The stamp is a read-modify-write of the whole record, so every field
    beside `reviewed_at` has to survive it — not only the ones a reader of the
    entry list happens to look at."""
    d = _seed_review(reviews_dir, "widget-42", repo="acme/widget", pr_number="42")
    (d / "meta.json").write_text(json.dumps({
        "repo": "acme/widget", "pr_number": 42, "review_type": "incremental",
        "prior_sha": "dead00", "delta_files": ["a.py", "b.py"],
    }))

    stamp_reviewed(d)

    meta = read_review_meta(d)
    assert meta.reviewed_at
    assert meta.prior_sha == "dead00"
    assert meta.delta_files == ("a.py", "b.py")
