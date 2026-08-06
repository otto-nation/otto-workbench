import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_pipeline
from review_common import AgentKind, Effort, Thinking
from review_preflight import PipelineState, PRContext, PRMetadata, ReviewJob


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
        assert low.skip_synthesis is True
        assert low.skip_holistic is True

    def test_medium_does_not_skip_phases(self):
        medium = review_pipeline.EFFORT_PRESETS[Effort.MEDIUM]
        assert medium.skip_synthesis is False
        assert medium.skip_holistic is False

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
    def _make_job(self, effort=Effort.MEDIUM, omitted_files=None):
        from review_preflight import PreflightData, PRContext, PRMetadata, ReviewJob
        pr = PRMetadata(
            title="test", body="", head="main", base="main",
            head_sha="abc", additions=10, deletions=5,
            changed_files=1, files=[],
        )
        preflight = PreflightData(
            diff="", commit_log="", file_contents={},
            file_permissions={}, claude_md="", architecture_md="",
            omitted_files=omitted_files or [],
        )
        return ReviewJob(
            repo="test/repo", pr_number="1", pr=pr,
            ctx=PRContext(), wt_path="/tmp", review_file="/tmp/review.md",
            session_log="/tmp/log.jsonl", reviews_dir="/tmp/reviews",
            effort=effort, preflight=preflight,
        )

    def test_medium_adds_turns_for_omitted(self):
        job = self._make_job(effort=Effort.MEDIUM, omitted_files=["big.py", "huge.py"])
        turns = review_pipeline._omitted_turns(job)
        assert turns == 2 * review_pipeline.OMITTED_FILE_TURNS

    def test_low_skips_omitted_turns(self):
        job = self._make_job(effort=Effort.LOW, omitted_files=["big.py", "huge.py"])
        turns = review_pipeline._omitted_turns(job)
        assert turns == 0

    def test_high_adds_turns_for_omitted(self):
        job = self._make_job(effort=Effort.HIGH, omitted_files=["big.py"])
        turns = review_pipeline._omitted_turns(job)
        assert turns == review_pipeline.OMITTED_FILE_TURNS

    def test_no_omitted_files_returns_zero(self):
        job = self._make_job(effort=Effort.MEDIUM)
        assert review_pipeline._omitted_turns(job) == 0


class TestHolisticSkipReason:
    def test_incremental_skips(self):
        reason = review_pipeline._holistic_skip_reason(False, True, 10)
        assert reason == "incremental review"

    def test_no_holistic_flag_skips(self):
        reason = review_pipeline._holistic_skip_reason(True, False, 10)
        assert reason == "--no-holistic"

    def test_low_effort_skips(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10, effort=Effort.LOW)
        assert reason == "effort=low"

    def test_medium_effort_does_not_skip(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10, effort=Effort.MEDIUM)
        assert reason is None

    def test_high_effort_does_not_skip(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10, effort=Effort.HIGH)
        assert reason is None

    def test_few_groups_skips(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 2)
        assert "threshold" in reason

    def test_enough_groups_does_not_skip(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10)
        assert reason is None


def _make_job(tmp_path, effort=Effort.MEDIUM, mode="pr"):
    review_file = str(tmp_path / "review.md")
    return ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata("t", "", "head", "main", "abc123", 100, 5, 3, []),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=review_file,
        session_log=str(tmp_path / "session.jsonl"),
        reviews_dir=str(tmp_path),
        effort=effort, mode=mode,
    )


class TestHolisticPhaseStateUpdate:
    @patch.object(review_pipeline, "_write_pipeline_state")
    def test_skip_incremental_marks_done(self, mock_write, tmp_path):
        job = _make_job(tmp_path)
        state = PipelineState(head_sha="abc", group_names=["g1"])
        assert state.holistic_done is False

        result = review_pipeline._run_holistic_phase(
            job, group_count=1, state=state,
            skip_holistic=False, resume_exists=False, incremental=True,
        )
        assert result == ("", "", "", 0.0)
        assert state.holistic_done is True
        mock_write.assert_called_once_with(job, state)

    @patch.object(review_pipeline, "_write_pipeline_state")
    def test_skip_no_holistic_flag_marks_done(self, mock_write, tmp_path):
        job = _make_job(tmp_path)
        state = PipelineState(head_sha="abc", group_names=["g1"])

        review_pipeline._run_holistic_phase(
            job, group_count=10, state=state,
            skip_holistic=True, resume_exists=False, incremental=False,
        )
        assert state.holistic_done is True
        mock_write.assert_called_once()

    @patch.object(review_pipeline, "_write_pipeline_state")
    def test_skip_already_done_no_write(self, mock_write, tmp_path):
        job = _make_job(tmp_path)
        state = PipelineState(head_sha="abc", group_names=["g1"], holistic_done=True)

        review_pipeline._run_holistic_phase(
            job, group_count=1, state=state,
            skip_holistic=False, resume_exists=False, incremental=True,
        )
        assert state.holistic_done is True
        mock_write.assert_not_called()


