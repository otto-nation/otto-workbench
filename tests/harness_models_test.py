"""The model tiers as Claude Code's and Pi's settings read them, and `config models`.

Not: writing those settings files (`claude_settings_models.bats`,
`pi_settings.bats`), the reference grammar (`agent_models_test`).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent.harness_models import CLAUDE_MANAGED_ENV, AutoNeedsTiers, AutoTakesNoLevel, harness_models
from agent.models import UnsetTier
from config.workbench_config import ModelsConfig

from workbench_config_support import roots, _write  # noqa: F401

config_cli = _load_lib("config_cli")


# ─── Claude Code ────────────────────────────────────────────────────────────

def test_each_tier_stands_behind_its_claude_alias():
    claude = harness_models(ModelsConfig(
        deep="claude-opus-5-5:high", standard="anthropic/claude-sonnet-5-5",
        light="google-vertex-claude/claude-haiku-5-5",
    )).claude
    assert claude.env == {
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5-5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-5-5",
    }
    assert claude.skipped == []


def test_a_tier_on_a_provider_the_claude_cli_cannot_run_is_skipped_and_said():
    claude = harness_models(ModelsConfig(light="openai/gpt-6-luna")).claude
    assert claude.env == {}
    assert claude.skipped == [
        "models.light: openai/gpt-6-luna is not a Claude model, so ANTHROPIC_DEFAULT_HAIKU_MODEL is left unset",
    ]


@pytest.mark.parametrize("bare", ["gemini-2.5-flash", "gpt-6-luna", "not-claude-distill-7b"])
def test_a_bare_id_that_is_not_a_claude_model_is_skipped(bare):
    # Valid for Pi under its defaultProvider; Claude Code would fail every request.
    claude = harness_models(ModelsConfig(light=bare)).claude
    assert claude.env == {}
    assert claude.skipped == [
        f"models.light: {bare} is not a Claude model, so ANTHROPIC_DEFAULT_HAIKU_MODEL is left unset",
    ]


@pytest.mark.parametrize("bare", [
    "us.anthropic.claude-opus-5-5-v1:0", "anthropic.claude-haiku-5-5-v1:0",
    "arn:aws:bedrock:us-east-1:1:application-inference-profile/x", "opusplan", "sonnet[1m]",
    "claude-opus-5-5[1m]", "claude-opus-4-6@20250514",
])
def test_a_bare_claude_id_arn_or_alias_is_a_claude_model(bare):
    claude = harness_models(ModelsConfig(deep=bare)).claude
    assert claude.env == {"ANTHROPIC_DEFAULT_OPUS_MODEL": bare}


def test_the_session_tier_gives_the_model_and_its_level_the_effort():
    claude = harness_models(ModelsConfig(deep="claude-opus-5-5", session="deep:xhigh")).claude
    assert (claude.model, claude.effort) == ("claude-opus-5-5", "xhigh")


@pytest.mark.parametrize("level", ["off", "minimal"])
def test_a_level_below_claudes_lowest_effort_runs_at_low(level):
    # The same mapping the pipeline's --effort uses, so a session and a phase on
    # one tier run at one effort.
    claude = harness_models(ModelsConfig(session=f"claude-opus-5-5:{level}")).claude
    assert (claude.model, claude.effort) == ("claude-opus-5-5", "low")


def test_a_session_with_no_level_names_no_effort():
    claude = harness_models(ModelsConfig(session="claude-opus-5-5")).claude
    assert (claude.model, claude.effort) == ("claude-opus-5-5", None)


def test_a_non_claude_session_leaves_claude_code_its_own_model():
    claude = harness_models(ModelsConfig(session="openai/gpt-6:high")).claude
    assert (claude.model, claude.effort) == (None, None)
    assert claude.skipped == ["models.session: openai/gpt-6:high is not a Claude model, so Claude Code keeps its own"]


def test_the_managed_env_names_the_session_variable_it_no_longer_writes():
    assert "ANTHROPIC_MODEL" in CLAUDE_MANAGED_ENV


# ─── Pi ─────────────────────────────────────────────────────────────────────

def test_pi_enables_every_model_once_in_tier_order_then_the_session():
    pi = harness_models(ModelsConfig(
        deep="anthropic/claude-opus-5-5", standard="anthropic/claude-opus-5-5",
        light="claude-haiku-5-5", session="openai/gpt-6",
    )).pi
    assert pi.enabled == ["anthropic/claude-opus-5-5", "claude-haiku-5-5", "openai/gpt-6"]


def test_pi_defaults_come_from_the_session_and_levels_from_every_ref():
    pi = harness_models(ModelsConfig(
        deep="anthropic/claude-opus-5-5:high", light="claude-haiku-5-5:low", session="deep",
    )).pi
    assert (pi.default_model, pi.default_provider, pi.default_thinking) == (
        "claude-opus-5-5", "anthropic", "high")
    # A bare id keeps its level under the bare key; the sync qualifies it.
    assert pi.thinking_levels == {"anthropic/claude-opus-5-5": "high", "claude-haiku-5-5": "low"}


def test_a_bare_session_names_no_provider_so_pi_keeps_its_own():
    pi = harness_models(ModelsConfig(session="claude-opus-5-5")).pi
    assert (pi.default_model, pi.default_provider) == ("claude-opus-5-5", None)


def test_nothing_named_is_nothing_written():
    view = harness_models(ModelsConfig())
    assert view.claude.env == {} and view.claude.model is None
    assert view.pi.enabled == [] and view.pi.default_model is None


def test_a_session_naming_an_unset_tier_is_refused():
    with pytest.raises(UnsetTier):
        harness_models(ModelsConfig(session="light"))


# ─── `config models` ────────────────────────────────────────────────────────

def test_config_models_prints_both_views_with_absent_pi_keys_left_out(capsys, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", """
models:
  deep: anthropic/claude-opus-5-5:high
  session: deep
