"""The store: review.json, meta.json and review.md written as one, and the prior round's copies."""

import re
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from dataclasses import replace

from pr.domains import ReviewStatus, ReviewVerdict
from review.document import ReviewDocument
from review.ingest import IngestRejected, ingest_review
from review.paths import FILENAME_META, FILENAME_PRIOR_META, FILENAME_PRIOR_RECORD, FILENAME_RECORD
from review.render import BANNER
from review.store import archive_prior, clear_prior, read_prior, read_review, update_review, write_review
from review.types import ReviewMeta, SEVERITIES

from review_record_support import FIXTURES, fixture_meta, fixture_text

_INPUTS = ("input.md", "raw.md", "before.md", "group-1.md", "group-2.md")
# Fixture inputs ingest must refuse. The parity test below skips these; the
# dedicated reject test asserts each still raises, so a silent skip cannot drop.
_EXPECTED_REJECT: tuple[tuple[str, str], ...] = ()


def _all_fixture_inputs() -> list[tuple[str, str]]:
    return [
        (case_dir.name, src)
        for case_dir in sorted(p for p in FIXTURES.iterdir() if p.is_dir())
        for src in _INPUTS
        if (case_dir / src).is_file()
    ]


def _fixture_inputs():
    reject = set(_EXPECTED_REJECT)
    return [pair for pair in _all_fixture_inputs() if pair not in reject]


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

    def test_rendered_review_md_mode_matches_write_text(self, tmp_path):
        review_file = _stored(tmp_path)
        probe = tmp_path / "probe.md"
        probe.write_text("probe\n")
        assert review_file.stat().st_mode == probe.stat().st_mode

    def test_write_review_does_not_call_umask(self, tmp_path, monkeypatch):
        def _umask(_mask):
            raise AssertionError("os.umask must not be called")

        monkeypatch.setattr("review.store.os.umask", _umask)
        _stored(tmp_path)

    def test_a_render_failure_does_not_write_the_record(self, tmp_path, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("render failed")

        monkeypatch.setattr("review.store.render_review", _boom)
        with pytest.raises(RuntimeError, match="render failed"):
            write_review(
                tmp_path / "review.md",
                ingest_review(fixture_text("synthesis_pr", "input.md")),
                fixture_meta("synthesis_pr"),
            )
        assert not (tmp_path / FILENAME_RECORD).exists()
        assert not (tmp_path / FILENAME_META).exists()
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

    def test_archiving_without_meta_does_not_keep_an_older_prior_sidecar(self, tmp_path):
        _stored(tmp_path)
        archive_prior(tmp_path)
        write_review(
            tmp_path / "review.md",
            ingest_review(fixture_text("synthesis_pr", "input.md")),
            fixture_meta("synthesis_pr"),
        )
        (tmp_path / FILENAME_META).unlink()
        archive_prior(tmp_path)
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

    def test_expected_reject_inputs_are_rejected(self):
        actual = []
        for case, src in _all_fixture_inputs():
            try:
                ingest_review((FIXTURES / case / src).read_text())
            except IngestRejected:
                actual.append((case, src))
        assert set(actual) == set(_EXPECTED_REJECT)
        for case, src in _EXPECTED_REJECT:
            with pytest.raises(IngestRejected):
                ingest_review((FIXTURES / case / src).read_text())

    @pytest.mark.parametrize("case,src", list(_fixture_inputs()))
    def test_from_stored_matches_parse_of_the_rendered_review(self, tmp_path, case, src):
        record = ingest_review((FIXTURES / case / src).read_text())
        meta = fixture_meta(case) if (FIXTURES / case / "meta.json").is_file() else ReviewMeta()
        review_file = tmp_path / "review.md"
        write_review(review_file, record, meta)
        stored = ReviewDocument.from_stored(read_review(review_file))
        parsed = ReviewDocument.parse(review_file.read_text())
        assert stored.findings == parsed.findings
        assert stored.open_findings == parsed.open_findings
        assert stored.open_counts == parsed.open_counts
        assert stored.verdict == parsed.verdict
        assert stored.header == parsed.header
        severity = {s.section.lower() for s in SEVERITIES}
        for heading in re.findall(r"^## (.+)$", review_file.read_text(), re.MULTILINE):
            if heading.strip().lower() in severity:
                continue
            assert stored.section(heading) == parsed.section(heading), heading

    def test_section_on_a_severity_heading_raises(self, tmp_path):
        doc = ReviewDocument.from_stored(read_review(_stored(tmp_path)))
        for heading in ("Must fix", "Should fix", "Nit", "Idioms"):
            with pytest.raises(ValueError, match=re.escape(heading)) as caught:
                doc.section(heading)
            assert ".findings" in str(caught.value)
