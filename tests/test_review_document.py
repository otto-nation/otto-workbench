"""Tests for the review document — its title, its metadata header, and the
frame that holds them above the body.

The header has three writers and only one of them is this module: the pipeline
and `review-rebuild` render it, and on the synthesis and single-agent paths the
review agent writes its own from prose in a template. So the header tests come
in two halves — what `render` and `from_meta` put on disk, and what `parse` and
`set_status` make of a header they did not write. `ReviewDocument` is tested
against the same split: what it renders for a document being built, and what it
makes of one it is handed.

The readers are the second half of that: what a document handed back says about
its sections, its findings and the call it reached. Absent and empty are the
distinction they turn on — a review nobody wrote reaches no verdict, while one
written with nothing in it approves.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import pytest
from agent_types import Mode
from pr_domains import ReviewStatus, ReviewVerdict
from review_document import (
    ReviewDocument, ReviewHeader,
    review_title, section_span, set_section, set_status,
    strip_sections,
)
from review_grammar import (
    FINDING_ID_RE, _extract_body_text, _FIRST_FILE_RE, finding_location,
    parse_finding_line,
)
from review_types import Finding, ReviewMeta, ReviewType
from review_verdict import (
    CLEAN_VERDICT, MECHANICAL_NOTE,
    build_mechanical_body, counts_prose, mechanical_verdict,
    open_counts, resolve_review_verdict, states_verdict, verdict_from_counts,
)


def _write(tmp_path, body: str) -> Path:
    review = tmp_path / "review.md"
    review.write_text(body)
    return review


def _findings(text: str) -> list[Finding]:
    """The findings a review's text declares, read the way every reader does."""
    return ReviewDocument.parse(text).findings


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


