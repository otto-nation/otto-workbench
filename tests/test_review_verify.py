"""Tests for the two gates that check a review against the tree it reviewed.

Evidence verification is tested from both ends: the pieces — pulling the quote
off a finding, normalizing each side of the comparison, reading the finding
lines the check walks — and the whole, which is what `_verify_findings` reports
and what `_remove_dropped_findings` leaves behind.

The comment-stripping cases carry the most weight, because the regression they
guard is one only the whole comparison shows: stripping the quote and not the
file leaves the file holding text the quote no longer has, and a quote copied
verbatim out of the file then fails to match it.

The disprove gate is the second half — reading an agent's verdicts back and
applying them.

`post_process_findings` is the pass all of that runs inside, and the last two
classes here test it as one: the cleanups it applies in order, and the
reconciliation that makes a review's Summary and Verdict account for what
verification removed. The reconciliation has no other caller, and its contract
is about the order — it must read counts renumbering has already settled — so
the whole pass is the only honest subject for it.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_spans
import review_verify
from review_document import SECTION_PRIOR_FINDINGS
from review_types import SEVERITY_MUST, severity_by_key
from review_verify import (
    DisproveResult, apply_disprove_results, parse_disprove_output,
    post_process_findings,
)


def _verifies(path: str, evidence: str | None, wt_path: str) -> bool:
    """Whether a finding's evidence matches the file it was quoted from."""
    return review_verify._match_evidence(path, evidence, wt_path)["match_result"]


# ── Evidence verification ────────────────────────────────────────────────────


class TestExtractEvidence:
    def test_extracts_from_blockquoted_fenced_code(self):
        body = (
            "missing error check on `db.Query()`\n"
            "  > ```go\n"
            "  > result := db.Query(query)\n"
            "  > ```"
        )
        assert review_verify._extract_evidence(body) == "result := db.Query(query)"

    def test_multiline_snippet(self):
        body = (
            "description\n"
            "  > ```python\n"
            "  > x = 1\n"
            "  > y = 2\n"
            "  > ```"
        )
        assert review_verify._extract_evidence(body) == "x = 1\ny = 2"

    def test_returns_none_when_no_evidence(self):
        assert review_verify._extract_evidence("just a description, no code block") is None

    def test_handles_no_language_tag(self):
        body = (
            "desc\n"
            "  > ```\n"
            "  > some_code()\n"
            "  > ```"
        )
        assert review_verify._extract_evidence(body) == "some_code()"


class TestNormalizeCode:
    def test_strips_whitespace_and_blank_lines(self):
        assert review_verify._normalize_code("  x = 1  \n\n  y = 2  ") == "x = 1\ny = 2"

    def test_empty_string(self):
        assert review_verify._normalize_code("") == ""

    def test_preserves_content(self):
        assert review_verify._normalize_code("result := db.Query(q)") == "result := db.Query(q)"


