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

from agent import phases as agent_phases
from agent import retry as agent_retry
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.registry import PHASES, RETRYABLE_FIX_PHASES, REVIEW_PHASES
from core.phases import Effort, Phase


class TestPhaseTurns:
    def test_a_phase_that_does_not_scale_takes_its_flat_budget(self):
        assert agent_phases.phase_turns(Phase.CI_FIX) == PHASES[Phase.CI_FIX].max_turns

    def test_items_below_the_flat_budget_do_not_shrink_it(self):
        # 3 items at 5 turns each is 15 — under the fix phase's flat 20.
        assert agent_phases.phase_turns(Phase.FIX, items=3) == 20

    def test_items_above_the_flat_budget_scale_it_up(self):
        assert agent_phases.phase_turns(Phase.FIX, items=6) == 30

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

    def test_a_full_fix_chunk_gets_the_per_item_rate(self):
        """Raising the rate without the cap would still bind at 2 turns/item."""
        chunk = agent_phases.phase_chunk_size(Phase.FIX)
        budget = agent_phases.phase_turns(Phase.FIX, items=chunk)
        assert budget / chunk >= 5


class TestEffortBuysTurns:
    """``--effort`` has to move the resource a deep review runs out of.

    The regression: a preset carried a thinking level and a dollar budget and
    nothing else, so a review that exhausted its turns was answered by a deeper
    preset with the same 15 turns — and a higher thinking level burning the
    larger dollar budget faster. Raising effort could not fix the failure it
    was reached for.
    """

    def test_high_effort_gives_a_phase_more_turns_than_medium(self):
        medium = agent_phases.phase_turns(Phase.SINGLE, Effort.MEDIUM)
        high = agent_phases.phase_turns(Phase.SINGLE, Effort.HIGH)
        assert high > medium

    def test_every_review_phase_gains_turns_at_high_effort(self):
        """Not just the one phase the regression was found on."""
        for phase in REVIEW_PHASES:
            medium = agent_phases.phase_turns(phase, Effort.MEDIUM)
            high = agent_phases.phase_turns(phase, Effort.HIGH)
            assert high > medium, phase

    # passes-at-base: pins the baseline the multiplier must leave alone
    def test_medium_is_the_registry_default_unscaled(self):
        """The baseline every registry number is written against."""
        assert (agent_phases.phase_turns(Phase.SINGLE, Effort.MEDIUM)
                == PHASES[Phase.SINGLE].max_turns)

    # passes-at-base: asserts the multiplier was not used to shrink low effort
    def test_low_effort_does_not_cut_below_the_registry_minimum(self):
        """A shallower review still has to write its file."""
        assert (agent_phases.phase_turns(Phase.SINGLE, Effort.LOW)
                >= PHASES[Phase.SINGLE].max_turns)

    # passes-at-base: back-compat, that the new scaling did not displace the old
    def test_the_omitted_file_bump_survives_the_multiplier(self):
        """Both scalings apply — the multiplier must not replace the bump."""
        flat = agent_phases.phase_turns(Phase.SINGLE, Effort.HIGH)
        bumped = agent_phases.phase_turns(Phase.SINGLE, Effort.HIGH, omitted_files=2)
        assert bumped == flat + agent_phases.omitted_turns(Effort.HIGH, 2)

    # passes-at-base: back-compat, that effort cannot lift a fix pass's cap
    def test_an_item_scaled_phase_still_stops_at_its_cap(self):
        """Effort must not lift the ceiling one agent can finish inside."""
        cap = PHASES[Phase.FIX].scaling.turns_cap
        assert agent_phases.phase_turns(
            Phase.FIX, Effort.HIGH, items=100,
        ) == cap


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
        assert agent_phases.phase_retry_turns(Phase.FIX, 30) == 60
        assert agent_phases.phase_retry_turns(Phase.COMMENTS_FIX, 60) == 120

    def test_partial_progress_does_not_shrink_the_retry(self):
        """Zero ticks doubles via turns_for; leftovers must not get less."""
        exhausted = Diagnosis(DiagnosisKind.MAX_TURNS)
        for phase in RETRYABLE_FIX_PHASES:
            spec = PHASES[phase]
            cap = spec.scaling.turns_cap or spec.max_turns
            originals = {spec.max_turns, cap}
            if cap > spec.max_turns:
                originals.add((spec.max_turns + cap) // 2)
            for original in sorted(originals):
                none = agent_retry.turns_for(
                    exhausted, original, ceiling=spec.retry.ceiling,
                )
                partial = agent_phases.phase_retry_turns(phase, original)
                assert partial >= none, (phase, original)

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
        assert agent_phases.phase_retry_turns(Phase.FIX, 500) == 120
        assert agent_phases.phase_retry_turns(Phase.COMMENTS_FIX, 500) == 120

    def test_every_retryable_phase_outgrows_a_maxed_first_pass(self):
        """The invariant the whole function exists for, over each phase reaching it.

        A pass that scaled to its `turns_cap` and still ran out is exactly when
        a retry matters, and it is the case a ceiling set at the cap silently
        broke: `min(cap + bump, cap)` is `cap`, so the retry re-ran at the
        budget that had just failed.
        """
        for phase in RETRYABLE_FIX_PHASES:
            spec = PHASES[phase]
            cap = spec.scaling.turns_cap or spec.max_turns
            assert agent_phases.phase_retry_turns(phase, cap) > cap, phase

    def test_a_ceiling_never_sits_at_or_below_the_cap_it_must_clear(self):
        """Guards the shape of the defect rather than one arithmetic result."""
        for phase in RETRYABLE_FIX_PHASES:
            spec = PHASES[phase]
            cap = spec.scaling.turns_cap or spec.max_turns
            assert spec.retry.ceiling > cap, phase


class TestPhaseChunkSize:
    """A chunk must fit both caps, or the pass starves on whichever binds first."""

    def test_the_fix_pass_chunk_fits_the_cap(self):
        scaling = PHASES[Phase.FIX].scaling
        chunk = agent_phases.phase_chunk_size(Phase.FIX)
        assert chunk * scaling.turns_per_item <= scaling.turns_cap

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
