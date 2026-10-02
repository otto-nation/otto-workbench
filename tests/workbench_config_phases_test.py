"""The per-phase model, thinking and effort chains, layer by layer.

Five layers answer a phase's settings — explicit argument, phase env, global
env, project config, global config — and each test pins one boundary between
two of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_report
import config.workbench_config_write
from core.phases import Effort, Phase, Thinking

from workbench_config_support import roots, _write


# ── Precedence across all five layers ───────────────────────────────────────


@pytest.fixture
def phase_cfg(roots):
    """A config that sets a phase model at both scopes, for layering tests."""
    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: global-section
  phases:
    scout:
      model: global-phase
""")
    _write(project / ".workbench.yml", """
agent:
  phases:
    scout:
      model: project-phase
""")
    return project


def test_layer_5_global_config_beats_the_built_in(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: from-global\n")
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "from-global"


def test_layer_4_project_config_beats_the_global(phase_cfg):
    import agent.phases

    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "project-phase"


def test_a_phase_entry_beats_the_section_within_one_file(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: section
  phases:
    scout:
      model: phase
""")
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "phase"
    assert agent.phases.phase_model(Phase.FIX, None, cfg) == "section"


def test_layer_3_global_env_beats_the_config(phase_cfg, monkeypatch):
    import agent.phases

    monkeypatch.setenv("WORKBENCH_AI_MODEL", "from-env")
    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "from-env"


def test_layer_2_phase_env_beats_the_global_env(phase_cfg, monkeypatch):
    import agent.phases

    monkeypatch.setenv("WORKBENCH_AI_MODEL", "from-env")
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "from-phase-env")
    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "from-phase-env"


def test_layer_1_explicit_beats_every_env_and_file(phase_cfg, monkeypatch):
    import agent.phases

    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "from-phase-env")
    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, "explicit", cfg) == "explicit"


def test_phase_model_loads_the_config_itself_when_not_given_one(roots):
    """The default argument is what a single-value caller relies on."""
    import agent.phases

    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: from-disk\n")
    assert agent.phases.phase_model(Phase.SCOUT, None) == "from-disk"


def test_thinking_layers_the_same_way(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  thinking: low
  phases:
    scout:
      thinking: high
""")
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_thinking_default(Phase.SCOUT, Effort.MEDIUM, cfg) is Thinking.HIGH
    assert agent.phases.phase_thinking_default(Phase.FIX, Effort.MEDIUM, cfg) is Thinking.LOW


def test_thinking_falls_back_to_the_effort_preset(roots):
    import agent.phases
    from agent.types import EFFORT_PRESETS

    _, project = roots
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_thinking_default(
        Phase.SCOUT, Effort.HIGH, cfg,
    ) == EFFORT_PRESETS[Effort.HIGH].thinking


def test_effort_falls_back_from_config_to_the_built_in(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  effort: high\n")
    assert agent.phases.resolve_effort(None, config.workbench_config.load_config(project)) is Effort.HIGH
    assert agent.phases.resolve_effort(Effort.LOW, config.workbench_config.load_config(project)) is Effort.LOW
    assert agent.phases.resolve_effort(None, config.workbench_config.WorkbenchConfig()) is Effort.MEDIUM