class TestMatchEvidence:
    def test_valid_evidence_passes(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tresult := db.Query(q)\n}\n")
        assert _verifies("handler.go", "result := db.Query(q)", str(tmp_path)) is True

    def test_evidence_not_in_file_fails(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        assert _verifies("handler.go", "result := db.Query(q)", str(tmp_path)) is False

    def test_file_not_found_fails(self, tmp_path):
        assert _verifies("missing.go", "any code", str(tmp_path)) is False

    def test_none_evidence_file_exists_passes(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n")
        assert _verifies("handler.go", None, str(tmp_path)) is True

    def test_none_evidence_file_missing_fails(self, tmp_path):
        assert _verifies("missing.go", None, str(tmp_path)) is False

    def test_indentation_mismatch_still_passes(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\t\tresult := db.Query(q)\n}\n")
        assert _verifies("handler.go", "result := db.Query(q)", str(tmp_path)) is True

    def test_trailing_go_comment_stripped(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("data.Completed[n] = completed\ndata.Posted[n] = posted\n")
        evidence = "data.Completed[n] = completed   // never read in template\ndata.Posted[n] = posted"
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    def test_trailing_template_comment_stripped(self, tmp_path):
        src = tmp_path / "page.html"
        src.write_text("{{ end }}\n")
        evidence = "{{ end }}\n{{/* no else — renders nothing */}}"
        assert _verifies("page.html", evidence, str(tmp_path)) is True

    def test_ellipsis_fragments_verified(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n\tif err != nil {\n\t\treturn err\n\t}\n\tuse(result)\n}\n")
        evidence = "result := db.Query(q)\n...\nuse(result)"
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    def test_ellipsis_fragment_not_in_file_fails(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n\tuse(result)\n}\n")
        evidence = "result := db.Query(q)\n...\nnonexistent_call()"
        assert _verifies("handler.go", evidence, str(tmp_path)) is False

    def test_comment_inside_string_not_stripped(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text('msg := "value // not a comment"\n')
        evidence = 'msg := "value // not a comment"'
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    @pytest.mark.parametrize("lines", [(0, 6), (1, 5), (2, 6), (4, 6)])
    def test_verbatim_quote_of_any_span_passes(self, tmp_path, lines):
        """Evidence copied out of the file verifies, whatever it spans.

        The regression: comment stripping ran on the quote only, so the file
        kept text the quote no longer had. Every span here — over a
        whole-line comment, over a trailing comment that is not on the last
        line, or both — failed to match the file it was copied from.
        """
        source = (
            "def run(action):\n"
            "    if not publishing.enabled():\n"
            "        # The closing line is the one read as the outcome, so it\n"
            "        # carries the label the body was printed under.\n"
            "        publishing.draft(action)  # not a post\n"
            "        return 0\n"
        )
        src = tmp_path / "publish.py"
        src.write_text(source)
        start, end = lines
        evidence = "\n".join(source.split("\n")[start:end])
        assert _verifies("publish.py", evidence, str(tmp_path)) is True

    def test_reviewer_annotation_comment_passes(self, tmp_path):
        # Reviewers annotate evidence with lines the file does not contain.
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        evidence = "result := db.Query(q)\n// err is never checked"
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    def test_wrong_code_beside_a_comment_still_fails(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\t// query the db\n\tresult := db.Query(q)\n}\n")
        evidence = "// query the db\nresult := db.Exec(q)"
        assert _verifies("handler.go", evidence, str(tmp_path)) is False


class TestStripComments:
    def test_strips_go_comment(self):
        assert review_verify._strip_comments("x = 1 // explanation") == "x = 1"

    def test_strips_python_comment(self):
        assert review_verify._strip_comments("x = 1 # explanation") == "x = 1"

    def test_strips_template_comment(self):
        assert review_verify._strip_comments("{{ end }}{{/* note */}}") == "{{ end }}"

    def test_preserves_comment_inside_string(self):
        line = 'msg := "value // not a comment"'
        assert review_verify._strip_comments(line) == line

    def test_no_comment_unchanged(self):
        assert review_verify._strip_comments("x = 1") == "x = 1"

    def test_drops_indented_whole_line_comment(self):
        assert review_verify._strip_comments("    # why this matters\n    x = 1") == "\n    x = 1"

    def test_drops_column_zero_whole_line_comment(self):
        assert review_verify._strip_comments("// why this matters\nx = 1") == "\nx = 1"

    def test_keeps_directive_with_no_space_after_marker(self):
        # Not prose: dropping these would erase code from the comparison.
        assert review_verify._strip_comments("#!/usr/bin/env bash") == "#!/usr/bin/env bash"
        assert review_verify._strip_comments("#include <stdio.h>") == "#include <stdio.h>"
        assert review_verify._strip_comments("\t//nolint:errcheck") == "\t//nolint:errcheck"


class TestVerificationDetail:
    def test_match_records_pass(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = review_verify._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is True
        assert detail["file_exists"] is True

    def test_mismatch_records_failure(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tx := 1\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = review_verify._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is False
        assert "longest_match_prefix" in detail
        assert "first_mismatch" in detail


class TestVerifyFindings:
    def test_returns_verification_summary(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tresult := db.Query(q)\n}\n")
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** **`handler.go:42`** — desc\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
        )
        out_text, result = review_verify._verify_findings(text, str(tmp_path))
        assert result["dropped"] == []
        assert result["findings_checked"] == 1
        assert result["findings_passed"] == 1
        assert result["findings_dropped"] == 0
        assert out_text == text

    def test_drops_unverified_and_returns_details(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** **`handler.go:42`** — desc\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
        )
        out_text, result = review_verify._verify_findings(text, str(tmp_path))
        assert result["dropped"] == ["S1"]
        assert result["findings_dropped"] == 1
        assert result["details"][0]["match_result"] is False
        assert "S1" not in out_text


class TestParseVerificationStripsLine:
    def test_path_excludes_line_number(self):
        text = '- **[M1]** **`pkg/handler.go:42`** — missing error check\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_path_excludes_line_range(self):
        text = '- **[S1]** **`pkg/handler.go:10-20`** — issue\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_checkbox_path_excludes_line(self):
        text = '- [ ] **[M1]** `handler.go:42` — desc\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "handler.go"


class TestParseVerificationKeepsAColonThatIsNotALineSuffix:
    """Verification reads the same path the rest of the pipeline parsed.

    This reader used to truncate at the last colon, so a path carrying one of
    its own verified against its prefix — a file that does not exist, which
    fails every evidence check. `review_grammar.strip_line_suffix` now takes
    off a line suffix and nothing else.
    """

    def test_a_prefixed_path_survives(self):
        text = '- **[M1]** **`ns:module.py`** — missing error check\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "ns:module.py"

    def test_a_drive_letter_survives(self):
        text = '- **[M1]** **`C:/src/x.py`** — missing error check\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "C:/src/x.py"

    def test_a_line_suffix_still_comes_off_a_prefixed_path(self):
        text = '- **[M1]** **`C:/src/x.py:12`** — missing error check\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "C:/src/x.py"


class TestParseVerificationSpacedPaths:
    def test_spaced_path_finding_is_not_swallowed_by_the_previous_one(self):
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** **`pkg/a.go:1`** — First finding\n"
            "- [ ] **[M2]** **`src/my notes.py:2`** — Second finding, spaced path\n"
            "- [ ] **[M3]** **`pkg/c.go:3`** — Third finding\n"
        )
        findings = review_verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2", "M3"]
        assert findings[0]["body"] == "First finding"
        assert findings[1]["path"] == "src/my notes.py"

    def test_spaced_path_with_line_range(self):
        text = '- **[S1]** **`src/my notes.py:12-18`** — issue\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/my notes.py"

    def test_non_ascii_spaced_path(self):
        text = '- [ ] **[M1]** **`src/café brûlé.py:42`** — desc\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/café brûlé.py"

    def test_spaced_path_in_a_bare_code_span(self):
        text = '- **[N1]** `docs/release notes.md:3` — stale\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "docs/release notes.md"

    def test_unchecked_and_plain_forms_both_parse(self):
        text = (
            "- [ ] **[M1]** **`pkg/a.go:1`** — checkbox form\n"
            "- **[M2]** **`pkg/b.go:2`** — plain form\n"
        )
        findings = review_verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2"]
        assert [f["body"] for f in findings] == ["checkbox form", "plain form"]

    def test_checked_finding_is_not_returned(self):
        text = '- [x] **[M1]** **`src/my notes.py:2`** — done\n'
        assert review_verify._parse_findings_for_verification(text) == []


class TestVerificationReadsEachFindingsOwnBody:
    """A finding is checked against the evidence written under it and no other.

    `VERIFY_FINDING_RE` selects which findings this gate checks; it does not
    decide where one ends. A declaration it cannot read used to be appended to
    the finding above it, which is how a finding came to be verified against a
    quotation belonging to its neighbour.
    """

    UNREADABLE_LOCATION = (
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "  > ```go\n"
        "  > x := 1\n"
        "  > ```\n"
        "- **[M2]** Nil pointer dereference at handler.go:42\n"
        "  > ```go\n"
        "  > y := 2\n"
        "  > ```\n"
        "- **[M3]** **`c.go:3`** — three\n"
    )

    def test_an_unreadable_location_is_not_checked(self):
        findings = review_verify._parse_findings_for_verification(self.UNREADABLE_LOCATION)
        assert [f["id"] for f in findings] == ["M1", "M3"]

    def test_an_unreadable_location_does_not_join_the_finding_above_it(self):
        findings = review_verify._parse_findings_for_verification(self.UNREADABLE_LOCATION)
        assert review_verify._extract_evidence(findings[0]["body"]) == "x := 1"

    def test_a_ledger_entry_is_not_checked(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "## Prior findings\n"
            "- **[S1]** **`old.go:2`** — Fixed\n"
        )
        findings = review_verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1"]

    def test_a_body_stops_at_the_resolved_finding_below_it(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  > ```go\n"
            "  > x := 1\n"
            "  > ```\n"
            "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
            "  > ```go\n"
            "  > y := 2\n"
            "  > ```\n"
        )
        findings = review_verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1"]
        assert "y := 2" not in findings[0]["body"]


class TestStripEvidenceBlocks:
    def test_strips_evidence_preserves_finding(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — missing error check\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — rename var\n"
        )
        result = review_verify._strip_evidence_blocks(text)
        assert "```go" not in result
        assert "result := db.Query" not in result
        assert "**[M1]**" in result
        assert "missing error check" in result
        assert "**[N1]**" in result

    def test_no_evidence_blocks_unchanged(self):
        content = "## Must fix\n- **[M1]** **`file.go:42`** — finding\n"
        assert review_verify._strip_evidence_blocks(content) == content

    def test_top_level_blockquote_preserved(self):
        content = (
            "## Summary\n"
            "> ```go\n"
            "> example code\n"
            "> ```\n"
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — finding\n"
        )
        result = review_verify._strip_evidence_blocks(content)
        assert "> ```go" in result

    def test_strips_unfenced_blockquote_evidence(self):
        text = (
            "## Should fix\n"
            "- **[S1]** **`docs/overview.md:511`** — stale config section\n"
            "  > # Team roster\n"
            "  > team:\n"
            "  >   - first_name: David\n"
            "  >\n"
            "  > docker run maximus daemon\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — rename var\n"
        )
        result = review_verify._strip_evidence_blocks(text)
        assert "Team roster" not in result
        assert "docker run" not in result
        assert "**[S1]**" in result
        assert "stale config section" in result
        assert "**[N1]**" in result


# ── What both gates take out ─────────────────────────────────────────────────
#
# Evidence verification and the disprove gate each drop findings from a
# finished review, and they used to measure a finding's body for themselves.
# The table below is run through both, asserting the same bytes each time, so a
# reading that drifts into one of them fails here rather than passing twice.


def _by_evidence_gate(text: str, ids: list[str]) -> str:
    """What evidence verification leaves behind when it drops `ids`."""
    return review_spans.drop_findings(text, ids)


def _by_disprove_gate(text: str, ids: list[str]) -> str:
    """What the disprove gate leaves behind when `ids` are falsified."""
    results = [DisproveResult(fid, "FALSIFIED", "challenged") for fid in ids]
    return apply_disprove_results(text, results)[0]


GATES = [
    pytest.param(_by_evidence_gate, id="evidence"),
    pytest.param(_by_disprove_gate, id="disprove"),
]

DROP_CASES = [
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "- a flat bullet continuing the finding\n"
        "- **[M2]** **`b.go:2`** — two\n",
        ["M1"],
        "## Must fix\n"
        "- **[M2]** **`b.go:2`** — two\n",
        id="a-flat-bullet-is-the-finding's-own-body",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
        "- **[M3]** **`c.go:3`** — three\n",
        ["M1"],
        "## Must fix\n"
        "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
        "- **[M3]** **`c.go:3`** — three\n",
        id="a-resolved-finding-below-a-dropped-one-survives",
    ),
    pytest.param(
        "## Must fix\n"
        "### Group A\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "### Group B\n"
        "- **[M2]** **`b.go:2`** — two\n",
        ["M1"],
        "## Must fix\n"
        "### Group A\n"
        "### Group B\n"
        "- **[M2]** **`b.go:2`** — two\n",
        id="a-sub-heading-below-a-dropped-finding-survives",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "  - **[M2]** **`b.go:2`** — nested declaration\n"
        "- **[M3]** **`c.go:3`** — three\n",
        ["M1"],
        "## Must fix\n"
        "  - **[M2]** **`b.go:2`** — nested declaration\n"
        "- **[M3]** **`c.go:3`** — three\n",
        id="an-indented-declaration-is-a-declaration",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "## Prior findings\n"
        "- **[M1]** `old.go` — Fixed\n",
        ["M1"],
        "## Must fix\n"
        "## Prior findings\n"
        "- **[M1]** `old.go` — Fixed\n",
        id="a-ledger-entry-whose-id-collides-is-left-alone",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "  > ```go\n"
        "  > x := 1\n"
        "  > ```\n"
        "\n"
        "- **[M2]** **`b.go:2`** — two\n",
        ["M1"],
        "## Must fix\n"
        "- **[M2]** **`b.go:2`** — two\n",
        id="an-evidence-block-goes-with-the-finding-that-quotes-it",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "## Nit\n"
        "- **[N1]** **`b.go:2`** — two\n",
        ["M1", "N1"],
        # The document's last finding owns the blank line closing the file, so
        # a review whose last finding goes loses its trailing newline with it.
        "## Must fix\n"
        "## Nit",
        id="every-named-finding-goes-at-once",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n",
        ["S9"],
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n",
        id="an-id-the-review-does-not-declare-changes-nothing",
    ),
]


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("text,ids,expected", DROP_CASES)
class TestBothGatesCutTheSameSpan:
    def test_the_gate_leaves_exactly_this(self, gate, text, ids, expected):
        assert gate(text, ids) == expected


class TestRemoveDroppedFindings:
    def test_remove_single(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — issue one\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = _by_evidence_gate(text, ["M1"])
        assert "issue one" not in result
        assert "issue two" in result

    def test_remove_with_continuation(self):
        text = (
            "- **[M1]** **`file.go:1`** — issue one\n"
            "  continuation line\n"
            "  more detail\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = _by_evidence_gate(text, ["M1"])
        assert "issue one" not in result
        assert "continuation line" not in result
        assert "issue two" in result

    def test_empty_dropped_list(self):
        text = "- **[M1]** **`file.go:1`** — issue\n"
        assert _by_evidence_gate(text, []) == text

    def test_remove_last_finding(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — only finding\n"
        )
        result = _by_evidence_gate(text, ["M1"])
        assert "only finding" not in result
        assert "## Must fix" in result


# ── Disprove-it gate ─────────────────────────────────────────────────────────


DISPROVE_OUTPUT = """\
## Disprove Results

- [M1] SURVIVES — confirmed: no nil check before deref at handler.go:42
- [M2] FALSIFIED — the error is handled in the caller at service.go:88
- [S1] SURVIVES — timeout not set, could hang indefinitely
- [S2] FALSIFIED — deprecated API was replaced in the same PR, see diff line 204
"""


class TestParseDisproveOutput:
    def test_parses_all_results(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert len(results) == 4

    def test_survives_verdict(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert results[0].finding_id == "M1"
        assert results[0].verdict == "SURVIVES"
        assert "nil check" in results[0].reason

    def test_falsified_verdict(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert results[1].finding_id == "M2"
        assert results[1].verdict == "FALSIFIED"
        assert "caller" in results[1].reason

    def test_empty_input(self):
        assert parse_disprove_output("") == []

    def test_no_matching_lines(self):
        assert parse_disprove_output("some random text\nno findings here\n") == []

    def test_double_dash_separator(self):
        text = "- [M1] SURVIVES -- reason here\n"
        results = parse_disprove_output(text)
        assert len(results) == 1
        assert results[0].reason == "reason here"


# ── apply_disprove_results ───────────────────────────────────────────────────


REVIEW_TEXT = """\
## Must fix

- [ ] **[M1]** Nil pointer dereference at handler.go:42
  The handler does not check for nil before calling `.Process()`.

  **Evidence:**
  ```go
  func Handle(r *Request) { r.Process() }
  ```

- [ ] **[M2]** Error ignored in database query
  The return value of `db.Query()` is discarded.

## Should fix

- [ ] **[S1]** No timeout on HTTP client
  The default client has no timeout, which could cause hangs.

- [ ] **[S2]** Using deprecated API
  `OldMethod()` is marked deprecated since v2.0.

## Nit

- [ ] **[N1]** Variable naming: `x` should be `count`
"""


class TestApplyDisproveResults:
    def test_removes_falsified_findings(self):
        results = [
            DisproveResult("M2", "FALSIFIED", "handled in caller"),
            DisproveResult("S2", "FALSIFIED", "replaced in same PR"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" in new_text
        assert "**[M2]**" not in new_text
        assert "**[S1]**" in new_text
        assert "**[S2]**" not in new_text
        assert "**[N1]**" in new_text

    def test_removes_multiline_body(self):
        results = [DisproveResult("M1", "FALSIFIED", "not real")]
        new_text, _ = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" not in new_text
        assert "r.Process()" not in new_text
        assert "**[M2]**" in new_text

    def test_all_survive(self):
        results = [
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "SURVIVES", "confirmed"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert new_text == REVIEW_TEXT
        assert stats["survived"] == 2
        assert stats["falsified"] == 0

    def test_all_falsified(self):
        results = [
            DisproveResult("M1", "FALSIFIED", "r1"),
            DisproveResult("M2", "FALSIFIED", "r2"),
            DisproveResult("S1", "FALSIFIED", "r3"),
            DisproveResult("S2", "FALSIFIED", "r4"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" not in new_text
        assert "**[M2]**" not in new_text
        assert "**[S1]**" not in new_text
        assert "**[S2]**" not in new_text
        assert "**[N1]**" in new_text
        assert stats["falsified"] == 4

    def test_empty_results(self):
        new_text, stats = apply_disprove_results(REVIEW_TEXT, [])
        assert new_text == REVIEW_TEXT
        assert stats["total_challenged"] == 0

    def test_stats_structure(self):
        results = [
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "FALSIFIED", "not real"),
            DisproveResult("S1", "SURVIVES", "confirmed"),
        ]
        _, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert stats["total_challenged"] == 3
        assert stats["survived"] == 2
        assert stats["falsified"] == 1
        assert "M2" in stats["falsified_ids"]
        assert stats["reasons"]["M2"] == "not real"

    def test_section_headers_preserved(self):
        results = [DisproveResult("M1", "FALSIFIED", "x")]
        new_text, _ = apply_disprove_results(REVIEW_TEXT, results)
        assert "## Must fix" in new_text
        assert "## Should fix" in new_text
        assert "## Nit" in new_text

    def test_finding_without_checkbox(self):
        review = "## Must fix\n\n- **[M1]** Simple finding\n  Detail line.\n"
        results = [DisproveResult("M1", "FALSIFIED", "not real")]
        new_text, stats = apply_disprove_results(review, results)
        assert "**[M1]**" not in new_text
        assert stats["falsified"] == 1


# ── post_process_findings ───────────────────────────────────────────────────


class TestPostProcessFindings:
    def test_skips_verify_when_no_wt_path(self, tmp_path):
        review = tmp_path / "review.md"
        must = severity_by_key(SEVERITY_MUST).section
        prefix = SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}1]** **`missing.py:10`** — bug\n"
            "  > ```\n"
            "  > old_code()\n"
            "  > ```\n"
        )
        post_process_findings(str(review))
        result = review.read_text()
        assert f"[{prefix}1]" in result

    def test_strips_evidence_blocks(self, tmp_path):
        review = tmp_path / "review.md"
        must = severity_by_key(SEVERITY_MUST).section
        prefix = SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}1]** **`foo.py:1`** — issue\n"
            "  > ```python\n"
            "  > x = 1\n"
            "  > ```\n"
        )
        post_process_findings(str(review))
        assert "```python" not in review.read_text()

    def test_strips_stable_ids(self, tmp_path):
        review = tmp_path / "review.md"
        must = severity_by_key(SEVERITY_MUST).section
        prefix = SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}1]** <!-- sid:abc12345 --> **`foo.py:1`** — issue\n"
        )
        post_process_findings(str(review))
        assert "sid:" not in review.read_text()

    def test_renumbers_findings(self, tmp_path):
        review = tmp_path / "review.md"
        must = severity_by_key(SEVERITY_MUST).section
        prefix = SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}3]** **`foo.py:1`** — first\n"
            f"- **[{prefix}7]** **`bar.py:1`** — second\n"
        )
        post_process_findings(str(review))
        result = review.read_text()
        assert f"[{prefix}1]" in result
        assert f"[{prefix}2]" in result

    def test_strips_prior_findings_ledger(self, tmp_path):
        review = tmp_path / "review.md"
        must = severity_by_key(SEVERITY_MUST).section
        prefix = SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}5]** **`foo.py:1`** — still broken\n"
            f"## {SECTION_PRIOR_FINDINGS}\n"
            f"- **[{prefix}2]** `bar.py` — Fixed\n"
        )
        post_process_findings(str(review))
        result = review.read_text()
        assert SECTION_PRIOR_FINDINGS not in result
        assert "bar.py" not in result
        # Renumbering sees only the findings this review actually reports.
        assert f"[{prefix}1]" in result

    def test_a_dropped_findings_citation_does_not_survive_it(self, tmp_path):
        # The whole chain: verification drops M1, renumbering pulls M2 into its
        # place, and the Nit that cited M1 must not end up citing the survivor.
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- **[M1]** **`handler.go:42`** — evidence no longer in the file\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
            "- **[M2]** **`handler.go:4`** — real problem\n"
            "  > ```go\n"
            "  > \tx := 1\n"
            "  > ```\n"
            "## Nit\n"
            "- **[N1]** **`handler.go:1`** — revisit once [M1] lands\n"
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "- **[M1]** **`handler.go:4`** — real problem" in result
        assert "revisit once [removed] lands" in result

    def test_strips_the_prior_findings_ledger(self, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "## Summary\nPrior finding fixed.\n"
            f"## {SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go` — Fixed\n"
        )
        post_process_findings(str(review))
        assert SECTION_PRIOR_FINDINGS not in review.read_text()


