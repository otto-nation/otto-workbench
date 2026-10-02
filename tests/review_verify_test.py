"""Tests for evidence verification — the gate that checks a review's quotes
against the tree it reviewed.

It is tested from both ends: the pieces — pulling the quote off a finding,
normalizing each side of the comparison, reading the finding lines the check
walks — and the whole, which is what `_verify_findings` reports.

The comment-stripping cases carry the most weight, because the regression they
guard is one only the whole comparison shows: stripping the quote and not the
file leaves the file holding text the quote no longer has, and a quote copied
verbatim out of the file then fails to match it.

What a dropped finding leaves behind, and the disprove gate, are tested in
review_verify_disprove_test.py; the pass both run inside in
review_verify_post_process_test.py.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import git_in, init_repo  # noqa: E402
import review.verify  # noqa: E402


def _verifies(path: str, evidence: str | None, wt_path: str) -> bool:
    """Whether a finding's evidence matches the file it was quoted from."""
    return review.verify._match_evidence(path, evidence, wt_path)["match_result"]


# ── Evidence verification ────────────────────────────────────────────────────


class TestExtractEvidence:
    def test_extracts_from_blockquoted_fenced_code(self):
        body = (
            "missing error check on `db.Query()`\n"
            "  > ```go\n"
            "  > result := db.Query(query)\n"
            "  > ```"
        )
        assert review.verify._extract_evidence(body) == "result := db.Query(query)"

    def test_multiline_snippet(self):
        body = (
            "description\n"
            "  > ```python\n"
            "  > x = 1\n"
            "  > y = 2\n"
            "  > ```"
        )
        assert review.verify._extract_evidence(body) == "x = 1\ny = 2"

    def test_returns_none_when_no_evidence(self):
        assert review.verify._extract_evidence("just a description, no code block") is None

    def test_handles_no_language_tag(self):
        body = (
            "desc\n"
            "  > ```\n"
            "  > some_code()\n"
            "  > ```"
        )
        assert review.verify._extract_evidence(body) == "some_code()"


class TestNormalizeCode:
    def test_strips_whitespace_and_blank_lines(self):
        assert review.verify._normalize_code("  x = 1  \n\n  y = 2  ") == "x = 1\ny = 2"

    def test_empty_string(self):
        assert review.verify._normalize_code("") == ""

    def test_preserves_content(self):
        assert review.verify._normalize_code("result := db.Query(q)") == "result := db.Query(q)"


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

    def test_a_file_that_will_not_decode_fails_rather_than_raising(self, tmp_path):
        """An undecodable file costs one finding its evidence, not the run.

        `UnicodeDecodeError` is a `ValueError`, so it walked straight through a
        guard naming only `OSError` and killed post-processing after every
        agent had run. A latin-1 script is the case that surfaced it — text to
        git, and so reachable from a finding's path like any other file.
        """
        (tmp_path / "latin1.sh").write_bytes(b"echo \xb2\xb2 done\n")
        assert _verifies("latin1.sh", "echo done", str(tmp_path)) is False

    def test_a_binary_file_fails_rather_than_raising(self, tmp_path):
        (tmp_path / "font.woff2").write_bytes(b"wOF2\x00\x01\xff\xfe\x00bad")
        assert _verifies("font.woff2", "any code", str(tmp_path)) is False

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
        assert review.verify._strip_comments("x = 1 // explanation") == "x = 1"

    def test_strips_python_comment(self):
        assert review.verify._strip_comments("x = 1 # explanation") == "x = 1"

    def test_strips_template_comment(self):
        assert review.verify._strip_comments("{{ end }}{{/* note */}}") == "{{ end }}"

    def test_preserves_comment_inside_string(self):
        line = 'msg := "value // not a comment"'
        assert review.verify._strip_comments(line) == line

    def test_no_comment_unchanged(self):
        assert review.verify._strip_comments("x = 1") == "x = 1"

    def test_drops_indented_whole_line_comment(self):
        assert review.verify._strip_comments("    # why this matters\n    x = 1") == "\n    x = 1"

    def test_drops_column_zero_whole_line_comment(self):
        assert review.verify._strip_comments("// why this matters\nx = 1") == "\nx = 1"

    def test_keeps_directive_with_no_space_after_marker(self):
        # Not prose: dropping these would erase code from the comparison.
        assert review.verify._strip_comments("#!/usr/bin/env bash") == "#!/usr/bin/env bash"
        assert review.verify._strip_comments("#include <stdio.h>") == "#include <stdio.h>"
        assert review.verify._strip_comments("\t//nolint:errcheck") == "\t//nolint:errcheck"


