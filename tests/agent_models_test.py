"""Model tiers: the reference grammar, the tier a phase reads, and what follows from it.

Not: each env and config layer's order against the next
(`workbench_config_phases_test`), the migration that fills the tiers
(`migration_models_from_agent_model.bats`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phases
import config.workbench_config
from agent.models import ModelRef, UnsetTier, parse_ref, resolve
from agent.registry import PHASES
from config.workbench_config import ModelsConfig
from core.phases import Effort, Phase, Thinking, Tier

from workbench_config_support import roots, _write  # noqa: F401


@pytest.fixture(autouse=True)
def _no_env_layers(monkeypatch):
    """The developer's shell often exports these; every test here starts clean."""
    for key in ("WORKBENCH_AI_MODEL", "WORKBENCH_AI_PROVIDER", "WORKBENCH_AI_THINKING"):
        monkeypatch.delenv(key, raising=False)
    for phase in Phase:
        monkeypatch.delenv(phase.model_env_key, raising=False)
        monkeypatch.delenv(phase.thinking_env_key, raising=False)


# ── The grammar ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "ref"), [
    ("claude-opus-5-5", ModelRef("claude-opus-5-5")),
    ("anthropic/claude-opus-5-5", ModelRef("claude-opus-5-5", provider="anthropic")),
    ("anthropic/claude-opus-5-5:high", ModelRef("claude-opus-5-5", "anthropic", "high")),
    ("claude-haiku-5-5:low", ModelRef("claude-haiku-5-5", level="low")),
    # OpenRouter ids carry slashes of their own: only the first one is the provider's.
    ("openrouter/anthropic/claude-x", ModelRef("anthropic/claude-x", provider="openrouter")),
    # A suffix that is not a thinking level is part of the id.
    ("amazon-bedrock/anthropic.claude-v2:0", ModelRef("anthropic.claude-v2:0", "amazon-bedrock")),
    ("ollama/qwen3:latest", ModelRef("qwen3:latest", provider="ollama")),
    ("ollama/qwen3:latest:xhigh", ModelRef("qwen3:latest", "ollama", "xhigh")),
    # A first segment that is not provider-shaped is the id's: a Bedrock ARN.
    ("arn:aws:bedrock:us-east-1:123:application-inference-profile/abc12",
     ModelRef("arn:aws:bedrock:us-east-1:123:application-inference-profile/abc12")),
    ("arn:aws:bedrock:us-east-1:123:application-inference-profile/abc12:high",
     ModelRef("arn:aws:bedrock:us-east-1:123:application-inference-profile/abc12", level="high")),
])
def test_a_reference_parses_into_its_parts(value, ref):
    assert parse_ref(value) == ref
    assert parse_ref(value).spec() == value


def test_every_thinking_level_is_a_level_suffix():
    for level in Thinking:
        assert parse_ref(f"m:{level.value}").level == level.value


def test_a_tier_name_reads_through_the_tiers_and_is_never_an_id():
    models = ModelsConfig(deep="anthropic/claude-opus-5-5:high")
    assert resolve("deep", models) == ModelRef("claude-opus-5-5", "anthropic", "high")
    assert resolve("", models) is None
    assert resolve(None, models) is None


def test_a_tier_name_takes_a_level_that_replaces_its_models():
    models = ModelsConfig(deep="anthropic/claude-opus-5-5:low")
    assert resolve("deep:high", models) == ModelRef("claude-opus-5-5", "anthropic", "high")


def test_naming_an_unset_tier_is_refused_not_skipped():
    """Asking for a tier is asking for its model; running another layer's is a different choice."""
    with pytest.raises(UnsetTier, match=r"--model names the light tier, but models\.light is not set"):
        resolve("light", ModelsConfig(), where="--model")


# ── The tier a phase reads ────────────────────────────────────────────────────


def test_every_phase_declares_a_tier():
    """A phase with no tier would run on the backend's default whatever the tiers say."""
    untiered = [phase for phase, spec in PHASES.items() if spec.tier is None]
    assert untiered == []


