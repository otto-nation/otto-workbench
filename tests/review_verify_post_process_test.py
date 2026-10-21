"""Tests for `post_process_record`, the pass evidence verification and the
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
from review.record import LedgerLine, SeveritySection
from review.render import render_body
from review.types import PriorDisposition
from review.verify import post_process_record

from review_record_support import finding, record


def _post_process(rec, path, wt_path="", changed_files=()):
    """Drive the record pass and write the rendered body back to `path`."""
    pth = Path(path)
    done = post_process_record(rec, wt_path, changed_files)
    pth.write_text(render_body(done.record))
    return done.verification

# ── post_process_record ────────────────────────────────────────────────────


class TestPostProcessFindings:
    def test_skips_verify_when_no_wt_path(self, tmp_path):
        review = tmp_path / "review.md"
        rec = record(finding(
            "M1", "bug", path="missing.py", line=10, evidence="old_code()",
        ))
        _post_process(rec, str(review))
        result = review.read_text()
        assert "[M1]" in result

    def test_strips_evidence_blocks(self, tmp_path):
        review = tmp_path / "review.md"
        rec = record(finding("M1", "issue", path="foo.py", evidence="x = 1"))
        _post_process(rec, str(review))
        assert "```python" not in review.read_text()

    def test_strips_stable_ids(self, tmp_path):
        review = tmp_path / "review.md"
        rec = record(finding("M1", "issue", path="foo.py", sid="abc12345"))
        _post_process(rec, str(review))
        assert "sid:" not in review.read_text()

    def test_renumbers_findings(self, tmp_path):
        review = tmp_path / "review.md"
        rec = record(
            finding("M3", "first", path="foo.py"),
            finding("M7", "second", path="bar.py"),
        )
        _post_process(rec, str(review))
        result = review.read_text()
        assert "[M1]" in result
        assert "[M2]" in result

    def test_strips_prior_findings_ledger(self, tmp_path):
        review = tmp_path / "review.md"
        rec = record(
            finding("M5", "still broken", path="foo.py"),
            ledger=(LedgerLine("M2", PriorDisposition.FIXED, "bar.py"),),
        )
        _post_process(rec, str(review))
        result = review.read_text()
        assert SECTION_PRIOR_FINDINGS not in result
        assert "bar.py" not in result
        # Renumbering sees only the findings this review actually reports.
        assert "[M1]" in result

    def test_a_dropped_findings_citation_does_not_survive_it(self, tmp_path):
        # The whole chain: verification drops M1, renumbering pulls M2 into its
        # place, and the Nit that cited M1 must not end up citing the survivor.
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        review = tmp_path / "review.md"
        rec = record(
            finding(
                "M1", "evidence no longer in the file", path="handler.go", line=42,
                evidence="result := db.Query(q)",
            ),
            finding(
                "M2", "real problem", path="handler.go", line=4,
                evidence="\tx := 1",
            ),
            finding("N1", "revisit once [M1] lands", path="handler.go"),
        )
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "- **[M1]** `handler.go:4` — real problem" in result
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
        rec = record(
            finding(
                "M1", "cannot be read to verify", path="latin1.sh",
                evidence="echo done",
            ),
            finding(
                "M2", "real problem", path="handler.go", line=4,
                evidence="\tx := 1",
            ),
        )

        verification = _post_process(rec, str(review), str(tmp_path))

        result = review.read_text()
        assert "- **[M1]** `handler.go:4` — real problem" in result
        assert "cannot be read to verify" not in result
        # The drop note still names the file, which is the record of why.
        assert verification["dropped"] == ["M1"]

    def test_strips_the_prior_findings_ledger(self, tmp_path):
        review = tmp_path / "review.md"
        rec = record(
            summary="Prior finding fixed.",
            ledger=(LedgerLine("M1", PriorDisposition.FIXED, "handler.go"),),
        )
        _post_process(rec, str(review))
        assert SECTION_PRIOR_FINDINGS not in review.read_text()


# ── reconcile_dropped_findings ──────────────────────────────────────────────


class TestReconcileDroppedFindings:
    """A review must never describe a finding evidence verification removed.

    The synthesis agent writes the Summary and the Verdict before verification
    runs, so both are asserted against the finished file rather than against
    the intermediate dict.
    """

    @staticmethod
    def _sample(tmp_path, verdict="Request changes — the unchecked error is a bug.", extra=()):
        """A review whose Summary names a must-fix that will not verify."""
        (tmp_path / "kept.py").write_text("x = 1\n")
        return record(
            finding("M1", "the error is never checked", path="deleted.py", line=10),
            *extra,
            finding("N1", "prefer a constant", path="kept.py"),
            summary=(
                "Solid change overall; the most serious problem is the unchecked "
                "error in `deleted.py`."
            ),
            verdict=verdict,
            severity_sections=(SeveritySection("M"), SeveritySection("N")),
        )

    def test_drop_leaves_a_note_naming_what_went(self, tmp_path):
        rec = self._sample(tmp_path)
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "Evidence verification removed 1 finding:" in result
        assert "> - Must fix — `deleted.py`: file not found" in result
        # The finding itself is gone, so only the note may mention it.
        assert "the error is never checked" not in result

    def test_note_lands_in_the_summary_it_corrects(self, tmp_path):
        rec = self._sample(tmp_path)
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        summary = result.index("## Summary")
        note = result.index("Evidence verification removed")
        assert summary < note < result.index("## Must fix")

    def test_note_does_not_cite_renumbered_ids(self, tmp_path):
        # Two must-fix findings, the second surviving and renumbered M2 -> M1.
        rec = self._sample(
            tmp_path,
            extra=(finding("M2", "also worth fixing", path="kept.py"),),
        )
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        note = result[result.index("Evidence verification removed"):]
        note = note[:note.index("## ")]
        assert "[M1]" not in note and "[M2]" not in note
        # M2 survived and took M1's number — citing the dropped ID would point
        # the reader at it.
        assert "- **[M1]** `kept.py:1`" in result

    def test_verdict_is_lowered_to_what_survives(self, tmp_path):
        rec = self._sample(tmp_path)
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        verdict = result[result.index("## Verdict"):]
        assert "Approve — 1 nit" in verdict
        assert "Request changes" not in result

    def test_disapprove_is_never_lowered(self, tmp_path):
        # Disapprove means the approach is wrong, which the finding counts do
        # not derive — dropping a finding cannot refute it. The note still says
        # what went, so the reader can weigh the verdict against it.
        rec = self._sample(tmp_path, verdict="**Disapprove** — the approach is wrong.")
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "**Disapprove** — the approach is wrong." in result
        assert "Evidence verification removed 1 finding:" in result

    def test_verdict_the_counts_still_support_is_left_alone(self, tmp_path):
        rec = self._sample(
            tmp_path,
            extra=(finding("M2", "also worth fixing", path="kept.py"),),
        )
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "Request changes — the unchecked error is a bug." in result
        assert "Evidence verification removed 1 finding:" in result

    def test_no_note_when_nothing_dropped(self, tmp_path):
        (tmp_path / "kept.py").write_text("x = 1\n")
        review = tmp_path / "review.md"
        rec = record(
            finding("M1", "the error is never checked", path="kept.py"),
            summary="One real problem.",
            verdict="Request changes — worth fixing.",
        )
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "Evidence verification" not in result
        assert "Request changes — worth fixing." in result

    def test_second_pass_does_not_stack_notes(self, tmp_path):
        rec = self._sample(tmp_path)
        done = post_process_record(rec, str(tmp_path))
        again = post_process_record(done.record, str(tmp_path))
        assert render_body(again.record).count("Evidence verification removed") == 1

    def test_note_survives_without_a_summary_section(self, tmp_path):
        # The mechanical paths post-process the merged content, which has no
        # Summary — they build one from the processed text afterwards.
        review = tmp_path / "review.md"
        rec = record(
            finding("M1", "the error is never checked", path="deleted.py", line=10),
            severity_sections=(SeveritySection("M"),),
        )
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert result.index("Evidence verification removed") < result.index("## Must fix")

    def test_drop_reason_reports_evidence_mismatch_with_char_offset(self, tmp_path):
        # kept.py exists, so the drop is a content mismatch rather than a
        # missing file — the reason must name the offset, not "file not found".
        (tmp_path / "kept.py").write_text("x = 1\n")
        review = tmp_path / "review.md"
        rec = record(
            finding(
                "M1", "quotes code that is not there", path="kept.py",
                evidence="y = 2",
            ),
            summary="One real problem.",
            verdict="Request changes — worth fixing.",
        )
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "> - Must fix — `kept.py`: evidence mismatch at char" in result

    def test_drop_note_pluralizes_the_header_for_multiple_findings(self, tmp_path):
        rec = self._sample(
            tmp_path,
            extra=(finding(
                "M2", "another error never checked", path="also-deleted.py",
            ),),
        )
        review = tmp_path / "review.md"
        _post_process(rec, str(review), str(tmp_path))
        result = review.read_text()

        assert "Evidence verification removed 2 findings:" in result
        assert "> - Must fix — `deleted.py`: file not found" in result
        assert "> - Must fix — `also-deleted.py`: file not found" in result
