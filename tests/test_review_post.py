import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_state import PostedAs, PostEvent, PostTracking
from proc import CmdResult
from serde import from_dict as serde_from_dict


# A GitHub outage as gh reports it: nothing on stdout, the status line on
# stderr. Shared so a failure-path test never has to restate the shape.
_API_UNAVAILABLE = CmdResult(1, "", "gh: Service unavailable (HTTP 503)")


def _make_sections(rp, **kwargs):
    """Build a ReviewSections from keyword args for test convenience."""
    entries = {}
    configs = {}
    order = []
    for key, content in kwargs.items():
        if not content:
            continue
        matched = [c for c in rp.KNOWN_SECTIONS if c.key == key]
        cfg = matched[0] if matched else rp.SectionConfig(
            key=key, header=key, position=rp.POSITION_AFTER,
        )
        entries[key] = content
        configs[key] = cfg
        order.append(key)
    known_order = [c.key for c in rp.KNOWN_SECTIONS]
    order.sort(key=lambda k: (
        known_order.index(k) if k in known_order else len(known_order), k,
    ))
    return rp.ReviewSections(entries=entries, configs=configs, order=order)


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
        assert rp._resolve_path("pkg/handler.go", hunks) == "pkg/handler.go"

    def test_basename_match_unique(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp._resolve_path("handler.go", hunks) == "pkg/handler.go"

    def test_basename_match_ambiguous(self, rp):
        hunks = {"pkg/a/handler.go": [(1, 10)], "pkg/b/handler.go": [(1, 10)]}
        assert rp._resolve_path("handler.go", hunks) is None

    def test_partial_path_match(self, rp):
        hunks = {"src/pkg/service/handler.go": [(1, 10)]}
        assert rp._resolve_path("service/handler.go", hunks) == "src/pkg/service/handler.go"

    def test_no_match(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp._resolve_path("other.go", hunks) is None


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


class TestFormatBodyText:
    def test_with_inline_shows_have_some_comments(self, rp):
        result = rp.format_body_text([], has_inline=True, severity_filter={"M", "S", "N"})
        assert "Have some comments" in result

    def test_without_inline_shows_review_findings(self, rp):
        result = rp.format_body_text([], has_inline=False, severity_filter={"M", "S", "N"})
        assert "Review findings" in result

    def test_body_findings_listed_with_path_refs(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=42,
            end_line=None, body="Fix it", full_path="pkg/handler.go", posted_id="M1",
        )
        result = rp.format_body_text([f], has_inline=True, severity_filter={"M"})
        assert "**[M1] [must-fix]**" in result
        assert "`handler.go:42`" in result
        assert "Fix it" in result

    def test_no_body_findings_single_line(self, rp):
        text = rp.format_body_text([], has_inline=True, severity_filter={"M", "S"})
        assert len(text.strip().split("\n")) == 1

    def test_pathless_finding_omits_backticks(self, rp):
        f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern across files.", posted_id="I1",
        )
        result = rp.format_body_text([f], has_inline=True, severity_filter={"I"})
        assert "``" not in result
        assert "**[I1]** Good pattern" in result
        assert "<details open>" in result

    def test_mixed_severities_grouped_with_headers(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix", posted_id="S1"),
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Nit issue", posted_id="N1"),
            rp.Finding(id="S2", severity="S", seq=2, path="c.go", line=30,
                       end_line=None, body="Another should", posted_id="S2"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"S", "N"})
        assert "### Should fix" in result
        assert "<summary>Nit (1)</summary>" in result
        s_idx = result.index("### Should fix")
        n_idx = result.index("<details open>")
        assert s_idx < n_idx
        assert result.index("S1") < result.index("N1")
        assert result.index("S2") < result.index("N1")

    def test_single_severity_nits_in_details(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit one", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="b.go", line=20,
                       end_line=None, body="Nit two", posted_id="N2"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert "<details open>" in result
        assert "<summary>Nit (2)</summary>" in result
        assert "</details>" in result
        assert "N1" in result
        assert "N2" in result

    def test_summary_and_verdict_prepended(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Clean refactor.", verdict="Approve."),
        )
        assert result.startswith("## Summary")
        assert "Clean refactor." in result
        assert "### Verdict" in result
        assert "Approve." in result
        assert "---" in result
        assert result.index("## Summary") < result.index("---")
        assert result.index("---") < result.index("Have some comments")

    def test_verdict_action_prefix_stripped(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Summary text.",
                                    verdict="Request changes — M1 and M2 are blockers."),
        )
        assert "### Verdict" in result
        assert "M1 and M2 are blockers." in result
        assert "Request changes" not in result

    def test_verdict_approve_prefix_stripped(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Summary text.",
                                    verdict="Approve — clean code."),
        )
        assert "### Verdict" in result
        assert "clean code." in result
        assert "Approve" not in result

    def test_verdict_action_prefix_stripped_plain_hyphen(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Summary text.",
                                    verdict="Needs discussion - looks good."),
        )
        assert "### Verdict" in result
        assert "looks good." in result
        assert "Needs discussion" not in result

    def test_verdict_bold_action_prefix_stripped(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Summary text.",
                                    verdict="**Request changes** — M1 and M2 are blockers."),
        )
        assert "### Verdict" in result
        assert "M1 and M2 are blockers." in result
        assert "Request changes" not in result

    def test_verdict_without_action_prefix_unchanged(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Summary text.", verdict="Looks good overall."),
        )
        assert "### Verdict" in result
        assert "Looks good overall." in result

    def test_summary_without_verdict(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,summary="Clean refactor."),
        )
        assert result.startswith("## Summary")
        assert "Clean refactor." in result
        assert "### Verdict" not in result
        assert "---" in result
        assert result.index("## Summary") < result.index("---")

    def test_empty_summary_omitted(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
        )
        assert "## Summary" not in result
        assert "### Verdict" not in result
        assert "---" not in result

    def test_outside_diff_header_only_for_demoted_findings(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix", posted_id="S1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"S"})
        assert "**Findings outside the diff:**" in result
        assert "Findings not in the diff" not in result

    def test_no_outside_diff_header_for_nits_only(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit issue", posted_id="N1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert "Findings outside the diff" not in result
        assert "<details open>" in result

    def test_nits_drop_severity_label(self, rp):
        f = rp.Finding(
            id="N1", severity="N", seq=1, path="a.go", line=10,
            end_line=None, body="Style issue", posted_id="N1",
        )
        result = rp.format_body_text([f], has_inline=True, severity_filter={"N"})
        assert "**[N1]**" in result
        assert "[nit]" not in result


class TestFormatBodyTextDetails:
    def test_nits_sorted_by_path_then_line(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Naming issue", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="a.go", line=30,
                       end_line=None, body="Another style", posted_id="N2"),
            rp.Finding(id="N3", severity="N", seq=3, path="a.go", line=10,
                       end_line=None, body="Style issue", posted_id="N3"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert "<summary>Nit (3)</summary>" in result
        assert result.index("Style issue") < result.index("Another style")
        assert result.index("Another style") < result.index("Naming issue")

    def test_nits_within_file_sorted_by_line(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=30,
                       end_line=None, body="Later", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="a.go", line=10,
                       end_line=None, body="Earlier", posted_id="N2"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert result.index("Earlier") < result.index("Later")

    def test_mixed_severity_body_shows_severity_then_details(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix this", posted_id="S1"),
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Nit issue", posted_id="N1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"S", "N"})
        assert "### Should fix" in result
        assert "<summary>Nit (1)</summary>" in result
        assert result.index("### Should fix") < result.index("<details open>")

    def test_nits_and_idioms_in_separate_details(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit", posted_id="N1"),
            rp.Finding(id="I1", severity="I", seq=1, path="a.go", line=20,
                       end_line=None, body="Idiom", posted_id="I1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N", "I"})
        assert "<summary>Nit (1)</summary>" in result
        assert "<summary>Idioms (1)</summary>" in result
        assert "Nit" in result
        assert "Idiom" in result

    def test_pathless_body_only_finding_in_details(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="File nit", posted_id="N1"),
            rp.Finding(id="I1", severity="I", seq=1, path="", line=None,
                       end_line=None, body="General pattern", posted_id="I1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N", "I"})
        assert "<summary>Nit (1)</summary>" in result
        assert "<summary>Idioms (1)</summary>" in result
        nit_idx = result.index("<summary>Nit")
        idiom_idx = result.index("<summary>Idioms")
        assert nit_idx < idiom_idx

    def test_static_analysis_appended_after_findings(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit", posted_id="N1"),
        ]
        sa = "### Nesting depth\n1 violation in 1 of 3 files checked\n\n- **`scripts/deploy.sh:42`** — depth 5 exceeds limit 4 (in main())"
        result = rp.format_body_text(
            findings, has_inline=True, severity_filter={"N"},
            sections=_make_sections(rp,static_analysis=sa),
        )
        assert "### Nesting depth" in result
        assert "depth 5 exceeds limit 4" in result
        assert result.index("Nit") < result.index("Nesting depth")

    def test_static_analysis_with_no_findings(self, rp):
        sa = "### Nesting depth\n1 violation in 1 of 3 files checked"
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            sections=_make_sections(rp,static_analysis=sa),
        )
        assert "Have some comments" in result
        assert "### Nesting depth" in result

    def test_static_analysis_empty_string_omitted(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
        )
        assert "Static Analysis" not in result


