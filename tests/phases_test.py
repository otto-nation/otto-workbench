"""The phase vocabulary the config and the agent layer both type their fields with."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from core import phases  # noqa: E402
from core.phases import (  # noqa: E402
    AgentKind, Effort, Mode, Phase, PhaseDomain, PhaseShape, Thinking,
)


def test_phase_derives_both_env_keys_from_its_value():
    assert phases.Phase.SYNTHESIS.model_env_key == "WORKBENCH_AI_SYNTHESIS_MODEL"
    assert phases.Phase.SYNTHESIS.thinking_env_key == "WORKBENCH_AI_SYNTHESIS_THINKING"


def test_the_vocabulary_reaches_nothing_above_core():
    """phases.py may not import from the agent layer — that is the cycle it breaks."""
    source = (
        Path(__file__).resolve().parent.parent / "ai" / "lib" / "core" / "phases.py"
    ).read_text()

    assert "agent.types" not in source
    assert "agent_types" not in source
    assert "config.workbench_config" not in source
    assert "workbench_config" not in source


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


class TestPhaseShapes:
    def test_a_shape_exists_for_each_backend_entry_point(self):
        """ai_backend does three things, so a phase can be one of three shapes.

        A fourth member here would be a shape no entry point can run.
        """
        assert {s.value for s in PhaseShape} == {"prompt", "agent", "fix"}


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
