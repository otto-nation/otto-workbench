"""The env thinking layers above the config, through `agent.phases.phase_settings`.

The config layers beneath them are pinned in workbench_config_phases_test and
agent_models_test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phases
from core.phases import Phase


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "config"))
    for key in ("WORKBENCH_AI_THINKING", "WORKBENCH_AI_GROUP_THINKING",
                "WORKBENCH_AI_MODEL", "WORKBENCH_AI_GROUP_MODEL"):
        monkeypatch.delenv(key, raising=False)


def _thinking(explicit=None):
    return agent.phases.phase_settings(Phase.GROUP, explicit).thinking


def test_the_phase_env_key_answers(monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_GROUP_THINKING", "medium")
    assert _thinking() == "medium"


def test_the_global_env_key_answers(monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "xhigh")
    assert _thinking() == "xhigh"


def test_the_phase_env_key_beats_the_global(monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_GROUP_THINKING", "medium")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "xhigh")
    assert _thinking() == "medium"


def test_a_level_on_the_explicit_model_beats_both_env_keys(monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_GROUP_THINKING", "medium")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "xhigh")
    assert _thinking("claude-x:high") == "high"


def test_a_level_on_the_phase_env_model_beats_the_global_thinking_key(monkeypatch):
    """The per-phase setting is more specific than the every-phase one, in env as in config."""
    monkeypatch.setenv("WORKBENCH_AI_GROUP_MODEL", "claude-x:high")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "low")
    assert _thinking() == "high"


def test_the_phase_thinking_key_beats_a_level_on_the_phase_env_model(monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_GROUP_MODEL", "claude-x:high")
    monkeypatch.setenv("WORKBENCH_AI_GROUP_THINKING", "minimal")
    assert _thinking() == "minimal"


def test_the_global_thinking_key_beats_a_level_on_the_global_env_model(monkeypatch):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-x:high")
    monkeypatch.setenv("WORKBENCH_AI_THINKING", "low")
    assert _thinking() == "low"