class TestOpenCounts:
    def test_every_severity_is_counted(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — description\n"
            "- **[M2]** path:2 — description\n"
            "## Should fix\n"
            "- **[S1]** path:3 — description\n"
        )
        assert document.open_counts == {"M": 2, "S": 1, "N": 0, "I": 0}

    def test_a_resolved_finding_is_no_longer_counted(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — active\n"
            "- ~~**[M2]** path:2 — resolved~~\n"
        )
        assert document.open_counts["M"] == 1

    def test_the_fix_passes_checkbox_does_not_hide_a_finding(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- [ ] **[M1]** path:1 — with checkbox\n"
            "- **[M2]** path:2 — without checkbox\n"
        )
        assert document.open_counts["M"] == 2

    def test_a_finding_the_fix_pass_ticked_off_is_not_open(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- [x] **[M1]** path:1 — fixed\n"
            "- [ ] **[M2]** path:2 — still open\n"
        )
        assert [f.id for f in document.open_findings] == ["M2"]
        assert document.open_counts["M"] == 1

    def test_a_declined_finding_is_still_open(self):
        """Wider than the fix pass's predicate: the review judged this one, so
        it is not work, but nothing fixed it either."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — *(declined — by design)*\n"
        )
        assert [f.id for f in document.open_findings] == ["M1"]
        assert document.open_counts["M"] == 1

    def test_an_indented_finding_is_counted(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "  - **[M1]** path:1 — indented under something\n"
        )
        assert document.open_counts["M"] == 1

    def test_a_finding_line_outside_a_severity_section_is_not_counted(self):
        """The prior-findings ledger declares nothing — it reports on the last
        review, and counting it inflated every tally taken over the whole body."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — bug\n"
            "## Prior findings\n"
            "- **[M1]** `old.go` — Fixed\n"
            "- **[S1]** `old.go` — Fixed\n"
        )
        assert document.open_counts == {"M": 1, "S": 0, "N": 0, "I": 0}

    def test_two_findings_sharing_an_id_are_two_findings(self):
        """A tally over the parse counts declarations, not distinct IDs: a
        duplicate is a merge bug to see, not one to hide."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** a.py:1 — one\n"
            "- **[M1]** b.py:2 — two\n"
        )
        assert document.open_counts["M"] == 2

    def test_a_document_declaring_nothing_is_zeroed_not_empty(self):
        """Callers index the result directly, so every key must be present."""
        assert ReviewDocument().open_counts == {"M": 0, "S": 0, "N": 0, "I": 0}

    def test_a_review_that_was_never_written_counts_as_one_that_found_nothing(self):
        """The reader that has no separate answer for absent, unlike the
        verdict below."""
        assert open_counts(None) == {"M": 0, "S": 0, "N": 0, "I": 0}
        document = ReviewDocument.parse("## Must fix\n- **[M1]** a.py:1 — bug\n")
        assert open_counts(document) == document.open_counts

    def test_a_reference_to_a_finding_is_not_a_second_finding(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — bug\n"
            "  - see [M1] above\n"
        )
        assert document.open_counts["M"] == 1


class TestFindingLocation:
    def test_bold_backtick_with_line_number(self):
        loc = finding_location("**`pkg/handler.go:42`**")
        assert (loc.path, loc.line, loc.end_line) == ("pkg/handler.go", 42, None)

    def test_bold_backtick_with_line_range(self):
        loc = finding_location("**`pkg/handler.go:10-20`**")
        assert (loc.path, loc.line, loc.end_line) == ("pkg/handler.go", 10, 20)

    def test_bold_only_format(self):
        loc = finding_location("**pkg/handler.go:5**")
        assert (loc.path, loc.line, loc.end_line) == ("pkg/handler.go", 5, None)

    def test_backtick_only_format(self):
        loc = finding_location("`pkg/handler.go:99`")
        assert (loc.path, loc.line, loc.end_line) == ("pkg/handler.go", 99, None)

    def test_no_line_number(self):
        loc = finding_location("**`handler.go`**")
        assert (loc.path, loc.line, loc.end_line) == ("handler.go", None, None)

    def test_no_path_match_names_nothing(self):
        loc = finding_location("some random text")
        assert not loc.named
        assert (loc.path, loc.line, loc.end_line) == ("", None, None)

    def test_a_named_location_reads_as_named(self):
        assert finding_location("**`handler.go`**").named

    def test_en_dash_range_separator(self):
        loc = finding_location("**`file.go:10–15`**")
        assert (loc.path, loc.line, loc.end_line) == ("file.go", 10, 15)

    def test_parenthesized_route_group(self):
        loc = finding_location(
            "**`ui-consumer/src/app/(authenticated)/rewards/page.tsx:186`**"
        )
        assert (loc.path, loc.line) == (
            "ui-consumer/src/app/(authenticated)/rewards/page.tsx", 186,
        )

    def test_nested_parenthesized_groups(self):
        loc = finding_location("**`app/(auth)/(dashboard)/page.tsx`**")
        assert loc.path == "app/(auth)/(dashboard)/page.tsx"

    def test_extensionless_script_path(self):
        """Bin scripts have no extension; a pathless finding never reconciles."""
        loc = finding_location("`ai/claude/bin/ci-check:777`")
        assert (loc.path, loc.line, loc.end_line) == ("ai/claude/bin/ci-check", 777, None)

    def test_extensionless_script_path_with_range(self):
        loc = finding_location("**`git/hooks/pre-push-workbench:124-131`**")
        assert (loc.path, loc.line, loc.end_line) == ("git/hooks/pre-push-workbench", 124, 131)

    def test_slashless_code_span_is_not_a_path(self):
        """A bare identifier in a code span is prose, not an extensionless path."""
        assert not finding_location("`session_log` defaults to empty").named

    def test_path_with_space(self):
        loc = finding_location("**`src/my notes.py`**")
        assert (loc.path, loc.line, loc.end_line) == ("src/my notes.py", None, None)

    def test_path_with_space_and_line_number(self):
        loc = finding_location("**`src/my notes.py:42`**")
        assert (loc.path, loc.line, loc.end_line) == ("src/my notes.py", 42, None)

    def test_non_ascii_path(self):
        loc = finding_location("**`src/café.py:7`**")
        assert (loc.path, loc.line, loc.end_line) == ("src/café.py", 7, None)

    def test_space_and_non_ascii_path_with_line_range(self):
        loc = finding_location("**`src/café brûlé.py:12-18`**")
        assert (loc.path, loc.line, loc.end_line) == ("src/café brûlé.py", 12, 18)

    def test_spaced_prose_span_is_not_a_path(self):
        """Admitting spaces must not let a sentence read as a filename."""
        assert not finding_location("**the retry loop never terminates**").named

    def test_spaced_prose_span_with_a_version_number_is_not_a_path(self):
        """A dotted token mid-sentence is not the extension of a spaced path."""
        assert not finding_location("**the fix lands in v2.0 of the tool**").named

    def test_escaped_underscores_in_dunder(self):
        loc = finding_location(r"**`scripts/\_\_main\_\_.py:10`**")
        assert (loc.path, loc.line) == ("scripts/__main__.py", 10)

    def test_escaped_underscores_in_bold(self):
        loc = finding_location(r"**scripts/\_\_main\_\_.py:10**")
        assert (loc.path, loc.line) == ("scripts/__main__.py", 10)

    def test_no_escapes_unchanged(self):
        loc = finding_location("**`scripts/__main__.py:10`**")
        assert (loc.path, loc.line) == ("scripts/__main__.py", 10)


class TestFindingIdRegex:
    def test_with_stable_id_comment(self):
        m = FINDING_ID_RE.match("- **[M1]** <!-- sid:abc12345 --> **`file.go:10`** — body")
        assert m is not None
        assert (m.group(2), m.group(3)) == ("M", "1")

    def test_checkbox_and_strikethrough_combined(self):
        m = FINDING_ID_RE.match("- [ ] ~~**[S1]** **`file.go:5`** — Fix~~")
        assert m is not None
        assert (m.group(1), m.group(2), m.group(3)) == (" ", "S", "1")

    def test_double_digit_seq(self):
        m = FINDING_ID_RE.match("- **[M12]** **`file.go:10`** — body")
        assert m is not None
        assert (m.group(2), m.group(3)) == ("M", "12")

    def test_checked_checkbox(self):
        m = FINDING_ID_RE.match("- [x] **[M1]** **`file.go:10`** — body")
        assert m is not None
        assert (m.group(1), m.group(2)) == ("x", "M")

    def test_no_checkbox(self):
        m = FINDING_ID_RE.match("- **[M1]** **`file.go:10`** — body")
        assert m is not None
        assert m.group(1) is None
        assert m.group(2) == "M"


class TestFirstFileRegex:
    def test_hidden_file_not_matched_at_start(self):
        # Leading dot is not in the regex character class — hidden files
        # are only matched when preceded by a directory component
        assert _FIRST_FILE_RE.match(".gitignore") is None

    def test_hidden_file_in_directory(self):
        m = _FIRST_FILE_RE.match("config/.gitignore")
        assert m is not None
        assert m.group(1) == "config/.gitignore"

    def test_file_with_dots_in_path(self):
        m = _FIRST_FILE_RE.match("v1.2.3/file.go")
        assert m is not None
        assert m.group(1) == "v1.2.3/file.go"

    def test_path_with_parentheses(self):
        m = _FIRST_FILE_RE.match("(auth)/page.tsx")
        assert m is not None
        assert m.group(1) == "(auth)/page.tsx"


class TestExtractBodyText:
    def test_em_dash_separator(self):
        assert _extract_body_text("prefix — the body text") == "the body text"

    def test_double_dash_fallback(self):
        assert _extract_body_text("prefix -- the body text") == "the body text"

    def test_hyphen_fallback(self):
        assert _extract_body_text("prefix - the body text") == "the body text"

    def test_no_separator_returns_empty(self):
        assert _extract_body_text("no separator here") == ""

    def test_em_dash_takes_precedence(self):
        assert _extract_body_text("a -- b — c") == "c"


class TestParseFindingLine:
    def test_standard_must_fix(self):
        f = parse_finding_line("- **[M1]** **`handler.go:42`** — Fix this bug")
        assert (f.id, f.severity, f.seq, f.path, f.line, f.body) == (
            "M1", "M", 1, "handler.go", 42, "Fix this bug",
        )

    def test_should_fix_with_line_range(self):
        f = parse_finding_line("- **[S3]** **`pkg/auth.go:10-20`** — Refactor")
        assert (f.id, f.severity, f.seq, f.line, f.end_line) == ("S3", "S", 3, 10, 20)

    def test_nit_finding(self):
        f = parse_finding_line("- **[N2]** **`file.go:5`** — Nit text")
        assert (f.severity, f.seq) == ("N", 2)

    def test_idiom_finding(self):
        f = parse_finding_line("- **[I1]** **`file.go:5`** — Use pattern")
        assert (f.severity, f.seq) == ("I", 1)

    def test_checkbox_variant(self):
        f = parse_finding_line("- [ ] **[S1]** **`file.go:5`** — Fix this")
        assert (f.id, f.body) == ("S1", "Fix this")

    def test_pathless_finding_uses_full_text_as_body(self):
        f = parse_finding_line(
            '- **[I1]** The `_comment` field convention is good practice and should be retained.'
        )
        assert f.path == ""
        assert "The `_comment` field convention" in f.body
        assert "should be retained" in f.body

    def test_pathless_finding_with_em_dash_keeps_full_text(self):
        f = parse_finding_line(
            '- **[I1]** Some pattern across files — this is good practice.'
        )
        assert f.path == ""
        assert "Some pattern across files" in f.body
        assert "good practice" in f.body

    def test_optional_bold_close(self):
        f = parse_finding_line(
            '- [ ] **[S1] `has_go` summary line dropped** — The step summary lost the output'
        )
        assert f is not None
        assert (f.severity, f.seq) == ("S", 1)

    def test_non_finding_returns_none(self):
        assert parse_finding_line("just a regular line") is None

    def test_section_header_returns_none(self):
        assert parse_finding_line("## Must fix") is None


class TestFindings:
    def test_single_finding_single_section(self):
        findings = _findings("## Must fix\n\n- **[M1]** **`file.go:10`** — Fix the bug\n")
        assert len(findings) == 1
        assert findings[0].body == "Fix the bug"

    def test_multiple_findings_one_section(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — First finding\n"
            "- **[M2]** **`b.go:2`** — Second finding\n"
        )
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "First finding"), ("M2", "Second finding"),
        ]

    def test_findings_across_multiple_sections(self):
        findings = _findings(
            "## Must fix\n\n- **[M1]** **`a.go:1`** — Must body\n\n"
            "## Should fix\n\n- **[S1]** **`b.go:2`** — Should body\n\n"
            "## Nit\n\n- **[N1]** **`c.go:3`** — Nit body\n"
        )
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "Must body"), ("S1", "Should body"), ("N1", "Nit body"),
        ]

    def test_last_finding_in_section_preserves_body(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — First must-fix\n"
            "- **[M2]** **`b.go:2`** — Last must-fix body\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`c.go:3`** — First should-fix\n\n"
            "## Nit\n\n"
            "- **[N1]** **`d.go:4`** — The only nit\n\n"
            "## Verdict\n\nAll done.\n"
        )
        by_id = {f.id: f for f in findings}
        assert by_id["M2"].body == "Last must-fix body"
        assert by_id["S1"].body == "First should-fix"
        assert by_id["N1"].body == "The only nit"
        assert all(f.body for f in findings)

    def test_multi_line_continuation(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`file.go:10`** — Main body text\n"
            "  More details here.\n"
            "  And another line.\n"
        )
        body = findings[0].body
        assert "Main body text" in body
        assert "More details here." in body
        assert "And another line." in body

    def test_multi_line_with_code_block(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`workflow.yml:10`** — Add permissions block:\n"
            "  ```yaml\n"
            "  permissions:\n"
            "    contents: read\n"
            "  ```\n"
            "  Note about the fix.\n"
        )
        body = findings[0].body
        assert "Add permissions block:" in body
        assert "permissions:" in body
        assert "contents: read" in body
        assert "Note about the fix." in body

    def test_multi_line_last_in_section_regression(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — Simple finding\n\n"
            "- **[M2]** **`workflow.yml:50-60`** — Complex finding.\n"
            "  Code block follows:\n"
            "  ```yaml\n"
            "  jobs:\n"
            "    build:\n"
            "      runs-on: ubuntu\n"
            "  ```\n"
            "  *(Note referencing other comments.)*\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`b.go:5`** — Should fix body\n"
        )
        m2 = next(f for f in findings if f.id == "M2")
        assert "Complex finding" in m2.body

    def test_code_block_indentation_preserved(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`file.py:10`** — Add this code:\n"
            "  ```python\n"
            "  def foo():\n"
            "      return bar\n"
            "  ```\n"
        )
        body = findings[0].body
        assert "    return bar" in body or "      return bar" in body

    def test_nested_indentation_preserved(self):
        findings = _findings(
            "## Should fix\n\n"
            "- **[S1]** **`config.yml:5`** — Use this structure:\n"
            "  ```yaml\n"
            "  jobs:\n"
            "    build:\n"
            "      runs-on: ubuntu\n"
            "  ```\n"
        )
        yaml_lines = [l for l in findings[0].body.split("\n") if "runs-on" in l]
        assert yaml_lines
        assert yaml_lines[0] != yaml_lines[0].lstrip()

    def test_strikethrough_skipped(self):
        findings = _findings(
            "## Must fix\n\n"
            "- ~~**[M1]** **`file.go:10`** — Resolved~~\n"
            "- **[M2]** **`file.go:20`** — Still open\n"
        )
        assert [f.id for f in findings] == ["M2"]

    def test_sub_headers_handled(self):
        findings = _findings(
            "## Must fix\n\n"
            "### Group A\n\n"
            "- **[M1]** **`a.go:1`** — First\n\n"
            "### Group B\n\n"
            "- **[M2]** **`b.go:2`** — Second\n"
        )
        assert [f.body for f in findings] == ["First", "Second"]

    def test_h3_severity_under_findings_parent(self):
        findings = _findings(
            "## Findings\n\n"
            "### Should-fix\n\n"
            "- **[S1]** **`file.go:10`** — Issue found\n\n"
            "### Nit\n\n"
            "- **[N1]** **`file.go:20`** — Style issue\n"
        )
        assert [f.severity for f in findings] == ["S", "N"]

    def test_non_severity_section_stops_parsing(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — A finding\n\n"
            "## Verdict\n\n"
            "This is not a finding: - **[M2]** **`b.go:2`** — Not parsed\n"
        )
        assert len(findings) == 1

    def test_empty_input(self):
        assert _findings("") == []

    def test_a_document_declaring_nothing_declares_nothing(self):
        assert ReviewDocument().findings == []

    def test_no_severity_sections(self):
        assert _findings("# Review\n\nSome preamble.\n\n## Summary\n\nNothing here.\n") == []

    def test_the_metadata_header_declares_no_findings(self):
        """The frame is read off before the body, so a header can never be
        mistaken for a declaration."""
        findings = _findings(
            "# Code Review\n\n"
            "<!-- head_sha: abc123 -->\n\n"
            "## Must fix\n\n- **[M1]** **`a.go:1`** — A finding\n"
        )
        assert [f.id for f in findings] == ["M1"]

    def test_finding_with_no_body(self):
        assert _findings("## Nit\n\n- **[N1]** **`file.go:10`**\n")[0].body == ""

    def test_case_insensitive_section_headers(self):
        findings = _findings(
            "## Must Fix\n\n"
            "- **[M1]** **`file.go:10`** — Bug found\n\n"
            "## SHOULD FIX\n\n"
            "- **[S1]** **`file.go:20`** — Cleanup needed\n\n"
            "## nit\n\n"
            "- **[N1]** **`file.go:30`** — Style issue\n"
        )
        assert [(f.severity, f.body) for f in findings] == [
            ("M", "Bug found"), ("S", "Cleanup needed"), ("N", "Style issue"),
        ]

    def test_stray_text_between_sections_does_not_corrupt_previous_finding(self):
        findings = _findings(
            "## Should fix\n"
            "- **[S1]** **`a.go:10`** — Real should-fix body\n\n"
            "## Nit\n"
            "_None in this file group._\n"
            "- **[N1]** **`b.go:20`** — Real nit body\n"
        )
        by_id = {f.id: f for f in findings}
        assert len(findings) == 2
        assert by_id["S1"].body == "Real should-fix body"
        assert by_id["N1"].body == "Real nit body"

    def test_empty_section_markers_do_not_corrupt_adjacent_findings(self):
        findings = _findings(
            "## Must fix\n"
            "_None in this file group._\n"
            "## Should fix\n"
            "- **[S1]** **`a.go:1`** — Should body\n"
            "- **[S2]** **`b.go:2`** — Last should body\n\n"
            "## Nit\n"
            "_None in this file group._\n"
            "- **[N1]** **`c.go:3`** — First nit\n\n"
            "## Idioms\n"
            "_None in this file group._\n"
            "- **[I1]** **`d.go:4`** — First idiom\n"
        )
        by_id = {f.id: f for f in findings}
        assert len(findings) == 4
        assert by_id["S1"].body == "Should body"
        assert by_id["S2"].body == "Last should body"
        assert by_id["N1"].body == "First nit"
        assert by_id["I1"].body == "First idiom"

    def test_finding_with_stable_id_comment(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** <!-- sid:abc12345 --> **`file.go:10`** — body text\n"
        )
        assert len(findings) == 1
        assert (findings[0].id, findings[0].path, findings[0].line) == ("M1", "file.go", 10)

    def test_idioms_section(self):
        findings = _findings("## Idioms\n\n- **[I1]** **`config.go:5`** — Good pattern\n")
        assert (findings[0].severity, findings[0].body) == ("I", "Good pattern")

    def test_a_whole_review_keeps_every_body(self):
        findings = _findings(
            "# Review: org/repo#42\n\n"
            "## Must fix\n\n"
            "- **[M1]** **`workflow.yml:10-20`** — Missing permissions block.\n"
            "  ```yaml\n"
            "  permissions:\n"
            "    contents: read\n"
            "  ```\n\n"
            "- **[M2]** **`handler.go:50`** — Race condition in handler.\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`config.sh:30`** — Hardcoded prefix.\n\n"
            "## Nit\n\n"
            "- **[N1]** **`test.sh:5`** — Tests not in CI.\n\n"
            "## Verdict\n\nRequest changes.\n"
        )
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert "Missing permissions block." in by_id["M1"].body
        assert "Race condition in handler." in by_id["M2"].body
        assert "Hardcoded prefix." in by_id["S1"].body
        assert "Tests not in CI." in by_id["N1"].body

    def test_every_last_in_section_retains_body(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — Must-fix alpha\n"
            "- **[M2]** **`b.go:2`** — Must-fix beta (last in section)\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`c.go:3`** — Should-fix only (last in section)\n\n"
            "## Nit\n\n"
            "- **[N1]** **`d.go:4`** — Nit alpha\n"
            "- **[N2]** **`e.go:5`** — Nit beta (last in section)\n\n"
            "## Verdict\n\nDone.\n"
        )
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert by_id["M2"].body == "Must-fix beta (last in section)"
        assert by_id["S1"].body == "Should-fix only (last in section)"
        assert by_id["N2"].body == "Nit beta (last in section)"

    def test_a_checked_finding_is_declared_though_open_counts_omits_it(self):
        """The two readings differ deliberately: `open_counts` reports what is
        still outstanding, `findings` reports every declaration."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- [x] **[M1]** **`a.go:1`** — done\n"
            "- [ ] **[M2]** **`b.go:2`** — open\n"
        )
        assert [f.id for f in document.findings] == ["M1", "M2"]
        assert document.open_counts["M"] == 1


