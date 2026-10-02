"""Tests for the review document — its title, its metadata header, and the
frame that holds them above the body.

The header has three writers and only one of them is this module: the pipeline
and `review-rebuild` render it, and on the synthesis and single-agent paths the
review agent writes its own from prose in a template. So the header tests come
in two halves — what `render` and `from_meta` put on disk, and what `parse`,
`set_status`, `set_meta` and `stamp_header` make of a header they did not
write.
`ReviewDocument` is tested against the same split: what it renders for a
document being built, and what it makes of one it is handed, down to its
sections. The findings it reads are in review_document_findings_test.py and the
call it reaches in review_document_verdict_test.py.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from core.phases import Mode
from pr.domains import ReviewStatus
from review.document import (
    MetaKey, ReviewDocument, ReviewHeader,
    review_title, section_span, set_meta, set_section, set_status,
    set_title, stamp_header, strip_sections,
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


class TestSetMetaOnTheHeadSha:
    """The harness' SHA over whatever the review agent typed.

    The marker is the point the next re-review measures its delta from, so
    these cover the ways an agent's header can differ from the harness': a
    wrong SHA, no marker at all, and — the one that matters — a wrong marker
    the parser would otherwise reach first. One key's worth of cases against
    the primitive every key goes through.
    """

    @staticmethod
    def _sha(content: str, head_sha: str) -> str:
        return set_meta(content, MetaKey.HEAD_SHA, head_sha)

    def test_a_wrong_sha_is_replaced(self):
        content = (
            "<!-- date: 2026-01-01 -->\n"
            "<!-- head_sha: deadbeef -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )
        updated = self._sha(content, "abc123")
        assert "<!-- head_sha: abc123 -->" in updated
        assert "deadbeef" not in updated

    def test_a_header_with_no_marker_gains_one_above_the_generator(self):
        content = "<!-- date: 2026-01-01 -->\n<!-- generator: 2.0.0 -->\n\n## Summary\n"
        assert self._sha(content, "abc123") == (
            "<!-- date: 2026-01-01 -->\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )

    def test_a_document_with_neither_gains_one_above_the_first_heading(self):
        content = "# Review: acme/widget#42\n<!-- date: 2026-01-01 -->\n\n## Summary\n"
        updated = self._sha(content, "abc123")
        assert updated.endswith("<!-- head_sha: abc123 -->\n\n## Summary\n")

    def test_the_stamped_sha_is_the_one_the_parser_reads(self):
        content = (
            "<!-- head_sha: deadbeef -->\n"
            "\n## Summary\n"
            "An agent that mentioned <!-- head_sha: cafe --> further down.\n"
        )
        assert ReviewHeader.parse(self._sha(content, "abc123")).head_sha == "abc123"

    def test_stamping_a_sha_that_is_already_there_changes_nothing(self):
        content = "<!-- head_sha: abc123 -->\n<!-- generator: 2.0.0 -->\n\n## Summary\n"
        assert self._sha(content, "abc123") == content

    def test_the_keys_the_editor_was_not_told_about_survive(self):
        content = (
            "<!-- head_sha: deadbeef -->\n"
            "<!-- an_agent_invention: keep me -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )
        assert "<!-- an_agent_invention: keep me -->" in self._sha(content, "abc123")

    def test_a_value_carrying_a_backslash_escape_is_written_literally(self):
        """The SHA is substituted in, not interpreted as a replacement template.

        No commit SHA contains a backslash, so this guards the editor rather
        than a case the pipeline can reach: `re.sub` reads `\\1` and `\\g<n>` in
        a string replacement, and a header writer that expanded them would
        corrupt the line it was asked to correct.
        """
        content = "<!-- head_sha: deadbeef -->\n\n## Summary\n"
        assert r"<!-- head_sha: \1abc -->" in self._sha(content, r"\1abc")


class TestSetMeta:
    """The primitive under every header edit, on the cases its callers share.

    Where an inserted key *lands* is what these pin. A stamp writes the header
    one key at a time, so without a canonical position the block would come
    out ordered by the accident of which keys the agent happened to write — a
    document that differs from a rendered one in nothing but arrangement.
    """

    def test_an_inserted_key_takes_its_canonical_position(self):
        content = "<!-- date: 2026-01-01 -->\n<!-- generator: 2.0.0 -->\n\n## Summary\n"
        assert set_meta(content, MetaKey.REVIEW_TYPE, "incremental") == (
            "<!-- date: 2026-01-01 -->\n"
            "<!-- review_type: incremental -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )

    def test_a_key_after_every_one_present_lands_at_the_end_of_the_block(self):
        content = "<!-- date: 2026-01-01 -->\n\n## Summary\n"
        assert set_meta(content, MetaKey.GENERATOR, "2.0.0") == (
            "<!-- date: 2026-01-01 -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )

    def test_a_key_before_every_one_present_lands_at_the_top(self):
        content = "<!-- generator: 2.0.0 -->\n\n## Summary\n"
        assert set_meta(content, MetaKey.DATE, "2026-01-01") == (
            "<!-- date: 2026-01-01 -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )

    def test_a_marker_quoted_in_the_body_does_not_attract_the_insertion(self):
        """A finding can quote a metadata comment; this module's own docs do.

        Keyed off the body's marker, the insertion would put a header line
        inside a finding — where `parse` would still find it, so the document
        would read correctly and look corrupt.
        """
        content = (
            "<!-- date: 2026-01-01 -->\n"
            "\n## Summary\n"
            "The agent wrote <!-- status: completed --> into its prose.\n"
        )
        updated = set_meta(content, MetaKey.GENERATOR, "2.0.0")
        assert updated.startswith(
            "<!-- date: 2026-01-01 -->\n<!-- generator: 2.0.0 -->\n\n## Summary\n"
        )

    def test_a_marker_quoted_in_the_body_is_not_edited_in_place_of_the_header(self):
        """The quotation is prose. Rewriting it is two failures at once.

        The header still does not state the key — so the run's claim is
        missing — and a finding now says something its author did not write.
        A whole-document search finds the body's marker first whenever the
        header has none, which is exactly when the stamp is needed.
        """
        content = (
            "<!-- date: 2026-01-01 -->\n"
            "\n## Must fix\n"
            "- **[M1]** `a.py:1` — it writes <!-- status: completed --> too early.\n"
        )

        updated = set_meta(content, MetaKey.STATUS, "partial")

        assert "it writes <!-- status: completed --> too early." in updated
        assert ReviewHeader.parse(updated).status is ReviewStatus.PARTIAL

    def test_a_document_that_is_only_a_body_gains_a_header_above_it(self):
        assert set_meta("## Summary\nbody\n", MetaKey.DATE, "2026-01-01") == (
            "<!-- date: 2026-01-01 -->\n\n## Summary\nbody\n"
        )

    def test_a_header_whose_last_marker_ends_the_text_still_gains_a_line(self):
        assert set_meta("<!-- date: 2026-01-01 -->", MetaKey.GENERATOR, "2.0.0") == (
            "<!-- date: 2026-01-01 -->\n<!-- generator: 2.0.0 -->\n"
        )


class TestStampHeader:
    """What the harness writes over an agent's header when the run is done."""

    def test_every_key_the_run_states_is_written(self):
        content = "# Review: acme/widget#42\n<!-- date: YYYY-MM-DD -->\n\n## Summary\n"
        header = ReviewHeader(
            date="2026-01-02", mode=Mode.PR, pr_number=42,
            head_sha="abc123", head_ref="feat", base_ref="main",
            review_type=ReviewType.INCREMENTAL, prior_sha="0ld",
            generator_version="review 9.9.9",
        )

        stamped = stamp_header(content, header)

        assert ReviewHeader.parse(stamped) == header

    def test_a_placeholder_the_agent_never_filled_is_replaced(self):
        content = "<!-- date: YYYY-MM-DD -->\n\n## Summary\n"

        stamped = stamp_header(content, ReviewHeader(date="2026-01-02"))

        assert "YYYY-MM-DD" not in stamped
        assert "<!-- date: 2026-01-02 -->" in stamped

    def test_a_key_the_run_has_nothing_to_say_about_is_left_alone(self):
        """Stamping is the harness correcting the record, not erasing it.

        A full review states no `prior_sha`, and a header that dropped
        whatever was there would be making a claim by omission.
        """
        content = "<!-- prior_sha: 0ld -->\n<!-- generator: 2.0.0 -->\n\n## Summary\n"

        stamped = stamp_header(content, ReviewHeader(date="2026-01-02"))

        assert "<!-- prior_sha: 0ld -->" in stamped

    def test_the_keys_the_stamp_was_not_told_about_survive(self):
        content = (
            "<!-- an_agent_invention: keep me -->\n"
            "<!-- generator: 2.0.0 -->\n"
            "\n## Summary\n"
        )

        stamped = stamp_header(content, ReviewHeader(date="2026-01-02"))

        assert "<!-- an_agent_invention: keep me -->" in stamped

    def test_a_document_with_no_header_gains_the_whole_block(self):
        content = "# Review: acme/widget#42\n\n## Summary\n"
        header = ReviewHeader(date="2026-01-02", head_sha="abc123")

        stamped = stamp_header(content, header)

        assert stamped == (
            "# Review: acme/widget#42\n\n"
            "<!-- date: 2026-01-02 -->\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- review_type: full -->\n"
            "\n## Summary\n"
        )

    def test_a_stamped_header_reads_back_in_the_order_render_writes(self):
        """One document, however it was assembled.

        A stamp and a render are the two ways a header reaches disk, and a
        reader diffing two reviews should not see the difference between them.
        """
        header = ReviewHeader(
            date="2026-01-02", mode=Mode.SELF, head_sha="abc123",
            base_ref="main", generator_version="review 9.9.9",
        )

        stamped = stamp_header("<!-- head_sha: wrong -->\n\n## Summary\n", header)

        assert stamped == f"{header.render()}\n## Summary\n"