class TestFormatPathRef:
    def test_path_with_line(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=42, end_line=None, body="")
        assert rp._format_path_ref(f) == "`file.go:42`"

    def test_path_with_line_range(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=20, body="")
        assert rp._format_path_ref(f) == "`file.go:10-20`"

    def test_path_only(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=None, end_line=None, body="")
        assert rp._format_path_ref(f) == "`file.go`"


class TestWritePostTracking:
    def _review_dir(self, tmp_path):
        """Create and return the folder-layout review directory."""
        d = tmp_path / "test-review"
        d.mkdir()
        return d

    def _read_tracking(self, d):
        return serde_from_dict(PostTracking, json.loads((d / "post.jsonl").read_text()))

    def test_submitted_true(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[123], commit_id="abc",
            inline_count=5, body_count=2, skipped_count=1, submitted=True,
        ))
        tracking = self._read_tracking(d)
        assert tracking.submitted is True

    def test_submitted_defaults_false(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[123], commit_id="abc",
            inline_count=5, body_count=2, skipped_count=1,
        ))
        tracking = self._read_tracking(d)
        assert tracking.submitted is False

    def test_review_id_from_review_ids(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[456], commit_id="abc",
            inline_count=3, body_count=1,
        ))
        tracking = self._read_tracking(d)
        assert tracking.review_id == 456
        assert tracking.review_ids == [456]
        assert tracking.chunk_count == 1

    def test_list_of_review_ids_with_chunk_count(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, PostTracking(
            posted_as=PostedAs.REVIEW.value, status=PostEvent.COMMENT.value,
            review_ids=[100, 200, 300], commit_id="abc",
            inline_count=90, body_count=5, skipped_count=2,
            submitted=True, chunk_count=3,
        ))
        tracking = self._read_tracking(d)
        assert tracking.review_id == 100
        assert tracking.review_ids == [100, 200, 300]
        assert tracking.chunk_count == 3
        assert tracking.submitted is True


class TestFormatSubmitCommand:
    def test_produces_correct_command(self, rp):
        result = rp._format_submit_command("org/repo", "42", 12345)
        assert result == "gh api repos/org/repo/pulls/42/reviews/12345/events --method POST -f event=COMMENT"


