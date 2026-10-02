"""Tests for `post_process_findings`, the pass evidence verification and the
disprove gate run inside — the cleanups it applies in order, and the
reconciliation that makes a review's Summary and Verdict account for what
verification removed.

The reconciliation has no other caller, and its contract is about the order —
it must read counts renumbering has already settled — so the whole pass is the
only honest subject for it.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.document import SECTION_PRIOR_FINDINGS
from review.types import SEVERITY_MUST, severity_by_key
from review.verify import post_process_findings


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

    def test_an_undecodable_file_costs_its_finding_and_not_the_pass(self, tmp_path):
        """The whole pass still finishes and still writes the review file.

        This is the property the unit case cannot state: the abort was in
        post-processing, so what it destroyed was not one evidence match but
        every agent's output, the review file left unwritten. The finding
        naming the undecodable file goes — nothing could verify it — and the
        one beside it survives and is renumbered into its place.
        """
        (tmp_path / "latin1.sh").write_bytes(b"echo \xb2\xb2 done\n")
        (tmp_path / "handler.go").write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- **[M1]** **`latin1.sh:1`** — cannot be read to verify\n"
            "  > ```sh\n"
            "  > echo done\n"
            "  > ```\n"
            "- **[M2]** **`handler.go:4`** — real problem\n"
            "  > ```go\n"
            "  > \tx := 1\n"
            "  > ```\n"
        )

        verification = post_process_findings(str(review), str(tmp_path))

        result = review.read_text()
        assert "- **[M1]** **`handler.go:4`** — real problem" in result
        assert "cannot be read to verify" not in result
        # The drop note still names the file, which is the record of why.
        assert verification["dropped"] == ["M1"]

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