class TestVerdict:
    @pytest.mark.parametrize("prose,expected", [
        ("Approve", ReviewVerdict.APPROVE),
        ("**Needs discussion**", ReviewVerdict.NEEDS_DISCUSSION),
        ("Request changes", ReviewVerdict.CHANGES_REQUESTED),
        ("Disapprove", ReviewVerdict.DISAPPROVE),
        ("disapprove", ReviewVerdict.DISAPPROVE),
    ])
    def test_the_verdict_section_is_read_however_it_is_worded(self, prose, expected):
        document = ReviewDocument.parse(f"## Verdict\n{prose} — rationale.\n")
        assert document.verdict is expected

    def test_wording_that_states_no_verdict_states_none(self):
        assert ReviewDocument.parse("## Verdict\nLooks fine to me.\n").verdict is None

    def test_a_document_with_no_verdict_section_states_none(self):
        text = "## Summary\nSome findings.\n## Must fix\n- **[M1]** a:1 — bug\n"
        assert ReviewDocument.parse(text).verdict is None

    def test_a_verdict_word_outside_the_section_is_not_the_verdict(self):
        """Prose elsewhere describes the review; only the Verdict section
        declares one."""
        text = "## Summary\nI would approve this once the tests land.\n"
        assert ReviewDocument.parse(text).verdict is None


