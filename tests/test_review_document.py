"""Tests for the review document — its title, its metadata header, and the
frame that holds them above the body.

The header has three writers and only one of them is this module: the pipeline
and `review-rebuild` render it, and on the synthesis and single-agent paths the
review agent writes its own from prose in a template. So the header tests come
in two halves — what `render` and `from_meta` put on disk, and what `parse` and
`set_status` make of a header they did not write. `ReviewDocument` is tested
against the same split: what it renders for a document being built, and what it
makes of one it is handed.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from agent_types import Mode
from pr_domains import ReviewStatus
from review_document import ReviewDocument, ReviewHeader, review_title, set_status
from review_types import ReviewMeta, ReviewType


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


class TestParse:
    def test_render_round_trips(self):
        header = ReviewHeader(
            date="2026-08-27", head_sha="abc123",
            review_type=ReviewType.INCREMENTAL,
            prior_sha="def456", prior_date="2026-08-20", delta_files=3,
            skipped_groups=2, total_groups=7,
            status=ReviewStatus.PARTIAL, generator_version="2.0.0",
        )
        assert ReviewHeader.parse(header.render()) == header

    def test_an_agent_written_header_parses_out_of_order(self):
        # What synthesis.md asks the agent for: a subset, in its own order,
        # embedded in a document rather than rendered as a block.
        text = (
            "# Review: acme/widget#42 — title\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "<!-- date: 2026-08-27 -->\n"
            "\n## Summary\n"
        )
        header = ReviewHeader.parse(text)
        assert header.head_sha == "abc123"
        assert header.date == "2026-08-27"
        assert header.generator_version == "2.0.0"
        assert header.review_type is ReviewType.FULL

    def test_a_document_with_no_header_parses_to_defaults(self):
        assert ReviewHeader.parse("## Summary\nnothing here\n") == ReviewHeader()

    def test_the_first_occurrence_of_a_key_wins(self):
        text = "<!-- head_sha: first -->\n<!-- head_sha: second -->\n"
        assert ReviewHeader.parse(text).head_sha == "first"

    def test_an_unrecognised_review_type_reads_as_full(self):
        assert ReviewHeader.parse(
            "<!-- review_type: sideways -->",
        ).review_type is ReviewType.FULL

    def test_a_garbled_count_reads_as_absent_and_the_rest_survives(self):
        header = ReviewHeader.parse(
            "<!-- head_sha: abc -->\n"
            "<!-- delta_files: many -->\n"
            "<!-- skipped_groups: some/of/them -->\n"
        )
        assert header.head_sha == "abc"
        assert header.delta_files is None
        assert header.skipped_groups == 0
        assert header.total_groups == 0

    def test_an_unrecognised_status_reads_as_absent(self):
        assert ReviewHeader.parse("<!-- status: pending -->").status is None


class TestSetStatus:
    def test_a_stale_status_is_replaced(self):
        content = (
            "<!-- head_sha: abc -->\n"
            "<!-- status: completed -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )
        updated = set_status(content, ReviewStatus.PARTIAL)
        assert "<!-- status: partial -->" in updated
        assert "<!-- status: completed -->" not in updated

    def test_a_header_with_no_status_gains_one_above_the_generator(self):
        content = "<!-- head_sha: abc -->\n<!-- generator: 2.0.0 -->\n\n## Summary\n"
        assert set_status(content, ReviewStatus.ERROR) == (
            "<!-- head_sha: abc -->\n"
            "<!-- status: error -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )

    def test_a_header_with_neither_gains_one_above_the_first_heading(self):
        content = "# Review: acme/widget#42\n<!-- head_sha: abc -->\n\n## Summary\n"
        updated = set_status(content, ReviewStatus.PARTIAL)
        assert updated.endswith("<!-- status: partial -->\n\n## Summary\n")

    def test_the_keys_the_editor_was_not_told_about_survive(self):
        content = (
            "<!-- head_sha: abc -->\n"
            "<!-- an_agent_invention: keep me -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )
        assert "<!-- an_agent_invention: keep me -->" in set_status(
            content, ReviewStatus.PARTIAL,
        )


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


class TestDocumentRender:
    def test_the_frame_goes_above_the_body_in_order(self):
        document = ReviewDocument(
            title="# Review: acme/widget#42",
            header=ReviewHeader(date="2026-08-27", head_sha="abc123"),
            body="## Summary\nnothing to report\n",
        )
        assert document.render() == (
            "# Review: acme/widget#42\n"
            "<!-- date: 2026-08-27 -->\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- review_type: full -->\n"
            "\n"
            "## Summary\nnothing to report\n"
        )

    def test_an_untitled_document_opens_with_its_header(self):
        rendered = ReviewDocument(body="## Summary\n").render()
        assert rendered.startswith("<!-- review_type: full -->\n")

    def test_write_puts_the_rendered_document_on_disk(self, tmp_path):
        document = ReviewDocument(title="# Review: acme/widget#42", body="## Summary\n")
        path = tmp_path / "review.md"
        document.write(path)
        assert path.read_text() == document.render()


class TestDocumentParse:
    def test_render_round_trips(self):
        document = ReviewDocument(
            title="# Review: acme/widget#42 — add caching",
            header=ReviewHeader(
                date="2026-08-27", head_sha="abc123",
                review_type=ReviewType.INCREMENTAL,
                prior_sha="def456", prior_date="2026-08-20", delta_files=3,
                skipped_groups=2, total_groups=7,
                status=ReviewStatus.PARTIAL, generator_version="2.0.0",
            ),
            body="## Summary\nfindings below\n",
        )
        assert ReviewDocument.parse(document.render()) == document

    def test_an_agent_written_document_splits_where_the_prose_ends(self):
        text = (
            "# Review: acme/widget#42 — add caching\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- date: 2026-08-27 -->\n"
            "\n"
            "## Summary\nthe body\n"
        )
        document = ReviewDocument.parse(text)
        assert document.title == "# Review: acme/widget#42 — add caching"
        assert document.header.head_sha == "abc123"
        assert document.body == "## Summary\nthe body\n"

    def test_a_document_with_no_title_is_all_header_and_body(self):
        document = ReviewDocument.parse("<!-- head_sha: abc -->\n\n## Summary\n")
        assert document.title == ""
        assert document.header.head_sha == "abc"
        assert document.body == "## Summary\n"

    def test_a_metadata_comment_further_down_stays_in_the_body(self):
        """A finding's stable ID is a comment of the same shape. Hoisting one
        into the header would move it in the document rendering what was
        parsed."""
        text = (
            "# Review: acme/widget#42\n"
            "<!-- head_sha: abc -->\n"
            "\n"
            "## Must fix\n"
            "- **[M1]** <!-- sid: a1b2c3 --> something\n"
        )
        document = ReviewDocument.parse(text)
        assert "sid: a1b2c3" in document.body
        assert document.header == ReviewHeader(head_sha="abc")

    def test_a_titled_document_stating_no_metadata_keeps_its_title(self):
        document = ReviewDocument.parse("# Review: acme/widget#42\n\n## Summary\n")
        assert document.title == "# Review: acme/widget#42"
        assert document.header == ReviewHeader()
        assert document.body == "## Summary\n"

    def test_a_bare_body_parses_to_a_document_with_no_frame(self):
        document = ReviewDocument.parse("## Summary\nnothing here\n")
        assert document.title == ""
        assert document.header == ReviewHeader()
        assert document.body == "## Summary\nnothing here\n"
