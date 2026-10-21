"""The record transforms that replaced document surgery, held to today's output."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent.diagnosis import Diagnosis, DiagnosisKind
from core.phases import Phase
from review.record import Disposition, LedgerLine, TriageEntry
from review.render import render_review
from review.state import PipelineState, apply_failures, failures_of
from review.types import PriorDisposition
from review.verdict import CLEAN_SUMMARY, CLEAN_VERDICT, PARTIAL_VERDICT, build_mechanical_record
from review.artifacts import DisproveArtifact, DisproveOutcome, DisproveVerdict
from review.verify import apply_disprove, post_process_record

from review_record_support import (
    canonical, copy_tree, expected_text, fixture_meta, fixture_record, finding, record,
)


class TestPostProcess:
    def test_the_evidence_fixture_comes_out_as_todays_pipeline_wrote_it(self, tmp_path):
        tree = copy_tree(tmp_path / "tree")
        raw = fixture_record("evidence_dropped", "raw.json")
        done = post_process_record(raw, str(tree), ["src/app.py", "src/cli.py"])
        assert done.verification["dropped"] == ["M1"]
        rendered = render_review(done.record, fixture_meta("evidence_dropped"))
        assert canonical(rendered) == canonical(expected_text("evidence_dropped"))

    def test_post_processing_twice_does_not_stack_notes(self, tmp_path):
        tree = copy_tree(tmp_path / "tree")
        raw = fixture_record("evidence_dropped", "raw.json")
        once = post_process_record(raw, str(tree)).record
        twice = post_process_record(once, str(tree)).record
        assert twice.summary.count("verification-drops") == 1

    def test_without_a_tree_only_the_cleanups_run(self):
        rec = record(
            finding("N3", "`a` — x", path="", sid="ab12cd34", stable_id=""),
            ledger=(LedgerLine("S1", PriorDisposition.FIXED, "a.py"),),
        )
        done = post_process_record(rec)
        assert done.verification is None and done.record.ledger == ()
        assert [(f.id, f.sid) for f in done.record.findings] == [("N1", "")]

    def test_a_drop_note_without_a_summary_gets_one(self, tmp_path):
        tree = copy_tree(tmp_path / "tree")
        rec = record(finding(
            "M1", "`--timeout` is compared as a string.",
            path="src/cli.py", line=5,
            evidence="if str(args.timeout) > elapsed:",
        ))
        done = post_process_record(rec, str(tree), ["src/cli.py"])
        assert done.verification["dropped"] == ["M1"]
        summary = done.record.summary.strip()
        assert summary.startswith("<!-- verification-drops -->")
        assert "findings across" not in summary
        lines = [ln for ln in summary.splitlines() if ln.strip()]
        assert lines[0] == "<!-- verification-drops -->"
        assert all(
            ln == "<!-- verification-drops -->" or ln.startswith(">")
            for ln in lines
        )
        rendered = render_review(done.record, fixture_meta("evidence_dropped"))
        assert summary in rendered


class TestDisprove:
    def test_a_falsified_finding_is_kept_but_hidden(self):
        rec = fixture_record("synthesis_pr")
        applied = apply_disprove(rec, DisproveArtifact(verdicts=(
            DisproveVerdict("S1", DisproveOutcome.FALSIFIED, "not reachable"),
            DisproveVerdict("S2", DisproveOutcome.SURVIVES, "ok"),
        )))
        assert applied.record.finding("S1") is None
        assert next(f for f in applied.record.findings if f.id == "S1").disposition is Disposition.FALSIFIED
        assert applied.summary == {"total_challenged": 2, "survived": 1, "falsified": 1,
                                   "falsified_ids": ["S1"], "reasons": {"S1": "not reachable"}}


class TestMechanical:
    def test_the_clean_review_body(self):
        merged = record(triage=(
            TriageEntry("src/app.py", "reviewed; nothing to report."),
        ))
        rec = build_mechanical_record(merged, group_count=2, summary_note=CLEAN_SUMMARY,
                                      verdict=CLEAN_VERDICT, file_count=3)
        assert canonical(render_review(rec, fixture_meta("clean"))) == canonical(expected_text("clean"))

    def test_a_drop_note_stays_inside_the_summary(self):
        merged = record(finding("N1", "x", path=""))
        from dataclasses import replace
        rec = build_mechanical_record(replace(merged, summary="<!-- verification-drops -->\n> note"),
                                      group_count=1, summary_note="Note.")
        assert rec.summary.startswith("1 finding across 1 groups. Note.\n\n<!-- verification-drops -->")


class TestFailures:
    def _state(self, **kw):
        return PipelineState(head_sha="x", group_names=["a", "b"], **kw)

    def test_rows_mirror_the_table_today(self):
        state = self._state(groups_failed={2: Diagnosis(DiagnosisKind.OUTPUT_MISSING)},
                            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)})
        rows = failures_of(state)
        assert [(r.agent, r.status) for r in rows] == [("group-2: b", "failed"), ("synthesis", "fallback")]

    def test_a_partial_run_withholds_a_stated_verdict(self):
        state = self._state(groups_failed={2: Diagnosis(DiagnosisKind.OUTPUT_MISSING)})
        rec = apply_failures(record(verdict="**Approve.**"), state)
        assert rec.verdict == PARTIAL_VERDICT.strip()

    def test_failures_with_no_named_groups_are_partial(self):
        """`group_count == 0` with failures is partial, matching the old rule."""
        state = PipelineState(
            head_sha="x", group_names=[],
            groups_failed={1: Diagnosis(DiagnosisKind.OUTPUT_MISSING)},
        )
        assert state.partial
        assert not state.all_groups_failed
