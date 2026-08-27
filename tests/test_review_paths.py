"""Tests for review_paths — the review directory's layout and its archives."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_paths import (
    ARCHIVE_KEEP_COUNT, archive_review, archives_dir, review_artifact_path,
)


# ── review_artifact_path ─────────────────────────────────────────────────────


def test_artifact_path_sits_beside_the_review(tmp_path):
    review_file = tmp_path / "reviews" / "repo-42" / "review.md"
    assert review_artifact_path(str(review_file), "meta.json") == str(
        review_file.parent / "meta.json"
    )


# ── archive_review ───────────────────────────────────────────────────────────


def test_archive_creates_prior_and_timestamped_archive(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-42"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("old review")
    session_log.write_text("old session")

    prior_path = archive_review(review_file, str(session_log))

    assert os.path.isfile(prior_path)
    assert prior_path.endswith("prior.md")
    assert Path(prior_path).read_text() == "old review"
    assert not review_file.exists()
    assert not session_log.exists()
    archives = list(archives_dir(review_dir).glob("2*.md"))
    assert len(archives) == 1


def test_archive_stamps_every_file_of_one_run_alike(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-7"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("review")
    session_log.write_text("session")
    (review_dir / "post.jsonl").write_text("post")

    archive_review(review_file, str(session_log))

    archived = sorted(archives_dir(review_dir).iterdir())
    stamps = {name.split(".")[0] for name in (p.name for p in archived)}
    assert len(archived) == 3
    assert len(stamps) == 1


def test_archive_no_existing_review_empty_prior(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-99"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"

    prior_path = archive_review(review_file, str(session_log))
    assert prior_path == ""


def test_archive_prunes_old_archives(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-1"
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir(parents=True)

    for i in range(1, 6):
        (archive_dir / f"2025010{i}-120000.md").write_text(f"archive {i}")
        (archive_dir / f"2025010{i}-120000.session.jsonl").write_text(f"session {i}")

    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("current review")
    session_log.write_text("current session")

    archive_review(review_file, str(session_log))

    assert len(list(archive_dir.glob("2*.md"))) <= ARCHIVE_KEEP_COUNT
    assert len(list(archive_dir.glob("2*.session.jsonl"))) <= ARCHIVE_KEEP_COUNT


def test_archive_prunes_even_with_nothing_to_rotate(tmp_path):
    """A keep count already exceeded is enforced by the run that reads it."""
    review_dir = tmp_path / "reviews" / "test-repo-2"
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir(parents=True)
    for i in range(1, 6):
        (archive_dir / f"2025010{i}-120000.md").write_text(f"archive {i}")

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    assert len(list(archive_dir.glob("2*.md"))) == ARCHIVE_KEEP_COUNT


def test_archive_intermediates_untouched(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-50"
    review_dir.mkdir(parents=True)
    (review_dir / "group-1.jsonl").write_text("group1")
    (review_dir / "group-1.md").write_text("group1md")
    (review_dir / "meta.json").write_text("meta")

    review_file = review_dir / "review.md"
    session_log = review_dir / "session.jsonl"

    prior_path = archive_review(review_file, str(session_log))

    assert prior_path == ""
    assert (review_dir / "group-1.jsonl").exists()
    assert (review_dir / "group-1.md").exists()
    assert (review_dir / "meta.json").exists()


def test_archive_post_jsonl(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-60"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "review.md"
    review_file.write_text("review")
    (review_dir / "post.jsonl").write_text("post data")

    archive_review(review_file, str(review_dir / "session.jsonl"))

    assert not (review_dir / "post.jsonl").exists()
    post_archives = list(archives_dir(review_dir).glob("2*.post.jsonl"))
    assert len(post_archives) == 1


def test_archive_self_review_paths(tmp_path):
    review_dir = tmp_path / "project" / "ignore" / "reviews"
    review_dir.mkdir(parents=True)
    review_file = review_dir / "self-review.md"
    session_log = review_dir / "session.jsonl"
    review_file.write_text("self-review content")
    session_log.write_text("session data")

    prior_path = archive_review(review_file, str(session_log))

    assert os.path.isfile(prior_path)
    assert "prior.md" in prior_path
    assert Path(prior_path).read_text() == "self-review content"
    assert not review_file.exists()
    assert not session_log.exists()
