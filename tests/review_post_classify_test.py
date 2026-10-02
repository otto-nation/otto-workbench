"""Tests for placing findings on a diff — `review.format`'s hunk parsing, path
resolution, classification into inline, file-level and skipped, posting-order
renumbering, and the inline comment each placed finding becomes.
"""

import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# The module the `rp` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `rp`.
import cli.review_post  # noqa: E402,F401


class TestParseDiffHunks:
    def test_single_file_single_hunk(self, rp):
        diff = (
            "diff --git a/file.go b/file.go\n"
            "--- a/file.go\n"
            "+++ b/file.go\n"
            "@@ -10,5 +10,7 @@ func main() {\n"
            "+new line\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert "file.go" in hunks
        assert rp.HunkRange(10, 16) in hunks["file.go"]

    def test_multiple_files(self, rp):
        diff = (
            "diff --git a/a.go b/a.go\n"
            "--- a/a.go\n"
            "+++ b/a.go\n"
            "@@ -1,3 +1,5 @@\n"
            "+line\n"
            "diff --git a/b.go b/b.go\n"
            "--- a/b.go\n"
            "+++ b/b.go\n"
            "@@ -10,2 +10,4 @@\n"
            "+line\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert "a.go" in hunks
        assert "b.go" in hunks

    def test_multiple_hunks_per_file(self, rp):
        diff = (
            "diff --git a/file.go b/file.go\n"
            "--- a/file.go\n"
            "+++ b/file.go\n"
            "@@ -1,3 +1,5 @@\n"
            "+line\n"
            "@@ -20,3 +22,5 @@\n"
            "+line\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert len(hunks["file.go"]) == 2
        assert rp.HunkRange(1, 5) in hunks["file.go"]
        assert rp.HunkRange(22, 26) in hunks["file.go"]

    def test_hunk_with_omitted_count(self, rp):
        diff = (
            "diff --git a/file.go b/file.go\n"
            "--- a/file.go\n"
            "+++ b/file.go\n"
            "@@ -5,3 +5 @@\n"
            "-removed\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert rp.HunkRange(5, 5) in hunks["file.go"]

    def test_new_file(self, rp):
        diff = (
            "diff --git a/new.go b/new.go\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.go\n"
            "@@ -0,0 +1,20 @@\n"
            "+package main\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert rp.HunkRange(1, 20) in hunks["new.go"]

    def test_no_diff_touches_nothing(self, rp):
        assert rp.parse_diff_hunks("") == {}

    def test_a_file_with_no_hunks_is_named_but_empty(self, rp):
        """A rename or a mode change: the diff touches the file and no line of
        it, which the classifier reads as two different answers."""
        diff = (
            "diff --git a/old.go b/new.go\n"
            "similarity index 100%\n"
            "--- a/old.go\n"
            "+++ b/new.go\n"
        )
        assert rp.parse_diff_hunks(diff) == {"new.go": []}


class TestResolvePath:
    def test_exact_match(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp.resolve_path("pkg/handler.go", hunks) == "pkg/handler.go"

    def test_basename_match_unique(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp.resolve_path("handler.go", hunks) == "pkg/handler.go"

    def test_basename_match_ambiguous(self, rp):
        hunks = {"pkg/a/handler.go": [(1, 10)], "pkg/b/handler.go": [(1, 10)]}
        assert rp.resolve_path("handler.go", hunks) is None

    def test_partial_path_match(self, rp):
        hunks = {"src/pkg/service/handler.go": [(1, 10)]}
        assert rp.resolve_path("service/handler.go", hunks) == "src/pkg/service/handler.go"

    def test_no_match(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp.resolve_path("other.go", hunks) is None


class TestClassifyFindings:
    DIFF_INLINE = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_inline_when_in_hunk(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (1, 0, 0)
        assert inline[0].classification == "inline"

    def test_file_level_when_line_not_in_hunk(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=50, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert fl[0].classification == "file_level"
        assert "not in any diff hunk" in fl[0].skip_reason

    def test_file_level_when_no_line_number(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=None, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert "no line number" in fl[0].skip_reason

    def test_skipped_when_path_not_in_diff(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="other.go", line=5, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 0, 1)
        assert skipped[0].classification == "skipped"

    def test_resolves_bare_filename(self, rp):
        diff = (
            "diff --git a/pkg/handler.go b/pkg/handler.go\n"
            "--- a/pkg/handler.go\n"
            "+++ b/pkg/handler.go\n"
            "@@ -1,3 +1,10 @@\n"
            "+line\n"
        )
        f = rp.Finding(id="M1", severity="M", seq=1, path="handler.go", line=5, end_line=None, body="x")
        inline, _, _ = rp.classify_findings([f], diff)
        assert len(inline) == 1
        assert inline[0].full_path == "pkg/handler.go"

    def test_end_line_snapped_to_hunk_boundary(self, rp):
        """end_line beyond the hunk gets snapped to hunk end."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=50, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (1, 0, 0)
        assert inline[0].end_line == 10

    def test_end_line_within_hunk_unchanged(self, rp):
        """end_line inside the hunk stays as-is."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=3, end_line=8, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (1, 0, 0)
        assert inline[0].end_line == 8

    def test_body_only_severity_forced_to_file_level(self, rp):
        """Nit findings are routed to body even when their line is in a diff hunk."""
        f = rp.Finding(id="N1", severity="N", seq=1, path="file.go", line=5, end_line=None, body="style issue")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert fl[0].classification == "file_level"
        assert "body-only" in fl[0].skip_reason

    def test_body_only_severity_resolves_full_path(self, rp):
        """Body-only findings still get full_path resolved for by_file grouping."""
        diff = (
            "diff --git a/pkg/handler.go b/pkg/handler.go\n"
            "--- a/pkg/handler.go\n"
            "+++ b/pkg/handler.go\n"
            "@@ -1,3 +1,10 @@\n"
            "+line\n"
        )
        f = rp.Finding(id="I1", severity="I", seq=1, path="handler.go", line=5, end_line=None, body="use pattern")
        inline, fl, skipped = rp.classify_findings([f], diff)
        assert len(fl) == 1
        assert fl[0].full_path == "pkg/handler.go"

    def test_idiom_with_no_path_still_file_level(self, rp):
        """Body-only findings without a path go to file_level with general finding reason."""
        f = rp.Finding(id="I1", severity="I", seq=1, path="", line=None, end_line=None, body="good pattern")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert len(fl) == 1
        assert "general finding" in fl[0].skip_reason

    def test_inline_severity_still_inline(self, rp):
        """Must-fix findings with lines in diff are still inline."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=None, body="bug")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert len(inline) == 1


class TestRenumberForPosting:
    def test_inline_sorted_by_path_then_line(self, rp):
        f1 = rp.Finding(id="M1", severity="M", seq=1, path="b.go", line=10, end_line=None, body="x", full_path="b.go")
        f2 = rp.Finding(id="M2", severity="M", seq=2, path="a.go", line=5, end_line=None, body="y", full_path="a.go")
        inline, _ = rp.renumber_for_posting([f1, f2], [])
        assert (inline[0].posted_id, inline[0].full_path) == ("M1", "a.go")
        assert (inline[1].posted_id, inline[1].full_path) == ("M2", "b.go")

    def test_body_continues_after_inline(self, rp):
        fi = rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=5, end_line=None, body="x", full_path="a.go")
        fb = rp.Finding(id="S2", severity="S", seq=2, path="b.go", line=None, end_line=None, body="y", full_path="b.go")
        inline, body = rp.renumber_for_posting([fi], [fb])
        assert inline[0].posted_id == "S1"
        assert body[0].posted_id == "S2"

    def test_independent_counters_per_severity(self, rp):
        f1 = rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=1, end_line=None, body="x", full_path="a.go")
        f2 = rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=2, end_line=None, body="y", full_path="a.go")
        f3 = rp.Finding(id="M2", severity="M", seq=2, path="a.go", line=3, end_line=None, body="z", full_path="a.go")
        inline, _ = rp.renumber_for_posting([f1, f2, f3], [])
        assert [f.posted_id for f in inline] == ["M1", "S1", "M2"]

    def test_same_file_sorted_by_line(self, rp):
        f1 = rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=30, end_line=None, body="x", full_path="a.go")
        f2 = rp.Finding(id="M2", severity="M", seq=2, path="a.go", line=5, end_line=None, body="y", full_path="a.go")
        inline, _ = rp.renumber_for_posting([f1, f2], [])
        assert inline[0].line == 5
        assert inline[1].line == 30


class TestFormatInlineComment:
    def test_single_line(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=42,
            end_line=None, body="Fix bug", full_path="pkg/file.go", posted_id="M1",
        )
        c = rp.format_inline_comment(f)
        assert c["path"] == "pkg/file.go"
        assert c["line"] == 42
        assert "**[M1] [must-fix]** Fix bug" in c["body"]
        assert c["side"] == "RIGHT"
        assert "start_line" not in c

    def test_omits_subject_type(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=10,
            end_line=None, body="Fix", full_path="pkg/file.go", posted_id="S1",
        )
        c = rp.format_inline_comment(f)
        assert "subject_type" not in c

    def test_multi_line_range(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=10,
            end_line=20, body="Refactor", full_path="pkg/file.go", posted_id="S1",
        )
        c = rp.format_inline_comment(f)
        assert c["start_line"] == 10
        assert c["line"] == 20
        assert c.get("start_side") == "RIGHT"

    @pytest.mark.parametrize(
        "sev_id,sev,label",
        [("M1", "M", "must-fix"), ("S1", "S", "should-fix"), ("N1", "N", "nit"), ("I1", "I", "idiom")],
    )
    def test_severity_labels(self, rp, sev_id, sev, label):
        f = rp.Finding(
            id=sev_id, severity=sev, seq=1, path="f.go", line=1,
            end_line=None, body="text", full_path="f.go", posted_id=sev_id,
        )
        body = rp.format_inline_comment(f)["body"]
        assert f"[{label}]" in body


class TestPostedCommentsCarryTheFindingIdentity:
    """The comment carries the hash, because the number it wears is reassigned.

    `renumber_for_posting` numbers by diff position, so the `[M1]` a reviewer
    reads names a different finding in the review file a round later. The hash
    goes in an HTML comment: invisible to the reviewer, and the only handle the
    next round has on which finding a reply thread belongs to.
    """

    def _finding(self, rp, **kwargs):
        return rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=42,
            end_line=None, body="Fix bug", full_path="pkg/file.go",
            posted_id="M1", **kwargs,
        )

    def test_the_identity_rides_along_in_an_html_comment(self, rp):
        body = rp.format_inline_comment(self._finding(rp, stable_id="abc12345"))["body"]
        assert body == "**[M1] [must-fix]** <!-- sid:abc12345 --> Fix bug"

    def test_a_finding_with_no_identity_posts_what_it_always_did(self, rp):
        body = rp.format_inline_comment(self._finding(rp))["body"]
        assert body == "**[M1] [must-fix]** Fix bug"


class TestClassifyFindingsEmptyPath:
    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_empty_path_classified_as_file_level(self, rp):
        f = rp.Finding(id="I1", severity="I", seq=1, path="", line=None, end_line=None, body="Good pattern")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert fl[0].classification == "file_level"
        assert "general finding" in fl[0].skip_reason

    def test_empty_path_no_warning(self, rp, capsys):
        f = rp.Finding(id="I1", severity="I", seq=1, path="", line=None, end_line=None, body="Good pattern")
        rp.classify_findings([f], self.DIFF)
        captured = capsys.readouterr()
        assert "empty path" not in captured.err

    def test_non_empty_path_not_affected(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert len(inline) == 1


class TestHunkEnd:
    def test_line_inside_hunk(self, rp):
        hunks = [rp.HunkRange(10, 20), rp.HunkRange(30, 40)]
        assert rp._hunk_end(15, hunks) == 20

    def test_line_outside_all_hunks(self, rp):
        hunks = [rp.HunkRange(10, 20), rp.HunkRange(30, 40)]
        assert rp._hunk_end(25, hunks) is None

    def test_line_at_hunk_start(self, rp):
        hunks = [rp.HunkRange(10, 20)]
        assert rp._hunk_end(10, hunks) == 20

    def test_line_at_hunk_end(self, rp):
        hunks = [rp.HunkRange(10, 20)]
        assert rp._hunk_end(20, hunks) == 20

    def test_multiple_hunks_returns_correct_end(self, rp):
        hunks = [rp.HunkRange(1, 5), rp.HunkRange(10, 15), rp.HunkRange(20, 25)]
        assert rp._hunk_end(12, hunks) == 15
        assert rp._hunk_end(3, hunks) == 5
        assert rp._hunk_end(22, hunks) == 25


class TestClassifyFindingsEdgeCases:
    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_end_line_equals_line_single_line(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=5, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert len(inline) == 1
        comment = rp.format_inline_comment(
            rp.Finding(
                id="M1", severity="M", seq=1, path="file.go", line=5,
                end_line=5, body="x", full_path="file.go", posted_id="M1",
            )
        )
        assert "start_line" not in comment
        assert comment["line"] == 5


class TestRenumberForPostingEdgeCases:
    def test_empty_inline_and_empty_body(self, rp):
        inline, body = rp.renumber_for_posting([], [])
        assert inline == []
        assert body == []

    def test_only_body_findings(self, rp):
        fb1 = rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=None, end_line=None, body="x", full_path="a.go")
        fb2 = rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=None, end_line=None, body="y", full_path="b.go")
        inline, body = rp.renumber_for_posting([], [fb1, fb2])
        assert len(inline) == 0
        assert len(body) == 2
        assert body[0].posted_id == "S1"
        assert body[1].posted_id == "N1"
