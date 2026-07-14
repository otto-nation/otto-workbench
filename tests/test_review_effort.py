import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"))

import review_pipeline
from review_preflight import PipelineState, PRContext, PRMetadata, ReviewJob


class TestEffortPresets:
    def test_all_tiers_exist(self):
        assert set(review_pipeline.EFFORT_PRESETS.keys()) == {"low", "medium", "high"}

    def test_all_tiers_have_same_keys(self):
        keys = None
        for tier, preset in review_pipeline.EFFORT_PRESETS.items():
            if keys is None:
                keys = set(preset.keys())
            else:
                assert set(preset.keys()) == keys, f"{tier} has different keys"

    def test_no_model_keys(self):
        for tier, preset in review_pipeline.EFFORT_PRESETS.items():
            for key in preset:
                assert not key.endswith("_model"), f"{tier} has model key: {key}"

    def test_thinking_override_values(self):
        assert review_pipeline.EFFORT_PRESETS["low"]["thinking"] == "low"
        assert review_pipeline.EFFORT_PRESETS["medium"]["thinking"] is None
        assert review_pipeline.EFFORT_PRESETS["high"]["thinking"] == "high"

    def test_low_skips_phases(self):
        low = review_pipeline.EFFORT_PRESETS["low"]
        assert low["skip_synthesis"] is True
        assert low["skip_angles"] is True
        assert low["skip_holistic"] is True

    def test_medium_does_not_skip_phases(self):
        medium = review_pipeline.EFFORT_PRESETS["medium"]
        assert medium["skip_synthesis"] is False
        assert medium["skip_angles"] is False
        assert medium["skip_holistic"] is False

    def test_agent_budget_scales_with_effort(self):
        assert review_pipeline.EFFORT_PRESETS["low"]["agent_budget"] < \
               review_pipeline.EFFORT_PRESETS["medium"]["agent_budget"] < \
               review_pipeline.EFFORT_PRESETS["high"]["agent_budget"]

    def test_max_groups_scales_with_effort(self):
        assert review_pipeline.EFFORT_PRESETS["low"]["max_groups"] < \
               review_pipeline.EFFORT_PRESETS["medium"]["max_groups"] < \
               review_pipeline.EFFORT_PRESETS["high"]["max_groups"]

    def test_low_has_higher_multi_phase_thresholds(self):
        low = review_pipeline.EFFORT_PRESETS["low"]
        medium = review_pipeline.EFFORT_PRESETS["medium"]
        assert low["multi_phase_line_threshold"] > medium["multi_phase_line_threshold"]
        assert low["multi_phase_file_threshold"] > medium["multi_phase_file_threshold"]


class TestEffortDefault:
    def test_returns_preset_value(self):
        assert review_pipeline._effort_default("low", "agent_budget", 0) == 3.0

    def test_returns_fallback_for_unknown_effort(self):
        assert review_pipeline._effort_default("unknown", "agent_budget", 99) == 99

    def test_returns_fallback_for_unknown_key(self):
        assert review_pipeline._effort_default("low", "nonexistent_key", "fallback") == "fallback"

    def test_medium_returns_default_values(self):
        assert review_pipeline._effort_default("medium", "agent_budget", 0) == 5.0

    def test_high_returns_high_values(self):
        assert review_pipeline._effort_default("high", "agent_budget", 0) == 8.0

    def test_boolean_keys(self):
        assert review_pipeline._effort_default("low", "skip_synthesis", False) is True
        assert review_pipeline._effort_default("medium", "skip_synthesis", True) is False


class TestEffortThinking:
    def test_low_overrides_phase_default(self):
        assert review_pipeline._effort_thinking("low", "high") == "low"

    def test_medium_uses_phase_default(self):
        assert review_pipeline._effort_thinking("medium", "high") == "high"

    def test_high_overrides_phase_default(self):
        assert review_pipeline._effort_thinking("high", "low") == "high"

    def test_unknown_effort_uses_phase_default(self):
        assert review_pipeline._effort_thinking("unknown", "medium") == "medium"

    def test_none_phase_default_with_override(self):
        assert review_pipeline._effort_thinking("low", None) == "low"

    def test_none_phase_default_without_override(self):
        assert review_pipeline._effort_thinking("medium", None) is None


