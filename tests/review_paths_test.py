"""Tests for review.paths — the review directory's layout and its archives."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.paths
from config.workbench_config import ReviewConfig
from review.paths import (
    ARCHIVE_META_EXT, ARCHIVE_RECORD_EXT, archive_review, archived_file,
    archived_session_usage, archived_stamps, archives_dir, review_artifact_path,
)

DEFAULT_KEEP = ReviewConfig().archive_keep


# ── review_artifact_path ─────────────────────────────────────────────────────


def test_artifact_path_sits_beside_the_review(tmp_path):
    review_file = tmp_path / "reviews" / "repo-42" / "review.md"
    assert review_artifact_path(str(review_file), "meta.json") == str(
        review_file.parent / "meta.json"
    )


# ── self_review_file_path ────────────────────────────────────────────────────


def test_self_review_file_path_matches_the_layout_review_entry_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    p = review.paths.self_review_file_path("otto-nation/otto-workbench", "isaac/feat/x")
    assert p == tmp_path / "reviews" / "otto-workbench-self-isaac-feat-x" / "review.md"


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

    assert len(list(archive_dir.glob("2*.md"))) <= DEFAULT_KEEP
    assert len(list(archive_dir.glob("2*.session.jsonl"))) <= DEFAULT_KEEP


def test_archive_prunes_even_with_nothing_to_rotate(tmp_path):
    """A keep count already exceeded is enforced by the run that reads it."""
    review_dir = tmp_path / "reviews" / "test-repo-2"
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir(parents=True)
    for i in range(1, 6):
        (archive_dir / f"2025010{i}-120000.md").write_text(f"archive {i}")

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    assert len(list(archive_dir.glob("2*.md"))) == DEFAULT_KEEP


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


# ── archiving the structured record ─────────────────────────────────────────


def _run_dir(tmp_path, name="test-repo-70"):
    review_dir = tmp_path / "reviews" / name
    review_dir.mkdir(parents=True)
    (review_dir / "review.md").write_text("review")
    (review_dir / "review.json").write_text('{"schema_version": 1}')
    (review_dir / "meta.json").write_text('{"head_sha": "abc"}')
    return review_dir


def test_archive_copies_the_record_and_sidecar_under_the_runs_stamp(tmp_path):
    review_dir = _run_dir(tmp_path)

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    stamp, = archived_stamps(review_dir)
    assert archived_file(review_dir, stamp, ARCHIVE_RECORD_EXT).read_text() == '{"schema_version": 1}'
    assert archived_file(review_dir, stamp, ARCHIVE_META_EXT).read_text() == '{"head_sha": "abc"}'


def test_archive_leaves_the_record_for_archive_prior_to_move(tmp_path):
    """`archive_prior` runs next and moves review.json to prior.json; a move
    here would leave it nothing to move and the re-review no predecessor."""
    review_dir = _run_dir(tmp_path)

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    assert (review_dir / "review.json").is_file()
    assert (review_dir / "meta.json").is_file()


def test_a_stale_directory_archives_no_sidecar_without_a_review(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-71"
    review_dir.mkdir(parents=True)
    (review_dir / "meta.json").write_text("{}")

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    assert not list(archives_dir(review_dir).glob(f"2*{ARCHIVE_META_EXT}"))


def test_each_archived_kind_is_pruned_on_its_own_count(tmp_path):
    """`.record.json` and `.meta.json` share the `.json` ending; pruning one
    kind must never count or delete the other."""
    review_dir = _run_dir(tmp_path, "test-repo-72")
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir()
    for i in range(1, 6):
        for suffix in (".md", ARCHIVE_RECORD_EXT, ARCHIVE_META_EXT):
            (archive_dir / f"2025010{i}-120000{suffix}").write_text("old")

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    for suffix in (".md", ARCHIVE_RECORD_EXT, ARCHIVE_META_EXT):
        assert len(list(archive_dir.glob(f"2*{suffix}"))) == DEFAULT_KEEP, suffix


def test_the_keep_count_comes_from_config(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "config.yml").write_text("review:\n  archive_keep: 1\n")
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_root))
    review_dir = tmp_path / "reviews" / "test-repo-73"
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir(parents=True)
    for i in range(1, 4):
        (archive_dir / f"2025010{i}-120000.md").write_text("old")

    archive_review(review_dir / "review.md", str(review_dir / "session.jsonl"))

    assert [p.name for p in archive_dir.glob("2*.md")] == ["20250103-120000.md"]


def test_archived_stamps_are_newest_first_and_name_only_runs_with_a_review(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-74"
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir(parents=True)
    (archive_dir / "20250101-120000.md").write_text("a")
    (archive_dir / "20250102-120000.md").write_text("b")
    (archive_dir / "20250103-120000.session.jsonl").write_text("orphan log")

    assert archived_stamps(review_dir) == ["20250102-120000", "20250101-120000"]


def test_archived_stamps_of_a_directory_never_archived_is_empty(tmp_path):
    assert archived_stamps(tmp_path / "reviews" / "never") == []


def test_archived_session_usage_reads_that_runs_logs_only(tmp_path):
    review_dir = tmp_path / "reviews" / "test-repo-75"
    archive_dir = archives_dir(review_dir)
    archive_dir.mkdir(parents=True)
    result = '{"type": "result", "total_cost_usd": 0.5, "usage": {"input_tokens": 7}}\n'
    (archive_dir / "20250101-120000.session.jsonl").write_text(result)
    (archive_dir / "20250102-120000.session.jsonl").write_text(
        '{"type": "result", "total_cost_usd": 9.0}\n')

    usage = archived_session_usage(review_dir, "20250101-120000")

    assert usage.cost == 0.5
