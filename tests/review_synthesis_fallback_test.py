"""Synthesis fallback in `review.steps`: what the step records and spends."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from core.phases import Phase
from pr.domains import ReviewStatus
from review.merge import merge_records
from review.record import Question, TriageEntry, load_record
from review.steps import Merged
from review.store import read_review
from review.verdict import (
    BUDGET_SUMMARY, FALLBACK_SUMMARY, NO_RUN_VERDICT, PARTIAL_VERDICT,
    SKIPPED_SUMMARY,
)
from review_record_support import finding, record


def _merged(*findings, **fields) -> Merged:
    return Merged(record(*findings, **fields))


class TestRunSynthesisOrFallback:
    """What the synthesis step records in state, and what it reports spending."""

    # A merge with one finding in it, so the step has something to carry: the
    # clean-review shortcut returns before synthesis and would answer for every
    # test below whatever the branch under test does.
    MERGED = _merged(finding("S1", "cleanup", path="api.go", line=10))

    def _make_state(self, ro):
        return ro.PipelineState(
            head_sha="abc123",
            group_names=["grp-1"],
            done={Phase.HOLISTIC},
            groups_done=[1],
        )

    def _make_job(self, ro, tmp_path, mode="pr", skip_phases=frozenset()):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode), skip_phases=skip_phases,
        )

    def test_a_diagnosed_synthesis_lands_in_state_failed(self, ro, tmp_path, monkeypatch):
        """`_run_synthesis_or_fallback` copies a phase's diagnosis into `state.failed`."""
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path, mode="self")
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            ro._publish(
                job,
                ro._build_mechanical_fallback(
                    job, count, merged.record, skipped_groups=skipped_groups,
                ),
            )
            return review.pipeline.PhaseResult(
                str(tmp_path / "synthesis.jsonl"),
                diagnosis=ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK),
            )

        monkeypatch.setattr(review.steps, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert state.failed == {Phase.SYNTHESIS: ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)}

    def test_a_synthesised_review_quoting_the_note_is_not_a_fallback(
        self, ro, tmp_path, monkeypatch,
    ):
        """A review whose prose quotes the old mechanical note was still written by the agent.

        The diagnosis is the pipeline's own record of which path wrote the
        file, so a review discussing the fallback machinery is not diagnosed
        as one.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nThe merge path stamps "
                "(mechanically merged, not synthesized) and "
                f"{FALLBACK_SUMMARY} on the document.\n\n"
                "## Verdict\nApprove\n"
            )
            return review.pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"))

        monkeypatch.setattr(review.steps, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert Phase.SYNTHESIS not in state.failed

    def test_synthesis_reports_what_its_log_records(self, ro, tmp_path, monkeypatch):
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nok\n\n## Verdict\nApprove\n")
            return review.pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"), 1.25)

        monkeypatch.setattr(review.steps, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result.cost == 1.25

    def test_a_clean_review_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, _merged(), [], 0, 0.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()

    def test_a_mention_of_a_finding_id_is_not_a_finding(self, ro, tmp_path, monkeypatch):
        """The merge declares nothing, so the review is clean — a triage note
        naming a prior ID used to send the run to synthesis with no findings."""
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(
            review.steps, "_phase_synthesis",
            lambda *a, **kw: pytest.fail("synthesis ran for a review with no findings"))

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1,
            _merged(triage=(TriageEntry("api.go", "reviewed, [M1] was fixed here"),)), [],
            0, 0.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()

    def test_a_synthesis_skipped_on_budget_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 25.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()

    def test_no_group_reports_partial_not_a_failed_run(self, ro, tmp_path, monkeypatch):
        """A group phase the operator switched off is not the pipeline failing.

        Every group is unreviewed either way, so the honest verdict is partial
        with `skipped` as the reason — `all groups failed` would blame the
        agents for a review nobody asked to run.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.GROUP}))
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        skipped = ro.Diagnosis(ro.DiagnosisKind.SKIPPED, detail="--no-group")
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [ro.GroupFailure("grp-1", skipped)],
            0, 0.0, 20.0,
        )
        assert state.failed == {Phase.SYNTHESIS: skipped}
        assert state.status is ro.ReviewStatus.PARTIAL

    def test_no_synthesis_writes_the_mechanical_merge(self, ro, tmp_path, monkeypatch):
        """`--no-synthesis` reaches the review file without an agent."""
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        # The merge is post-processed like any other review, and that drops a
        # finding whose file it cannot find.
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(
            review.steps, "_phase_synthesis",
            lambda *a, **kw: pytest.fail("synthesis ran despite --no-synthesis"))

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()
        assert Phase.SYNTHESIS in state.done
        assert state.failed == {}
        assert "api.go:10" in Path(job.review_file).read_text()

    def test_no_synthesis_publishes_the_groups_questions(
        self, ro, tmp_path, monkeypatch,
    ):
        """`--no-synthesis` publishes the groups' questions through the merge."""
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        g1 = record(
            finding("S1", "cleanup", path="api.go", line=10),
            questions=(Question("Q1", None, None, "first?"),),
        )
        g2 = record(questions=(Question("Q1", None, None, "second?"),))
        ro._run_synthesis_or_fallback(
            job, state, "", 1, Merged(merge_records([g1, g2])), [], 0, 0.0, 20.0,
        )
        written = Path(job.review_file).read_text()
        assert "**[Q1]**" in written and "**[Q2]**" in written
        rec = load_record(tmp_path)
        assert [q.body for q in rec.questions] == ["first?", "second?"]

    def test_no_synthesis_writes_a_summary_the_gate_can_resume_from(
        self, ro, tmp_path, monkeypatch,
    ):
        """`--no-synthesis` leaves a review `is_complete_review` accepts.

        Without the section a run resumed at the disprove gate reads its own
        review as unfinished and re-enters synthesis to rewrite it.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 0.0, 20.0,
        )

        written = Path(job.review_file).read_text()
        assert "## Summary" in written
        assert SKIPPED_SUMMARY in written
        # The operator stopped synthesis; no agent failed.
        assert FALLBACK_SUMMARY not in written
        assert ro.is_complete_review(job.review_file)

    def test_a_budget_cut_off_writes_a_summary_naming_the_budget(
        self, ro, tmp_path, monkeypatch,
    ):
        """The budget path says why synthesis did not run, not that it failed."""
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 25.0, 20.0,
        )

        written = Path(job.review_file).read_text()
        assert "## Summary" in written
        assert BUDGET_SUMMARY in written
        assert FALLBACK_SUMMARY not in written
        assert ro.is_complete_review(job.review_file)

    def test_a_budget_cut_off_still_checks_its_findings_against_the_tree(
        self, ro, tmp_path, monkeypatch,
    ):
        """The run least able to afford an unchecked claim still checks them.

        Evidence verification and prior-finding reconciliation read the work
        tree and spend none of the budget that ran out, so the path that ships
        group output on a cut-off post-processes it like every other one.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 25.0, 20.0,
        )

        assert job.verification is not None

    @pytest.mark.parametrize(
        ("skipped", "cost_so_far"), [(True, 0.0), (False, 25.0)],
        ids=["no-synthesis", "budget"],
    )
    def test_neither_no_synthesis_path_states_a_verdict(
        self, ro, tmp_path, monkeypatch, skipped, cost_so_far,
    ):
        """Neither path weighed the review, so neither approves or blocks it."""
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path,
            skip_phases=frozenset({ro.Phase.SYNTHESIS}) if skipped else frozenset(),
        )
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, cost_so_far, 20.0,
        )

        assert "## Verdict" not in Path(job.review_file).read_text()

    def test_every_group_failed_stores_error_and_the_no_run_line(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        state = ro.PipelineState(
            head_sha="abc123",
            group_names=["a", "b"],
            done={Phase.HOLISTIC},
        )
        failed = [
            ro.GroupFailure("a", ro.Diagnosis(ro.DiagnosisKind.AGENT_ERROR, detail="boom")),
            ro.GroupFailure("b", ro.Diagnosis(ro.DiagnosisKind.AGENT_ERROR, detail="boom")),
        ]
        ro._run_synthesis_or_fallback(
            job, state, "", 2, _merged(), failed, 0, 0.0, 20.0,
        )
        stored = read_review(Path(job.review_file))
        assert stored.meta.status is ReviewStatus.ERROR
        assert f"**No verdict.** {NO_RUN_VERDICT}" in Path(job.review_file).read_text()

    def test_no_group_stores_partial_and_withholds_approve(self, ro, tmp_path):
        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.GROUP}))
        (tmp_path / "wt").mkdir()
        state = self._make_state(ro)
        skipped = ro.Diagnosis(ro.DiagnosisKind.SKIPPED, detail="--no-group")
        ro._run_synthesis_or_fallback(
            job, state, "", 1, _merged(),
            [ro.GroupFailure("grp-1", skipped)],
            0, 0.0, 20.0,
        )
        stored = read_review(Path(job.review_file))
        assert stored.meta.status is ReviewStatus.PARTIAL
        assert f"**No verdict.** {PARTIAL_VERDICT}" in Path(job.review_file).read_text()
