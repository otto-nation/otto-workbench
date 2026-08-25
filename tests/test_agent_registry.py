"""Tests for agent_registry — what the phases the workbench ships are set to.

The rules a spec applies to its own fields are ``test_agent_types``. Here the
subject is the inventory: that every phase has an entry, and that the numbers
and pins in those entries are still the ones each call site had before it
became a phase. A change to any of these should be a deliberate edit here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent_registry
from agent_registry import PHASES, REVIEW_PHASES
from agent_types import AgentKind, Phase, PhaseDomain, PhaseShape, Thinking


class TestPhasesRegistry:
    def test_covers_every_phase(self):
        assert set(PHASES) == set(Phase)

    def test_every_phase_defaults_to_sonnet(self):
        assert {s.model for s in PHASES.values()} == {"sonnet"}

    def test_every_phase_carries_a_label(self):
        assert all(s.label for s in PHASES.values())

    def test_no_phase_is_declared_twice(self):
        """Keying the specs by their own phase is what stops the two drifting.

        The cost is that a second spec for a phase already declared overwrites
        the first with no error — the count is the only place that shows.
        """
        assert len(agent_registry._SPECS) == len(PHASES)


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
            Phase.COMMENTS_TRIAGE: PhaseDomain.COMMENTS,
            Phase.CI_FIX: PhaseDomain.CI,
            Phase.REBASE: PhaseDomain.REBASE,
            Phase.DESCRIBE: PhaseDomain.DESCRIBE,
        }
        assert {p: s.domain for p, s in PHASES.items()} == expected

    def test_review_phases_are_the_review_domain(self):
        assert set(REVIEW_PHASES) == {
            p for p, s in PHASES.items() if s.domain is PhaseDomain.REVIEW
        }

    def test_review_phases_keep_the_registry_order(self):
        # review_common globs artifacts in this order, and a reader of
        # docs/ai-review.md reads the pipeline in it.
        assert REVIEW_PHASES == tuple(
            p for p in PHASES if PHASES[p].domain is PhaseDomain.REVIEW
        )


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
            # Nor did any prompt-shaped call, which had no way to name one:
            # ai_backend.prompt took no thinking argument until they became
            # phases. Their default is still the backend's.
            Phase.COMMENTS_TRIAGE: None,
            Phase.REBASE: None,
            Phase.DESCRIBE: None,
        }
        assert {p: s.thinking for p, s in PHASES.items()} == expected


class TestPhaseMaxTurnsDefaults:
    """Only a phase that runs an agent loop has turns to spend.

    A prompt-shaped phase is one stateless call, so ``run_prompt`` reads
    neither ``max_turns`` nor ``max_budget`` and whatever the field defaults to
    is inert. Pinning a number for it here would assert a default nothing
    reads; that ``run_prompt`` reads neither is ``test_agent_invoke``'s.
    """

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
        assert {
            p: s.max_turns for p, s in PHASES.items()
            if s.shape is not PhaseShape.PROMPT
        } == expected


class TestPhaseBudgetDefaults:
    """A fix phase outside a review pins its own dollar cap.

    There is no ``--effort`` at those entry points, so a ``None`` budget would
    resolve to nothing at all rather than to a preset. A prompt-shaped phase
    pins nothing because it spends no budget the pipeline caps.
    """

    def test_only_the_non_review_phases_pin_a_budget(self):
        pinned = {p for p, s in PHASES.items() if s.max_budget is not None}
        assert pinned == {Phase.COMMENTS_FIX, Phase.CI_FIX}

    def test_preserves_current_caps(self):
        assert PHASES[Phase.COMMENTS_FIX].max_budget == 2.0
        assert PHASES[Phase.CI_FIX].max_budget == 3.0


class TestPhaseChunking:
    def test_the_comment_fix_pass_chunks_at_ten(self):
        # 60 turns at 5 each allows 12; $5 at $0.50 each allows 10.
        assert PHASES[Phase.COMMENTS_FIX].scaling.chunk_size == 10

    def test_the_ci_fix_pass_chunks_at_ten(self):
        """The rates restate the flat budget the pass always had, over ten items.

        Both caps therefore have to land on the same ten, or the flat numbers
        `phase_turns` and `phase_budget` answer would no longer be what one
        chunk actually costs.
        """
        # 20 turns at 2 each allows 10; $3 at $0.25 each allows 12.
        assert PHASES[Phase.CI_FIX].scaling.chunk_size == 10


class TestPhaseShapes:
    """A phase's shape is the backend entry point it is allowed to reach."""

    def test_only_the_fix_phases_edit_the_workspace(self):
        editing = {p for p, s in PHASES.items() if s.shape is PhaseShape.FIX}
        assert editing == {Phase.FIX, Phase.COMMENTS_FIX, Phase.CI_FIX}

    def test_only_the_stateless_phases_are_prompts(self):
        stateless = {p for p, s in PHASES.items() if s.shape is PhaseShape.PROMPT}
        assert stateless == {
            Phase.COMMENTS_TRIAGE, Phase.REBASE, Phase.DESCRIBE,
        }

    def test_every_shape_the_vocabulary_names_is_in_use(self):
        # The remaining phases are tool-using agents. PhaseShape is closed at
        # three because ai_backend has three entry points, so a shape no phase
        # reaches would be a fourth one nothing runs.
        assert {s.shape for s in PHASES.values()} == set(PhaseShape)


