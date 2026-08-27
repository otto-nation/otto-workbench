"""Tests for the review document's metadata header.

The header has three writers and only one of them is this module: the pipeline
and `review-rebuild` render it, and on the synthesis and single-agent paths the
review agent writes its own from prose in a template. So the tests come in two
halves — what `render` puts on disk, and what `parse` and `set_status` make of a
header they did not write.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from pr_domains import ReviewStatus
from review_document import ReviewHeader, set_status
from review_types import ReviewType


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
