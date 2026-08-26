"""Tests for agent_types — the vocabulary an agent invocation is described in.

Everything here is provable without knowing which phases exist: the env keys a
phase name derives, and the rules a ``PhaseSpec`` applies to whatever it is
handed. What the nine real phases are set to is ``test_agent_registry``.
"""

import dataclasses
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent_types import (
    AgentKind, Effort, Mode, Phase, PhaseDomain, PhaseShape, PhaseSpec,
    ItemScaling, Thinking,
)


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

    def test_a_member_answers_nothing_that_needs_the_registry(self):
        """The vocabulary imports nothing, so it cannot reach the inventory.

        `Phase.domain` used to read `PHASES[self]`, which made the enum depend
        on the module that depends on it. Those questions belong to the spec.
        """
        for name in ("domain", "log_filename", "output_filename"):
            assert not hasattr(Phase.GROUP, name), name


class TestPhaseSpecDefaults:
    def test_is_frozen(self):
        spec = PhaseSpec(Phase.GROUP, PhaseDomain.REVIEW, "Group review")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.max_turns = 99

    def test_a_new_phase_inherits_the_omitted_bump(self):
        """The default is on, so forgetting the flag over-budgets rather than under."""
        spec = PhaseSpec(Phase.GROUP, PhaseDomain.REVIEW, "Group review")
        assert spec.scales_with_omitted is True

    def test_a_new_phase_is_a_tool_using_agent(self):
        """The shape a phase gets by default is the one with no special powers.

        Defaulting to FIX would hand the workspace to a phase nobody said may
        write to it; defaulting to PROMPT would silently drop the tools a
        forgetful declaration still expects to have.
        """
        spec = PhaseSpec(Phase.GROUP, PhaseDomain.REVIEW, "Group review")
        assert spec.shape is PhaseShape.AGENT

    def test_a_new_phase_takes_no_pinned_agent_or_budget(self):
        spec = PhaseSpec(Phase.GROUP, PhaseDomain.REVIEW, "Group review")
        assert spec.agent is None
        assert spec.max_budget is None
        assert spec.thinking is None


class TestPhaseShapes:
    def test_a_shape_exists_for_each_backend_entry_point(self):
        """ai_backend does three things, so a phase can be one of three shapes.

        A fourth member here would be a shape no entry point can run.
        """
        assert {s.value for s in PhaseShape} == {"prompt", "agent", "fix"}


class TestItemScaling:
    def test_chunk_size_is_the_tightest_cap(self):
        # 60 turns at 5 each allows 12; $5 at $0.50 each allows 10.
        scaling = ItemScaling(turns_per_item=5, turns_cap=60,
                              budget_per_item=0.5, budget_cap=5.0)
        assert scaling.chunk_size == 10

    def test_one_scaling_resource_bounds_the_chunk_alone(self):
        assert ItemScaling(turns_per_item=2, turns_cap=60).chunk_size == 30

    def test_a_phase_that_scales_with_nothing_bounds_no_chunk(self):
        assert ItemScaling().chunk_size == 0


class TestPhaseSpecArtifactNames:
    """A spec derives its filenames from its own phase, not from a lookup.

    These assert the derivation rules against specs built here; that the nine
    real phases still resolve to the names they always had is pinned in
    test_agent_registry.
    """

    @staticmethod
    def _review(phase: Phase) -> PhaseSpec:
        return PhaseSpec(phase, PhaseDomain.REVIEW, "label")

    def test_both_names_share_the_phase_as_their_stem(self):
        spec = self._review(Phase.HOLISTIC)
        assert spec.log_filename == "holistic.jsonl"
        assert spec.output_filename == "holistic.md"

    def test_the_fan_out_phase_carries_an_index(self):
        spec = self._review(Phase.GROUP)
        assert spec.log_filename == "group-{}.jsonl"
        assert spec.output_filename == "group-{}.md"

    def test_single_names_no_log_of_its_own(self):
        # It writes to the job's log, which the caller may point anywhere.
        assert self._review(Phase.SINGLE).log_filename == ""

    def test_a_phase_that_writes_the_review_file_names_no_artifact(self):
        for phase in (Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX):
            assert self._review(phase).output_filename == ""

    def test_a_phase_outside_review_names_no_review_artifact(self):
        """It writes into its own entry point's tracking directory.

        Answering with a `comments_fix.md` nobody writes would read as a real
        path — the review sweep would glob for it and the fix pass would look
        for its log where it never lands.
        """
        spec = PhaseSpec(Phase.COMMENTS_FIX, PhaseDomain.COMMENTS, "Fix pass")
        with pytest.raises(ValueError, match="entry point"):
            spec.output_filename
        with pytest.raises(ValueError, match="entry point"):
            spec.log_filename