class TestVerificationDetail:
    def test_match_records_pass(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = review.verify._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is True
        assert detail["file_exists"] is True

    def test_mismatch_records_failure(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tx := 1\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = review.verify._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
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
        out_text, result = review.verify._verify_findings(text, str(tmp_path))
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
        out_text, result = review.verify._verify_findings(text, str(tmp_path))
        assert result["dropped"] == ["S1"]
        assert result["findings_dropped"] == 1
        assert result["details"][0]["match_result"] is False
        assert "S1" not in out_text


class TestParseVerificationStripsLine:
    def test_path_excludes_line_number(self):
        text = '- **[M1]** **`pkg/handler.go:42`** — missing error check\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_path_excludes_line_range(self):
        text = '- **[S1]** **`pkg/handler.go:10-20`** — issue\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_checkbox_path_excludes_line(self):
        text = '- [ ] **[M1]** `handler.go:42` — desc\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "handler.go"


class TestParseVerificationReadsThePostersLocation:
    """Verification checks the file the poster will place the finding on.

    The path comes from `finding_location`, not from a second reading of the
    line. That second reading kept any suffix it did not know as part of the
    path, so a finding at `replay.py:_short` was stat'd as a file literally
    named that and dropped as "file not found" while the poster read the same
    line as `replay.py`.
    """

    @pytest.mark.parametrize("location, path", [
        ("ai/lib/git/replay.py:_short", "ai/lib/git/replay.py"),
        ("ai/lib/git/replay.py:_short:12", "ai/lib/git/replay.py"),
        ("pkg/x.py:Foo.bar", "pkg/x.py"),
        ("pkg/x.py:~690", "pkg/x.py"),
        ("pkg/x.py:L12", "pkg/x.py"),
    ])
    def test_a_suffix_that_is_not_a_line_number_comes_off(self, location, path):
        text = f"- **[S1]** `{location}` — gap\n"
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == path

    def test_agrees_with_the_poster_on_every_location(self):
        """The property the fix is for, rather than a list of shapes."""
        locations = [
            "ai/lib/git/replay.py:_short", "src/x.py:64,82", "pkg/x.py:Foo.bar",
            "src/my notes.py:12-18", "ai/bin/ci-check", "handler.go:42",
        ]
        for location in locations:
            line = f"- **[S1]** **`{location}`** — gap"
            verified = review.verify._parse_findings_for_verification(line + "\n")
            posted = review.spans.finding_spans(line + "\n")[0].finding.path
            assert verified[0]["path"] == posted, location

    @pytest.mark.parametrize("location, path", [
        ("ns:module.py", "ns:module.py"),
        ("C:/src/x.py", "C:/src/x.py"),
        ("C:/src/x.py:12", "C:/src/x.py"),
        ("Makefile:3", "Makefile"),
    ])
    def test_a_location_the_poster_cannot_place_is_checked_as_written(self, location, path):
        """No placement to contradict, so the gate keeps checking what was written.

        Skipping these would let an unsupported quote through whenever its
        file has a name the poster cannot read — the gate failing open.
        """
        text = f"- **[M1]** **`{location}`** — missing error check\n"
        assert review.verify._parse_findings_for_verification(text)[0]["path"] == path


class TestVerifyFindsTheFileThePosterWould:
    """`_verify_findings` resolves a location with the poster's rule.

    End to end through the seam the run uses, against a real worktree, so the
    finding that was dropped in production is the subject.
    """

    _EVIDENCE = (
        "  > ```python\n"
        "  > def _short(sha):\n"
        "  > ```\n"
    )

    @staticmethod
    def _tree(tmp_path):
        init_repo(tmp_path)
        (tmp_path / "ai/lib/git").mkdir(parents=True)
        (tmp_path / "ai/lib/git/replay.py").write_text("def _short(sha):\n    return sha\n")
        git_in(tmp_path, "add", ".")
        return str(tmp_path)

    def test_a_symbol_suffixed_finding_survives(self, tmp_path):
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** `ai/lib/git/replay.py:_short` — fail-open gap\n"
            + self._EVIDENCE
        )
        _, result = review.verify._verify_findings(text, self._tree(tmp_path))
        assert result["dropped"] == []
        assert result["findings_passed"] == 1

    def test_a_bare_basename_resolves_to_the_tracked_file(self, tmp_path):
        text = "## Should fix\n- [ ] **[S1]** `replay.py:1` — gap\n" + self._EVIDENCE
        _, result = review.verify._verify_findings(text, self._tree(tmp_path))
        assert result["dropped"] == []
        assert result["details"][0]["resolved_path"] == "ai/lib/git/replay.py"

    def test_a_basename_two_files_share_is_still_not_found(self, tmp_path):
        wt = self._tree(tmp_path)
        (tmp_path / "other").mkdir()
        (tmp_path / "other/replay.py").write_text("def _short(sha):\n")
        git_in(tmp_path, "add", ".")
        text = "## Should fix\n- [ ] **[S1]** `replay.py:1` — gap\n" + self._EVIDENCE
        _, result = review.verify._verify_findings(text, wt)
        assert result["dropped"] == ["S1"]
        assert result["details"][0]["file_exists"] is False

    def test_a_basename_unique_in_the_diff_resolves_though_the_tree_has_two(self, tmp_path):
        """The poster resolves against the diff, so the gate must agree.

        `replay.py:1` is one file among the changed ones, so it posts inline;
        read against the whole tree it is ambiguous and was dropped as "file
        not found".
        """
        wt = self._tree(tmp_path)
        (tmp_path / "other").mkdir()
        (tmp_path / "other/replay.py").write_text("def _short(sha):\n")
        git_in(tmp_path, "add", ".")
        text = "## Should fix\n- [ ] **[S1]** `replay.py:1` — gap\n" + self._EVIDENCE
        _, result = review.verify._verify_findings(text, wt, ["ai/lib/git/replay.py"])
        assert result["dropped"] == []
        assert result["details"][0]["resolved_path"] == "ai/lib/git/replay.py"

    def test_an_unplaceable_finding_with_no_such_quote_is_still_dropped(self, tmp_path):
        """`Makefile` reads as no location to the poster; the gate still checks it."""
        wt = self._tree(tmp_path)
        (tmp_path / "Makefile").write_text("all:\n\ttrue\n")
        text = "## Should fix\n- [ ] **[S1]** **`Makefile:3`** — gap\n" + self._EVIDENCE
        _, result = review.verify._verify_findings(text, wt)
        assert result["dropped"] == ["S1"]
        assert result["details"][0]["file_exists"] is True

    def test_a_worktree_that_is_not_there_drops_rather_than_raising(self, tmp_path):
        """Resolution asks git, and git cannot start in a missing directory."""
        text = "## Should fix\n- [ ] **[S1]** `replay.py:1` — gap\n" + self._EVIDENCE
        _, result = review.verify._verify_findings(text, str(tmp_path / "gone"))
        assert result["dropped"] == ["S1"]

    def test_a_file_that_does_not_exist_is_still_dropped(self, tmp_path):
        text = "## Should fix\n- [ ] **[S1]** `ai/lib/git/nope.py:1` — gap\n" + self._EVIDENCE
        _, result = review.verify._verify_findings(text, self._tree(tmp_path))
        assert result["dropped"] == ["S1"]


class TestParseVerificationReadsALineList:
    """A finding naming two discrete lines still names one file.

    A duplicate declaration is the case that produces this location: the
    finding cites both offsets, and the path used to keep the whole `:64,82`
    suffix. `_match_evidence` then stat'd a path no filesystem holds and
    `_drop_reason` called it "file not found", discarding a correct finding
    about a file that was right there.
    """

    def test_a_comma_separated_pair_leaves_the_path(self):
        text = '- **[S2]** `ai/lib/review/run.py:64,82` — duplicate field\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "ai/lib/review/run.py"

    def test_a_three_line_list_leaves_the_path(self):
        text = '- **[S2]** `ai/lib/review/run.py:12,18,24` — duplicate field\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "ai/lib/review/run.py"

    def test_a_real_file_named_by_a_line_list_is_found(self, tmp_path):
        """The drop this whole fix exists to prevent, end to end.

        Through the seam the run uses: the reader strips the location, and
        `_match_evidence` stats what it handed back. Calling `_match_evidence`
        with an unstripped path instead would test a layer that never sees one.
        """
        (tmp_path / "run.py").write_text('command: str = ""\n')
        text = '- **[S2]** `run.py:64,82` — duplicate field\n'
        finding = review.verify._parse_findings_for_verification(text)[0]
        detail = review.verify._match_evidence(finding["path"], None, str(tmp_path))
        assert detail["file_exists"]
        assert detail["match_result"]


class TestParseVerificationSpacedPaths:
    def test_spaced_path_finding_is_not_swallowed_by_the_previous_one(self):
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** **`pkg/a.go:1`** — First finding\n"
            "- [ ] **[M2]** **`src/my notes.py:2`** — Second finding, spaced path\n"
            "- [ ] **[M3]** **`pkg/c.go:3`** — Third finding\n"
        )
        findings = review.verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2", "M3"]
        assert findings[0]["body"] == "First finding"
        assert findings[1]["path"] == "src/my notes.py"

    def test_spaced_path_with_line_range(self):
        text = '- **[S1]** **`src/my notes.py:12-18`** — issue\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/my notes.py"

    def test_non_ascii_spaced_path(self):
        text = '- [ ] **[M1]** **`src/café brûlé.py:42`** — desc\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/café brûlé.py"

    def test_spaced_path_in_a_bare_code_span(self):
        text = '- **[N1]** `docs/release notes.md:3` — stale\n'
        findings = review.verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "docs/release notes.md"

    def test_unchecked_and_plain_forms_both_parse(self):
        text = (
            "- [ ] **[M1]** **`pkg/a.go:1`** — checkbox form\n"
            "- **[M2]** **`pkg/b.go:2`** — plain form\n"
        )
        findings = review.verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2"]
        assert [f["body"] for f in findings] == ["checkbox form", "plain form"]

    def test_checked_finding_is_not_returned(self):
        text = '- [x] **[M1]** **`src/my notes.py:2`** — done\n'
        assert review.verify._parse_findings_for_verification(text) == []


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
        findings = review.verify._parse_findings_for_verification(self.UNREADABLE_LOCATION)
        assert [f["id"] for f in findings] == ["M1", "M3"]

    def test_an_unreadable_location_does_not_join_the_finding_above_it(self):
        findings = review.verify._parse_findings_for_verification(self.UNREADABLE_LOCATION)
        assert review.verify._extract_evidence(findings[0]["body"]) == "x := 1"

    def test_a_ledger_entry_is_not_checked(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "## Prior findings\n"
            "- **[S1]** **`old.go:2`** — Fixed\n"
        )
        findings = review.verify._parse_findings_for_verification(text)
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
        findings = review.verify._parse_findings_for_verification(text)
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
        result = review.verify._strip_evidence_blocks(text)
        assert "```go" not in result
        assert "result := db.Query" not in result
        assert "**[M1]**" in result
        assert "missing error check" in result
        assert "**[N1]**" in result

    def test_no_evidence_blocks_unchanged(self):
        content = "## Must fix\n- **[M1]** **`file.go:42`** — finding\n"
        assert review.verify._strip_evidence_blocks(content) == content

    def test_top_level_blockquote_preserved(self):
        content = (
            "## Summary\n"
            "> ```go\n"
            "> example code\n"
            "> ```\n"
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — finding\n"
        )
        result = review.verify._strip_evidence_blocks(content)
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
        result = review.verify._strip_evidence_blocks(text)
        assert "Team roster" not in result
        assert "docker run" not in result
        assert "**[S1]**" in result
        assert "stale config section" in result
        assert "**[N1]**" in result
