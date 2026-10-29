"""Which setting answered a phase's model and thinking level.

`agent.phases` already decides both; these tests pin that it also says *where*
each came from — the key a reader would edit and the tier a value borrowed — so
`config phases` reports provenance from the resolver itself rather than from a
second copy of the chain. Precedence between layers is
`workbench_config_phases_test`'s; this suite asserts the label on the winner.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phases
import config.workbench_config
from agent.models import ModelLayer, named_tier
from agent.phases import ThinkingLayer
from core.phases import Phase, Thinking, Tier

from workbench_config_support import roots, _write  # noqa: F401

TIERS = """
models:
  deep: anthropic/claude-opus-5:high
  standard: anthropic/claude-sonnet-5
  light: openai/gpt-6-mini:low
"""


def _cfg(roots, text):
    config_root, project = roots
    _write(config_root / "config.yml", text)
    return config.workbench_config.load_config(project)


# ── named_tier ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("value", "tier"), [
    ("deep", Tier.DEEP),
    ("light:high", Tier.LIGHT),
    ("xai/grok-4.6", None),
    ("deep-research-model", None),
    ("", None),
    (None, None),
])
def test_named_tier_reads_a_tier_name_with_or_without_a_level(value, tier):
    assert named_tier(value) is tier


# ── The model ─────────────────────────────────────────────────────────────────


def test_the_tier_layer_names_the_tier_key(roots):
    resolved = agent.phases.resolve_phase_model(Phase.SCOUT, None, _cfg(roots, TIERS))
    assert (resolved.layer, resolved.key, resolved.tier) == (ModelLayer.TIER, "models.light", None)


def test_a_phase_env_model_names_its_env_key(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "xai/grok-4.6")
    resolved = agent.phases.resolve_phase_model(Phase.SCOUT, None, _cfg(roots, TIERS))
    assert (resolved.layer, resolved.key, resolved.tier) == (
        ModelLayer.PHASE_ENV, "WORKBENCH_AI_SCOUT_MODEL", None)


def test_a_phase_config_tier_name_records_the_tier_it_borrowed(roots):
    cfg = _cfg(roots, TIERS + "agent:\n  phases:\n    synthesis:\n      model: light\n")
    resolved = agent.phases.resolve_phase_model(Phase.SYNTHESIS, None, cfg)
    assert (resolved.layer, resolved.key, resolved.tier) == (
        ModelLayer.PHASE_CONFIG, "agent.phases.synthesis.model", Tier.LIGHT)
    assert resolved.ref.id == "gpt-6-mini"


def test_a_global_env_tier_name_records_the_tier_it_borrowed(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "deep:low")
    resolved = agent.phases.resolve_phase_model(Phase.FIX, None, _cfg(roots, TIERS))
    assert (resolved.layer, resolved.key, resolved.tier) == (
        ModelLayer.GLOBAL_ENV, "WORKBENCH_AI_MODEL", Tier.DEEP)
    assert resolved.ref.level == "low"


# ── The thinking level ────────────────────────────────────────────────────────


def _thinking(roots, text, phase=Phase.SCOUT):
    cfg = _cfg(roots, text)
    resolved = agent.phases.resolve_phase_model(phase, None, cfg)
    return agent.phases.resolve_phase_thinking(phase, resolved, cfg)


def test_the_phase_thinking_env_key_beats_a_level_on_the_phase_env_model(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "xai/grok-4.6:high")
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_THINKING", "minimal")
    got = _thinking(roots, TIERS)
    assert (got.level, got.layer, got.key) == (
        "minimal", ThinkingLayer.PHASE_ENV, "WORKBENCH_AI_SCOUT_THINKING")


def test_a_level_on_the_phase_env_model_beats_the_global_thinking_env(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "xai/grok-4.6:high")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "minimal")
    got = _thinking(roots, TIERS)
    assert (got.level, got.layer, got.key) == (
        "high", ThinkingLayer.PHASE_ENV_MODEL, "WORKBENCH_AI_SCOUT_MODEL")


def test_the_global_thinking_env_beats_a_level_on_the_global_env_model(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "deep")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "minimal")
    got = _thinking(roots, TIERS)
    assert (got.level, got.layer, got.key) == (
        "minimal", ThinkingLayer.GLOBAL_ENV, "WORKBENCH_AI_THINKING")


def test_a_level_carried_through_a_borrowed_tier_names_the_tier(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "deep")
    got = _thinking(roots, TIERS)
    assert (got.level, got.layer, got.key, got.tier) == (
        "high", ThinkingLayer.GLOBAL_ENV_MODEL, "WORKBENCH_AI_MODEL", Tier.DEEP)


def test_the_phase_thinking_key_beats_a_level_on_the_phase_model(roots):
    got = _thinking(roots, TIERS + """
agent:
  phases:
    fix:
      model: anthropic/claude-x:high
      thinking: low
""", Phase.FIX)
    assert (got.level, got.layer, got.key) == (
        Thinking.LOW, ThinkingLayer.PHASE_CONFIG, "agent.phases.fix.thinking")


def test_a_level_on_the_phase_config_model(roots):
    got = _thinking(roots, TIERS + "agent:\n  phases:\n    fix:\n      model: anthropic/claude-x:high\n",
                    Phase.FIX)
    assert (got.level, got.layer, got.key) == (
        "high", ThinkingLayer.PHASE_CONFIG_MODEL, "agent.phases.fix.model")


def test_a_level_on_the_tier_model(roots):
    got = _thinking(roots, TIERS)
    assert (got.level, got.layer, got.key) == ("low", ThinkingLayer.TIER_MODEL, "models.light")


def test_the_section_wide_thinking_key(roots):
    got = _thinking(roots, "agent:\n  thinking: max\n", Phase.FIX)
    assert (got.level, got.layer, got.key) == (Thinking.MAX, ThinkingLayer.AGENT_CONFIG, "agent.thinking")


def test_the_spec_answers_last_and_names_no_key(roots):
    got = _thinking(roots, "", Phase.SINGLE)
    assert (got.level, got.layer, got.key) == (Thinking.MEDIUM, ThinkingLayer.SPEC, None)


def test_nothing_set_and_no_spec_level_is_none(roots):
    assert _thinking(roots, "", Phase.COMMENTS_TRIAGE) is None


# ── One table, two readers ────────────────────────────────────────────────────


@pytest.mark.parametrize("phase", list(Phase))
def test_phase_settings_runs_the_level_resolve_phase_thinking_reports(roots, monkeypatch, phase):
    """The report and the run read one chain, so they cannot disagree."""
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "xai/grok-4.6:high")
    monkeypatch.setenv("WORKBENCH_AI_FIX_THINKING", "minimal")
    cfg = _cfg(roots, TIERS + "agent:\n  thinking: medium\n  phases:\n    synthesis:\n      model: light\n")
    resolved = agent.phases.resolve_phase_model(phase, None, cfg)
    reported = agent.phases.resolve_phase_thinking(phase, resolved, cfg)
    ran = agent.phases.phase_settings(phase, None, cfg).thinking
    assert (reported.level if reported else None) == ran
