"""Tests for the review body `review.render.comment_body` writes — finding
lines, the permalinks `review.format` resolves, and what the body leaves out
for a finding the fix pass declined.
"""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# The module the `rp` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `rp`.
import cli.review_post  # noqa: E402,F401
import review.dedup  # noqa: E402
import review.render  # noqa: E402

from review.record import ProseSection  # noqa: E402
from review_record_support import record  # noqa: E402


def _comment(findings, has_inline, severity_filter, **fields):
    return review.render.comment_body(
        record(**fields), findings, has_inline=has_inline,
        severity_filter=severity_filter,
    )


class TestCommentBody:
    def test_with_inline_shows_have_some_comments(self):
        result = _comment([], True, {"M", "S", "N"})
        assert "Have some comments" in result

    def test_without_inline_shows_review_findings(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=42,
            end_line=None, body="Fix it", posted_id="M1",
        )
        result = _comment([f], False, {"M", "S", "N"})
        assert "Review findings" in result

    def test_no_findings_at_all_drops_the_lead_in(self):
        result = _comment(
            [], False, {"M", "S", "N"},
            summary="Clean.", static_analysis="### Nesting depth",
        )
        assert "Review findings" not in result
        assert "Have some comments" not in result
        assert "Clean." in result
        assert "### Nesting depth" in result

    def test_no_findings_and_no_sections_is_empty(self):
        assert _comment([], False, {"M"}) == ""

    def test_body_findings_listed_with_path_refs(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=42,
            end_line=None, body="Fix it", full_path="pkg/handler.go", posted_id="M1",
        )
        result = _comment([f], True, {"M"})
        assert "**[M1] [must-fix]**" in result
        assert "`handler.go:42`" in result
        assert "Fix it" in result

    def test_no_body_findings_single_line(self):
        text = _comment([], True, {"M", "S"})
        assert len(text.strip().split("\n")) == 1

    def test_pathless_finding_omits_backticks(self, rp):
        f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern across files.", posted_id="I1",
        )
        result = _comment([f], True, {"I"})
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
        result = _comment(findings, True, {"S", "N"})
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
        result = _comment(findings, True, {"N"})
        assert "<details open>" in result
        assert "<summary>Nit (2)</summary>" in result
        assert "</details>" in result
        assert "N1" in result
        assert "N2" in result

    def test_summary_and_verdict_prepended(self):
        result = _comment(
            [], True, {"M"}, summary="Clean refactor.", verdict="Approve.",
        )
        assert result.startswith("## Summary")
        assert "Clean refactor." in result
        assert "### Verdict" in result
        assert "Approve." in result
        assert "---" in result
        assert result.index("## Summary") < result.index("---")
        assert result.index("---") < result.index("Have some comments")

    def test_verdict_action_prefix_stripped(self):
        result = _comment(
            [], True, {"M"}, summary="Summary text.",
            verdict="Request changes — M1 and M2 are blockers.",
        )
        assert "### Verdict" in result
        assert "M1 and M2 are blockers." in result
        assert "Request changes" not in result

    def test_verdict_approve_prefix_stripped(self):
        result = _comment(
            [], True, {"M"}, summary="Summary text.",
            verdict="Approve — clean code.",
        )
        assert "### Verdict" in result
        assert "clean code." in result
        assert "Approve" not in result

    def test_verdict_action_prefix_stripped_plain_hyphen(self):
        result = _comment(
            [], True, {"M"}, summary="Summary text.",
            verdict="Needs discussion - looks good.",
        )
        assert "### Verdict" in result
        assert "looks good." in result
        assert "Needs discussion" not in result

    def test_verdict_bold_action_prefix_stripped(self):
        result = _comment(
            [], True, {"M"}, summary="Summary text.",
            verdict="**Request changes** — M1 and M2 are blockers.",
        )
        assert "### Verdict" in result
        assert "M1 and M2 are blockers." in result
        assert "Request changes" not in result

    def test_verdict_without_action_prefix_unchanged(self):
        result = _comment(
            [], True, {"M"}, summary="Summary text.",
            verdict="Looks good overall.",
        )
        assert "### Verdict" in result
        assert "Looks good overall." in result

    def test_summary_without_verdict(self):
        result = _comment([], False, {"M"}, summary="Clean refactor.")
        assert result.startswith("## Summary")
        assert "Clean refactor." in result
        assert "### Verdict" not in result
        assert "---" in result
        assert result.index("## Summary") < result.index("---")
        assert result.endswith("---")

    def test_empty_summary_omitted(self):
        result = _comment([], True, {"M"})
        assert "## Summary" not in result
        assert "### Verdict" not in result
        assert "---" not in result

    def test_outside_diff_header_only_for_demoted_findings(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix", posted_id="S1"),
        ]
        result = _comment(findings, True, {"S"})
        assert "**Findings outside the diff:**" in result
        assert "Findings not in the diff" not in result

    def test_no_outside_diff_header_for_nits_only(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit issue", posted_id="N1"),
        ]
        result = _comment(findings, True, {"N"})
        assert "Findings outside the diff" not in result
        assert "<details open>" in result

    def test_nits_drop_severity_label(self, rp):
        f = rp.Finding(
            id="N1", severity="N", seq=1, path="a.go", line=10,
            end_line=None, body="Style issue", posted_id="N1",
        )
        result = _comment([f], True, {"N"})
        assert "**[N1]**" in result
        assert "[nit]" not in result

    def test_body_findings_read_back_through_the_dedup_reader(self, rp):
        must = rp.Finding(
            id="M1", severity="M", seq=1, path="a.py", line=3,
            end_line=None, body="lock never released", posted_id="M1",
        )
        idiom = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="pattern holds", posted_id="I1",
        )
        declined = rp.Finding(
            id="S1", severity="S", seq=1, path="b.py", line=12,
            end_line=None, body="use a constant", posted_id="S1",
            declined=True,
        )
        nit = rp.Finding(
            id="N1", severity="N", seq=1, path="c.py", line=1,
            end_line=None, body="trailing space", posted_id="N1",
        )
        body = _comment([must, idiom, declined, nit], False, {"M", "S", "I", "N"})
        found = {item.body: item.path for item in review.dedup._extract_body_findings(body)}
        assert found["lock never released"] == "a.py"
        assert found["use a constant"] == "b.py"
        assert found["trailing space"] == "c.py"
        assert "- **[N1]** `c.py:1` — trailing space" in body
        assert "pattern holds" in body

    def test_the_models_line_closes_the_body(self):
        rec = record(summary="Clean.", extra=(ProseSection("Review gaps", "- group 2"),))
        body = review.render.comment_body(rec, [], has_inline=False, severity_filter={"M"},
                                          models="single m (low)")
        assert body.endswith("- group 2\n\n<sub>Models: single m (low)</sub>")

    def test_no_models_line_without_a_body(self):
        body = review.render.comment_body(record(), [], has_inline=False, severity_filter={"M"},
                                          models="single m (low)")
        assert body == ""

    # passes-at-base: pins the pre-2b default (no models => byte-identical body)
    def test_no_models_line_when_meta_names_none(self):
        body = review.render.comment_body(record(summary="Clean."), [], has_inline=False,
                                          severity_filter={"M"})
        assert "Models:" not in body and "<sub>" not in body

    def test_a_model_id_with_a_newline_stays_one_line(self):
        body = review.render.comment_body(record(summary="Clean."), [], has_inline=False,
                                          severity_filter={"M"},
                                          models="single m\nnightly")
        assert "<sub>Models: single m nightly</sub>" in body
        inner = body[body.rfind("<sub>"):body.rfind("</sub>")]
        assert "\n" not in inner