class TestPhaseSpecTemplates:
    """A spec answers which prompt file it renders, per mode where that differs."""

    @staticmethod
    def _review(phase: Phase, template) -> PhaseSpec:
        return PhaseSpec(phase, PhaseDomain.REVIEW, "label", template=template)

    def test_one_template_answers_every_mode(self):
        spec = self._review(Phase.HOLISTIC, "holistic.md")
        assert spec.template_for() == "holistic.md"
        assert spec.template_for(Mode.SELF) == "holistic.md"

    def test_a_mode_keyed_template_answers_the_mode_asked_for(self):
        spec = self._review(
            Phase.SINGLE, {Mode.PR: "single-agent.md", Mode.SELF: "self-review.md"},
        )
        assert spec.template_for(Mode.PR) == "single-agent.md"
        assert spec.template_for(Mode.SELF) == "self-review.md"

    def test_the_default_mode_is_the_pr(self):
        """A caller outside a review has no mode to name, and must still ask."""
        spec = self._review(Phase.SINGLE, {Mode.PR: "a.md", Mode.SELF: "b.md"})
        assert spec.template_for() == "a.md"

    def test_a_phase_declaring_no_template_says_so(self):
        """An empty string reaches `render` as a missing file one layer later."""
        spec = PhaseSpec(Phase.REBASE, PhaseDomain.REBASE, "Rebase assist")
        with pytest.raises(ValueError, match="no prompt template"):
            spec.template_for()

    def test_a_mapping_missing_a_mode_names_the_phase_that_owes_it_one(self):
        """A bare KeyError names the mode and leaves the phase to be guessed."""
        spec = self._review(Phase.SINGLE, {Mode.PR: "single-agent.md"})
        with pytest.raises(ValueError, match="single.*no prompt template for self"):
            spec.template_for(Mode.SELF)

    def test_a_mode_keyed_template_cannot_be_written_through(self):
        """Every spec is a singleton in PHASES; a write would move every review."""
        spec = self._review(Phase.SINGLE, {Mode.PR: "a.md", Mode.SELF: "b.md"})
        with pytest.raises(TypeError):
            spec.template[Mode.PR] = "other.md"

    def test_the_declared_mapping_is_copied_rather_than_adopted(self):
        """A caller keeping its literal must not keep a handle on the spec."""
        declared = {Mode.PR: "a.md", Mode.SELF: "b.md"}
        spec = self._review(Phase.SINGLE, declared)
        declared[Mode.PR] = "other.md"
        assert spec.template_for(Mode.PR) == "a.md"


class TestVocabularyIsClosed:
    """These enums name choices, so an unrecognised string is not one of them."""

    def test_every_member_set_is_pinned(self):
        assert {e.value for e in Effort} == {"low", "medium", "high"}
        assert {t.value for t in Thinking} == {"low", "medium", "high"}
        assert {a.value for a in AgentKind} == {"reviewer", "reviewer-lite"}
        assert {m.value for m in Mode} == {"pr", "self"}
        assert {d.value for d in PhaseDomain} == {
            "review", "comments", "ci", "rebase", "describe",
        }
