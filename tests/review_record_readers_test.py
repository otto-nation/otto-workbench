"""Every reader answers from review.json and meta.json; a directory without the record is stale."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import core.log
from review.document import ReviewDocument
from review.paths import FILENAME_RECORD
from review.record import load_record
from review.recover import read_review_sha
from review.sections import ReviewSections

from review_record_support import fixture_meta, fixture_text, store_review


def test_a_bare_review_md_is_stale(tmp_path):
    (tmp_path / "review.md").write_text(fixture_text("synthesis_pr", "input.md"))
    assert ReviewDocument.read(tmp_path / "review.md") is None
    assert read_review_sha(tmp_path / "review.md") == ""


def test_the_head_sha_is_meta_json_s(tmp_path):
    path = store_review(tmp_path / "review.md", fixture_text("synthesis_pr", "input.md"), fixture_meta("synthesis_pr"))
    assert read_review_sha(path) == "2cb613ec084559a9590f074eabde546c44b284a3"


def test_editing_review_md_changes_nothing_a_reader_sees(tmp_path):
    path = store_review(tmp_path / "review.md", fixture_text("synthesis_pr", "input.md"), fixture_meta("synthesis_pr"))
    path.write_text(path.read_text().replace("**[S1]**", "**[M9]**"))
    assert [f.id for f in ReviewDocument.read(path).findings][0] == "S1"


def test_posting_sections_come_from_the_record(tmp_path):
    path = store_review(tmp_path / "review.md", fixture_text("synthesis_pr", "input.md"), fixture_meta("synthesis_pr"))
    sections = ReviewSections.from_record(ReviewDocument.read(path).record)
    assert sections.get("summary").startswith("Adds speech-to-text")
    assert sections.get("verdict").startswith("**Approve.**")
    assert "disprove" in sections.get("agent_failures")


def test_batch_plan_calls_a_stale_self_review_stale(tmp_path):
    import batch.plan
    (tmp_path / "review.md").write_text("# old\n")
    need = batch.plan.review_need(tmp_path / "review.md", "abc")
    assert need.needed and "predates review.json" in need.reason


def test_a_record_missing_schema_version_loads_as_none(tmp_path):
    (tmp_path / FILENAME_RECORD).write_text(json.dumps({"summary": "x"}))
    assert load_record(tmp_path) is None


def test_undecodable_json_loads_as_none_without_a_schema_warning(tmp_path):
    (tmp_path / FILENAME_RECORD).write_text("{not json")
    warned = []
    with patch.object(core.log, "warn", side_effect=warned.append):
        assert load_record(tmp_path) is None
    assert warned
    assert any("could not" in msg for msg in warned)
    assert not any("schema None" in msg for msg in warned)


def test_retro_skips_a_directory_without_review_json(tmp_path):
    from retro.reviews import scan_local_reviews

    reviews_dir = tmp_path / "reviews" / "myrepo-self-1"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "review.md").write_text(
        "# Self-Review: myrepo — branch\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- [ ] **[M1]** `src/main.go` — Missing error handling\n"
    )
    scan = scan_local_reviews(tmp_path / "reviews", [], {})
    assert scan.consumed == []
    assert scan.repos == []