# ── reconcile_dropped_findings ──────────────────────────────────────────────


class TestReconcileDroppedFindings:
    """A review must never describe a finding evidence verification removed.

    The synthesis agent writes the Summary and the Verdict before verification
    runs, so both are asserted against the finished file rather than against
    the intermediate dict.
    """

    @staticmethod
    def _review(tmp_path, verdict="Request changes — the unchecked error is a bug.", extra=""):
        """A review whose Summary names a must-fix that will not verify."""
        (tmp_path / "kept.py").write_text("x = 1\n")
        review = tmp_path / "review.md"
        review.write_text(
            "## Summary\n"
            "Solid change overall; the most serious problem is the unchecked "
            "error in `deleted.py`.\n"
            "## Must fix\n"
            "- **[M1]** **`deleted.py:10`** — the error is never checked\n"
            f"{extra}"
            "## Nit\n"
            "- **[N1]** **`kept.py:1`** — prefer a constant\n"
            f"## Verdict\n{verdict}\n"
        )
        return review

    def test_drop_leaves_a_note_naming_what_went(self, tmp_path):
        review = self._review(tmp_path)
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "Evidence verification removed 1 finding:" in result
        assert "> - Must fix — `deleted.py`: file not found" in result
        # The finding itself is gone, so only the note may mention it.
        assert "the error is never checked" not in result

    def test_note_lands_in_the_summary_it_corrects(self, tmp_path):
        review = self._review(tmp_path)
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        summary = result.index("## Summary")
        note = result.index("Evidence verification removed")
        assert summary < note < result.index("## Must fix")

    def test_note_does_not_cite_renumbered_ids(self, tmp_path):
        # Two must-fix findings, the second surviving and renumbered M2 -> M1.
        review = self._review(
            tmp_path,
            extra="- **[M2]** **`kept.py:1`** — also worth fixing\n",
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        note = result[result.index("Evidence verification removed"):]
        note = note[:note.index("## ")]
        assert "[M1]" not in note and "[M2]" not in note
        # M2 survived and took M1's number — citing the dropped ID would point
        # the reader at it.
        assert "- **[M1]** **`kept.py:1`**" in result

    def test_verdict_is_lowered_to_what_survives(self, tmp_path):
        review = self._review(tmp_path)
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        verdict = result[result.index("## Verdict"):]
        assert verdict.strip().startswith("## Verdict\nApprove — 1 nit")
        assert "Request changes" not in result

    def test_disapprove_is_never_lowered(self, tmp_path):
        # Disapprove means the approach is wrong, which the finding counts do
        # not derive — dropping a finding cannot refute it. The note still says
        # what went, so the reader can weigh the verdict against it.
        review = self._review(tmp_path, verdict="**Disapprove** — the approach is wrong.")
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "**Disapprove** — the approach is wrong." in result
        assert "Evidence verification removed 1 finding:" in result

    def test_verdict_the_counts_still_support_is_left_alone(self, tmp_path):
        review = self._review(
            tmp_path,
            extra="- **[M2]** **`kept.py:1`** — also worth fixing\n",
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "Request changes — the unchecked error is a bug." in result
        assert "Evidence verification removed 1 finding:" in result

    def test_no_note_when_nothing_dropped(self, tmp_path):
        (tmp_path / "kept.py").write_text("x = 1\n")
        review = tmp_path / "review.md"
        review.write_text(
            "## Summary\nOne real problem.\n"
            "## Must fix\n- **[M1]** **`kept.py:1`** — the error is never checked\n"
            "## Verdict\nRequest changes — worth fixing.\n"
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "Evidence verification" not in result
        assert "Request changes — worth fixing." in result

    def test_second_pass_does_not_stack_notes(self, tmp_path):
        review = self._review(tmp_path)
        post_process_findings(str(review), str(tmp_path))
        post_process_findings(str(review), str(tmp_path))
        assert review.read_text().count("Evidence verification removed") == 1

    def test_note_survives_without_a_summary_section(self, tmp_path):
        # The mechanical paths post-process the merged content, which has no
        # Summary — they build one from the processed text afterwards.
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n- **[M1]** **`deleted.py:10`** — the error is never checked\n"
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert result.index("Evidence verification removed") < result.index("## Must fix")

    def test_drop_reason_reports_evidence_mismatch_with_char_offset(self, tmp_path):
        # kept.py exists, so the drop is a content mismatch rather than a
        # missing file — the reason must name the offset, not "file not found".
        (tmp_path / "kept.py").write_text("x = 1\n")
        review = tmp_path / "review.md"
        review.write_text(
            "## Summary\nOne real problem.\n"
            "## Must fix\n"
            "- **[M1]** **`kept.py:1`** — quotes code that is not there\n"
            "  > ```python\n"
            "  > y = 2\n"
            "  > ```\n"
            "## Verdict\nRequest changes — worth fixing.\n"
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "> - Must fix — `kept.py`: evidence mismatch at char" in result

    def test_drop_note_pluralizes_the_header_for_multiple_findings(self, tmp_path):
        review = self._review(
            tmp_path,
            extra="- **[M2]** **`also-deleted.py:1`** — another error never checked\n",
        )
        post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "Evidence verification removed 2 findings:" in result
        assert "> - Must fix — `deleted.py`: file not found" in result
        assert "> - Must fix — `also-deleted.py`: file not found" in result