class TestSetTitle:
    def test_the_title_on_disk_is_replaced(self):
        content = "# Review: guessed/repo#1\n<!-- date: 2026-01-02 -->\n"

        assert set_title(content, "# Review: acme/widget#42") == (
            "# Review: acme/widget#42\n<!-- date: 2026-01-02 -->\n"
        )

    def test_a_document_that_opens_with_no_title_gains_one(self):
        assert set_title("<!-- date: 2026-01-02 -->\n", "# Review: acme/widget#42") == (
            "# Review: acme/widget#42\n<!-- date: 2026-01-02 -->\n"
        )

    def test_a_heading_further_down_is_not_mistaken_for_the_title(self):
        content = "<!-- date: 2026-01-02 -->\n\n## Summary\n# Not a title\n"

        assert set_title(content, "# Review: acme/widget#42").endswith(
            "## Summary\n# Not a title\n"
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


class TestSectionSpan:
    def test_the_span_excludes_the_heading_and_stops_at_the_next_one(self):
        text = "## Summary\nfirst\n\n## Verdict\nApprove\n"
        assert section_span(text, "Summary").body_of(text) == "\nfirst\n\n"

    def test_the_last_section_runs_to_the_end_of_the_text(self):
        text = "## Summary\nfirst\n\n## Verdict\nApprove\n"
        span = section_span(text, "Verdict")
        assert span.body_of(text) == "\nApprove\n"
        assert span.end == len(text)

    def test_what_falls_outside_the_span_is_what_an_edit_puts_back(self):
        """The offsets are the contract, not just the slice between them — an
        in-place edit rewrites the body and leaves both sides untouched."""
        text = "## Summary\nfirst\n\n## Verdict\nApprove\n"
        span = section_span(text, "Summary")
        assert text[:span.start] == "## Summary"
        assert text[span.end:] == "## Verdict\nApprove\n"

    def test_a_section_the_text_does_not_carry_has_no_span(self):
        assert section_span("## Summary\nfirst\n", "Verdict") is None

    def test_headers_match_however_the_writer_capitalised_them(self):
        """The review agent writes its own headings, so `## Must Fix` and
        `## Must fix` name the same section."""
        text = "## Must Fix\n- **[M1]** a.py:1 — bug\n"
        assert section_span(text, "Must fix").body_of(text).strip() == "- **[M1]** a.py:1 — bug"

    def test_a_heading_carrying_more_than_the_header_is_a_different_section(self):
        assert section_span("## Verdict and rationale\nApprove\n", "Verdict") is None

    def test_the_heading_offset_names_the_whole_section(self):
        """What an edit that replaces a section has to slice out — the body
        offsets alone leave the old heading behind."""
        text = "## Summary\nfirst\n\n## Verdict\nApprove\n"
        span = section_span(text, "Verdict")
        assert text[span.heading_start:span.end] == "## Verdict\nApprove\n"


class TestSetSection:
    def test_a_section_already_there_is_replaced_where_it_stands(self):
        text = "## Summary\n\nold\n\n## Verdict\n\nApprove\n"
        assert set_section(text, "Summary", "new") == "## Summary\n\nnew\n\n## Verdict\n\nApprove\n"

    def test_a_new_section_goes_above_its_anchor(self):
        text = "## Summary\n\nthe prose\n\n## Verdict\n\nApprove\n"
        assert set_section(text, "Static Analysis", "clean", before="Verdict") == (
            "## Summary\n\nthe prose\n\n## Static Analysis\n\nclean\n\n## Verdict\n\nApprove\n"
        )

    def test_a_document_missing_the_anchor_still_gets_the_section(self):
        """A `--no-synthesis` run reaches here with no Summary to sit above, and
        the section it asked for is the report of why — dropping it is the one
        outcome the caller cannot mean."""
        text = "## Must fix\n\n- **[M1]** a.py:1 — bug\n"
        assert set_section(text, "Agent Failures", "one failed", before="Summary") == (
            "## Must fix\n\n- **[M1]** a.py:1 — bug\n\n## Agent Failures\n\none failed\n"
        )

    def test_an_empty_body_removes_the_section(self):
        text = "## Summary\n\nthe prose\n\n## Agent Failures\n\none failed\n\n## Verdict\n\nApprove\n"
        assert set_section(text, "Agent Failures", "") == (
            "## Summary\n\nthe prose\n\n## Verdict\n\nApprove\n"
        )

    def test_an_empty_body_for_a_section_that_is_not_there_changes_nothing(self):
        text = "## Summary\n\nthe prose\n"
        assert set_section(text, "Agent Failures", "", before="Summary") == text

    def test_the_caller_states_the_body_and_this_states_the_heading(self):
        assert set_section("", "Verdict", "  Approve  ") == "## Verdict\n\nApprove\n"


class TestStripSections:
    def test_the_named_section_goes_heading_and_all(self):
        text = "## Summary\nprose\n## Prior findings\n- **[M1]** a.py — Fixed\n## Verdict\nApprove\n"
        assert strip_sections(text, ["Prior findings"]) == (
            "## Summary\nprose\n## Verdict\nApprove\n"
        )

    def test_a_section_the_document_does_not_carry_changes_nothing(self):
        text = "## Summary\nprose\n"
        assert strip_sections(text, ["Prior findings"]) == text

    def test_every_occurrence_goes_not_only_the_first(self):
        """Group outputs are concatenated before the merge runs, so one
        heading appears once per group."""
        text = (
            "## File Triage\nfirst\n## Must fix\n- **[M1]** a.py — bug\n"
            "## File Triage\nsecond\n"
        )
        assert strip_sections(text, ["File Triage"]) == "## Must fix\n- **[M1]** a.py — bug\n"

    def test_the_heading_is_matched_without_regard_to_case(self):
        text = "## Summary\nprose\n## PRIOR FINDINGS\n- **[M1]** a.py — Fixed\n"
        assert strip_sections(text, ["Prior findings"]) == "## Summary\nprose\n"

    def test_several_headers_go_in_one_pass(self):
        text = "## File Triage\nt\n## Must fix\n- **[M1]** a.py — bug\n## Prior findings\np\n"
        assert strip_sections(text, ["File Triage", "Prior findings"]) == (
            "## Must fix\n- **[M1]** a.py — bug\n"
        )


class TestSection:
    def test_a_section_reads_back_stripped(self):
        document = ReviewDocument.parse("## Summary\n\nthe prose\n\n## Verdict\nApprove\n")
        assert document.section("Summary") == "the prose"

    def test_a_section_the_document_does_not_carry_is_empty(self):
        assert ReviewDocument.parse("## Summary\nprose\n").section("Verdict") == ""

    def test_the_metadata_header_is_not_part_of_any_section(self):
        """Read off the body, so the frame above it cannot be mistaken for the
        first section's contents."""
        text = (
            "# Review: acme/widget#42\n"
            "<!-- head_sha: abc -->\n"
            "\n"
            "## Summary\nthe prose\n"
        )
        assert ReviewDocument.parse(text).section("Summary") == "the prose"


class TestRead:
    def test_a_document_on_disk_reads_back_parsed(self, tmp_path):
        review = _write(tmp_path, "# Review: acme/widget#42\n<!-- head_sha: abc -->\n\n## Summary\nx\n")
        document = ReviewDocument.read(review)
        assert document is not None
        assert document.header.head_sha == "abc"
        assert document.section("Summary") == "x"

    def test_a_review_nobody_wrote_is_not_an_empty_one(self, tmp_path):
        """The distinction every caller of `read` turns on: absent is None, and
        an empty file is a document that declares nothing."""
        assert ReviewDocument.read(tmp_path / "nonexistent.md") is None
        assert ReviewDocument.read(None) is None
        assert ReviewDocument.read(_write(tmp_path, "")) == ReviewDocument()

    def test_a_path_that_is_not_a_file_has_no_document(self, tmp_path):
        assert ReviewDocument.read(tmp_path) is None

    def test_a_path_given_as_a_string_reads_the_same(self, tmp_path):
        review = _write(tmp_path, "## Summary\nx\n")
        assert ReviewDocument.read(str(review)) == ReviewDocument.read(review)