""")
    assert config_cli.main(["models"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claude"]["env"] == {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5"}
    assert (out["claude"]["model"], out["claude"]["effortLevel"]) == ("claude-opus-5-5", "high")
    assert out["claude"]["managed_env"] == list(CLAUDE_MANAGED_ENV)
    assert out["pi"] == {
        "defaultModel": "claude-opus-5-5",
        "defaultProvider": "anthropic",
        "defaultThinkingLevel": "high",
        "enabledModels": ["anthropic/claude-opus-5-5"],
        "modelThinkingLevels": {"anthropic/claude-opus-5-5": "high"},
    }


def test_config_models_ignores_a_repo_config(capsys, roots, monkeypatch):
    # Both settings files are the machine's; no repo the sync runs from may move them.
    _, project = roots
    _write(project / ".workbench.yml", "models:\n  session: anthropic/claude-opus-5-5\n")
    monkeypatch.chdir(project)
    assert config_cli.main(["models"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claude"]["model"] is None
    assert out["pi"] == {}


def test_config_models_refuses_an_unset_tier_in_one_line(capsys, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "models:\n  session: standard\n")
    assert config_cli.main(["models"]) == 1
    err = capsys.readouterr().err
    assert "standard" in err
    assert "Traceback" not in err


# ─── models.session: auto ───────────────────────────────────────────────────

_ROUTED = ModelsConfig(
    deep="anthropic/claude-opus-5-5:high", standard="anthropic/claude-sonnet-5-5",
    light="openai/gpt-6-luna", session="auto",
)


def test_auto_runs_opusplan_on_claude_code_behind_the_tier_aliases():
    claude = harness_models(_ROUTED).claude
    assert (claude.model, claude.effort) == ("opusplan", None)
    # opusplan resolves through these, so it plans on deep and builds on standard.
    assert claude.env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-5-5"
    assert claude.env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-sonnet-5-5"


def test_auto_starts_pi_on_the_router_and_enables_every_tier_beside_it():
    pi = harness_models(_ROUTED).pi
    assert (pi.default_model, pi.default_provider, pi.default_thinking) == ("auto", "workbench", None)
    assert pi.enabled == [
        "anthropic/claude-opus-5-5", "anthropic/claude-sonnet-5-5", "openai/gpt-6-luna", "workbench/auto",
    ]


@pytest.mark.parametrize("models, missing", [
    (ModelsConfig(deep="anthropic/claude-opus-5-5", standard="anthropic/claude-sonnet-5-5", session="auto"),
     "models.light"),
    # A bare id would be qualified with `workbench` on its way into enabledModels.
    (ModelsConfig(deep="claude-opus-5-5", standard="anthropic/claude-sonnet-5-5",
                  light="anthropic/claude-haiku-5-5", session="auto"), "models.deep"),
])
def test_auto_needs_every_tier_to_name_its_provider(models, missing):
    with pytest.raises(AutoNeedsTiers, match=f"fix {missing}"):
        harness_models(models)


def test_config_models_prints_each_tier_for_the_router(capsys, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", """
models:
  deep: anthropic/claude-opus-5-5:high
  standard: anthropic/claude-sonnet-5-5
  light: openai/gpt-6-luna
  session: auto
""")
    assert config_cli.main(["models"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tiers"] == {
        "deep": {"provider": "anthropic", "id": "claude-opus-5-5", "level": "high"},
        "standard": {"provider": "anthropic", "id": "claude-sonnet-5-5", "level": None},
        "light": {"provider": "openai", "id": "gpt-6-luna", "level": None},
    }
    assert out["claude"]["model"] == "opusplan"


def test_config_models_refuses_an_incomplete_auto_in_one_line(capsys, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "models:\n  deep: anthropic/claude-opus-5-5\n  session: auto\n")
    assert config_cli.main(["models"]) == 1
    err = capsys.readouterr().err
    assert "models.standard, models.light" in err
    assert "Traceback" not in err


def test_auto_leaves_claude_code_its_own_model_when_a_planning_tier_is_not_claude():
    # opusplan would plan on Claude Code's built-in Opus, not on the deep tier.
    claude = harness_models(ModelsConfig(
        deep="openai/gpt-6", standard="anthropic/claude-sonnet-5-5",
        light="anthropic/claude-haiku-5-5", session="auto",
    )).claude
    assert claude.model is None
    assert any("Claude Code keeps its own model" in note for note in claude.skipped)


def test_auto_with_a_level_is_refused_rather_than_read_as_a_model_named_auto():
    with pytest.raises(AutoTakesNoLevel, match="auto takes no level"):
        harness_models(replace(_ROUTED, session="auto:high"))
