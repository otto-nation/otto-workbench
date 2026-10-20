"""Tests for `review.merge` — deduplicating triage entries, findings and sections."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.grammar import FindingIdentity
from review.ingest import ingest_review
from review.merge import merge_records, renumber_record
from review.record import ReviewRecord
from review.render import render_body

from review_merge_support import _merge, _triage
from review_record_support import canonical


# ── 8. triage cleanup (via merge_records) ───────────────────────────────────


class TestCleanTriage:
    def test_valid_triage_lines(self):
        merged = merge_records([_triage("- `file.go` reviewed", "- `other.go` skimmed")])
        texts = "\n".join(e.text for e in merged.triage)
        assert "file.go" in texts
        assert "other.go" in texts

    def test_mixed_valid_invalid(self):
        merged = merge_records([_triage(
            "- `file.go` reviewed", "some other text", "- `b.go` done",
        )])
        texts = "\n".join(e.text for e in merged.triage)
        assert "file.go" in texts
        assert "b.go" in texts
        assert "some other text" not in texts

    def test_empty_input(self):
        assert merge_records([ReviewRecord()]).triage == ()


# ── 9. triage dedup (via merge_records) ─────────────────────────────────────


class TestDedupTriage:
    def test_with_duplicates(self):
        merged = merge_records([_triage(
            "- `file.go` — reviewed", "- `file.go` — reviewed again",
        )])
        assert sum("file.go" in e.text for e in merged.triage) == 1

    def test_no_duplicates(self):
        merged = merge_records([_triage("- `a.go` — reviewed", "- `b.go` — reviewed")])
        texts = "\n".join(e.text for e in merged.triage)
        assert "a.go" in texts
        assert "b.go" in texts

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
        merged = _merge(M=(
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
        ))
        assert sum("same issue" in f.body for f in merged.findings) == 1

    def test_no_duplicates(self):
        merged = _merge(M=(
            "- **[M1]** **`a.go:1`** — issue a\n"
            "- **[M2]** **`b.go:2`** — issue b\n"
        ))
        bodies = "\n".join(f.body for f in merged.findings)
        assert "issue a" in bodies
        assert "issue b" in bodies
        assert [f.id for f in merged.findings] == ["M1", "M2"]

    def test_multiline_continuation(self):
        merged = _merge(M=(
            "- **[M1]** **`file.go:1`** — same issue\n"
            "  continuation line\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "  another continuation\n"
        ))
        bodies = "\n".join(f.body for f in merged.findings)
        assert bodies.count("same issue") == 1
        assert "another continuation" not in bodies

    def test_a_dropped_id_records_the_copy_that_survived_it(self):
        # M2 is the same finding as M1, so a reference to it is not dangling —
        # it belongs on the copy that stayed, and closing the gaps moves it there.
        merged = _merge(M=(
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "- **[M3]** **`other.go:2`** — see [M2] for context\n"
        ))
        other = next(f for f in merged.findings if f.path == "other.go")
        assert other.id == "M2"
        assert "[M1]" in other.body

    def test_a_duplicate_filed_under_the_wrong_severity_still_declares_an_id(self):
        # Same path+desc under two ID prefixes: each severity keeps its own copy.
        merged = merge_records([ingest_review(
            "## Should fix\n"
            "- **[S1]** **`file.go:1`** — same issue\n"
            "- **[M4]** **`file.go:1`** — same issue\n",
            strict=False,
        )])
        assert sorted(f.id for f in merged.findings) == ["M1", "S1"]


# ── 11b. section merge (via merge_records / renumber_record) ────────────────


class TestDedupSections:
    def test_references_follow_the_surviving_copy(self):
        merged = _merge(M=(
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "- **[M3]** **`other.go:2`** — see [M2] for context\n"
        ))
        rendered = canonical(render_body(merged, sids=True))
        assert "- **[M2]** **`other.go:2`** — see [M1] for context" in rendered

    def test_a_citation_across_severities_follows_the_gap_closing(self):
        # S1 was dropped before the merge; closing the gap hands its number to
        # S2, and the Must-fix finding citing S2 has to come along.
        merged = _merge(
            M="- **[M1]** **`a.go:1`** — issue a, blocked on [S2]",
            S="- **[S2]** **`b.go:1`** — issue b",
        )
        rendered = canonical(render_body(merged, sids=True))
        assert "blocked on [S1]" in rendered
        assert "- **[S1]** **`b.go:1`** — issue b" in rendered

    def test_a_citation_nothing_declares_names_nothing(self):
        merged = _merge(
            M="- **[M1]** **`a.go:1`** — issue a, blocked on [S4]",
            S="- **[S1]** **`b.go:1`** — issue b",
        )
        assert "blocked on [removed]" in merged.findings[0].body

    def test_a_citation_of_a_misfiled_duplicate_follows_the_survivor(self):
        # The second copy carries a Must-fix ID under Should fix. Each severity
        # keeps its own statement of the issue; the citation still names a real
        # finding after the gaps close.
        merged = merge_records([ingest_review(
            "## Must fix\n"
            "- **[M1]** **`other.go:2`** — see [M4] for context\n"
            "## Should fix\n"
            "- **[S1]** **`file.go:1`** — same issue\n"
            "- **[M4]** **`file.go:1`** — same issue\n",
            strict=False,
        )])
        rendered = canonical(render_body(merged, sids=True))
        assert "see [M2] for context" in rendered
        assert rendered.count("same issue") == 2

    def test_a_severity_nothing_declares_is_left_alone(self):
        # No Should-fix finding anywhere, so every S in the text belongs to some
        # other document — the prior review, a quoted log line — and stays put.
        # This is the merge-wide pass (`renumber_record`); the group shift marks
        # undeclared prefixes `[removed]` because a group cannot cite another.
        record = ingest_review(
            "## Must fix\n- **[M1]** **`a.go:1`** — uploads to S4 buckets, see [S9]\n",
            strict=False,
        )
        out = renumber_record(record)
        assert "uploads to S4 buckets, see [S9]" in out.findings[0].body