class TestStatesVerdict:
    """The one reading of which runs state a verdict at all.

    Both the resolver and the mechanically merged body ask this rather than
    testing the mode for themselves, so a self-review cannot state a verdict
    in one place and withhold it in the other.
    """

    def test_a_pr_review_states_one(self):
        assert states_verdict(Mode.PR)

    def test_a_self_review_does_not(self):
        assert not states_verdict(Mode.SELF)

    def test_a_review_whose_metadata_named_no_mode_still_states_one(self):
        """A missing mode is not a claim that the review had no PR."""
        assert states_verdict(None)


class TestResolveVerdict:
    def test_the_counts_speak_when_the_prose_does_not(self):
        document = ReviewDocument.parse("## Should fix\n- **[S1]** a.py:1 — improvement\n")
        assert resolve_review_verdict(document) is ReviewVerdict.NEEDS_DISCUSSION

    def test_prose_cannot_under_report_blocking_findings(self):
        document = ReviewDocument.parse(
            "## Must fix\n- **[M1]** a.py:1 — bug\n\n## Verdict\nApprove — looks fine.\n",
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_counts_cannot_discard_a_stronger_call(self):
        document = ReviewDocument.parse(
            "## Nit\n- **[N1]** a.py:1 — style\n\n## Verdict\nRequest changes — rework it.\n",
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_disapprove_survives_any_counts(self):
        document = ReviewDocument.parse("## Verdict\nDisapprove — wrong approach.\n")
        assert resolve_review_verdict(document) is ReviewVerdict.DISAPPROVE

    def test_a_self_review_is_advisory(self):
        document = ReviewDocument.parse("## Must fix\n- **[M1]** a.py:1 — bug\n")
        assert resolve_review_verdict(document, mode=Mode.SELF) is None

    def test_a_self_review_still_reports_disapprove(self):
        """Disapprove judges the approach, which holds with or without a PR."""
        document = ReviewDocument.parse("## Verdict\nDisapprove — wrong approach.\n")
        assert resolve_review_verdict(document, mode=Mode.SELF) is ReviewVerdict.DISAPPROVE

    def test_a_review_that_was_never_written_reaches_no_verdict(self):
        assert resolve_review_verdict(None) is None

    def test_a_review_that_found_nothing_approves(self):
        """The other half of the distinction above: an empty document is a
        review that ran and had nothing to say."""
        assert resolve_review_verdict(ReviewDocument()) is ReviewVerdict.APPROVE

    def test_the_counts_come_from_the_document_it_was_handed(self, tmp_path):
        """One document, read once — the verdict cannot be resolved against
        counts from a file the caller re-read in between."""
        document = ReviewDocument.read(
            _write(tmp_path, "## Should fix\n- **[S1]** a.py:1 — improvement\n\n## Verdict\nApprove — fine.\n"),
        )
        assert resolve_review_verdict(document) is ReviewVerdict.NEEDS_DISCUSSION


class TestVerdictFromCounts:
    def test_a_must_fix_blocks(self):
        assert verdict_from_counts({"M": 1, "S": 0}) is ReviewVerdict.CHANGES_REQUESTED

    def test_a_should_fix_opens_a_discussion(self):
        assert verdict_from_counts({"M": 0, "S": 2}) is ReviewVerdict.NEEDS_DISCUSSION

    def test_nothing_blocking_approves(self):
        assert verdict_from_counts({"N": 3}) is ReviewVerdict.APPROVE

    def test_a_severity_the_tally_omits_counts_as_none(self):
        """A partial tally is read rather than refused — the mechanical paths
        hand this whatever counts they have."""
        assert verdict_from_counts({}) is ReviewVerdict.APPROVE


class TestCountsProse:
    def test_severities_read_out_in_order(self):
        assert counts_prose({"M": 2, "S": 1, "N": 1}) == "2 must-fix, 1 should-fix, 1 nit"

    def test_a_severity_with_none_is_left_out(self):
        assert counts_prose({"M": 0, "S": 1}) == "1 should-fix"

    def test_an_empty_tally_reads_as_nothing(self):
        assert counts_prose({"M": 0, "S": 0, "N": 0, "I": 0}) == ""


# ── The body a review has when no agent wrote one ───────────────────────────


class TestMechanicalVerdict:
    def test_must_fix_present(self):
        assert mechanical_verdict({"M": 2, "S": 1, "N": 0, "I": 0}).startswith(
            "Request changes"
        )

    def test_should_fix_no_must(self):
        assert mechanical_verdict({"M": 0, "S": 3, "N": 1, "I": 0}).startswith(
            "Needs discussion"
        )

    def test_nits_and_idioms_only(self):
        result = mechanical_verdict({"M": 0, "S": 0, "N": 2, "I": 1})
        assert result.startswith("Approve")
        assert "2 nit" in result
        assert "1 idiom" in result

    def test_no_findings(self):
        assert "no findings" in mechanical_verdict({"M": 0, "S": 0, "N": 0, "I": 0})

    def test_zero_counts_for_some(self):
        result = mechanical_verdict({"M": 1, "S": 0, "N": 0, "I": 0})
        assert "Request changes" in result
        assert "1 must-fix" in result
        assert "should-fix" not in result

    def test_the_note_says_no_agent_reached_this(self):
        """The pipeline reads it back to record that synthesis did not run."""
        assert MECHANICAL_NOTE in mechanical_verdict({"M": 1})
        assert MECHANICAL_NOTE in mechanical_verdict({})


class TestBuildMechanicalBody:
    @staticmethod
    def _must_fix_content(count=1):
        lines = ["## Must fix"]
        for i in range(1, count + 1):
            lines.append(f"- **[M{i}]** **`f{i}.py`** — bug {i}")
        return "\n".join(lines) + "\n"

    def test_opens_with_the_summary_it_generates(self):
        """The body starts at `## Summary` — the title and metadata header above
        it belong to the document, not to the merge."""
        result = build_mechanical_body(
            self._must_fix_content(), group_count=2, summary_note="Test note.",
        )
        assert result.startswith("## Summary")
        assert "1 finding" in result
        assert "2 groups" in result
        assert "Test note." in result

    def test_includes_verdict_by_default(self):
        result = build_mechanical_body(
            self._must_fix_content(), group_count=1, summary_note="note",
        )
        assert "## Verdict" in result
        assert "Request changes" in result

    def test_excludes_verdict_when_disabled(self):
        result = build_mechanical_body(
            self._must_fix_content(), group_count=1, summary_note="note",
            include_verdict=False,
        )
        assert "## Verdict" not in result

    def test_a_stated_verdict_replaces_the_derived_one(self):
        """A path that reached the review file without an agent but knows what
        the call is says so — the clean run does, and its verdict carries no
        mechanical note because nothing stood in for a synthesis."""
        result = build_mechanical_body(
            "## File Triage\n- `a.py` — tier 2\n", group_count=1,
            summary_note="note", verdict=CLEAN_VERDICT,
        )
        assert "Approve — clean review." in result
        assert MECHANICAL_NOTE not in result

    def test_no_findings_verdict(self):
        result = build_mechanical_body(
            "## Must fix\n_none._\n", group_count=1, summary_note="note",
        )
        assert "No findings" in result
        assert "Approve" in result

    def test_file_count_in_scope(self):
        result = build_mechanical_body(
            self._must_fix_content(), group_count=2, summary_note="note", file_count=3,
        )
        assert "across 3 files in 2 groups" in result

    def test_the_prior_findings_ledger_does_not_inflate_the_count(self):
        """The ledger reports the last review, so its lines are not findings
        this one declares — counting them said `2 findings` where there is one."""
        result = build_mechanical_body(
            self._must_fix_content() + "## Prior findings\n- **[M1]** `old.go` — Fixed\n",
            group_count=1, summary_note="note",
        )
        assert "1 finding across" in result