def _ref(phase, explicit, cfg):
    resolved = agent.phases.resolve_phase_model(phase, explicit, cfg)
    return resolved.ref if resolved is not None else None


def _cfg(roots, text):
    config_root, project = roots
    _write(config_root / "config.yml", text)
    return config.workbench_config.load_config(project)


def test_each_phase_runs_its_tiers_model(roots):
    cfg = _cfg(roots, """
models:
  deep: anthropic/claude-opus-5-5
  standard: anthropic/claude-sonnet-5-5
  light: openai/gpt-6-luna
""")
    expected = {
        Tier.DEEP: "claude-opus-5-5",
        Tier.STANDARD: "claude-sonnet-5-5",
        Tier.LIGHT: "gpt-6-luna",
    }
    for phase, spec in PHASES.items():
        assert agent.phases.phase_model(phase, None, cfg) == expected[spec.tier], phase


def test_the_scan_phases_and_the_synthesis_are_split_across_tiers():
    """The assignments the cost argument rests on: cheap scans, a deep synthesis."""
    assert PHASES[Phase.SCOUT].tier is Tier.LIGHT
    assert PHASES[Phase.GROUP].tier is Tier.LIGHT
    assert PHASES[Phase.FIX].tier is Tier.STANDARD
    assert PHASES[Phase.SYNTHESIS].tier is Tier.DEEP
    assert PHASES[Phase.DISPROVE].tier is Tier.DEEP


def test_an_unset_tier_leaves_its_phases_to_the_backend_default(roots):
    cfg = _cfg(roots, "models:\n  deep: claude-opus-5-5\n")
    assert _ref(Phase.SCOUT, None, cfg) is None
    assert agent.phases.phase_model(Phase.SYNTHESIS, None, cfg) == "claude-opus-5-5"


def test_a_phase_override_can_borrow_another_tiers_model(roots):
    cfg = _cfg(roots, """
models:
  deep: anthropic/claude-opus-5-5
  light: anthropic/claude-haiku-5-5
agent:
  phases:
    scout:
      model: deep
""")
    assert _ref(Phase.SCOUT, None, cfg) == ModelRef("claude-opus-5-5", "anthropic")


def test_an_override_naming_an_unset_tier_is_refused(roots):
    cfg = _cfg(roots, """
models:
  light: anthropic/claude-haiku-5-5
agent:
  phases:
    scout:
      model: deep
""")
    with pytest.raises(UnsetTier, match=r"agent\.phases\.scout\.model names the deep tier"):
        agent.phases.phase_model(Phase.SCOUT, None, cfg)


def test_an_explicit_tier_name_that_is_unset_is_refused(roots):
    cfg = _cfg(roots, "models:\n  light: anthropic/claude-haiku-5-5\n")
    with pytest.raises(UnsetTier, match="--model names the deep tier"):
        agent.phases.phase_settings(Phase.SCOUT, "deep", cfg)


# ── What follows from the reference ───────────────────────────────────────────


def test_the_provider_is_the_phases_models(roots, monkeypatch):
    cfg = _cfg(roots, "models:\n  light: openai/gpt-6-luna\n  deep: anthropic/claude-opus-5-5\n")
    assert agent.phases.phase_settings(Phase.SCOUT, None, cfg).provider == "openai"
    assert agent.phases.phase_settings(Phase.SYNTHESIS, None, cfg).provider == "anthropic"


def test_a_provider_written_on_the_reference_beats_the_env_key(roots, monkeypatch):
    """An id is a name in one provider's catalogue; sending it elsewhere is a different model."""
    cfg = _cfg(roots, "models:\n  light: openai/gpt-6-luna\n")
    monkeypatch.setenv("WORKBENCH_AI_PROVIDER", "openrouter")
    assert agent.phases.phase_settings(Phase.SCOUT, None, cfg).provider == "openai"
    explicit = agent.phases.phase_settings(Phase.SCOUT, "xai/grok-4.7", cfg)
    assert explicit.provider == "xai"


