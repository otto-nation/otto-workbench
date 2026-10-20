"""Tests for the review document — its title, its metadata header, and the
read view of a stored review.

`ReviewHeader.render` and `from_meta` write the header from the sidecar.
`covers` and `review_title` are the other two remaining answers. Findings and
verdicts are tested in review_document_findings_test.py and
review_document_verdict_test.py against the record-backed document.
"""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from core.phases import Mode
from pr.domains import ReviewStatus
from review.document import (
    ReviewDocument, ReviewHeader, covers, review_title,
)
from review.types import ReviewMeta, ReviewType

from review_document_support import _write


class TestRender:
    def test_a_full_review_states_only_what_it_knows(self):
        rendered = ReviewHeader(date="2026-08-27", head_sha="abc123").render()
        assert rendered == (
            "<!-- date: 2026-08-27 -->\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- review_type: full -->\n"
        )

    def test_review_type_is_stated_even_at_its_default(self):
        assert "<!-- review_type: full -->" in ReviewHeader().render()

    def test_an_incremental_review_states_what_it_is_a_delta_against(self):
        rendered = ReviewHeader(
            date="2026-08-27", head_sha="abc123",
            review_type=ReviewType.INCREMENTAL,
            prior_sha="def456", prior_date="2026-08-20", delta_files=3,
        ).render()
        assert "<!-- review_type: incremental -->" in rendered
        assert "<!-- prior_sha: def456 -->" in rendered
        assert "<!-- prior_date: 2026-08-20 -->" in rendered
        assert "<!-- delta_files: 3 -->" in rendered

    def test_a_group_count_of_zero_skipped_is_still_reported(self):
        rendered = ReviewHeader(skipped_groups=0, total_groups=5).render()
        assert "<!-- skipped_groups: 0/5 -->" in rendered

    def test_no_group_count_writes_no_ratio(self):
        assert "skipped_groups" not in ReviewHeader(skipped_groups=2).render()

    def test_status_and_generator_are_written_when_set(self):
        rendered = ReviewHeader(
            status=ReviewStatus.PARTIAL, generator_version="2.0.0",
        ).render()
        assert "<!-- status: partial -->" in rendered
        assert "<!-- generator: 2.0.0 -->" in rendered

    def test_a_run_still_in_flight_states_no_status(self):
        assert "status" not in ReviewHeader(head_sha="abc").render()


class TestFromMeta:
    def test_the_header_repeats_what_the_sidecar_attributes(self):
        meta = ReviewMeta(head_sha="abc123", generator_version="2.0.0")
        header = ReviewHeader.from_meta(meta)
        assert (header.head_sha, header.generator_version) == ("abc123", "2.0.0")

    def test_an_incremental_sidecar_carries_the_delta_over(self):
        meta = ReviewMeta(
            head_sha="abc123", review_type=ReviewType.INCREMENTAL,
            prior_sha="def456", delta_files=("a.py", "b.py"),
        )
        header = ReviewHeader.from_meta(meta)
        assert header.review_type == ReviewType.INCREMENTAL
        assert (header.prior_sha, header.delta_files) == ("def456", 2)

    def test_a_full_review_states_no_delta_count(self):
        """`0` would read as a delta that moved nothing, which is a different
        claim from a review that is a delta against nothing."""
        assert ReviewHeader.from_meta(ReviewMeta()).delta_files is None

    def test_a_sidecar_stating_no_review_type_reads_as_full(self):
        assert ReviewHeader.from_meta(ReviewMeta()).review_type == ReviewType.FULL

    def test_overrides_win_over_the_sidecar(self):
        header = ReviewHeader.from_meta(
            ReviewMeta(head_sha="abc123"), date="2026-08-27", head_sha="override",
        )
        assert (header.date, header.head_sha) == ("2026-08-27", "override")


