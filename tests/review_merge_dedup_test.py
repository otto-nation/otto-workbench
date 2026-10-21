"""Tests for `review.merge` — deduplicating triage entries, findings and sections."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.grammar import FindingIdentity
from review.merge import merge_records, renumber_record
from review.record import ReviewRecord, TriageEntry
from review.render import render_body

from review_merge_support import _merged_of, _triaged
from review_record_support import canonical, finding, record


# ── 8. triage cleanup (via merge_records) ───────────────────────────────────


class TestCleanTriage:
    def test_valid_triage_lines(self):
        merged = merge_records([_triaged(
            TriageEntry("file.go", "reviewed"), TriageEntry("other.go", "skimmed"),
        )])
        assert [e.path for e in merged.triage] == ["file.go", "other.go"]

    def test_mixed_valid_invalid(self):
        merged = merge_records([_triaged(
            TriageEntry("file.go", "reviewed"),
            TriageEntry("", "some other text"),
            TriageEntry("b.go", "done"),
        )])
        assert [e.path for e in merged.triage] == ["file.go", "b.go"]

    def test_empty_input(self):
        assert merge_records([ReviewRecord()]).triage == ()


# ── 9. triage dedup (via merge_records) ─────────────────────────────────────


class TestDedupTriage:
    def test_with_duplicates(self):
        merged = merge_records([_triaged(
            TriageEntry("file.go", "reviewed"),
            TriageEntry("file.go", "reviewed again"),
        )])
        assert [e.path for e in merged.triage] == ["file.go"]

    def test_no_duplicates(self):
        merged = merge_records([_triaged(
            TriageEntry("a.go", "reviewed"), TriageEntry("b.go", "reviewed"),
        )])
        assert [e.path for e in merged.triage] == ["a.go", "b.go"]

    def test_empty_input(self):
        assert merge_records([ReviewRecord()]).triage == ()


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


# ── 11. finding dedup (via merge_records) ───────────────────────────────────


class TestDedupFindings:
    def test_with_duplicates(self):
        merged = _merged_of(
            finding("M1", "same issue", path="file.go"),
            finding("M2", "same issue", path="file.go"),
        )
        assert sum("same issue" in f.body for f in merged.findings) == 1

    def test_no_duplicates(self):
        merged = _merged_of(
            finding("M1", "issue a", path="a.go"),
            finding("M2", "issue b", path="b.go", line=2),
        )
        bodies = "\n".join(f.body for f in merged.findings)
        assert "issue a" in bodies
        assert "issue b" in bodies
        assert [f.id for f in merged.findings] == ["M1", "M2"]

    def test_multiline_continuation(self):
        merged = _merged_of(
            finding("M1", "same issue\n  continuation line", path="file.go"),
            finding("M2", "same issue\n  another continuation", path="file.go"),
        )
        bodies = "\n".join(f.body for f in merged.findings)
        assert bodies.count("same issue") == 1
        assert "another continuation" not in bodies

    def test_a_dropped_id_records_the_copy_that_survived_it(self):
        # M2 is the same finding as M1, so a reference to it is not dangling —
        # it belongs on the copy that stayed, and closing the gaps moves it there.
        merged = _merged_of(
            finding("M1", "same issue", path="file.go"),
            finding("M2", "same issue", path="file.go"),
            finding("M3", "see [M2] for context", path="other.go", line=2),
        )
        other = next(f for f in merged.findings if f.path == "other.go")
        assert other.id == "M2"
        assert "[M1]" in other.body

    def test_a_same_issue_at_another_severity_is_kept(self):
        # D10: severity follows the ID prefix, so dedup does not collapse across it.
        merged = merge_records([record(
            finding("S1", "same issue", path="file.go"),
            finding("M4", "same issue", path="file.go"),
        )])
        assert sorted(f.id for f in merged.findings) == ["M1", "S1"]


# ── 11b. section merge (via merge_records / renumber_record) ────────────────


class TestDedupSections:
    def test_references_follow_the_surviving_copy(self):
        merged = _merged_of(
            finding("M1", "same issue", path="file.go"),
            finding("M2", "same issue", path="file.go"),
            finding("M3", "see [M2] for context", path="other.go", line=2),
        )
        rendered = canonical(render_body(merged, sids=True))
        assert "- **[M2]** `other.go:2` — see [M1] for context" in rendered

    def test_a_citation_across_severities_follows_the_gap_closing(self):
        # S1 was dropped before the merge; closing the gap hands its number to
        # S2, and the Must-fix finding citing S2 has to come along.
        merged = _merged_of(
            finding("M1", "issue a, blocked on [S2]", path="a.go"),
            finding("S2", "issue b", path="b.go"),
        )
        rendered = canonical(render_body(merged, sids=True))
        assert "blocked on [S1]" in rendered
        assert "- **[S1]** `b.go:1` — issue b" in rendered

    def test_a_citation_nothing_declares_names_nothing(self):
        merged = _merged_of(
            finding("M1", "issue a, blocked on [S4]", path="a.go"),
            finding("S1", "issue b", path="b.go"),
        )
        assert "blocked on [removed]" in merged.findings[0].body

    def test_a_citation_of_a_same_issue_at_another_severity_still_names_it(self):
        # D10: severity follows the ID prefix, so both copies stay and the citation
        # still names a real finding after the gaps close.
        merged = merge_records([record(
            finding("M1", "see [M4] for context", path="other.go", line=2),
            finding("S1", "same issue", path="file.go"),
            finding("M4", "same issue", path="file.go"),
        )])
        rendered = canonical(render_body(merged, sids=True))
        assert "see [M2] for context" in rendered
        assert rendered.count("same issue") == 2

    def test_a_severity_nothing_declares_is_left_alone(self):
        # No Should-fix finding anywhere, so every S in the text belongs to some
        # other document — the prior review, a quoted log line — and stays put.
        # This is the merge-wide pass (`renumber_record`); the group shift marks
        # undeclared prefixes `[removed]` because a group cannot cite another.
        out = renumber_record(record(
            finding("M1", "uploads to S4 buckets, see [S9]", path="a.go"),
        ))
        assert "uploads to S4 buckets, see [S9]" in out.findings[0].body
