import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from review.grammar import (  # noqa: E402
    BODY_FINDING_RE, BOLD_FINDING_ID_RE, SID_MARKER_RE,
    FindingIdentity, finding_tag, location_ref,
    posted_finding_tag, sid_marker, strip_line_suffix, strip_sid_markers,
)
from review.types import SEVERITIES  # noqa: E402

DESC = "missing error check"


class TestDedupStaysLineSensitive:
    """Pins the line a finding starts at as part of its dedup key.

    The stable ID is line-insensitive on purpose, so a finding carries forward
    when the code moves. The dedup key is not, and unifying the two readers
    must not quietly make it so. Where the finding *ends* is the complementary
    property, pinned by `TestDedupIgnoresWhereARangeEnds`.
    """

    def test_same_file_different_lines_do_not_collapse(self):
        a = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        b = FindingIdentity.of_fields("ai/lib/x.py", 40, DESC)
        assert a.dedup_key != b.dedup_key

    def test_same_file_same_line_collapse(self):
        a = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        b = FindingIdentity("ai/lib/x.py", 12, DESC)
        assert a.dedup_key == b.dedup_key


class TestDedupIgnoresWhereARangeEnds:
    """Pins the end of a range as no part of the dedup key.

    Two groups that open at the same line and disagree about how far the
    problem reaches are one finding reported twice, not two. `of_fields` takes
    the start line only, so a range and the bare line it starts at share a key.
    The start line is still read — `TestDedupStaysLineSensitive` pins that half.
    """

    def test_ranges_sharing_a_start_line_collapse(self):
        a = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        b = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        assert a.dedup_key == b.dedup_key

    def test_a_range_collapses_with_the_bare_line_it_starts_at(self):
        a = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        b = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        assert a.dedup_key == b.dedup_key

    def test_ranges_starting_at_different_lines_stay_apart(self):
        a = FindingIdentity.of_fields("ai/lib/x.py", 12, DESC)
        b = FindingIdentity.of_fields("ai/lib/x.py", 30, DESC)
        assert a.dedup_key != b.dedup_key


class TestStripLineSuffix:
    """Pins what `review.verify` and `review.dedup` take off a captured path.

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

    def test_a_trailing_line_list_comes_off(self):
        """A finding about two discrete lines names both, comma-separated.

        The list is a line suffix like any other: leaving it on the path sent
        the verifier to stat `run.py:64,82`, which no filesystem holds, and it
        reported "file not found" about a file that exists — dropping a
        correct finding.
        """
        assert strip_line_suffix("src/x.py:64,82") == "src/x.py"
        assert strip_line_suffix("src/x.py:64, 82") == "src/x.py"
        assert strip_line_suffix("src/x.py:12,18,24") == "src/x.py"

    def test_a_line_list_mixing_ranges_comes_off(self):
        assert strip_line_suffix("src/x.py:12-18,24") == "src/x.py"
        assert strip_line_suffix("src/x.py:12,18-24") == "src/x.py"

    def test_a_path_with_no_suffix_is_returned_whole(self):
        assert strip_line_suffix("src/x.py") == "src/x.py"

    def test_a_colon_that_is_not_a_line_suffix_survives(self):
        assert strip_line_suffix("ns:module.py") == "ns:module.py"
        assert strip_line_suffix("pkg:sub/x.go") == "pkg:sub/x.go"
        assert strip_line_suffix("C:/src/x.py") == "C:/src/x.py"

    def test_only_the_suffix_comes_off_a_path_that_also_carries_a_colon(self):
        assert strip_line_suffix("C:/src/x.py:12") == "C:/src/x.py"


class TestSidMarkerIsWrittenAndReadTheSameWay:
    """One spelling for the marker, across the writer, the readers and the strip.

    The marker was written in `review.merge`, matched inline four times here,
    tested for with a bare substring, and captured by a regex of
    `review.reconcile`'s own. A writer and a reader that disagree leave a
    finding whose identity nothing can recover.
    """

    def test_a_written_marker_strips_back_off_whole(self):
        line = f"- **[M1]**{sid_marker('abc12345')} **`file.go:42`** — {DESC}"
        assert strip_sid_markers(line) == f"- **[M1]** **`file.go:42`** — {DESC}"

    def test_a_line_with_no_marker_is_unchanged(self):
        line = f"- **[M1]** **`file.go:42`** — {DESC}"
        assert strip_sid_markers(line) == line

    def test_a_written_marker_yields_its_id_back(self):
        assert SID_MARKER_RE.findall(sid_marker("abc12345")) == ["abc12345"]


class TestThePostedSpellingIsWrittenAndReadTheSameWay:
    """A finding wears two tags, and both readers here accept both.

    The review file writes `**[M1]**` and a posted comment writes
    `**[M1] [must-fix]**`. Only the first had an owner: the second was spelled
    twice in `review.format` and read by nobody, so every reader below returned
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


