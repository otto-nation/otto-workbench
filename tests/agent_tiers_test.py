"""Agents declaring a tier: the shipped agents' frontmatter, and the tier view sync renders them from.

Not: rendering an installed agent (`claude_agents_tiers.bats`), the session and
alias half of the view (`harness_models_test`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import _load_lib

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

from agent.harness_models import ClaudeTier, harness_models
from config.workbench_config import ModelsConfig
from core.phases import Tier

from workbench_config_support import roots, _write  # noqa: F401

config_cli = _load_lib("config_cli")

AGENTS = sorted((REPO_ROOT / "ai" / "claude" / "agents").glob("*.md"))


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    assert lines[0] == "---", path
    end = lines.index("---", 1)
    return dict(line.split(":", 1) for line in lines[1:end] if ":" in line and not line.startswith(" "))


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_every_shipped_agent_declares_a_tier_the_config_has(path):
    # A tier the config does not have installs as `inherit` with nothing said.
    fields = _frontmatter(path)
    assert fields["tier"].strip() in {tier.value for tier in Tier}


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_no_shipped_agent_names_a_model_its_tier_would_replace(path):
    fields = _frontmatter(path)
    assert "model" not in fields and "effort" not in fields


def test_each_claude_tier_carries_its_model_and_effort():
    claude = harness_models(ModelsConfig(
        deep="anthropic/claude-opus-5-5:xhigh", standard="claude-sonnet-5-5",
        light="claude-haiku-5-5:minimal",
    )).claude
    assert claude.tiers == {
        Tier.DEEP: ClaudeTier("claude-opus-5-5", "xhigh"),
        Tier.STANDARD: ClaudeTier("claude-sonnet-5-5", None),
        # Below Claude's lowest effort, as the pipeline runs it.
        Tier.LIGHT: ClaudeTier("claude-haiku-5-5", "low"),
    }


def test_a_tier_naming_no_claude_model_has_no_claude_tier():
    claude = harness_models(ModelsConfig(light="openai/gpt-6-luna")).claude
    assert claude.tiers == {}


def test_config_models_prints_the_tiers_sync_installs_agents_from(capsys, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "models:\n  deep: claude-opus-5-5:high\n  light: openai/gpt-6-luna\n")
    assert config_cli.main(["models"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claude"]["tiers"] == {"deep": {"model": "claude-opus-5-5", "effort": "high"}}
