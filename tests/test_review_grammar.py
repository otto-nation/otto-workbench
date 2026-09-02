import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from review_grammar import (  # noqa: E402
    BODY_FINDING_RE, BOLD_FINDING_ID_RE, SID_MARKER_RE, FindingIdentity,
    finding_tag, has_sid_marker, parse_finding_line, posted_finding_tag,
    sid_marker, strip_line_suffix, strip_sid_markers,
)
from review_types import SEVERITIES  # noqa: E402

DESC = "missing error check"


class TestFindingIdentityReadsEveryLocationShape:
    """Every shape a review writes for a location yields an identity.

    Before this module existed, dedup read the path with a bold-only regex and
    returned nothing for the three non-bold shapes, while carry-forward hashed
    all of them to the same stable ID. The same finding shipped twice.
    """

    def test_bold_backtick_path_with_line(self):
        ident = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        assert ident.path == "ai/lib/x.py"
        assert ident.line == 12

    def test_bold_backtick_path_without_line(self):
        ident = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py`** — {DESC}")
        assert ident.path == "ai/lib/x.py"
        assert ident.line is None

    def test_plain_backtick_path_is_no_longer_invisible(self):
        ident = FindingIdentity.of(f"- **[M1]** `ai/lib/x.py` — {DESC}")
        assert ident is not None
        assert ident.path == "ai/lib/x.py"

    def test_plain_backtick_path_with_line_is_no_longer_invisible(self):
        ident = FindingIdentity.of(f"- **[M1]** `ai/lib/x.py:12` — {DESC}")
        assert ident is not None
        assert ident.path == "ai/lib/x.py"
        assert ident.line == 12

    def test_bare_path_is_no_longer_invisible(self):
        ident = FindingIdentity.of(f"- **[M1]** ai/lib/x.py:12 — {DESC}")
        assert ident is not None
        assert ident.path == "ai/lib/x.py"
        assert ident.line == 12

    def test_bold_label_that_is_not_a_filename_still_reads(self):
        ident = FindingIdentity.of(f"- **[M1]** **Documentation** — {DESC}")
        assert ident.path == "Documentation"

    def test_checkbox_before_the_severity_still_reads(self):
        ident = FindingIdentity.of(f"- [ ] **[M1]** **`handler.go:42`** — {DESC}")
        assert ident.path == "handler.go"
        assert ident.line == 42

    def test_stable_id_marker_before_the_location_still_reads(self):
        ident = FindingIdentity.of(
            f"- **[M1]** <!-- sid:abc --> **`handler.go:42`** — {DESC}"
        )
        assert ident.path == "handler.go"
        assert ident.line == 42


class TestDedupStaysLineSensitive:
    """Pins the line a finding starts at as part of its dedup key.

    The stable ID is line-insensitive on purpose, so a finding carries forward
    when the code moves. The dedup key is not, and unifying the two readers
    must not quietly make it so. Where the finding *ends* is the complementary
    property, pinned by `TestDedupIgnoresWhereARangeEnds`.
    """

    def test_same_file_different_lines_do_not_collapse(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:40`** — {DESC}")
        assert a.dedup_key != b.dedup_key

    def test_same_file_same_line_collapse(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** `ai/lib/x.py:12` — {DESC}")
        assert a.dedup_key == b.dedup_key


class TestDedupIgnoresWhereARangeEnds:
    """Pins the end of a range as no part of the dedup key.

    Two groups that open at the same line and disagree about how far the
    problem reaches are one finding reported twice, not two. Dedup used to key
    on the range as written and ship both. The start line is still read —
    `TestDedupStaysLineSensitive` pins that half.
    """

    def test_ranges_sharing_a_start_line_collapse(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12-18`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12-40`** — {DESC}")
        assert a.dedup_key == b.dedup_key

    def test_a_range_collapses_with_the_bare_line_it_starts_at(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12-18`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        assert a.dedup_key == b.dedup_key

    def test_ranges_starting_at_different_lines_stay_apart(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12-18`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:30-40`** — {DESC}")
        assert a.dedup_key != b.dedup_key


class TestBoldLabelsKeepTheirColons:
    """A bold label that is not a filename is hashed whole.

    Every reader used to truncate a location at its last colon, which read
    `**Docs: the readme**` as `Docs` and gave it the same stable ID as a
    separate `**Docs**` finding — a collision that carried one review's
    finding forward onto the other's. Only a line suffix comes off now.
    """

    def test_a_labels_colon_no_longer_collides_with_the_bare_label(self):
        labelled = FindingIdentity.of(f"- **[M1]** **Docs: the readme** — {DESC}")
        bare = FindingIdentity.of(f"- **[M1]** **Docs** — {DESC}")
        assert labelled.path == "Docs: the readme"
        assert labelled.stable_id != bare.stable_id

    def test_a_line_suffix_still_comes_off_a_filename(self):
        ident = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        assert ident.path == "ai/lib/x.py"
        assert ident.stable_id == FindingIdentity("ai/lib/x.py", None, DESC).stable_id


class TestStripLineSuffix:
    """Pins what `review_verify` and `review_dedup` take off a captured path.

    Both readers match `LINE_SUFFIX` inside the span they capture and have to
    remove it afterwards. Truncating at the last colon — what each did for
    itself — read a path that carries a colon of its own as its own prefix,
    so the same line verified against a different file than it parsed as.
    """

    def test_a_trailing_line_number_comes_off(self):
        assert strip_line_suffix("src/x.py:12") == "src/x.py"

    def test_a_trailing_range_comes_off(self):
        assert strip_line_suffix("src/x.py:12-18") == "src/x.py"
        assert strip_line_suffix("src/x.py:12–18") == "src/x.py"

    def test_a_path_with_no_suffix_is_returned_whole(self):
        assert strip_line_suffix("src/x.py") == "src/x.py"

    def test_a_colon_that_is_not_a_line_suffix_survives(self):
        assert strip_line_suffix("ns:module.py") == "ns:module.py"
        assert strip_line_suffix("pkg:sub/x.go") == "pkg:sub/x.go"
        assert strip_line_suffix("C:/src/x.py") == "C:/src/x.py"

    def test_only_the_suffix_comes_off_a_path_that_also_carries_a_colon(self):
        assert strip_line_suffix("C:/src/x.py:12") == "C:/src/x.py"


class TestStableIdIsUnchanged:
    """The markers are on disk; the formula cannot move.

    Every prior review carries `<!-- sid:xxxx -->` markers this formula
    produced. A different hash makes one round of re-reviews report every
    prior finding as new.
    """

    def test_every_shape_of_one_finding_hashes_the_same(self):
        shapes = [
            f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}",
            f"- **[M1]** **`ai/lib/x.py`** — {DESC}",
            f"- **[M1]** `ai/lib/x.py` — {DESC}",
            f"- **[M1]** `ai/lib/x.py:12` — {DESC}",
        ]
        ids = {FindingIdentity.of(s).stable_id for s in shapes}
        assert ids == {"0e9022af"}


class TestSidMarkerIsWrittenAndReadTheSameWay:
    """One spelling for the marker, across the writer, the readers and the strip.

    The marker was written in `review_merge`, matched inline four times here,
    tested for with a bare substring, and captured by a regex of
    `review_reconcile`'s own. A writer and a reader that disagree leave a
    finding whose identity nothing can recover.
    """

    def test_a_written_marker_strips_back_off_whole(self):
        line = f"- **[M1]**{sid_marker('abc12345')} **`file.go:42`** — {DESC}"
        assert strip_sid_markers(line) == f"- **[M1]** **`file.go:42`** — {DESC}"

    def test_a_line_with_no_marker_is_unchanged(self):
        line = f"- **[M1]** **`file.go:42`** — {DESC}"
        assert strip_sid_markers(line) == line

    def test_a_written_marker_is_seen_by_the_containment_test(self):
        assert has_sid_marker(f"- **[M1]**{sid_marker('abc12345')} rest")
        assert not has_sid_marker("- **[M1]** rest")

    def test_a_written_marker_yields_its_id_back(self):
        assert SID_MARKER_RE.findall(sid_marker("abc12345")) == ["abc12345"]

    def test_a_written_marker_is_skipped_by_the_declaration_readers(self):
        ident = FindingIdentity.of(
            f"- **[M1]**{sid_marker('abc12345')} **`handler.go:42`** — {DESC}"
        )
        assert ident.path == "handler.go"
        assert ident.line == 42


class TestThePostedSpellingIsWrittenAndReadTheSameWay:
    """A finding wears two tags, and both readers here accept both.

    The review file writes `**[M1]**` and a posted comment writes
    `**[M1] [must-fix]**`. Only the first had an owner: the second was spelled
    twice in `review_format` and read by nobody, so every reader below returned
    nothing on a posted body and the thread-state annotation never fired.
    """

    @pytest.mark.parametrize("severity", [s.key for s in SEVERITIES])
    def test_every_severitys_posted_tag_yields_its_id_back(self, severity):
        tag = posted_finding_tag(f"{severity}1", severity)
        assert BOLD_FINDING_ID_RE.search(tag).group(1) == f"{severity}1"

    def test_the_review_file_tag_still_yields_its_id_back(self):
        assert BOLD_FINDING_ID_RE.search(finding_tag("M1")).group(1) == "M1"

    def test_a_posted_body_line_reads_its_path_and_body(self):
        line = f"- {posted_finding_tag('M1', 'M')} `ai/lib/x.py:12` — {DESC}"
        m = BODY_FINDING_RE.search(line)
        assert strip_line_suffix(m.group(1) or m.group(2)) == "ai/lib/x.py"
        assert m.group(3) == DESC

    def test_a_review_file_body_line_still_reads(self):
        line = f"- {finding_tag('M1')} `ai/lib/x.py:12` — {DESC}"
        m = BODY_FINDING_RE.search(line)
        assert strip_line_suffix(m.group(1) or m.group(2)) == "ai/lib/x.py"
        assert m.group(3) == DESC

    def test_a_bracketed_token_that_is_not_a_severity_label_is_not_the_tag(self):
        assert BOLD_FINDING_ID_RE.search("**[M1] [whatever]**") is None


class TestAParsedFindingCarriesTheIdentityItHashesTo:
    """The `stable_id` comes off the declaration line, where the identity is.

    Set here rather than derived from the parsed finding, because
    `finding_spans` replaces `body` with the whole multi-line span and the hash
    is over the declaration's own wording.
    """

    def test_a_parsed_findings_id_is_the_one_the_annotator_stamps(self):
        line = f"- **[M1]** **`handler.go:42`** — {DESC}"
        assert parse_finding_line(line).stable_id == FindingIdentity.of(line).stable_id

    def test_a_plain_backtick_line_hashes_the_same_as_a_bold_one(self):
        bold = parse_finding_line(f"- **[M1]** **`handler.go:42`** — {DESC}")
        plain = parse_finding_line(f"- **[M1]** `handler.go:42` — {DESC}")
        assert bold.stable_id == plain.stable_id != ""

    def test_a_line_the_identity_cannot_read_carries_none(self):
        assert parse_finding_line("- **[M1]** no location here").stable_id == ""
