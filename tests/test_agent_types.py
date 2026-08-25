"""Tests for agent_types — the phase registry and the vocabulary around it."""

import dataclasses
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent_types
from agent_types import (
    REVIEW_PHASES, AgentKind, Effort, Phase, PhaseDomain, Thinking,
)


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

    def test_every_phase_carries_a_label(self):
        assert all(s.label for s in agent_types.PHASES.values())


class TestPhaseDomains:
    """Which entry point runs a phase is registry data, not a naming guess."""

    def test_preserves_current_domains(self):
        expected = {
            Phase.SINGLE: PhaseDomain.REVIEW,
            Phase.HOLISTIC: PhaseDomain.REVIEW,
            Phase.SCOUT: PhaseDomain.REVIEW,
            Phase.GROUP: PhaseDomain.REVIEW,
            Phase.SYNTHESIS: PhaseDomain.REVIEW,
            Phase.DISPROVE: PhaseDomain.REVIEW,
            Phase.FIX: PhaseDomain.REVIEW,
            Phase.COMMENTS_FIX: PhaseDomain.COMMENTS,
            Phase.CI_FIX: PhaseDomain.CI,
        }
        assert {p: s.domain for p, s in agent_types.PHASES.items()} == expected

    def test_the_property_reads_the_registry(self):
        for phase, spec in agent_types.PHASES.items():
            assert phase.domain is spec.domain

    def test_review_phases_are_the_review_domain(self):
        assert set(REVIEW_PHASES) == {
            p for p, s in agent_types.PHASES.items()
            if s.domain is PhaseDomain.REVIEW
        }

    def test_a_phase_outside_review_names_no_review_artifact(self):
        """It writes into its own entry point's tracking directory.

        Answering with a `comments_fix.md` nobody writes would read as a real
        path — the review sweep would glob for it and the fix pass would look
        for its log where it never lands.
        """
        for phase in (Phase.COMMENTS_FIX, Phase.CI_FIX):
            with pytest.raises(ValueError, match="entry point"):
                phase.output_filename
            with pytest.raises(ValueError, match="entry point"):
                phase.log_filename


class TestPhaseEnvKeys:
    """One phase, one spelling — the config key and the env keys agree."""

    def test_both_keys_derive_from_the_phase_value(self):
        for phase in Phase:
            assert phase.model_env_key == f"WORKBENCH_AI_{phase.value.upper()}_MODEL"
            assert phase.thinking_env_key == f"WORKBENCH_AI_{phase.value.upper()}_THINKING"

    def test_every_phase_value_is_a_usable_env_key_fragment(self):
        """A hyphenated value would build an env key no shell can export.

        The config key takes the value verbatim, so a phase named ``ci-fix``
        would read fine from YAML and produce ``WORKBENCH_AI_CI-FIX_MODEL``,
        which nothing can set. Underscores are the separator a new phase takes.
        """
        for phase in Phase:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", phase.value), phase

    def test_keys_are_distinct_across_phases(self):
        keys = [p.model_env_key for p in Phase] + [p.thinking_env_key for p in Phase]
        assert len(set(keys)) == len(keys)


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
            # Neither fix pass named a thinking level before it was a phase —
            # both called the backend without one and took its default.
            Phase.COMMENTS_FIX: None,
            Phase.CI_FIX: None,
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
            Phase.COMMENTS_FIX: 20,
            Phase.CI_FIX: 20,
        }
        actual = {p: s.max_turns for p, s in agent_types.PHASES.items()}
        assert actual == expected


class TestPhaseBudgetDefaults:
    """A phase outside a review pins its own dollar cap.

    There is no ``--effort`` at those entry points, so a ``None`` budget would
    resolve to nothing at all rather than to a preset.
    """

    def test_only_the_non_review_phases_pin_a_budget(self):
        pinned = {
            p for p, s in agent_types.PHASES.items() if s.max_budget is not None
        }
        assert pinned == {Phase.COMMENTS_FIX, Phase.CI_FIX}

    def test_preserves_current_caps(self):
        assert agent_types.PHASES[Phase.COMMENTS_FIX].max_budget == 2.0
        assert agent_types.PHASES[Phase.CI_FIX].max_budget == 3.0


class TestItemScaling:
    def test_chunk_size_is_the_tightest_cap(self):
        # 60 turns at 5 each allows 12; $5 at $0.50 each allows 10.
        scaling = agent_types.PHASES[Phase.COMMENTS_FIX].scaling
        assert scaling.chunk_size == 10

    def test_a_phase_that_scales_with_nothing_bounds_no_chunk(self):
        assert agent_types.PHASES[Phase.CI_FIX].scaling.chunk_size == 0


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

    def test_only_the_fix_phases_edit(self):
        editing = {p for p, s in agent_types.PHASES.items() if s.edits}
        assert editing == {Phase.FIX, Phase.COMMENTS_FIX, Phase.CI_FIX}

    def test_no_editing_phase_pins_a_reviewer_agent(self):
        # Every AgentKind is a persona forbidden from touching source files.
        for phase, spec in agent_types.PHASES.items():
            assert not (spec.edits and spec.agent), phase


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
        spec = agent_types.PhaseSpec(Phase.GROUP, PhaseDomain.REVIEW, "Group review")
        assert spec.scales_with_omitted is True


class TestPhaseLogNames:
    """Each review phase's session log is named after the phase.

    Adding a phase must not mean naming its log by hand, so these assert the
    convention over the review domain rather than a hand-written list. The
    exception is the pinning test: it is what proves the convention renamed
    nothing.
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
        assert {p: p.log_filename for p in REVIEW_PHASES} == expected

    def test_every_phase_but_single_has_a_distinct_log(self):
        names = [p.log_filename for p in REVIEW_PHASES if p is not Phase.SINGLE]
        assert all(names)
        assert len(set(names)) == len(names)

    def test_single_names_no_log_of_its_own(self):
        # It writes to the job's log, which the caller may point anywhere.
        assert Phase.SINGLE.log_filename == ""

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in REVIEW_PHASES if "{}" in p.log_filename}
        assert indexed == {Phase.GROUP}


class TestPhaseOutputNames:
    """Each review phase's findings artifact is named after the phase.

    Mirrors TestPhaseLogNames: assert the convention over the review domain
    rather than a hand-written list, with one pinning test proving the
    convention renamed nothing.
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
        assert {p: p.output_filename for p in REVIEW_PHASES} == expected

    def test_phases_that_write_the_review_file_name_no_artifact(self):
        # single and synthesis produce review.md; fix edits it in place.
        empty = {p for p in REVIEW_PHASES if not p.output_filename}
        assert empty == {Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX}

    def test_every_artifact_name_is_distinct(self):
        names = [p.output_filename for p in REVIEW_PHASES if p.output_filename]
        assert len(set(names)) == len(names)

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in REVIEW_PHASES if "{}" in p.output_filename}
        assert indexed == {Phase.GROUP}

    def test_stem_is_shared_with_the_log(self):
        # The two properties differ only by extension. Sharing the stem is
        # what stops them drifting the way the constants drifted from the
        # logs — a phase renamed for one is renamed for both.
        both = [p for p in REVIEW_PHASES if p.log_filename and p.output_filename]
        assert both
        for phase in both:
            assert phase.log_filename.removesuffix(".jsonl") == (
                phase.output_filename.removesuffix(".md")
            )
