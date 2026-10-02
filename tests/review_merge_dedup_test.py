"""Tests for `review.merge` — deduplicating triage entries, findings and sections."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.merge
from review.grammar import FindingIdentity

from review_merge_support import _sections


# ── 8. _clean_triage ────────────────────────────────────────────────────────


class TestCleanTriage:
    def test_valid_triage_lines(self):
        text = "- `file.go` reviewed\n- `other.go` skimmed"
        result = review.merge._clean_triage(text)
        assert "file.go" in result
        assert "other.go" in result

    def test_mixed_valid_invalid(self):
        text = "- `file.go` reviewed\nsome other text\n- `b.go` done"
        result = review.merge._clean_triage(text)
        assert "file.go" in result
        assert "b.go" in result
        assert "some other text" not in result

    def test_empty_input(self):
        assert review.merge._clean_triage("") == ""


# ── 9. _dedup_triage ────────────────────────────────────────────────────────


class TestDedupTriage:
    def test_with_duplicates(self):
        text = "- `file.go` — reviewed\n- `file.go` — reviewed again"
        result = review.merge._dedup_triage(text)
        assert result.count("file.go") == 1

    def test_no_duplicates(self):
        text = "- `a.go` — reviewed\n- `b.go` — reviewed"
        result = review.merge._dedup_triage(text)
        assert "a.go" in result
        assert "b.go" in result

    def test_empty_input(self):
        assert review.merge._dedup_triage("") == ""


# ── 10. FindingIdentity ─────────────────────────────────────────────────────


class TestFindingDedupKey:
    def test_standard_finding(self):
        line = "- **[M1]** **`pkg/handler.go:42`** — missing error check"
        result = FindingIdentity.of(line)
        assert result is not None
        assert "handler.go" in result.path
        assert "missing error check" in result.desc

    def test_checkbox_finding(self):
        line = "- [ ] **[S1]** **`handler.go:10`** — issue here"
        result = FindingIdentity.of(line)
        assert result is not None
        assert "handler.go" in result.path

    def test_stable_id(self):
        line = "- **[M1]** <!-- sid:abc12345 --> **`file.go:1`** — desc"
        result = FindingIdentity.of(line)
        assert result is not None
        assert "file.go" in result.path

    def test_non_finding_line(self):
        assert FindingIdentity.of("just some text") is None


# ── 11. _dedup_findings ─────────────────────────────────────────────────────


class TestDedupFindings:
    def test_with_duplicates(self):
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
        )
        result = review.merge._dedup_findings(text)
        assert result.text.count("same issue") == 1

    def test_no_duplicates(self):
        text = (
            "- **[M1]** **`a.go:1`** — issue a\n"
            "- **[M2]** **`b.go:2`** — issue b\n"
        )
        result = review.merge._dedup_findings(text)
        assert "issue a" in result.text
        assert "issue b" in result.text
        assert result.merged_into == {}

    def test_multiline_continuation(self):
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "  continuation line\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "  another continuation\n"
        )
        result = review.merge._dedup_findings(text)
        assert result.text.count("same issue") == 1
        assert "another continuation" not in result.text

    def test_a_dropped_id_records_the_copy_that_survived_it(self):
        # M2 is the same finding as M1, so a reference to it is not dangling —
        # it belongs on the copy that stayed, and closing the gaps moves it there.
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
        )
        assert review.merge._dedup_findings(text).merged_into == {
            review.merge.FindingId("M", 2): review.merge.FindingId("M", 1),
        }

    def test_a_duplicate_filed_under_the_wrong_severity_still_declares_an_id(self):
        # The copy dropped here sits in the Should-fix section carrying a
        # Must-fix ID. Reading only the section's own prefix found no
        # declaration on it, so nothing recorded where its references should go.
        text = (
            "- **[S1]** **`file.go:1`** — same issue\n"
            "- **[M4]** **`file.go:1`** — same issue\n"
        )
        assert review.merge._dedup_findings(text).merged_into == {
            review.merge.FindingId("M", 4): review.merge.FindingId("S", 1),
        }


# ── 11b. _dedup_sections ────────────────────────────────────────────────────


class TestDedupSections:
    def test_references_follow_the_surviving_copy(self):
        sections = _sections(M=(
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "- **[M3]** **`other.go:2`** — see [M2] for context\n"
        ))
        result = review.merge._dedup_sections(sections)
        assert "- **[M2]** **`other.go:2`** — see [M1] for context" in result["M"]

    def test_a_citation_across_severities_follows_the_gap_closing(self):
        # S1 was dropped before the merge; closing the gap hands its number to
        # S2, and the Must-fix finding citing S2 has to come along.
        sections = _sections(
            M="- **[M1]** **`a.go:1`** — issue a, blocked on [S2]",
            S="- **[S2]** **`b.go:1`** — issue b",
        )
        result = review.merge._dedup_sections(sections)
        assert "blocked on [S1]" in result["M"]
        assert "- **[S1]** **`b.go:1`** — issue b" in result["S"]

    def test_a_citation_nothing_declares_names_nothing(self):
        sections = _sections(
            M="- **[M1]** **`a.go:1`** — issue a, blocked on [S4]",
            S="- **[S1]** **`b.go:1`** — issue b",
        )
        result = review.merge._dedup_sections(sections)
        assert "blocked on [removed]" in result["M"]

    def test_a_citation_of_a_misfiled_duplicate_follows_the_survivor(self):
        # The second copy is filed under Should fix carrying a Must-fix ID.
        # Dedup drops it, and the citation belongs on the copy that survived —
        # which is a Should-fix finding, so the reference changes severity too.
        sections = _sections(
            M="- **[M1]** **`other.go:2`** — see [M4] for context",
            S=(
                "- **[S1]** **`file.go:1`** — same issue\n"
                "- **[M4]** **`file.go:1`** — same issue\n"
            ),
        )
        result = review.merge._dedup_sections(sections)
        assert "- **[M1]** **`other.go:2`** — see [S1] for context" in result["M"]
        assert result["S"].count("same issue") == 1

    def test_a_severity_nothing_declares_is_left_alone(self):
        # No Should-fix finding anywhere, so every S in the text belongs to some
        # other document — the prior review, a quoted log line — and stays put.
        sections = _sections(M="- **[M1]** **`a.go:1`** — uploads to S4 buckets, see [S9]")
        result = review.merge._dedup_sections(sections)
        assert "uploads to S4 buckets, see [S9]" in result["M"]