class TestReviewTitle:
    def test_a_pr_review_names_the_pr_and_its_subject(self):
        meta = ReviewMeta(repo="acme/widget", pr_number=42, title="add caching")
        assert review_title(meta) == "# Review: acme/widget#42 — add caching"

    def test_a_pr_with_no_recorded_subject_is_named_by_number_alone(self):
        meta = ReviewMeta(repo="acme/widget", pr_number=42)
        assert review_title(meta) == "# Review: acme/widget#42"

    def test_a_sidecar_numbering_no_pr_is_named_by_its_repository(self):
        """`#None` is a number the review does not have."""
        assert review_title(ReviewMeta(repo="acme/widget")) == "# Review: acme/widget"

    def test_a_self_review_is_named_by_the_branch_it_covers(self):
        meta = ReviewMeta(repo="acme/widget", head_ref="feat/caching", mode=Mode.SELF)
        assert review_title(meta) == "# Self-Review: acme/widget — feat/caching"

    def test_a_self_review_off_an_unnamed_branch_says_so(self):
        meta = ReviewMeta(repo="acme/widget", mode=Mode.SELF)
        assert review_title(meta) == "# Self-Review: acme/widget — unknown"

    def test_an_enterprise_review_names_the_instance(self):
        """Two instances can serve one slug; the title has to say which."""
        meta = ReviewMeta(repo="acme/widget", pr_number=42, host="ghe.acme.com")
        assert review_title(meta) == "# Review: ghe.acme.com/acme/widget#42"

    def test_an_enterprise_self_review_names_it_too(self):
        meta = ReviewMeta(repo="acme/widget", head_ref="feat/x", mode=Mode.SELF,
                          host="ghe.acme.com")
        assert review_title(meta) == (
            "# Self-Review: ghe.acme.com/acme/widget — feat/x")

    # passes-at-base: asserts the rendering the change was careful not to move
    def test_a_public_host_renders_the_bare_slug(self):
        meta = ReviewMeta(repo="acme/widget", pr_number=42, host="github.com")
        assert review_title(meta) == "# Review: acme/widget#42"


class TestRead:
    def test_a_document_on_disk_reads_back_parsed(self, tmp_path):
        from review.types import ReviewMeta
        from review_record_support import store_review
        review = store_review(
            tmp_path / "review.md", "## Summary\nx\n", ReviewMeta(head_sha="abc"),
        )
        document = ReviewDocument.read(review)
        assert document is not None
        assert document.header.head_sha == "abc"
        assert document.section("Summary") == "x"

    def test_a_review_nobody_wrote_is_not_an_empty_one(self, tmp_path):
        """The distinction every caller of `read` turns on: absent and stale
        are None, including a bare review.md with no review.json."""
        assert ReviewDocument.read(tmp_path / "nonexistent.md") is None
        assert ReviewDocument.read(None) is None
        assert ReviewDocument.read(_write(tmp_path, "")) is None

    def test_a_path_that_is_not_a_file_has_no_document(self, tmp_path):
        assert ReviewDocument.read(tmp_path) is None

    def test_a_path_given_as_a_string_reads_the_same(self, tmp_path):
        from review_record_support import store_review
        review = store_review(tmp_path / "review.md", "## Summary\nx\n")
        assert ReviewDocument.read(str(review)) == ReviewDocument.read(review)


class TestCovers:
    """Whether a review whose header states one SHA covers another.

    Every caller that asks whether a review is current — batch's plan, the
    review preflight and auto-recover, and the delta collector — asks it here,
    so none can disagree.
    """

    FULL = "abcdef0123456789abcdef0123456789abcdef01"

    @pytest.mark.parametrize("reviewed,sha,expected", [
        (FULL, FULL, True),
        ("abcdef0", FULL, True),
        ("abc123", "abc123", True),
        ("abcdef", FULL, False),
        ("a", FULL, False),
        ("", FULL, False),
        ("", "", False),
        (FULL, "", False),
        ("1111111", FULL, False),
        (FULL, "abcdef0", False),
    ])
    def test_table(self, reviewed, sha, expected):
        assert covers(reviewed, sha) is expected
