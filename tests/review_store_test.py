"""The store: review.json, meta.json and review.md written as one, and the prior round's copies."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from dataclasses import replace

from pr.domains import ReviewStatus, ReviewVerdict
from review.document import ReviewDocument
from review.ingest import ingest_review
from review.paths import FILENAME_PRIOR_META, FILENAME_PRIOR_RECORD, FILENAME_RECORD
from review.render import BANNER
from review.store import archive_prior, clear_prior, read_prior, read_review, update_review, write_review

from review_record_support import fixture_meta, fixture_text


def _stored(tmp_path, case="synthesis_pr"):
    review_file = tmp_path / "review.md"
    write_review(review_file, ingest_review(fixture_text(case, "input.md")), fixture_meta(case))
    return review_file


class TestTheStore:
    def test_all_three_files_are_written_together(self, tmp_path):
        review_file = _stored(tmp_path)
        assert (tmp_path / FILENAME_RECORD).is_file() and (tmp_path / "meta.json").is_file()
        assert BANNER in review_file.read_text()

    def test_reading_back_returns_record_and_meta(self, tmp_path):
        stored = read_review(_stored(tmp_path))
        assert stored.meta.status is ReviewStatus.PARTIAL
        assert stored.record.verdict.startswith("**Approve.**")

    def test_a_directory_without_review_json_is_stale(self, tmp_path):
        (tmp_path / "review.md").write_text("# old\n## Summary\nx\n")
        assert read_review(tmp_path / "review.md") is None

    def test_an_update_rewrites_the_render(self, tmp_path):
        review_file = _stored(tmp_path)
        update_review(review_file, lambda r: replace(r, verdict="changed"), status=ReviewStatus.ERROR)
        text = review_file.read_text()
        assert "changed" in text and "<!-- status: error -->" in text

    def test_updating_a_stale_directory_does_nothing(self, tmp_path):
        assert update_review(tmp_path / "review.md") is None
        assert not (tmp_path / "review.md").exists()


class TestThePriorRound:
    def test_archiving_moves_the_record_and_copies_its_sidecar(self, tmp_path):
        _stored(tmp_path)
        archive_prior(tmp_path)
        prior = read_prior(tmp_path)
        assert prior.meta.head_sha == "2cb613ec084559a9590f074eabde546c44b284a3"
        assert [f.id for f in prior.record.findings] == ["S1", "S2", "N1", "N2", "I1"]
        assert not (tmp_path / FILENAME_RECORD).exists()

    def test_archiving_a_stale_directory_leaves_no_prior(self, tmp_path):
        (tmp_path / FILENAME_PRIOR_RECORD).write_text("{}")
        (tmp_path / "review.md").write_text("# old\n")
        archive_prior(tmp_path)
        assert read_prior(tmp_path) is None and not (tmp_path / FILENAME_PRIOR_RECORD).exists()

    def test_clearing_removes_both_copies(self, tmp_path):
        _stored(tmp_path)
        archive_prior(tmp_path)
        clear_prior(tmp_path)
        assert not (tmp_path / FILENAME_PRIOR_RECORD).exists()
        assert not (tmp_path / FILENAME_PRIOR_META).exists()


class TestARecordBackedDocument:
    def test_the_read_api_answers_from_the_record(self, tmp_path):
        doc = ReviewDocument.from_stored(read_review(_stored(tmp_path)))
        assert [f.id for f in doc.findings] == ["S1", "S2", "N1", "N2", "I1"]
        assert doc.open_counts == {"M": 0, "S": 2, "N": 2, "I": 1}
        assert doc.verdict is ReviewVerdict.APPROVE
        assert doc.header.status is ReviewStatus.PARTIAL
        assert doc.section("Summary").startswith("Adds speech-to-text")
        assert doc.title.startswith("# Review: acme/widget#38")

    def test_open_findings_leave_out_the_fixed(self, tmp_path):
        doc = ReviewDocument.from_stored(read_review(_stored(tmp_path, "self_incremental_fixed")))
        assert [f.id for f in doc.open_findings] == ["S1", "N2"]

    def test_a_record_backed_document_cannot_render_or_write(self, tmp_path):
        doc = ReviewDocument.from_stored(read_review(_stored(tmp_path)))
        with pytest.raises(TypeError, match="write_review"):
            doc.render()
        with pytest.raises(TypeError, match="write_review"):
            doc.write(tmp_path / "out.md")
