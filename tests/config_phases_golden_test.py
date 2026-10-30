"""`otto-workbench config phases --json`, recorded whole for each case the spec names.

Each case writes a config (and an environment), runs the command through
`config_cli.main`, and compares the parsed JSON with a fixture. Spot assertions
beside the goldens state the fact each case exists for, so a regenerated
fixture that records a wrong answer still fails here.

The fixtures are byte-exact command output, pretty-printed, and are written,
never hand-edited. Regenerate them by calling `_write_goldens(roots,
monkeypatch, capsys)` from a throwaway test in this file, then read the diff:
a change is a change to what `config phases` reports. A new phase or a phase
moving tier in `agent.registry` changes every fixture — that is the report
tracking the registry, and the diff should show exactly that.

The last test holds the report to the run: for every case and every phase, the
model and thinking level reported are the ones `agent.phases.phase_settings`
resolves, so the report cannot drift from what a phase actually runs on.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phases
import config.workbench_config
import core.errors
from core.phases import Phase

from workbench_config_support import roots, _write  # noqa: F401

config_cli = _load_lib("config_cli")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "config_phases"

TIERS = """
models:
  deep: anthropic/claude-opus-5:high
  standard: anthropic/claude-sonnet-5
  light: openai/gpt-6-mini:low
"""


@dataclass(frozen=True)
class Case:
    """One config: the global file, the project file (None for none), and the env."""

    global_yaml: str
    project_yaml: str | None = None
    env: dict[str, str] = field(default_factory=dict)


CASES: dict[str, Case] = {
    "no_tiers": Case(""),
    "tiers_set": Case(TIERS),
    "phase_override": Case(TIERS + """
agent:
  phases:
    fix:
      model: anthropic/claude-x:high
      thinking: low
"""),
    "env_override": Case(TIERS, env={
        "WORKBENCH_AI_MODEL": "deep",
        "WORKBENCH_AI_SCOUT_MODEL": "xai/grok-4.6:high",
        "WORKBENCH_AI_THINKING": "minimal",
    }),
    "project_outranks_global": Case(TIERS, project_yaml="models:\n  standard: openai/gpt-6\n"),
    "tier_name_value": Case(TIERS, project_yaml="agent:\n  phases:\n    synthesis:\n      model: light\n"),
    "unset_tier_name": Case("""
models:
  light: openai/gpt-6-mini:low
agent:
  phases:
    synthesis:
      model: deep
"""),
}


def _apply(case: Case, roots, monkeypatch) -> Path:
    """Write *case* into the sandbox, replacing whatever an earlier case left."""
    config_root, project = roots
    _write(config_root / "config.yml", case.global_yaml)
    project_file = project / config.workbench_config.PROJECT_CONFIG_NAME
    if case.project_yaml is None:
        project_file.unlink(missing_ok=True)
    else:
        _write(project_file, case.project_yaml)
    for key, value in case.env.items():
        monkeypatch.setenv(key, value)
    return project


def _render(case: Case, roots, monkeypatch, capsys) -> dict:
    project = _apply(case, roots, monkeypatch)
    assert config_cli.main(["phases", "--json", str(project)]) == 0
    return json.loads(capsys.readouterr().out)


def _write_goldens(roots, monkeypatch, capsys) -> None:
    """Rewrite every fixture from current output; run via `REGEN_GOLDENS=1 pytest -k regenerate`."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, case in CASES.items():
        with monkeypatch.context() as scoped:
            got = _render(case, roots, scoped, capsys)
        (FIXTURES / f"{name}.json").write_text(json.dumps(got, indent=2) + "\n")


@pytest.mark.skipif(os.environ.get("REGEN_GOLDENS") != "1", reason="set REGEN_GOLDENS=1 to rewrite fixtures")
def test_regenerate_goldens(roots, monkeypatch, capsys):
    _write_goldens(roots, monkeypatch, capsys)


# ── Goldens ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden(name, roots, monkeypatch, capsys):
    got = _render(CASES[name], roots, monkeypatch, capsys)
    assert got == json.loads((FIXTURES / f"{name}.json").read_text())


# ── What each case is for ────────────────────────────────────────────────────


def _src(layer, key, scope, via=None):
    return {"layer": layer, "key": key, "scope": scope, "via": via}


def test_no_tiers_is_data_not_failure(roots, monkeypatch, capsys):
    got = _render(CASES["no_tiers"], roots, monkeypatch, capsys)
    assert got["tiers"]["deep"] == {"value": None, "scope": None}
    single = got["phases"]["single"]
    assert single["model"] is None
    assert single["model_source"] == _src("unset", "models.standard", None)
    assert single["thinking_source"] == _src("spec", None, "default")
    assert got["phases"]["ci_fix"]["thinking_source"] == _src("unset", None, None)


def test_tiers_set_carry_their_level_into_thinking(roots, monkeypatch, capsys):
    got = _render(CASES["tiers_set"], roots, monkeypatch, capsys)
    assert got["tiers"]["deep"] == {"value": "anthropic/claude-opus-5:high", "scope": "global"}
    synthesis = got["phases"]["synthesis"]
    assert synthesis["thinking"] == "high"
    assert synthesis["thinking_source"] == _src("tier_model", "models.deep", "global")
    assert got["phases"]["comments_triage"]["thinking_source"] == _src("tier_model", "models.light", "global")