class TestCommentBodyDetails:
    def test_nits_sorted_by_path_then_line(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Naming issue", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="a.go", line=30,
                       end_line=None, body="Another style", posted_id="N2"),
            rp.Finding(id="N3", severity="N", seq=3, path="a.go", line=10,
                       end_line=None, body="Style issue", posted_id="N3"),
        ]
        result = _comment(findings, True, {"N"})
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
        result = _comment(findings, True, {"N"})
        assert result.index("Earlier") < result.index("Later")

    def test_mixed_severity_body_shows_severity_then_details(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix this", posted_id="S1"),
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Nit issue", posted_id="N1"),
        ]
        result = _comment(findings, True, {"S", "N"})
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
        result = _comment(findings, True, {"N", "I"})
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
        result = _comment(findings, True, {"N", "I"})
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
        result = _comment(findings, True, {"N"}, static_analysis=sa)
        assert "### Nesting depth" in result
        assert "depth 5 exceeds limit 4" in result
        assert result.index("Nit") < result.index("Nesting depth")

    def test_static_analysis_with_no_findings(self):
        sa = "### Nesting depth\n1 violation in 1 of 3 files checked"
        result = _comment([], True, {"M"}, static_analysis=sa)
        assert "Have some comments" in result
        assert "### Nesting depth" in result

    def test_static_analysis_empty_string_omitted(self):
        result = _comment([], True, {"M"})
        assert "Static Analysis" not in result


class TestFormatFindingLine:
    def test_with_path_and_line(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=42,
            end_line=None, body="Fix bug", posted_id="M1",
        )
        result = review.render._comment_finding_line(f)
        assert "**[M1] [must-fix]**" in result
        assert "`file.go:42`" in result
        assert "Fix bug" in result
        assert result.startswith("- ")

    def test_with_path_only_no_line(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=None,
            end_line=None, body="Refactor", posted_id="S1",
        )
        result = review.render._comment_finding_line(f)
        assert "`file.go`" in result
        assert "Refactor" in result

    def test_pathless_finding(self, rp):
        f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern across files.", posted_id="I1",
        )
        result = review.render._comment_finding_line(f)
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

    def test_an_enterprise_host_replaces_public_github(self, rp):
        m = rp._PERMALINK_REF_RE.search("see file.go:42")
        result = rp._build_permalink("owner/repo", "abc123", m, "ghe.acme.com")
        assert "https://ghe.acme.com/owner/repo/blob/abc123/file.go#L42" in result
        assert "github.com" not in result

    def test_an_enterprise_host_carries_through_a_line_range(self, rp):
        m = rp._PERMALINK_REF_RE.search("see file.go:10-20")
        result = rp._build_permalink("owner/repo", "def456", m, "ghe.acme.com")
        assert "https://ghe.acme.com/owner/repo/blob/def456/file.go#L10-L20" in result
        assert "github.com" not in result


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

    def test_findings_render_on_the_enterprise_host(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see pkg/handler.go:42",
        )
        rp.resolve_permalinks(
            [f], "org/repo", self.DIFF, "head-sha", "base-sha", "ghe.acme.com")
        assert "https://ghe.acme.com/org/repo/blob/head-sha/pkg/handler.go#L42" in f.body
        assert "github.com" not in f.body

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
        text = _comment([demoted, declined], False, {"S"})
        heading = "**Declined — considered and not carried forward:**"
        assert heading in text
        assert text.index("real bug") < text.index(heading), \
            "the findings being asked for come before the ones that were declined"
        assert text.index(heading) < text.index("use a constant")

    def test_a_decline_alone_still_renders_its_block(self, rp):
        declined = self._declined(rp)
        declined.posted_id = "S1"
        text = _comment([declined], False, {"S"})
        assert "**Declined — considered and not carried forward:**" in text
        assert "use a constant" in text