class TestPhaseAgentPins:
    """Three phases are pinned to reviewer-lite regardless of --effort.

    They receive pre-collected data and do no context gathering, so raising
    effort must not upgrade them. A change to this mapping should be a
    deliberate edit to this test, not an incidental side effect.
    """

    def test_pinned_phases(self):
        pinned = {p for p, s in PHASES.items() if s.agent is not None}
        assert pinned == {Phase.GROUP, Phase.SCOUT, Phase.DISPROVE}

    def test_pinned_phases_use_reviewer_lite(self):
        for phase in (Phase.GROUP, Phase.SCOUT, Phase.DISPROVE):
            assert PHASES[phase].agent is AgentKind.REVIEWER_LITE

    def test_effort_derived_phases(self):
        derived = {
            p for p, s in PHASES.items()
            if s.agent is None and s.shape is PhaseShape.AGENT
        }
        assert derived == {Phase.SINGLE, Phase.HOLISTIC, Phase.SYNTHESIS}

    def test_no_editing_phase_pins_a_reviewer_agent(self):
        # Every AgentKind is a persona forbidden from touching source files.
        for phase, spec in PHASES.items():
            assert not (spec.shape is PhaseShape.FIX and spec.agent), phase

    def test_no_stateless_phase_pins_an_agent(self):
        # A prompt runs no agent definition at all, so a pin here would name a
        # persona the backend is never told about.
        for phase, spec in PHASES.items():
            assert not (spec.shape is PhaseShape.PROMPT and spec.agent), phase


class TestOmittedTurnBump:
    """Which phases pay for omitted files is a property of the spec.

    Before, it was the presence or absence of `+ _omitted_turns(job)` at each
    call site — which is how the parallel group fan-out lost its bump. Changing
    this mapping should be a deliberate edit to this test.
    """

    def test_source_reading_phases_scale(self):
        scaling = {p for p, s in PHASES.items() if s.scales_with_omitted}
        assert scaling == {Phase.SINGLE, Phase.HOLISTIC, Phase.SCOUT, Phase.GROUP}


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
        assert {p: PHASES[p].log_filename for p in REVIEW_PHASES} == expected

    def test_every_phase_but_single_has_a_distinct_log(self):
        names = [
            PHASES[p].log_filename for p in REVIEW_PHASES if p is not Phase.SINGLE
        ]
        assert all(names)
        assert len(set(names)) == len(names)

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in REVIEW_PHASES if "{}" in PHASES[p].log_filename}
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
        assert {p: PHASES[p].output_filename for p in REVIEW_PHASES} == expected

    def test_phases_that_write_the_review_file_name_no_artifact(self):
        # single and synthesis produce review.md; fix edits it in place.
        empty = {p for p in REVIEW_PHASES if not PHASES[p].output_filename}
        assert empty == {Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX}

    def test_every_artifact_name_is_distinct(self):
        names = [
            PHASES[p].output_filename for p in REVIEW_PHASES
            if PHASES[p].output_filename
        ]
        assert len(set(names)) == len(names)

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in REVIEW_PHASES if "{}" in PHASES[p].output_filename}
        assert indexed == {Phase.GROUP}

    def test_stem_is_shared_with_the_log(self):
        # The two properties differ only by extension. Sharing the stem is
        # what stops them drifting the way the constants drifted from the
        # logs — a phase renamed for one is renamed for both.
        specs = [
            PHASES[p] for p in REVIEW_PHASES
            if PHASES[p].log_filename and PHASES[p].output_filename
        ]
        assert specs
        for spec in specs:
            assert spec.log_filename.removesuffix(".jsonl") == (
                spec.output_filename.removesuffix(".md")
            )