class TestOmittedTurns:
    def _make_job(self, effort="medium", omitted_files=None):
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
        job = self._make_job(effort="medium", omitted_files=["big.py", "huge.py"])
        turns = review_pipeline._omitted_turns(job)
        assert turns == 2 * review_pipeline.OMITTED_FILE_TURNS

    def test_low_skips_omitted_turns(self):
        job = self._make_job(effort="low", omitted_files=["big.py", "huge.py"])
        turns = review_pipeline._omitted_turns(job)
        assert turns == 0

    def test_high_adds_turns_for_omitted(self):
        job = self._make_job(effort="high", omitted_files=["big.py"])
        turns = review_pipeline._omitted_turns(job)
        assert turns == review_pipeline.OMITTED_FILE_TURNS

    def test_no_omitted_files_returns_zero(self):
        job = self._make_job(effort="medium")
        assert review_pipeline._omitted_turns(job) == 0


class TestHolisticSkipReason:
    def test_incremental_skips(self):
        reason = review_pipeline._holistic_skip_reason(False, True, 10)
        assert reason == "incremental review"

    def test_no_holistic_flag_skips(self):
        reason = review_pipeline._holistic_skip_reason(True, False, 10)
        assert reason == "--no-holistic"

    def test_low_effort_skips(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10, effort="low")
        assert reason == "effort=low"

    def test_medium_effort_does_not_skip(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10, effort="medium")
        assert reason is None

    def test_high_effort_does_not_skip(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10, effort="high")
        assert reason is None

    def test_few_groups_skips(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 2)
        assert "threshold" in reason

    def test_enough_groups_does_not_skip(self):
        reason = review_pipeline._holistic_skip_reason(False, False, 10)
        assert reason is None


def _make_job(tmp_path, effort="medium", mode="pr"):
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


class TestAnglesPhaseStateUpdate:
    @patch.object(review_pipeline, "_phase_group_reviews", return_value=([], []))
    @patch.object(review_pipeline, "_write_pipeline_state")
    def test_pr_mode_marks_angles_done(self, mock_write, mock_groups, tmp_path):
        job = _make_job(tmp_path, mode="pr")
        state = PipelineState(head_sha="abc", group_names=["g1"])
        assert state.angles_done is False

        review_pipeline._run_groups_and_angles(
            job, groups=[], group_count=0,
            holistic_content="", max_parallel=1,
            skip_groups=None, state=state,
        )
        assert state.angles_done is True
        mock_write.assert_called_once_with(job, state)

    @patch.object(review_pipeline, "_phase_group_reviews", return_value=([], []))
    @patch.object(review_pipeline, "_write_pipeline_state")
    def test_effort_skip_marks_angles_done(self, mock_write, mock_groups, tmp_path):
        job = _make_job(tmp_path, mode="self", effort="low")
        state = PipelineState(head_sha="abc", group_names=["g1"])

        review_pipeline._run_groups_and_angles(
            job, groups=[], group_count=0,
            holistic_content="", max_parallel=1,
            skip_groups=None, state=state,
        )
        assert state.angles_done is True

    @patch.object(review_pipeline, "_phase_group_reviews", return_value=([], []))
    @patch.object(review_pipeline, "_write_pipeline_state")
    def test_already_done_no_write(self, mock_write, mock_groups, tmp_path):
        job = _make_job(tmp_path, mode="pr")
        state = PipelineState(head_sha="abc", group_names=["g1"], angles_done=True)

        review_pipeline._run_groups_and_angles(
            job, groups=[], group_count=0,
            holistic_content="", max_parallel=1,
            skip_groups=None, state=state,
        )
        assert state.angles_done is True
        mock_write.assert_not_called()
