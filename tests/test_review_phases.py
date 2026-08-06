import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_pipeline
from review_common import AgentKind, Effort, Phase, Thinking


class TestPhasesRegistry:
    def test_covers_every_phase(self):
        assert set(review_pipeline.PHASES) == set(Phase)

    def test_key_matches_spec_phase(self):
        for phase, spec in review_pipeline.PHASES.items():
            assert spec.phase is phase

    def test_spec_is_frozen(self):
        spec = review_pipeline.PHASES[Phase.GROUP]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.max_turns = 99

    def test_every_phase_defaults_to_sonnet(self):
        assert {s.model for s in review_pipeline.PHASES.values()} == {"sonnet"}


class TestPhaseThinkingDefaults:
    def test_preserves_current_levels(self):
        expected = {
            Phase.SINGLE: Thinking.MEDIUM,
            Phase.HOLISTIC: Thinking.MEDIUM,
            Phase.SCOUT: Thinking.LOW,
            Phase.GROUP: Thinking.LOW,
            Phase.SYNTHESIS: Thinking.MEDIUM,
            Phase.DISPROVE: Thinking.MEDIUM,
            Phase.FIX: Thinking.LOW,
        }
        actual = {p: s.thinking for p, s in review_pipeline.PHASES.items()}
        assert actual == expected


class TestPhaseMaxTurnsDefaults:
    def test_preserves_current_budgets(self):
        expected = {
            Phase.SINGLE: 15,
            Phase.HOLISTIC: 15,
            Phase.SCOUT: 10,
            Phase.GROUP: 15,
            Phase.SYNTHESIS: 15,
            Phase.DISPROVE: 15,
            Phase.FIX: 20,
        }
        actual = {p: s.max_turns for p, s in review_pipeline.PHASES.items()}
        assert actual == expected


class TestPhaseAgentPins:
    """Four phases are pinned to reviewer-lite regardless of --effort.

    They receive pre-collected data and do no context gathering, so raising
    effort must not upgrade them. A change to this mapping should be a
    deliberate edit to this test, not an incidental side effect.
    """

    def test_pinned_phases(self):
        pinned = {p for p, s in review_pipeline.PHASES.items() if s.agent is not None}
        assert pinned == {Phase.GROUP, Phase.SCOUT, Phase.DISPROVE, Phase.FIX}

    def test_pinned_phases_use_reviewer_lite(self):
        for phase in (Phase.GROUP, Phase.SCOUT, Phase.DISPROVE, Phase.FIX):
            assert review_pipeline.PHASES[phase].agent is AgentKind.REVIEWER_LITE

    def test_effort_derived_phases(self):
        derived = {p for p, s in review_pipeline.PHASES.items() if s.agent is None}
        assert derived == {Phase.SINGLE, Phase.HOLISTIC, Phase.SYNTHESIS}
