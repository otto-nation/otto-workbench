import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent_phases
import review_phases
import review_pipeline
import review_steps
from phases import AgentKind, Effort, Phase, Thinking
from review_phases import PhaseResult
from review_state import PipelineState
from review_types import PRContext, PRMetadata, ReviewJob


class TestEffortPresets:
    def test_all_tiers_exist(self):
        assert set(review_pipeline.EFFORT_PRESETS) == set(Effort)

    def test_presets_are_frozen(self):
        import dataclasses

        preset = review_pipeline.EFFORT_PRESETS[Effort.LOW]
        with pytest.raises(dataclasses.FrozenInstanceError):
            preset.agent_budget = 99.0

    def test_thinking_override_values(self):
        assert review_pipeline.EFFORT_PRESETS[Effort.LOW].thinking is Thinking.LOW
        assert review_pipeline.EFFORT_PRESETS[Effort.MEDIUM].thinking is None
        assert review_pipeline.EFFORT_PRESETS[Effort.HIGH].thinking is Thinking.HIGH

    def test_low_skips_phases(self):
        low = review_pipeline.EFFORT_PRESETS[Effort.LOW]
        assert low.skips == frozenset({
            Phase.HOLISTIC, Phase.SCOUT, Phase.SYNTHESIS, Phase.DISPROVE,
        })

    def test_medium_does_not_skip_phases(self):
        assert review_pipeline.EFFORT_PRESETS[Effort.MEDIUM].skips == frozenset()

    def test_high_does_not_skip_phases(self):
        assert review_pipeline.EFFORT_PRESETS[Effort.HIGH].skips == frozenset()

    def test_agent_budget_scales_with_effort(self):
        assert review_pipeline.EFFORT_PRESETS[Effort.LOW].agent_budget < \
               review_pipeline.EFFORT_PRESETS[Effort.MEDIUM].agent_budget < \
               review_pipeline.EFFORT_PRESETS[Effort.HIGH].agent_budget

    def test_max_groups_scales_with_effort(self):
        assert review_pipeline.EFFORT_PRESETS[Effort.LOW].max_groups < \
               review_pipeline.EFFORT_PRESETS[Effort.MEDIUM].max_groups < \
               review_pipeline.EFFORT_PRESETS[Effort.HIGH].max_groups

    def test_low_has_higher_multi_phase_thresholds(self):
        low = review_pipeline.EFFORT_PRESETS[Effort.LOW]
        medium = review_pipeline.EFFORT_PRESETS[Effort.MEDIUM]
        assert low.multi_phase_line_threshold > medium.multi_phase_line_threshold
        assert low.multi_phase_file_threshold > medium.multi_phase_file_threshold

    def test_preset_values_unchanged(self):
        low = review_pipeline.EFFORT_PRESETS[Effort.LOW]
        assert low.agent_budget == 3.0
        assert low.max_groups == 6
        assert low.agent is AgentKind.REVIEWER_LITE
        medium = review_pipeline.EFFORT_PRESETS[Effort.MEDIUM]
        assert medium.agent_budget == 5.0
        assert medium.max_groups == 8
        assert medium.agent is AgentKind.REVIEWER
        high = review_pipeline.EFFORT_PRESETS[Effort.HIGH]
        assert high.agent_budget == 8.0
        assert high.max_groups == 16
        assert high.agent is AgentKind.REVIEWER

    def test_unknown_effort_raises(self):
        with pytest.raises(KeyError):
            review_pipeline.EFFORT_PRESETS["extreme"]

    def test_helpers_are_gone(self):
        """The fallback arguments masked typos — nothing should reintroduce them."""
        assert not hasattr(review_pipeline, "_effort_default")
        assert not hasattr(review_pipeline, "_effort_thinking")


class TestOmittedTurns:
    """The effort tier decides whether omitted files cost turns at all."""

    def test_medium_adds_turns_for_omitted(self):
        turns = agent_phases.omitted_turns(Effort.MEDIUM, 2)
        assert turns == 2 * agent_phases.OMITTED_FILE_TURNS

    def test_low_skips_omitted_turns(self):
        assert agent_phases.omitted_turns(Effort.LOW, 2) == 0

    def test_high_adds_turns_for_omitted(self):
        assert agent_phases.omitted_turns(Effort.HIGH, 1) == agent_phases.OMITTED_FILE_TURNS

    def test_no_omitted_files_returns_zero(self):
        assert agent_phases.omitted_turns(Effort.MEDIUM, 0) == 0


def _make_job(tmp_path, effort=Effort.MEDIUM, mode="pr", skip_phases=frozenset()):
    review_file = str(tmp_path / "review.md")
    return ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata("t", "", "head", "main", "abc123", 100, 5, 3, []),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=review_file,
        session_log=str(tmp_path / "session.jsonl"),
        effort=effort, mode=mode, skip_phases=skip_phases,
    )


