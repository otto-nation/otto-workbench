"""Tests for review.rebuild: finding the group files and refusing what it cannot rebuild."""

import sys

from conftest import REPO_ROOT

LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import review.rebuild  # noqa: E402
from agent.registry import PHASES  # noqa: E402
from core.phases import Phase  # noqa: E402
from review.paths import ReviewMeta, write_review_meta  # noqa: E402


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