def test_the_env_key_supplies_a_provider_to_a_bare_reference(roots, monkeypatch):
    cfg = _cfg(roots, "models:\n  light: claude-haiku-5-5\n")
    monkeypatch.setenv("WORKBENCH_AI_PROVIDER", "google-vertex-claude")
    assert agent.phases.phase_settings(Phase.SCOUT, None, cfg).provider == "google-vertex-claude"


def test_a_bare_id_names_no_provider(roots):
    cfg = _cfg(roots, "models:\n  light: claude-haiku-5-5\n")
    assert agent.phases.phase_settings(Phase.SCOUT, None, cfg).provider is None


def test_the_references_level_beats_the_effort_preset_and_the_spec(roots):
    cfg = _cfg(roots, "models:\n  deep: anthropic/claude-opus-5-5:xhigh\n")
    assert agent.phases.phase_settings(Phase.SYNTHESIS, None, cfg, Effort.LOW).thinking == "xhigh"


def test_a_phases_own_thinking_level_beats_its_tiers(roots):
    cfg = _cfg(roots, """
models:
  deep: anthropic/claude-opus-5-5:xhigh
agent:
  phases:
    synthesis:
      thinking: low
""")
    assert agent.phases.phase_settings(Phase.SYNTHESIS, None, cfg).thinking == "low"


def test_a_tiers_level_beats_the_section_wide_thinking_level(roots):
    """One tier is more specific than every phase."""
    cfg = _cfg(roots, "models:\n  deep: anthropic/claude-opus-5-5:xhigh\nagent:\n  thinking: low\n")
    assert agent.phases.phase_settings(Phase.SYNTHESIS, None, cfg).thinking == "xhigh"
    assert agent.phases.phase_settings(Phase.FIX, None, cfg).thinking == "low"


def test_a_level_on_the_explicit_model_beats_every_configured_level(roots, monkeypatch):
    cfg = _cfg(roots, "agent:\n  thinking: low\n  phases:\n    fix:\n      thinking: low\n")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "medium")
    assert agent.phases.phase_settings(Phase.FIX, "anthropic/claude-x:high", cfg).thinking == "high"


def test_a_level_on_an_env_model_beats_the_config_but_not_the_thinking_env(roots, monkeypatch):
    cfg = _cfg(roots, "agent:\n  thinking: low\n")
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "anthropic/claude-x:high")
    assert agent.phases.phase_settings(Phase.FIX, None, cfg).thinking == "high"
    monkeypatch.setenv("WORKBENCH_AI_FIX_THINKING", "minimal")
    assert agent.phases.phase_settings(Phase.FIX, None, cfg).thinking == "minimal"


def test_an_explicit_model_carries_its_own_provider_and_level(roots):
    cfg = _cfg(roots, "models:\n  standard: anthropic/claude-sonnet-5-5\n")
    settings = agent.phases.phase_settings(Phase.FIX, "xai/grok-4.7:high", cfg)
    assert (settings.model, settings.provider, settings.thinking) == ("grok-4.7", "xai", "high")


def test_the_eval_label_keeps_the_level(roots):
    """Two runs of one model at two levels are two results, and must read so."""
    import eval.task

    class _Task:
        phase = Phase.FIX

    _cfg(roots, "models:\n  standard: anthropic/claude-opus-5-5:low\n")
    assert eval.task.resolved_model(_Task(), "") == "claude-opus-5-5:low"
    settings = eval.task.resolved_settings(_Task(), "")
    assert (settings.model, settings.provider, settings.thinking) == ("claude-opus-5-5", "anthropic", "low")


def test_the_eval_label_of_a_bare_model_is_its_id(roots):
    """A tier rewritten with its provider still files under the baseline its id was recorded as."""
    import eval.task

    class _Task:
        phase = Phase.FIX

    _cfg(roots, "models:\n  standard: openrouter/claude-opus-5-5\n")
    assert eval.task.resolved_model(_Task(), "") == "claude-opus-5-5"


def test_eval_refuses_a_phase_no_layer_names_a_model_for(roots):
    import eval.task

    class _Task:
        phase = Phase.FIX

    _cfg(roots, "models:\n  deep: x\n")
    with pytest.raises(eval.task.EvalModelUnset):
        eval.task.resolved_settings(_Task(), "")