LONG = (
    "The retry loop re-reads the whole session log on every attempt, "
    "which is quadratic in the number of attempts made"
)


# The literals are what `FindingIdentity.of` produced for the rendered lines
# `- **[M1]** **`ai/lib/x.py:12`** — Missing check on the db.Query error`,
# `- [ ] **[S2]** `Src/Handler.go` — <LONG>`, `- **[N1]** **`pkg/a.go:3-9`** —   padded body`
# and `- **[M1]** `a.py`, `b.py` — x` (`of` took the first of several files too),
# captured before the line reader was deleted. A sid already posted on GitHub
# is one of these; a fields identity that hashes differently orphans its thread.
@pytest.mark.parametrize("path, line, body, sid", [
    ("ai/lib/x.py", 12, "Missing check on the db.Query error", "99e705a8"),
    ("Src/Handler.go", None, LONG, "6998c8c3"),
    ("pkg/a.go", 3, "  padded body", "78fa736a"),
    ("a.py", None, "x", "8239ab41"),
])
def test_fields_hash_to_the_sid_the_rendered_line_did(path, line, body, sid):
    assert FindingIdentity.of_fields(path, line, body).stable_id == sid


# The shapes `of` hashed differently from the fields, pinned so the divergence
# is a decision (D12) rather than a surprise. `stored` is what `of` gave the
# line, which a schema-1 record keeps through the upgrade; `fields` is what
# `of_fields` gives the same finding as ingest stores it. The lines:
# `- **[M1]** `a.py:3` -- body here` (no em dash, so `of` hashed an empty
# description), `- **[S1]** **Documentation** — the README is stale` (`of` read
# the label as a path through `_fallback_location`), and
# `- [ ] **[S1]** `a.py:1` — short *(declined — why)*` (`of` hashed the
# annotation into the description).
@pytest.mark.parametrize("path, line, body, stored, fields", [
    ("a.py", 3, "body here", "5e406135", "94e8c5c8"),
    ("", None, "**Documentation** — the README is stale", "d284f4e9", "ff46b213"),
    ("a.py", 1, "short", "7b0fdab7", "2ccda39f"),
])
def test_these_shapes_hash_differently_from_the_line_they_were(path, line, body, stored, fields):
    assert FindingIdentity.of_fields(path, line, body).stable_id == fields
    assert fields != stored


def test_a_pathless_finding_hashes_with_an_empty_path():
    body = "PR title says docs-only but changes code"
    assert FindingIdentity.of_fields("", None, body).stable_id == "f47fca21"


def test_only_the_first_line_of_the_body_is_the_description():
    assert FindingIdentity.of_fields("a.py", 1, "head\nmore").desc == "head"


def test_location_ref_spells_the_three_shapes():
    assert location_ref("a.py", None, None) == "`a.py`"
    assert location_ref("a.py", 3, None) == "`a.py:3`"
    assert location_ref("a.py", 3, 5) == "`a.py:3-5`"