class TestChunkComments:
    def test_no_chunking_under_threshold(self, rp):
        comments = [{"body": f"c{i}"} for i in range(10)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 10

    def test_exact_boundary_not_chunked(self, rp):
        comments = [{"body": f"c{i}"} for i in range(30)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 30

    def test_splits_above_threshold(self, rp):
        comments = [{"body": f"c{i}"} for i in range(82)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 3
        assert [len(c) for c in chunks] == [30, 30, 22]

    def test_empty_input(self, rp):
        chunks = rp._chunk_comments([], 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 0


class TestCheckExistingPending:
    def test_returns_review_id(self, rp):
        reviews = json.dumps([{"id": 12345, "state": "PENDING"}])
        with patch("gh_client.api", return_value=CmdResult(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1") == 12345

    def test_returns_none_when_no_pending(self, rp):
        reviews = json.dumps([{"id": 1, "state": "APPROVED"}])
        with patch("gh_client.api", return_value=CmdResult(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_returns_none_for_empty_list(self, rp):
        with patch("gh_client.api", return_value=CmdResult(0, "[]")):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_returns_none_on_api_failure(self, rp):
        with patch("gh_client.api", return_value=CmdResult(1)):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_api_failure_warns_with_the_cause(self, rp, capsys):
        # None also means "no pending review", and a caller that reads it that
        # way opens a second one — so the failure has to be audible.
        failure = _API_UNAVAILABLE
        with patch("gh_client.api", return_value=failure):
            assert rp._check_existing_pending("org/repo", "1") is None
        assert "HTTP 503" in capsys.readouterr().err


class TestPostReview:
    PAYLOAD = {"body": "test", "commit_id": "abc123"}

    def test_no_existing_pending(self, rp):
        with (
            patch("review_github._check_existing_pending", return_value=None),
            patch("gh_client.api", return_value=CmdResult(0, '{"id": 42}')),
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}

    def test_deletes_existing_pending(self, rp):
        with (
            patch("review_github._check_existing_pending", return_value=999),
            patch("gh_client.api", return_value=CmdResult(0, '{"id": 42}')) as mock_api,
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}
            delete_calls = [c for c in mock_api.call_args_list
                            if c.kwargs.get("method") == "DELETE"]
            assert len(delete_calls) == 1

    def test_with_submit(self, rp):
        with (
            patch("review_github._check_existing_pending", return_value=None),
            patch("gh_client.api", return_value=CmdResult(0, '{"id": 42}')) as mock_api,
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD, submit=True)
        assert result == {"id": 42}
        sent = json.loads(mock_api.call_args.kwargs["input_text"])
        assert sent["event"] == "COMMENT"


class TestSubmitReview:
    def test_success(self, rp):
        with patch("gh_client.api", return_value=CmdResult(0, '{"ok": true}')) as mock_api:
            assert rp._submit_review("org/repo", "1", 42) is True
        assert mock_api.call_args[0][0] == "repos/org/repo/pulls/1/reviews/42/events"

    def test_failure_warns(self, rp, capsys):
        with patch("gh_client.api", return_value=CmdResult(1, '{"message": "bad request"}')):
            assert rp._submit_review("org/repo", "1", 42) is False
        assert "Failed to submit" in capsys.readouterr().err


class TestFetchPrRefs:
    def test_success(self, rp):
        pr_json = json.dumps({
            "head": {"sha": "abc123", "ref": "feat/branch"},
            "base": {"ref": "main"},
        })
        with patch("gh_client.api", return_value=CmdResult(0, pr_json)):
            meta = rp._fetch_pr_refs("org/repo", "1")
            assert meta["head_sha"] == "abc123"
            assert meta["head_ref"] == "feat/branch"
            assert meta["base_ref"] == "main"

    def test_failure_exits(self, rp):
        with (
            patch("gh_client.api", return_value=CmdResult(1)),
            pytest.raises(SystemExit),
        ):
            rp._fetch_pr_refs("org/repo", "1")


class TestGetDiff:
    def test_success(self, rp):
        with patch("gh_client.api", return_value=CmdResult(0, "diff --git a/f b/f\n")):
            assert rp._get_diff("org/repo", "1") == "diff --git a/f b/f\n"

    def test_failure_returns_empty(self, rp):
        with patch("gh_client.api", return_value=CmdResult(1)):
            assert rp._get_diff("org/repo", "1") == ""


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


class TestWordSet:
    def test_extracts_lowercase_words(self, rp):
        assert rp.word_set("Hello World_Foo 123") == {"hello", "world_foo", "123"}

    def test_empty_string(self, rp):
        assert rp.word_set("") == set()

    def test_strips_punctuation(self, rp):
        assert rp.word_set("error — missing `check`") == {"error", "missing", "check"}


class TestJaccard:
    def test_identical_sets(self, rp):
        assert rp.jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self, rp):
        assert rp.jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self, rp):
        assert rp.jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_both_empty(self, rp):
        assert rp.jaccard(set(), set()) == 1.0

    def test_one_empty(self, rp):
        assert rp.jaccard({"a"}, set()) == 0.0


class TestDedupAgainstPosted:
    def _make_finding(self, rp, id_str, path, body):
        return rp.Finding(
            id=id_str, severity=id_str[0], seq=int(id_str[1:]),
            path=path, line=42, end_line=None, body=body,
        )

    @patch("review_dedup._fetch_bot_comments")
    def test_skips_duplicate(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "handler.go", "body": "missing error check on db.Query result"},
        ]
        f = self._make_finding(rp, "M1", "handler.go", "missing error check on db.Query result")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 0
        assert len(deduped) == 1
        assert deduped[0].skip_reason == "duplicate of existing comment"

    @patch("review_dedup._fetch_bot_comments")
    def test_keeps_non_duplicate(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "handler.go", "body": "missing error check on db.Query result"},
        ]
        f = self._make_finding(rp, "S1", "handler.go", "unused import os")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0

    @patch("review_dedup._fetch_bot_comments")
    def test_different_file_not_duplicate(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "handler.go", "body": "missing error check"},
        ]
        f = self._make_finding(rp, "M1", "other.go", "missing error check")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1

    @patch("review_dedup._fetch_bot_comments")
    def test_no_existing_comments_keeps_all(self, mock_fetch, rp):
        mock_fetch.return_value = []
        f = self._make_finding(rp, "M1", "handler.go", "finding text")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0


class TestIsLineResolutionError:
    def test_matching_text(self, rp):
        assert rp._is_line_resolution_error("Line could not be resolved to a position") is True

    def test_non_matching_text(self, rp):
        assert rp._is_line_resolution_error("Something else went wrong") is False

    def test_case_insensitive(self, rp):
        assert rp._is_line_resolution_error("LINE COULD NOT BE RESOLVED") is True


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


class TestExtractBodyFindings:
    def test_standard_finding_in_body(self, rp):
        body = "- **[M1]** **`handler.go:42`** — Fix the bug"
        results = rp._extract_body_findings(body)
        assert len(results) == 1
        assert results[0]["path"] == "handler.go"
        assert results[0]["body"] == "Fix the bug"

    def test_multiple_findings(self, rp):
        body = (
            "- **[M1]** **`a.go:10`** — First issue\n"
            "- **[S1]** **`b.go:20`** — Second issue\n"
        )
        results = rp._extract_body_findings(body)
        assert len(results) == 2
        assert results[0]["path"] == "a.go"
        assert results[1]["path"] == "b.go"

    def test_no_findings(self, rp):
        body = "Just some regular text with no findings."
        results = rp._extract_body_findings(body)
        assert len(results) == 0

    def test_path_extraction_with_line_number_suffix(self, rp):
        body = "- **[M1]** **`handler.go:42`** — Fix bug"
        results = rp._extract_body_findings(body)
        assert results[0]["path"] == "handler.go"

    def test_a_colon_that_is_not_a_line_suffix_survives(self, rp):
        """Dedup compares the path the rest of the pipeline parsed.

        This reader used to truncate at the last colon, so an already-posted
        comment on `ns:module.py` was recorded against `ns` and matched no
        finding — the same finding posted again on every re-review.
        """
        body = "- **[M1]** **`ns:module.py`** — Fix bug"
        results = rp._extract_body_findings(body)
        assert results[0]["path"] == "ns:module.py"

    def test_a_line_suffix_still_comes_off_a_path_carrying_a_colon(self, rp):
        body = "- **[M1]** **`C:/src/x.py:12`** — Fix bug"
        results = rp._extract_body_findings(body)
        assert results[0]["path"] == "C:/src/x.py"


class TestFormatFindingLine:
    def test_with_path_and_line(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=42,
            end_line=None, body="Fix bug", posted_id="M1",
        )
        result = rp._format_finding_line(f)
        assert "**[M1] [must-fix]**" in result
        assert "`file.go:42`" in result
        assert "Fix bug" in result
        assert result.startswith("- ")

    def test_with_path_only_no_line(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=None,
            end_line=None, body="Refactor", posted_id="S1",
        )
        result = rp._format_finding_line(f)
        assert "`file.go`" in result
        assert "Refactor" in result

    def test_pathless_finding(self, rp):
        f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern across files.", posted_id="I1",
        )
        result = rp._format_finding_line(f)
        assert "**[I1] [idiom]**" in result
        assert "Good pattern across files." in result
        assert "`" not in result.split("**")[-1]


class TestBuildPermalink:
    def test_with_line_number_only(self, rp):
        m = rp._PERMALINK_REF_RE.search("see file.go:42")
        result = rp._build_permalink("owner/repo", "abc123", m)
        assert "https://github.com/owner/repo/blob/abc123/file.go#L42" in result
        assert "`file.go:42`" in result

    def test_with_line_range(self, rp):
        m = rp._PERMALINK_REF_RE.search("see file.go:10-20")
        result = rp._build_permalink("owner/repo", "def456", m)
        assert "https://github.com/owner/repo/blob/def456/file.go#L10-L20" in result
        assert "`file.go:10-20`" in result

    def test_url_format_correctness(self, rp):
        m = rp._PERMALINK_REF_RE.search("at pkg/handler.go:5")
        result = rp._build_permalink("org/repo", "sha123", m)
        assert result.startswith("[")
        assert "](https://github.com/org/repo/blob/sha123/pkg/handler.go#L5)" in result


class TestResolvePermalinks:
    DIFF = (
        "diff --git a/pkg/handler.go b/pkg/handler.go\n"
        "--- a/pkg/handler.go\n"
        "+++ b/pkg/handler.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_reference_to_file_in_diff_uses_head_ref(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see pkg/handler.go:42",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "head-sha", "base-sha")
        assert "head-sha" in f.body
        assert "base-sha" not in f.body

    def test_reference_to_file_not_in_diff_uses_base_ref(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see other.go:10",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "head-sha", "base-sha")
        assert "base-sha" in f.body
        assert "head-sha" not in f.body

    def test_empty_refs_no_transformation(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see other.go:10",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "", "")
        assert f.body == "see other.go:10"

    def test_no_matching_references(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="just plain text with no refs",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "head-sha", "base-sha")
        assert f.body == "just plain text with no refs"


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


class TestDedupAgainstPostedEdgeCases:
    def _make_finding(self, rp, id_str, path, body):
        return rp.Finding(
            id=id_str, severity=id_str[0], seq=int(id_str[1:]),
            path=path, line=42, end_line=None, body=body,
        )

    @patch("review_dedup._fetch_bot_comments")
    def test_jaccard_at_threshold_boundary(self, mock_fetch, rp):
        # Build words so Jaccard is exactly 0.6: 3 shared out of 5 total
        # a = {"a", "b", "c"}, b = {"a", "b", "c", "d", "e"} => 3/5 = 0.6
        mock_fetch.return_value = [
            {"path": "file.go", "body": "a b c d e"},
        ]
        f = self._make_finding(rp, "M1", "file.go", "a b c")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(deduped) == 1
        assert deduped[0].skip_reason == "duplicate of existing comment"

    @patch("review_dedup._fetch_bot_comments")
    def test_empty_path_on_both_sides_not_matched(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "", "body": "missing error check"},
        ]
        f = self._make_finding(rp, "M1", "", "missing error check")
        # Override to set path="" since _make_finding sets path to the arg
        f.path = ""
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0


class TestHeadShaRegex:
    def test_standard_sha(self, rp):
        text = "<!-- head_sha: abc123def456 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is not None
        assert m.group(1) == "abc123def456"

    def test_uppercase_hex_does_not_match(self, rp):
        text = "<!-- head_sha: ABC123DEF456 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is None

    def test_short_sha_matches(self, rp):
        text = "<!-- head_sha: abc1234 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is not None
        assert m.group(1) == "abc1234"


class TestReclassifyAndRetry:
    DIFF_OLD = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )
    DIFF_NEW = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -40,3 +40,10 @@\n"
        "+line\n"
    )

    def _make_args(self, repo="org/repo", pr="1"):
        import argparse
        args = argparse.Namespace()
        args.repo = repo
        args.pr = pr
        args.review_file = "/tmp/test-review.md"
        args.submit = False
        return args

    def test_reclassify_recovers_inline_with_fresh_diff(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        body_f = rp.Finding(
            id="N1", severity="N", seq=1, path="file.go", line=None,
            end_line=None, body="Nit", full_path="file.go",
            classification="file_level", skip_reason="no line number",
        )

        with (
            patch("review_github._get_diff", return_value=self.DIFF_NEW),
            patch("review_posting._post_chunked_review", return_value=[{"id": 42}]),
            patch("review_github._check_existing_pending", return_value=None),
        ):
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [body_f],
                "abc123", 30, {"M", "N"}, False,
            )
            assert len(inline) == 1
            assert inline[0].posted_id == "M1"
            assert results == [{"id": 42}]

    def test_reclassify_demotes_all_when_retry_also_fails(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )

        call_count = [0]
        def failing_then_succeeding(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise rp.LineResolutionError("still broken")
            return [{"id": 99}]

        with (
            patch("review_github._get_diff", return_value=self.DIFF_NEW),
            patch("review_posting._post_chunked_review", side_effect=failing_then_succeeding),
            patch("review_github._check_existing_pending", return_value=None),
        ):
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [],
                "abc123", 30, {"M"}, False,
            )
            assert len(inline_comments) == 0
            assert len(inline) == 0
            assert results == [{"id": 99}]

    def test_reclassify_preserves_skipped_findings_in_body(self, rp):
        inline_f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=5,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        skipped_f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern",
            classification="skipped", skip_reason="general finding",
        )

        with (
            patch("review_github._get_diff", return_value=self.DIFF_NEW),
            patch("review_posting._post_chunked_review", return_value=[{"id": 42}]),
            patch("review_github._check_existing_pending", return_value=None),
        ):
            _, _, body, _, _ = rp._reclassify_and_retry(
                self._make_args(), [inline_f], [skipped_f],
                "abc123", 30, {"M", "I"}, False,
            )
            assert any(f.body == "Good pattern" for f in body)


