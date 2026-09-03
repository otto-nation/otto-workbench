"""Tests for agent_phases — what a phase's registry entry resolves to.

The budget arithmetic lives here rather than beside each caller: three fix
passes size themselves the same way off three different registry entries, and
asserting the arithmetic once per caller is how the copies drifted in the first
place. What each *caller* does with the number it gets is still tested beside
that caller.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent_phases
from agent_registry import PHASES
from phases import Effort, Phase


class TestPhaseTurns:
    def test_a_phase_that_does_not_scale_takes_its_flat_budget(self):
        assert agent_phases.phase_turns(Phase.CI_FIX) == PHASES[Phase.CI_FIX].max_turns

    def test_items_below_the_flat_budget_do_not_shrink_it(self):
        # 5 items at 2 turns each is 10 — under the fix phase's flat 20.
        assert agent_phases.phase_turns(Phase.FIX, items=5) == 20

    def test_items_above_the_flat_budget_scale_it_up(self):
        assert agent_phases.phase_turns(Phase.FIX, items=25) == 50

    def test_scaling_stops_at_the_cap(self):
        cap = PHASES[Phase.FIX].scaling.turns_cap
        assert agent_phases.phase_turns(Phase.FIX, items=100) == cap

    def test_the_comments_pass_scales_at_its_own_rate(self):
        assert agent_phases.phase_turns(Phase.COMMENTS_FIX) == 20
        assert agent_phases.phase_turns(Phase.COMMENTS_FIX, items=1) == 20
        assert agent_phases.phase_turns(Phase.COMMENTS_FIX, items=5) == 25
        assert agent_phases.phase_turns(Phase.COMMENTS_FIX, items=100) == 60

    def test_a_full_chunk_is_not_capped_down(self):
        """The chunk size exists to keep a full batch inside the caps."""
        for phase in (Phase.FIX, Phase.COMMENTS_FIX):
            scaling = PHASES[phase].scaling
            chunk = agent_phases.phase_chunk_size(phase)
            assert agent_phases.phase_turns(phase, items=chunk) <= scaling.turns_cap


class TestPhaseBudget:
    def test_a_pinned_budget_ignores_the_effort_preset(self):
        assert agent_phases.phase_budget(Phase.CI_FIX, Effort.HIGH) == 3.0

    def test_an_unpinned_budget_takes_the_effort_preset(self):
        assert agent_phases.phase_budget(Phase.GROUP, Effort.LOW) == 3.0
        assert agent_phases.phase_budget(Phase.GROUP, Effort.HIGH) == 8.0

    def test_an_unpinned_budget_outside_a_review_is_unset(self):
        # There is no --effort at those entry points and no preset to fall to.
        assert agent_phases.phase_budget(Phase.GROUP) is None

    def test_items_below_the_pinned_budget_do_not_shrink_it(self):
        assert agent_phases.phase_budget(Phase.COMMENTS_FIX, items=1) == 2.0

    def test_items_above_the_pinned_budget_scale_it_up(self):
        assert agent_phases.phase_budget(Phase.COMMENTS_FIX, items=5) == 2.5

    def test_scaling_stops_at_the_cap(self):
        cap = PHASES[Phase.COMMENTS_FIX].scaling.budget_cap
        assert agent_phases.phase_budget(Phase.COMMENTS_FIX, items=100) == cap


class TestPhaseRetryTurns:
    """A retry must outgrow the attempt it replaces, or it fails identically."""

    def test_a_small_budget_floors_at_the_phase_minimum(self):
        assert agent_phases.phase_retry_turns(Phase.FIX, 20) == 40
        assert agent_phases.phase_retry_turns(Phase.COMMENTS_FIX, 5) == 30

    def test_the_bump_is_applied_above_the_floor(self):
        assert agent_phases.phase_retry_turns(Phase.FIX, 30) == 50
        assert agent_phases.phase_retry_turns(Phase.COMMENTS_FIX, 60) == 75

    def test_a_retry_never_shrinks_the_budget_that_just_ran_out(self):
        for phase in (Phase.FIX, Phase.COMMENTS_FIX, Phase.CI_FIX):
            for original in (5, 20, 60, 500):
                retried = agent_phases.phase_retry_turns(phase, original)
                assert retried >= min(original, PHASES[phase].retry.ceiling), phase

    def test_a_capped_comments_pass_retries_above_its_first_pass_cap(self):
        # Its retry ceiling is set above the cap for exactly this: a retry
        # clamped to the budget that just ran out fails the same way.
        cap = PHASES[Phase.COMMENTS_FIX].scaling.turns_cap
        assert agent_phases.phase_retry_turns(Phase.COMMENTS_FIX, cap) > cap

    def test_the_ceiling_binds(self):
        assert agent_phases.phase_retry_turns(Phase.FIX, 500) == 60
        assert agent_phases.phase_retry_turns(Phase.COMMENTS_FIX, 500) == 120

    def test_a_phase_with_no_retry_budget_keeps_what_it_had(self):
        assert agent_phases.phase_retry_turns(Phase.CI_FIX, 20) == 20


class TestPhaseChunkSize:
    """A chunk must fit both caps, or the pass starves on whichever binds first."""

    def test_the_comments_pass_chunk_fits_both_caps(self):
        scaling = PHASES[Phase.COMMENTS_FIX].scaling
        chunk = agent_phases.phase_chunk_size(Phase.COMMENTS_FIX)
        assert chunk * scaling.turns_per_item <= scaling.turns_cap
        assert chunk * scaling.budget_per_item <= scaling.budget_cap

    def test_the_ci_pass_chunk_fits_both_caps(self):
        scaling = PHASES[Phase.CI_FIX].scaling
        chunk = agent_phases.phase_chunk_size(Phase.CI_FIX)
        assert chunk * scaling.turns_per_item <= scaling.turns_cap
        assert chunk * scaling.budget_per_item <= scaling.budget_cap

    def test_a_phase_that_scales_with_nothing_bounds_no_chunk(self):
        """A prompt-shaped phase is handed one call's worth of work, not a list."""
        assert agent_phases.phase_chunk_size(Phase.COMMENTS_TRIAGE) == 0
