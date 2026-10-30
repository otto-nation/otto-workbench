"""agent.phase_report — the resolver's answer per phase, with the scope behind each source.

The CLI goldens (`config_phases_golden_test`) record whole outputs; this suite
pins the rules those outputs are built from: where a scope comes from, what an
unset value looks like, and that an operator error stays on its phase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phase_report
import config.workbench_config
from agent.registry import PHASES
from core.phases import Phase, Tier

from workbench_config_support import roots, _write  # noqa: F401


def _row(report, phase):
    return next(row for row in report.phases if row.phase is phase)


def test_every_registry_phase_is_reported_in_registry_order(roots):
    report = agent.phase_report.phase_report(roots[1])
    assert [row.phase for row in report.phases] == list(PHASES)
    assert [row.tier for row in report.tiers] == list(Tier)


def _tier(report, tier):
    return next(t for t in report.tiers if t.tier is tier)


def test_an_unset_tier_is_null_with_no_scope_and_names_the_key_to_set(roots):
    report = agent.phase_report.phase_report(roots[1])
    assert _tier(report, Tier.DEEP) == agent.phase_report.TierRow(Tier.DEEP, None, None)
    row = _row(report, Phase.SYNTHESIS)
    assert row.model is None and row.error is None
    assert row.model_source == agent.phase_report.Source("unset", "models.deep", None)


def test_a_tier_set_by_the_project_names_the_project_scope(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "models:\n  light: openai/gpt-6-mini\n")
    _write(project / ".workbench.yml", "models:\n  light: xai/grok-4.6-mini\n")
    report = agent.phase_report.phase_report(project)
    assert _tier(report, Tier.LIGHT) == agent.phase_report.TierRow(
        Tier.LIGHT, "xai/grok-4.6-mini", config.workbench_config.PROJECT_SCOPE)
    assert _row(report, Phase.SCOUT).model_source.scope == config.workbench_config.PROJECT_SCOPE


def test_an_env_source_has_the_env_scope(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "minimal")
    source = _row(agent.phase_report.phase_report(roots[1]), Phase.FIX).thinking_source
    assert source == agent.phase_report.Source("global_env", "WORKBENCH_AI_THINKING", "env")


def test_the_spec_source_has_the_default_scope_and_no_key(roots):
    source = _row(agent.phase_report.phase_report(roots[1]), Phase.SINGLE).thinking_source
    assert source == agent.phase_report.Source("spec", None, config.workbench_config.DEFAULT_SCOPE)


def test_a_borrowed_tier_is_reported_as_via(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "models:\n  light: openai/gpt-6-mini:low\n")
    _write(project / ".workbench.yml", "agent:\n  phases:\n    synthesis:\n      model: light\n")
    source = _row(agent.phase_report.phase_report(project), Phase.SYNTHESIS).model_source
    assert source == agent.phase_report.Source(
        "phase_config", "agent.phases.synthesis.model", "project",
        agent.phase_report.Source("tier", "models.light", "global"))


def test_an_unset_tier_name_is_an_error_on_that_phase_only(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  phases:\n    synthesis:\n      model: deep\n")
    report = agent.phase_report.phase_report(project)
    bad = _row(report, Phase.SYNTHESIS)
    assert "agent.phases.synthesis.model names the deep tier" in bad.error
    assert (bad.model, bad.model_source, bad.thinking, bad.thinking_source) == (None, None, None, None)
    assert _row(report, Phase.DISPROVE).error is None


def test_the_removed_provider_env_key_is_an_error_on_every_phase(roots, monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_PROVIDER", "xai")
    report = agent.phase_report.phase_report(roots[1])
    assert all("WORKBENCH_AI_PROVIDER" in row.error for row in report.phases)


def test_an_unreadable_scope_raises_config_error(roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "models: [\n")
    with pytest.raises(config.workbench_config.ConfigError):
        agent.phase_report.phase_report(roots[1])


def test_as_json_shape(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "models:\n  deep: anthropic/claude-opus-5:high\n")
    out = agent.phase_report.phase_report(project).as_json()
    assert list(out) == ["schema", "tiers", "phases"]
    assert out["schema"] == agent.phase_report.SCHEMA_VERSION
    assert out["tiers"]["deep"] == {"value": "anthropic/claude-opus-5:high", "scope": "global"}
    assert out["phases"]["synthesis"] == {
        "domain": "review",
        "tier": "deep",
        "model": {"provider": "anthropic", "id": "claude-opus-5", "level": "high"},
        "model_source": {"layer": "tier", "key": "models.deep", "scope": "global", "via": None},
        "thinking": "high",
        "thinking_source": {"layer": "tier_model", "key": "models.deep", "scope": "global", "via": None},
        "error": None,
    }