def test_a_phase_thinking_key_beats_the_level_on_its_model(roots, monkeypatch, capsys):
    fix = _render(CASES["phase_override"], roots, monkeypatch, capsys)["phases"]["fix"]
    assert fix["model"] == {"provider": "anthropic", "id": "claude-x", "level": "high"}
    assert fix["model_source"] == _src("phase_config", "agent.phases.fix.model", "global")
    assert (fix["thinking"], fix["thinking_source"]) == (
        "low", _src("phase_config", "agent.phases.fix.thinking", "global"))


def test_the_phase_env_beats_the_global_env(roots, monkeypatch, capsys):
    got = _render(CASES["env_override"], roots, monkeypatch, capsys)
    scout = got["phases"]["scout"]
    assert scout["model"] == {"provider": "xai", "id": "grok-4.6", "level": "high"}
    assert scout["model_source"] == _src("phase_env", "WORKBENCH_AI_SCOUT_MODEL", "env")
    assert (scout["thinking"], scout["thinking_source"]["layer"]) == ("high", "phase_env_model")
    single = got["phases"]["single"]
    assert single["model"]["id"] == "claude-opus-5"
    assert single["model_source"] == _src(
        "global_env", "WORKBENCH_AI_MODEL", "env", _src("tier", "models.deep", "global"))
    assert (single["thinking"], single["thinking_source"]) == (
        "minimal", _src("global_env", "WORKBENCH_AI_THINKING", "env"))


def test_the_project_file_outranks_the_global_one(roots, monkeypatch, capsys):
    got = _render(CASES["project_outranks_global"], roots, monkeypatch, capsys)
    assert got["tiers"]["standard"] == {"value": "openai/gpt-6", "scope": "project"}
    assert got["tiers"]["deep"]["scope"] == "global"
    assert got["phases"]["single"]["model_source"] == _src("tier", "models.standard", "project")


def test_a_tier_name_as_a_phase_value_borrows_that_tier(roots, monkeypatch, capsys):
    synthesis = _render(CASES["tier_name_value"], roots, monkeypatch, capsys)["phases"]["synthesis"]
    via = _src("tier", "models.light", "global")
    assert synthesis["model"] == {"provider": "openai", "id": "gpt-6-mini", "level": "low"}
    assert synthesis["model_source"] == _src("phase_config", "agent.phases.synthesis.model", "project", via)
    assert synthesis["thinking_source"] == _src(
        "phase_config_model", "agent.phases.synthesis.model", "project", via)


def test_a_tier_name_naming_an_unset_tier_is_that_phases_error(roots, monkeypatch, capsys):
    got = _render(CASES["unset_tier_name"], roots, monkeypatch, capsys)
    synthesis = got["phases"]["synthesis"]
    assert synthesis["error"].startswith(
        "agent.phases.synthesis.model names the deep tier, but models.deep is not set")
    assert [synthesis[k] for k in ("model", "model_source", "thinking", "thinking_source")] == [None] * 4
    assert got["phases"]["disprove"]["error"] is None


# ── The report is what a run uses ────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_report_is_what_phase_settings_resolves(name, roots, monkeypatch, capsys):
    got = _render(CASES[name], roots, monkeypatch, capsys)
    cfg = config.workbench_config.load_config(roots[1])
    for phase in Phase:
        assert phase.value in got["phases"], phase
        _assert_row_matches_run(phase, got["phases"][phase.value], cfg)


def _assert_row_matches_run(phase, row, cfg) -> None:
    if row["error"] is not None:
        with pytest.raises(core.errors.OperatorError):
            agent.phases.phase_settings(phase, None, cfg)
        return
    settings = agent.phases.phase_settings(phase, None, cfg)
    model = row["model"] or {}
    assert (model.get("id"), model.get("provider")) == (settings.model, settings.provider), phase
    expected = str(settings.thinking) if settings.thinking is not None else None
    assert row["thinking"] == expected, phase


# ── The command around the report ────────────────────────────────────────────


def test_an_unreadable_config_exits_one_and_prints_no_json(roots, capsys):
    config_root, project = roots
    _write(config_root / "config.yml", "models: [\n")
    assert config_cli.main(["phases", "--json", str(project)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "config.yml" in captured.err


def test_the_table_names_each_source(roots, monkeypatch, capsys):
    project = _apply(CASES["env_override"], roots, monkeypatch)
    assert config_cli.main(["phases", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Tiers" in out and "Phases" in out
    assert "WORKBENCH_AI_SCOUT_MODEL (env)" in out
    assert "WORKBENCH_AI_MODEL (env) → models.deep (global)" in out


def test_the_table_shows_a_phase_error_in_place(roots, monkeypatch, capsys):
    project = _apply(CASES["unset_tier_name"], roots, monkeypatch)
    assert config_cli.main(["phases", str(project)]) == 0
    assert "agent.phases.synthesis.model names the deep tier" in capsys.readouterr().out