class TestFormatCommentBody:
    def test_includes_sha_drift_header(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=10,
            end_line=None, body="Fix this", posted_id="M1",
        )
        body = rp._format_comment_body([f], {"M"}, "aaa1111", "bbb2222", 3)
        assert "aaa1111" in body
        assert "bbb2222" in body
        assert "3 new commits" in body
        assert "Fix this" in body

    def test_single_commit_no_plural(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1,
            end_line=None, body="body", posted_id="S1",
        )
        body = rp._format_comment_body([f], {"S"}, "aaa", "bbb", 1)
        assert "1 new commit)" in body
        assert "commits" not in body

    def test_renumbers_findings(self, rp):
        findings = [
            rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=1,
                       end_line=None, body="first"),
            rp.Finding(id="M2", severity="M", seq=2, path="b.go", line=2,
                       end_line=None, body="second"),
        ]
        body = rp._format_comment_body(findings, {"M"}, "aaa", "bbb", 1)
        assert "[M1]" in body
        assert "[M2]" in body


class TestShaDriftReverify:
    """SHA drift should re-verify positions against the current diff and post
    inline, not fall back to a plain issue comment."""

    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    REVIEW_TEXT = (
        "<!-- head_sha: aaa1111bbb2222 -->\n"
        "## Summary\nOk\n\n"
        "## Must fix\n"
        "- **[M1]** **`file.go:5`** — Fix this bug\n"
    )

    def _make_args(self, tmp_path, repo="org/repo", pr="1"):
        import argparse
        review_dir = tmp_path / "review"
        review_dir.mkdir(exist_ok=True)
        review_file = review_dir / "review.md"
        review_file.write_text(self.REVIEW_TEXT)
        args = argparse.Namespace()
        args.repo = repo
        args.pr = pr
        args.review_file = str(review_file)
        args.dry_run = False
        args.submit = False
        args.chunk_size = 30
        args.severity = "M,S,N,I"
        args.debug = False
        return args, review_file

    def test_drift_posts_inline_not_comment(self, rp, tmp_path):
        args, review_file = self._make_args(tmp_path)
        sidecar = rp.ReviewMeta(repo="org/repo")
        trail = MagicMock()

        new_head = "ccc3333ddd4444"
        pr_data = rp.PRData(
            viewer_login="bot",
            head_sha=new_head, head_ref="feat", base_ref="main",
            reviews=[],
        )

        post_calls = []
        def capture_post(endpoint, **kw):
            post_calls.append(endpoint)
            return {"id": 42}

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=self.DIFF),
            patch.object(rp, "dedup_against_posted", return_value=(
                [rp.Finding(id="M1", severity="M", seq=1, path="file.go",
                            line=5, end_line=None, body="Fix this bug",
                            posted_id="M1", classification="inline",
                            full_path="file.go")],
                [],
            )),
            patch.object(rp, "fetch_bot_reviews", return_value=[]),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=None),
            patch("gh_client.api_json", side_effect=capture_post),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp._run_post(trail, args, "org/repo", sidecar, review_file)

        assert any("pulls" in c and "reviews" in c for c in post_calls), \
            f"Expected review API call, got: {post_calls}"
        assert not any("issues" in c for c in post_calls), \
            "Should not fall back to issue comment API"

    def test_the_classification_diff_is_always_fetched(self, rp, tmp_path):
        """Nothing writes a diff into the sidecar, so nothing reads one back
        out: a cached diff only a test ever populates is a branch the pipeline
        never takes, and findings would be placed against it."""
        args, review_file = self._make_args(tmp_path)
        sidecar = rp.ReviewMeta(repo="org/repo")
        trail = MagicMock()

        new_head = "ccc3333ddd4444"
        pr_data = rp.PRData(
            viewer_login="bot",
            head_sha=new_head, head_ref="feat", base_ref="main",
            reviews=[],
        )

        diff_calls = []
        def capture_diff(repo, pr):
            diff_calls.append(repo)
            return self.DIFF

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", side_effect=capture_diff),
            patch.object(rp, "dedup_against_posted", return_value=([], [])),
            patch.object(rp, "fetch_bot_reviews", return_value=[]),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=None),
            patch("gh_client.api_json", return_value={"id": 42}),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp._run_post(trail, args, "org/repo", sidecar, review_file)

        assert len(diff_calls) == 1, "Should fetch fresh diff, not use sidecar"

    def test_drift_records_sha_in_tracking(self, rp, tmp_path):
        args, review_file = self._make_args(tmp_path)
        sidecar = rp.ReviewMeta(repo="org/repo")
        trail = MagicMock()

        new_head = "ccc3333ddd4444"
        pr_data = rp.PRData(
            viewer_login="bot",
            head_sha=new_head, head_ref="feat", base_ref="main",
            reviews=[],
        )

        with (
            patch.object(rp, "fetch_pr_data", return_value=pr_data),
            patch.object(rp, "_get_diff", return_value=self.DIFF),
            patch.object(rp, "dedup_against_posted", return_value=(
                [rp.Finding(id="M1", severity="M", seq=1, path="file.go",
                            line=5, end_line=None, body="Fix",
                            posted_id="M1", classification="inline",
                            full_path="file.go")],
                [],
            )),
            patch.object(rp, "fetch_bot_reviews", return_value=[]),
            patch.object(rp, "check_review_already_posted", return_value=set()),
            patch.object(rp, "_check_existing_pending", return_value=None),
            patch("gh_client.api_json", return_value={"id": 42}),
            patch.object(rp, "resolve_permalinks"),
        ):
            rp._run_post(trail, args, "org/repo", sidecar, review_file)

        post_file = review_file.parent / "post.jsonl"
        tracking = serde_from_dict(PostTracking, json.loads(post_file.read_text()))
        assert tracking.review_sha == "aaa1111bbb2222"
        assert tracking.head_sha_at_post == new_head
        assert tracking.sha_drifted is True
        assert tracking.posted_as == PostedAs.REVIEW.value