class TestHolisticSkipReason:
    """Phase 1 is two candidate scans, so it drops out only when both are off."""

    def test_incremental_skips(self, tmp_path):
        reason = review_steps._holistic_skip_reason(
            _make_job(tmp_path), True, 10)
        assert reason == "incremental review"

    def test_both_scan_flags_skip(self, tmp_path):
        job = _make_job(
            tmp_path, skip_phases=frozenset({Phase.HOLISTIC, Phase.SCOUT}))
        assert review_steps._holistic_skip_reason(job, False, 10) == \
            "--no-holistic --no-scout"

    def test_no_holistic_alone_falls_back_to_scout(self, tmp_path):
        job = _make_job(tmp_path, skip_phases=frozenset({Phase.HOLISTIC}))
        assert review_steps._holistic_skip_reason(job, False, 10) is None
        assert review_steps._scan_phase(job) is Phase.SCOUT

    def test_no_scout_alone_falls_back_to_holistic(self, tmp_path):
        job = _make_job(tmp_path, skip_phases=frozenset({Phase.SCOUT}))
        assert review_steps._holistic_skip_reason(job, False, 10) is None
        assert review_steps._scan_phase(job) is Phase.HOLISTIC

    def test_low_effort_skips(self, tmp_path):
        reason = review_steps._holistic_skip_reason(
            _make_job(tmp_path, effort=Effort.LOW), False, 10)
        assert reason == "effort=low"

    def test_medium_effort_does_not_skip(self, tmp_path):
        reason = review_steps._holistic_skip_reason(
            _make_job(tmp_path, effort=Effort.MEDIUM), False, 10)
        assert reason is None

    def test_high_effort_does_not_skip(self, tmp_path):
        reason = review_steps._holistic_skip_reason(
            _make_job(tmp_path, effort=Effort.HIGH), False, 10)
        assert reason is None

    def test_few_groups_skips(self, tmp_path):
        reason = review_steps._holistic_skip_reason(
            _make_job(tmp_path), False, 2)
        assert "threshold" in reason

    def test_enough_groups_does_not_skip(self, tmp_path):
        reason = review_steps._holistic_skip_reason(
            _make_job(tmp_path), False, 10)
        assert reason is None


class TestHolisticPhaseStateUpdate:
    @patch.object(review_steps, "_write_pipeline_state")
    def test_skip_incremental_marks_done(self, mock_write, tmp_path):
        job = _make_job(tmp_path)
        state = PipelineState(head_sha="abc", group_names=["g1"])
        assert state.scanned is False

        result = review_steps._run_holistic_phase(
            job, group_count=1, state=state, incremental=True,
        )
        assert result == PhaseResult()
        assert state.scanned is True
        mock_write.assert_called_once_with(job, state)

    @patch.object(review_steps, "_write_pipeline_state")
    def test_skip_both_scan_flags_marks_done(self, mock_write, tmp_path):
        job = _make_job(
            tmp_path, skip_phases=frozenset({Phase.HOLISTIC, Phase.SCOUT}))
        state = PipelineState(head_sha="abc", group_names=["g1"])

        review_steps._run_holistic_phase(
            job, group_count=10, state=state, incremental=False,
        )
        assert state.scanned is True
        mock_write.assert_called_once()

    @patch.object(review_steps, "_write_pipeline_state")
    def test_skip_already_done_no_write(self, mock_write, tmp_path):
        job = _make_job(tmp_path)
        state = PipelineState(
            head_sha="abc", group_names=["g1"], done={Phase.SCOUT})

        review_steps._run_holistic_phase(
            job, group_count=1, state=state, incremental=True,
        )
        assert state.scanned is True
        mock_write.assert_not_called()

    @patch.object(review_steps, "_write_pipeline_state")
    def test_a_skipped_scan_records_the_scan_it_would_have_run(
        self, mock_write, tmp_path,
    ):
        """`--no-scout` alone falls back to the holistic scan, so a run that
        skips phase 1 for some other reason records `holistic` — a single
        `holistic_done` bool recorded the scout scan under the holistic name."""
        job = _make_job(tmp_path, skip_phases=frozenset({Phase.SCOUT}))
        state = PipelineState(head_sha="abc", group_names=["g1"])

        review_steps._run_holistic_phase(
            job, group_count=1, state=state, incremental=True,
        )
        assert state.done == {Phase.HOLISTIC}


class TestShouldDisprove:
    """`--disprove` beats the preset; `--no-disprove` beats `--disprove`.

    The two sources of a skip are kept apart on the job for this one gate —
    everywhere else a skipped phase is a skipped phase.
    """

    def test_medium_runs_the_gate(self, tmp_path):
        assert review_phases._should_disprove(_make_job(tmp_path)) is True

    def test_low_effort_drops_the_gate(self, tmp_path):
        job = _make_job(tmp_path, effort=Effort.LOW)
        assert review_phases._should_disprove(job) is False

    def test_explicit_disprove_buys_back_a_dropped_gate(self, tmp_path):
        job = _make_job(tmp_path, effort=Effort.LOW)
        assert review_phases._should_disprove(job, True) is True

    def test_no_disprove_beats_explicit_disprove(self, tmp_path):
        job = _make_job(tmp_path, skip_phases=frozenset({Phase.DISPROVE}))
        assert review_phases._should_disprove(job, True) is False

    def test_no_disprove_drops_the_gate(self, tmp_path):
        job = _make_job(tmp_path, skip_phases=frozenset({Phase.DISPROVE}))
        assert review_phases._should_disprove(job) is False


