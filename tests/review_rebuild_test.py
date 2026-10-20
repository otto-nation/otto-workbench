"""Tests for review.rebuild: finding the group files and refusing what it cannot rebuild."""

import sys

import pytest
from conftest import REPO_ROOT

LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import review.rebuild  # noqa: E402
from agent.registry import PHASES  # noqa: E402
from core.phases import Phase  # noqa: E402
from review.paths import ReviewMeta, read_review_meta, write_review_meta  # noqa: E402


def _group(review_dir, n):
    path = review_dir / PHASES[Phase.GROUP].output_filename.format(n)
    path.write_text("## Findings\n")
    return str(path)


def test_discover_group_files_stops_at_the_first_gap(tmp_path):
    first, second = _group(tmp_path, 1), _group(tmp_path, 2)
    _group(tmp_path, 4)
    assert review.rebuild.discover_group_files(tmp_path) == [first, second]


def test_discover_group_files_is_empty_without_group_one(tmp_path):
    _group(tmp_path, 2)
    assert review.rebuild.discover_group_files(tmp_path) == []


def test_rebuild_refuses_a_directory_with_no_group_files(tmp_path, capsys):
    assert review.rebuild.rebuild(tmp_path, "7", script="review-rebuild") == 1
    assert "No group finding files" in capsys.readouterr().err


def test_rebuild_refuses_a_sidecar_that_names_no_repo(tmp_path, capsys):
    _group(tmp_path, 1)
    write_review_meta(tmp_path, ReviewMeta())
    assert review.rebuild.rebuild(tmp_path, "7", script="review-rebuild") == 1
    assert "Cannot determine repository" in capsys.readouterr().err
    assert not (tmp_path / "review.md").exists()


def test_rebuild_refuses_a_group_file_carrying_the_render_banner(tmp_path):
    from review.ingest import IngestRejected
    from review.paths import FILENAME_RECORD
    from review.render import BANNER
    path = tmp_path / PHASES[Phase.GROUP].output_filename.format(1)
    path.write_text(f"{BANNER}\n## Findings\n- **[M1]** **`a.py:1`** \u2014 x\n")
    write_review_meta(tmp_path, ReviewMeta(repo="acme/widget", pr_number=1))
    with pytest.raises(IngestRejected):
        review.rebuild.rebuild(tmp_path, "1", script="review-rebuild")
    assert not (tmp_path / FILENAME_RECORD).exists()


def test_rebuild_preserves_sidecar_fields_other_than_date_and_status(tmp_path):
    from datetime import date
    from pr.domains import ReviewStatus

    path = tmp_path / PHASES[Phase.GROUP].output_filename.format(1)
    path.write_text("## Nit\n- **[N1]** **`a.py:1`** \u2014 x\n")
    original = ReviewMeta(
        repo="acme/widget", pr_number=1,
        head_sha="abc123def", generator_version="9.9",
        started_at="2026-01-01T00:00:00+00:00",
        reviewed_at="2026-01-02T00:00:00+00:00",
        fix_commit_sha="def456abc", fix_commit_status="unpushed",
        static_declined={"site-hash": "won't fix"},
        date="2020-01-01", status=ReviewStatus.COMPLETED,
    )
    write_review_meta(tmp_path, original)
    assert review.rebuild.rebuild(tmp_path, "1", script="review-rebuild") == 0
    meta = read_review_meta(tmp_path)
    assert meta.head_sha == original.head_sha
    assert meta.generator_version == original.generator_version
    assert meta.started_at == original.started_at
    assert meta.reviewed_at == original.reviewed_at
    assert meta.fix_commit_sha == original.fix_commit_sha
    assert meta.fix_commit_status == original.fix_commit_status
    assert meta.static_declined == original.static_declined
    assert meta.date == date.today().isoformat()
    assert meta.status is None