class TestHandleChunkFailure:
    def test_exits_with_error(self, rp):
        with pytest.raises(SystemExit):
            rp._handle_chunk_failure(2, 3, [])

    def test_logs_partial_post(self, rp, capsys):
        with pytest.raises(SystemExit):
            rp._handle_chunk_failure(2, 3, [{"id": 100}])
        err = capsys.readouterr().err
        assert "Partial post" in err
        assert "100" in err


class TestCountNewCommits:
    def test_finds_review_sha_and_counts_after(self, rp):
        commits = [
            {"sha": "aaa111"},
            {"sha": "bbb222"},
            {"sha": "ccc333"},
        ]
        with patch("gh_client.api", return_value=CmdResult(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "bbb222") == 1

    def test_no_match_returns_total(self, rp):
        commits = [{"sha": "aaa"}, {"sha": "bbb"}]
        with patch("gh_client.api", return_value=CmdResult(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "zzz") == 2

    def test_api_failure_returns_zero(self, rp):
        with patch("gh_client.api", return_value=CmdResult(1)):
            assert rp._count_new_commits("org/repo", "1", "aaa") == 0

    def test_prefix_match(self, rp):
        commits = [{"sha": "aabbccdd1234"}, {"sha": "eeff5678"}]
        with patch("gh_client.api", return_value=CmdResult(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "aabbccdd") == 1


class TestCollectInlineComments:
    def test_filters_by_bot_user(self, rp):
        comments = [
            {"path": "a.go", "body": "fix", "user": {"login": "bot"}},
            {"path": "b.go", "body": "nit", "user": {"login": "human"}},
            {"path": "c.go", "body": "issue", "user": {"login": "bot"}},
        ]
        with patch("gh_client.api_json", return_value=comments):
            result = rp._collect_inline_comments("org/repo", "1", "bot")
            assert len(result) == 2
            assert all(r["path"] in ("a.go", "c.go") for r in result)

    def test_empty_comments(self, rp):
        with patch("gh_client.api_json", return_value=[]):
            result = rp._collect_inline_comments("org/repo", "1", "bot")
            assert result == []

    def test_the_identity_marker_comes_off_before_anything_compares_the_text(self, rp):
        """The marker is a handle on the finding, not part of what it says.

        A fresh finding carries no marker, so left on, it is two tokens only the
        posted side has — every similarity score against it comes out lower than
        the wording earns.
        """
        comments = [{
            "path": "a.go",
            "body": "**[M1] [must-fix]** <!-- sid:abc12345 --> Fix bug",
            "user": {"login": "bot"},
        }]
        with patch("gh_client.api_json", return_value=comments):
            result = rp._collect_inline_comments("org/repo", "1", "bot")
        assert result[0]["body"] == "**[M1] [must-fix]** Fix bug"


class TestCollectReviewFindings:
    def test_extracts_from_bot_review_bodies(self, rp):
        reviews = [
            {"body": "- **[M1]** **`a.go:1`** — issue one", "user": {"login": "bot"}},
            {"body": "no findings", "user": {"login": "human"}},
        ]
        with patch("gh_client.api_json", return_value=reviews):
            result = rp._collect_review_findings("org/repo", "1", "bot")
            assert len(result) == 1
            assert result[0]["path"] == "a.go"

    def test_skips_empty_bodies(self, rp):
        reviews = [
            {"body": "", "user": {"login": "bot"}},
        ]
        with patch("gh_client.api_json", return_value=reviews):
            result = rp._collect_review_findings("org/repo", "1", "bot")
            assert result == []


class TestFetchBotComments:
    def test_combines_inline_and_review_findings(self, rp):
        with (
            patch("gh_client.login", return_value="bot"),
            patch("gh_client.api_json", side_effect=[
                [{"path": "a.go", "body": "inline", "user": {"login": "bot"}}],
                [{"body": "- **[M1]** **`b.go:1`** — review", "user": {"login": "bot"}}],
            ]),
        ):
            result = rp._fetch_bot_comments("org/repo", "1")
            assert len(result) == 2

    def test_api_failure_returns_empty(self, rp):
        with patch("gh_client.login", return_value=""):
            assert rp._fetch_bot_comments("org/repo", "1") == []

    def test_empty_login_returns_empty(self, rp):
        with patch("gh_client.login", return_value=""):
            assert rp._fetch_bot_comments("org/repo", "1") == []


class TestDryRunIntegration:
    """Integration tests exercising the review-post --dry-run code path via subprocess."""

    from pathlib import Path as _Path
    _REPO_ROOT = _Path(__file__).resolve().parent.parent
    _REVIEW_POST = _REPO_ROOT / "ai" / "claude" / "bin" / "review-post"

    REVIEW_MD = (
        "# Review: test-org/test-repo#42 — Fix handler\n"
        "<!-- head_sha: abc123def456 -->\n"
        "\n"
        "## File Triage\n"
        "- `handler.go` — **Tier 2** (application logic)\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- **[M1]** **`handler.go:11`** — missing error check\n"
        "\n"
        "## Should fix\n"
        "\n"
        "- **[S1]** **`handler.go:25`** — unclear variable name\n"
        "\n"
        "## Nit\n"
        "\n"
        "- **[N1]** **`config.sh:2`** — trailing whitespace\n"
        "\n"
        "## Verdict\n"
        "\n"
        "Request changes.\n"
    )

    DIFF_TEXT = (
        "diff --git a/handler.go b/handler.go\n"
        "--- a/handler.go\n"
        "+++ b/handler.go\n"
        "@@ -10,3 +10,5 @@ func main() {\n"
        "     existing()\n"
        "+    added()\n"
        "+    alsoAdded()\n"
        "     kept()\n"
        "diff --git a/config.sh b/config.sh\n"
        "--- a/config.sh\n"
        "+++ b/config.sh\n"
        "@@ -1,3 +1,4 @@\n"
        " #!/bin/bash\n"
        "+set -e\n"
        " echo hello\n"
    )

    def _setup_review(self, tmp_path):
        """Write the review markdown and sidecar meta.json into tmp_path."""
        review_dir = tmp_path / "test-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        review_file.write_text(self.REVIEW_MD)
        meta = {"repo": "test/repo", "head_sha": "abc123def456"}
        (review_dir / "meta.json").write_text(json.dumps(meta))
        return review_file

    @classmethod
    def _gh_stub(cls, tmp_path):
        """A `gh` on PATH answering the one API call a dry run makes.

        The run fetches the PR diff to place findings on lines. Without a stub
        the subprocess asks the real API about a repo that does not exist, so
        what these tests assert would depend on the network answering.
        """
        bin_dir = tmp_path / "stub-bin"
        bin_dir.mkdir(exist_ok=True)
        diff_file = bin_dir / "pr.diff"
        diff_file.write_text(cls.DIFF_TEXT)
        stub = bin_dir / "gh"
        stub.write_text(f"#!/bin/bash\ncat {diff_file}\n")
        stub.chmod(0o755)
        return bin_dir

    def _run_dry_run(self, review_file, tmp_path, extra_args=None):
        """Run review-post --dry-run and return the CompletedProcess."""
        cmd = [
            sys.executable, str(self._REVIEW_POST),
            "--pr", "42",
            "--review-file", str(review_file),
            "--dry-run",
        ]
        if extra_args:
            cmd.extend(extra_args)
        import os
        env = {
            **os.environ, "NO_COLOR": "1", "TERM": "dumb",
            "PATH": f"{self._gh_stub(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        }
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )

    @staticmethod
    def _extract_json(stdout):
        """Extract the JSON object from stdout which may contain log lines."""
        # The JSON payload is printed between blank lines. Use greedy match
        # to find the outermost { ... } — lazy .*? would stop at the first
        # } at column 0, truncating nested objects.
        match = re.search(r"^\{.*^\}", stdout, re.MULTILINE | re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No JSON payload found in stdout: {stdout!r}")

    def test_dry_run_exits_0(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_dry_run_outputs_json_payload(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "body" in payload or "comments" in payload

    def test_dry_run_omits_file_triage_from_body(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "File Triage" not in payload["body"]
        assert "Tier 2" not in payload["body"]

    def test_dry_run_with_severity_filter(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, tmp_path, extra_args=["--severity", "M"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        # With only M severity, the body/comments should reference M1 but not S1 or N1
        payload_text = json.dumps(payload)
        assert "M1" in payload_text
        assert "S1" not in payload_text
        assert "N1" not in payload_text

    def test_dry_run_with_missing_review_file_exits_nonzero(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist.md"
        result = self._run_dry_run(nonexistent, tmp_path)
        assert result.returncode != 0

    def test_dry_run_without_repo_in_meta_exits_nonzero(self, tmp_path):
        review_dir = tmp_path / "no-repo-meta"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        review_file.write_text(self.REVIEW_MD)
        # meta.json present but missing the 'repo' field
        (review_dir / "meta.json").write_text(json.dumps({"head_sha": "abc123"}))
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode != 0
        assert "repo" in result.stderr.lower() or "meta" in result.stderr.lower()

    def test_dry_run_includes_static_analysis(self, tmp_path):
        review_with_sa = (
            "# Review: test-org/test-repo#42 — Fix handler\n"
            "<!-- head_sha: abc123def456 -->\n"
            "\n"
            "## Summary\n"
            "Clean refactor.\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`handler.go:11`** — missing error check\n"
            "\n"
            "## Static Analysis\n"
            "\n"
            "### Nesting depth\n"
            "1 violation in 1 of 3 files checked\n"
            "\n"
            "- **`config.sh:42`** — depth 5 exceeds limit 4 (in main())\n"
            "\n"
            "## Verdict\n"
            "\n"
            "Request changes.\n"
        )
        review_dir = tmp_path / "sa-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        review_file.write_text(review_with_sa)
        meta = {"repo": "test/repo", "head_sha": "abc123def456"}
        (review_dir / "meta.json").write_text(json.dumps(meta))
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "Nesting depth" in payload["body"]
        assert "depth 5 exceeds limit 4" in payload["body"]

    def test_dry_run_auto_discovers_unknown_section(self, tmp_path):
        review_with_custom = (
            "# Review: test-org/test-repo#42 — Fix handler\n"
            "<!-- head_sha: abc123def456 -->\n"
            "\n"
            "## Summary\n"
            "Clean refactor.\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`handler.go:11`** — missing error check\n"
            "\n"
            "## Performance Notes\n"
            "\n"
            "Consider caching the DB query at handler.go:15.\n"
            "\n"
            "## Verdict\n"
            "\n"
            "Request changes.\n"
        )
        review_dir = tmp_path / "autodiscovery-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        review_file.write_text(review_with_custom)
        meta = {"repo": "test/repo", "head_sha": "abc123def456"}
        (review_dir / "meta.json").write_text(json.dumps(meta))
        result = self._run_dry_run(review_file, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "Performance Notes" in payload["body"]
        assert "caching the DB query" in payload["body"]


class TestCheckReviewAlreadyPosted:
    def test_no_reviews_returns_empty(self, rp):
        assert rp.check_review_already_posted([], "some body") == []

    def test_match_returns_ids(self, rp):
        bot_reviews = [
            {"id": 42, "body": "some body text here", "state": "COMMENTED"},
        ]
        assert rp.check_review_already_posted(bot_reviews, "some body text here") == [42]

    def test_zero_similarity_not_matched(self, rp):
        bot_reviews = [
            {"id": 42, "body": "completely different content", "state": "COMMENTED"},
        ]
        assert rp.check_review_already_posted(bot_reviews, "unrelated words here") == []

    def test_partial_overlap_below_threshold(self, rp):
        shared = "alpha bravo charlie delta echo foxtrot golf hotel"
        different = "india juliet kilo lima mike november oscar papa quebec romeo sierra tango"
        bot_reviews = [
            {"id": 42, "body": f"{shared} {different}", "state": "COMMENTED"},
        ]
        assert rp.check_review_already_posted(bot_reviews, f"{shared} unique words not in review") == []


class TestFetchBotReviews:
    def test_returns_bot_reviews(self, rp, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 1, "user": {"login": "bot"}, "state": "COMMENTED", "body": "review text"},
            {"id": 2, "user": {"login": "human"}, "state": "COMMENTED", "body": "human review"},
            {"id": 3, "user": {"login": "bot"}, "state": "PENDING", "body": "pending"},
        ])
        result = rp.fetch_bot_reviews("org/repo", "1")
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_ignores_pending(self, rp, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 42, "body": "some body text here", "state": "PENDING", "user": {"login": "bot"}},
        ])
        assert rp.fetch_bot_reviews("org/repo", "1") == []

    def test_ignores_dismissed(self, rp, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 42, "body": "some body text here", "state": "DISMISSED", "user": {"login": "bot"}},
        ])
        assert rp.fetch_bot_reviews("org/repo", "1") == []

    def test_ignores_other_users(self, rp, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "bot")
        monkeypatch.setattr("gh_client.api_json", lambda *a, **k: [
            {"id": 42, "body": "some body text here", "state": "COMMENTED", "user": {"login": "alice"}},
        ])
        assert rp.fetch_bot_reviews("org/repo", "1") == []

    def test_api_failure_returns_empty(self, rp, monkeypatch):
        monkeypatch.setattr("gh_client.login", lambda *a, **k: "")
        assert rp.fetch_bot_reviews("org/repo", "1") == []


class TestReviewSections:
    REVIEW_TEXT = (
        "# Review: test-org/test-repo#42 — Fix handler\n"
        "<!-- head_sha: abc123def456 -->\n"
        "\n"
        "## Summary\n"
        "Clean refactor with good test coverage.\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- **[M1]** **`handler.go:11`** — missing error check\n"
        "\n"
        "## Should fix\n"
        "\n"
        "- **[S1]** **`handler.go:25`** — unused variable\n"
        "\n"
        "## Static Analysis\n"
        "\n"
        "### Nesting depth\n"
        "1 violation in 1 of 3 files checked\n"
        "\n"
        "## Verdict\n"
        "\n"
        "Request changes — M1 is a blocker.\n"
    )

    def test_from_text_extracts_known_sections(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        assert sections.get("summary") == "Clean refactor with good test coverage."
        assert "Request changes" in sections.get("verdict")
        assert "Nesting depth" in sections.get("static_analysis")

    def test_from_text_ignores_severity_headers(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        assert sections.get("must_fix") == ""
        assert sections.get("should_fix") == ""

    def test_get_returns_empty_for_absent(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        assert sections.get("nonexistent") == ""

    def test_before_findings_returns_ordered_pairs(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        before = sections.before_findings()
        keys = [cfg.key for cfg, _ in before]
        assert "summary" in keys
        assert "verdict" in keys
        assert keys.index("summary") < keys.index("verdict")

    def test_after_findings_returns_passthrough(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        after = sections.after_findings()
        keys = [cfg.key for cfg, _ in after]
        assert "static_analysis" in keys

    def test_empty_constructor(self, rp):
        sections = rp.ReviewSections()
        assert sections.get("summary") == ""
        assert sections.before_findings() == []
        assert sections.after_findings() == []

    def test_auto_discovers_unknown_section(self, rp):
        text = (
            "## Summary\n"
            "Quick summary.\n"
            "\n"
            "## Performance Notes\n"
            "Consider caching the DB query.\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`a.go:1`** — bug\n"
        )
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("performance_notes") == "Consider caching the DB query."
        after = sections.after_findings()
        keys = [cfg.key for cfg, _ in after]
        assert "performance_notes" in keys

    def test_file_triage_is_not_extracted(self, rp):
        text = (
            "## File Triage\n"
            "- `a.go` — **Tier 1** (core logic)\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`a.go:1`** — bug\n"
        )
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("file_triage") == ""
        assert sections.after_findings() == []

    def test_before_findings_omits_empty(self, rp):
        text = "## Must fix\n\n- **[M1]** **`a.go:1`** — bug\n"
        sections = rp.ReviewSections.from_text(text)
        assert sections.before_findings() == []

    def test_severity_alias_nits_ignored(self, rp):
        text = "## Nits\n\n- **[N1]** **`a.go:1`** — nit\n"
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("nits") == ""


class TestDeclinedFindingsAreNotAskedFor:
    """A declined finding was considered and rejected. Posting it as a comment
    asks for work the review already decided against, so it is stated in the
    body instead — and it stays stated whatever the diff says about its line."""

    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def _declined(self, rp):
        return rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=5, end_line=None,
            body="use a constant *(declined — the literal is clearer here)*",
            declined=True, decline_reason="the literal is clearer here",
        )

    def test_a_declined_finding_in_a_hunk_is_skipped_not_inlined(self, rp):
        f = self._declined(rp)
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert (len(inline), len(fl), len(skipped)) == (0, 0, 1)
        assert skipped[0].skip_reason == rp.SKIP_DECLINED

    def test_an_open_finding_beside_it_still_goes_inline(self, rp):
        open_finding = rp.Finding(
            id="S2", severity="S", seq=2, path="file.go", line=6, end_line=None,
            body="real bug",
        )
        inline, _, skipped = rp.classify_findings(
            [self._declined(rp), open_finding], self.DIFF,
        )
        assert [f.id for f in inline] == ["S2"]
        assert [f.id for f in skipped] == ["S1"]

    def test_the_body_states_a_decline_apart_from_what_it_asks_for(self, rp):
        declined = self._declined(rp)
        declined.posted_id = "S1"
        demoted = rp.Finding(
            id="S2", severity="S", seq=2, path="file.go", line=90, end_line=None,
            body="real bug", posted_id="S2",
        )
        text = rp.format_body_text([demoted, declined], False, {"S"})
        heading = "**Declined — considered and not carried forward:**"
        assert heading in text
        assert text.index("real bug") < text.index(heading), \
            "the findings being asked for come before the ones that were declined"
        assert text.index(heading) < text.index("use a constant")

    def test_a_decline_alone_still_renders_its_block(self, rp):
        declined = self._declined(rp)
        declined.posted_id = "S1"
        text = rp.format_body_text([declined], False, {"S"})
        assert "**Declined — considered and not carried forward:**" in text
        assert "use a constant" in text


class TestPostSkipsResolvedAndDeclinedFindings:
    """What reaches the payload from a review a fix pass has already worked
    through: not the findings it ticked, and not the ones it declined."""

    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    REVIEW_TEXT = (
        "<!-- head_sha: aaa1111bbb2222 -->\n"
        "## Summary\nOk\n\n"
        "## Should fix\n"
        "- [x] **[S1]** **`file.go:4`** — already fixed by the fix pass\n"
        "- [ ] **[S2]** **`file.go:5`** — declined on the merits. "
        "*(declined — the tradeoff is deliberate)*\n"
        "- [ ] **[S3]** **`file.go:6`** — genuinely open\n"
    )

    def _dry_run_payload(self, rp, tmp_path, capsys, review_text=None):
        import argparse
        review_dir = tmp_path / "review"
        review_dir.mkdir(exist_ok=True)
        review_file = review_dir / "review.md"
        review_file.write_text(review_text or self.REVIEW_TEXT)

        args = argparse.Namespace()
        args.repo = "org/repo"
        args.pr = "1"
        args.review_file = str(review_file)
        args.dry_run = True
        args.submit = False
        args.chunk_size = 30
        args.severity = "M,S,N,I"
        args.debug = False

        with patch.object(rp, "_get_diff", return_value=self.DIFF):
            rp._run_post(MagicMock(), args, "org/repo", rp.ReviewMeta(repo="org/repo"),
                         review_file)

        out = capsys.readouterr().out
        return json.loads(out[out.index("{"):out.rindex("}") + 1])

    def test_only_the_open_finding_is_commented_on(self, rp, tmp_path, capsys):
        payload = self._dry_run_payload(rp, tmp_path, capsys)
        bodies = [c["body"] for c in payload["comments"]]
        assert len(bodies) == 1
        assert "genuinely open" in bodies[0]

    def test_a_fixed_finding_reaches_neither_comments_nor_body(self, rp, tmp_path, capsys):
        payload = self._dry_run_payload(rp, tmp_path, capsys)
        whole = json.dumps(payload)
        assert "already fixed by the fix pass" not in whole

    def test_a_declined_finding_is_stated_in_the_body_only(self, rp, tmp_path, capsys):
        payload = self._dry_run_payload(rp, tmp_path, capsys)
        assert "declined on the merits" in payload["body"]
        assert not any("declined on the merits" in c["body"] for c in payload["comments"])

    def test_a_wholly_resolved_review_posts_nothing(self, rp, tmp_path, capsys):
        text = (
            "<!-- head_sha: aaa1111bbb2222 -->\n"
            "## Summary\nOk\n\n"
            "## Should fix\n"
            "- [x] **[S1]** **`file.go:4`** — already fixed\n"
        )
        with pytest.raises(SystemExit) as exc:
            self._dry_run_payload(rp, tmp_path, capsys, review_text=text)
        assert exc.value.code == 0
