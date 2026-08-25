"""Tests for agent_types — the phase registry and the vocabulary around it."""

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent_types
from agent_types import AgentKind, Effort, Phase, Thinking


class TestPhasesRegistry:
    def test_covers_every_phase(self):
        assert set(agent_types.PHASES) == set(Phase)

    def test_key_matches_spec_phase(self):
        for phase, spec in agent_types.PHASES.items():
            assert spec.phase is phase

    def test_spec_is_frozen(self):
        spec = agent_types.PHASES[Phase.GROUP]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.max_turns = 99

    def test_every_phase_defaults_to_sonnet(self):
        assert {s.model for s in agent_types.PHASES.values()} == {"sonnet"}


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
        actual = {p: s.thinking for p, s in agent_types.PHASES.items()}
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
        actual = {p: s.max_turns for p, s in agent_types.PHASES.items()}
        assert actual == expected


class TestPhaseAgentPins:
    """Three phases are pinned to reviewer-lite regardless of --effort.

    They receive pre-collected data and do no context gathering, so raising
    effort must not upgrade them. A change to this mapping should be a
    deliberate edit to this test, not an incidental side effect.
    """

    def test_pinned_phases(self):
        pinned = {p for p, s in agent_types.PHASES.items() if s.agent is not None}
        assert pinned == {Phase.GROUP, Phase.SCOUT, Phase.DISPROVE}

    def test_pinned_phases_use_reviewer_lite(self):
        for phase in (Phase.GROUP, Phase.SCOUT, Phase.DISPROVE):
            assert agent_types.PHASES[phase].agent is AgentKind.REVIEWER_LITE

    def test_effort_derived_phases(self):
        derived = {
            p for p, s in agent_types.PHASES.items()
            if s.agent is None and not s.edits
        }
        assert derived == {Phase.SINGLE, Phase.HOLISTIC, Phase.SYNTHESIS}

    def test_only_the_fix_phase_edits(self):
        editing = {p for p, s in agent_types.PHASES.items() if s.edits}
        assert editing == {Phase.FIX}


class TestOmittedTurnBumpRegistry:
    """Which phases pay for omitted files is a property of the spec.

    Before, it was the presence or absence of `+ _omitted_turns(job)` at each
    call site — which is how the parallel group fan-out lost its bump. Changing
    this mapping should be a deliberate edit to this test.
    """

    def test_source_reading_phases_scale(self):
        scaling = {
            p for p, s in agent_types.PHASES.items() if s.scales_with_omitted
        }
        assert scaling == {Phase.SINGLE, Phase.HOLISTIC, Phase.SCOUT, Phase.GROUP}

    def test_a_new_phase_inherits_the_bump(self):
        """The default is on, so forgetting the flag over-budgets rather than under."""
        assert agent_types.PhaseSpec(Phase.GROUP).scales_with_omitted is True


class TestPhaseLogNames:
    """Each phase's session log is named after the phase.

    Adding a phase must not mean naming its log by hand, so these assert the
    convention over the enum rather than a hand-written list. The exception
    is the pinning test: it is what proves the convention renamed nothing.
    """

    def test_preserves_current_filenames(self):
        expected = {
            Phase.SINGLE: "",
            Phase.HOLISTIC: "holistic.jsonl",
            Phase.SCOUT: "scout.jsonl",
            Phase.GROUP: "group-{}.jsonl",
            Phase.SYNTHESIS: "synthesis.jsonl",
            Phase.DISPROVE: "disprove.jsonl",
            Phase.FIX: "fix.jsonl",
        }
        assert {p: p.log_filename for p in Phase} == expected

    def test_every_phase_but_single_has_a_distinct_log(self):
        names = [p.log_filename for p in Phase if p is not Phase.SINGLE]
        assert all(names)
        assert len(set(names)) == len(names)

    def test_single_names_no_log_of_its_own(self):
        # It writes to the job's log, which the caller may point anywhere.
        assert Phase.SINGLE.log_filename == ""

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in Phase if "{}" in p.log_filename}
        assert indexed == {Phase.GROUP}


class TestPhaseOutputNames:
    """Each phase's findings artifact is named after the phase.

    Mirrors TestPhaseLogNames: assert the convention over the enum rather
    than a hand-written list, with one pinning test proving the convention
    renamed nothing.
    """

    def test_preserves_current_filenames(self):
        expected = {
            Phase.SINGLE: "",
            Phase.HOLISTIC: "holistic.md",
            Phase.SCOUT: "scout.md",
            Phase.GROUP: "group-{}.md",
            Phase.SYNTHESIS: "",
            Phase.DISPROVE: "disprove.md",
            Phase.FIX: "",
        }
        assert {p: p.output_filename for p in Phase} == expected

    def test_phases_that_write_the_review_file_name_no_artifact(self):
        # single and synthesis produce review.md; fix edits it in place.
        empty = {p for p in Phase if not p.output_filename}
        assert empty == {Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX}

    def test_every_artifact_name_is_distinct(self):
        names = [p.output_filename for p in Phase if p.output_filename]
        assert len(set(names)) == len(names)

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in Phase if "{}" in p.output_filename}
        assert indexed == {Phase.GROUP}

    def test_stem_is_shared_with_the_log(self):
        # The two properties differ only by extension. Sharing the stem is
        # what stops them drifting the way the constants drifted from the
        # logs — a phase renamed for one is renamed for both.
        both = [p for p in Phase if p.log_filename and p.output_filename]
        assert both
        for phase in both:
            assert phase.log_filename.removesuffix(".jsonl") == (
                phase.output_filename.removesuffix(".md")
            )
