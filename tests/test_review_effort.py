import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"))

import review_pipeline


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

    def test_medium_matches_existing_defaults(self):
        medium = review_pipeline.EFFORT_PRESETS["medium"]
        assert medium["group_model"] == review_pipeline.DEFAULT_MODEL_GROUP
        assert medium["group_thinking"] == review_pipeline.DEFAULT_THINKING_GROUP
        assert medium["holistic_model"] == review_pipeline.DEFAULT_MODEL_HOLISTIC
        assert medium["synthesis_model"] == review_pipeline.DEFAULT_MODEL_SYNTHESIS
        assert medium["single_model"] == review_pipeline.DEFAULT_MODEL_SINGLE
        assert medium["angles_model"] == review_pipeline.DEFAULT_MODEL_ANGLES
        assert medium["fix_model"] == review_pipeline.DEFAULT_MODEL_FIX

    def test_low_uses_cheaper_models(self):
        low = review_pipeline.EFFORT_PRESETS["low"]
        assert low["group_model"] == "haiku"
        assert low["single_model"] == "sonnet"
        assert low["fix_model"] == "haiku"

    def test_high_uses_opus_for_groups(self):
        high = review_pipeline.EFFORT_PRESETS["high"]
        assert high["group_model"] == "opus"
        assert high["group_thinking"] == "medium"

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
        assert review_pipeline._effort_default("low", "group_model", "fallback") == "haiku"

    def test_returns_fallback_for_unknown_effort(self):
        assert review_pipeline._effort_default("unknown", "group_model", "fallback") == "fallback"

    def test_returns_fallback_for_unknown_key(self):
        assert review_pipeline._effort_default("low", "nonexistent_key", "fallback") == "fallback"

    def test_medium_returns_default_values(self):
        assert review_pipeline._effort_default("medium", "group_model", "fallback") == "sonnet"
        assert review_pipeline._effort_default("medium", "agent_budget", 0) == 5.0

    def test_high_returns_high_values(self):
        assert review_pipeline._effort_default("high", "group_model", "fallback") == "opus"
        assert review_pipeline._effort_default("high", "agent_budget", 0) == 8.0

    def test_boolean_keys(self):
        assert review_pipeline._effort_default("low", "skip_synthesis", False) is True
        assert review_pipeline._effort_default("medium", "skip_synthesis", True) is False
