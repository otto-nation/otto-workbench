"""``context.compact_at``: the config key, each harness's spelling of it, and the review budget.

Not: writing the settings files (`claude_settings_context.bats`,
`pi_compaction.bats`), where the review budget's window comes from
(`review_prompt_window_test`).
"""

import io
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.backend_claude
import agent.invoke
import agent.phases
import agent.pi_models
import review.pipeline
from core.phases import Phase
from review_phases_support import _job
from agent.backend import AgentInvocation
from agent.context_ceiling import capped_window, claude_window, pi_reserve_tokens
from config.workbench_config import ConfigError, load_config
from review.budget import (
    MODEL_CONTEXT_TOKENS, UNSET_MODEL_FLOOR_TOKENS, UnknownModelWindow, model_window_tokens,
    prompt_budget_bytes,
)

config_cli = _load_lib("config_cli")

_LISTING = """\
provider              model             context  max-out  thinking  images
anthropic             claude-opus-5-5   1M       128K     yes       yes
google-vertex-claude  claude-opus-5-5   500K     128K     yes       yes
anthropic             claude-haiku-4-5  200K     64K      yes       yes
anthropic             broken-row        ?        64K      yes       yes
"""


def _write_config(text: str) -> None:
    root = Path(os.environ["WORKBENCH_CONFIG_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yml").write_text(text)


# ── The key ─────────────────────────────────────────────────────────────────


def test_the_ceiling_reads_from_the_config():
    _write_config("context:\n  compact_at: 200000\n")
    assert load_config(None).context.compact_at == 200_000


def test_unset_there_is_no_ceiling():
    assert load_config(None).context.compact_at is None


@pytest.mark.parametrize("value", [0, -5])
def test_a_ceiling_that_is_not_a_positive_count_is_refused_where_it_is_read(value):
    _write_config(f"context:\n  compact_at: {value}\n")
    with pytest.raises(ConfigError, match="positive token count"):
        load_config(None)


# ── Each harness's spelling ─────────────────────────────────────────────────


@pytest.mark.parametrize(("compact_at", "window", "noted"), [
    (None, None, False),
    (200_000, 200_000, False),
    (100_000, 100_000, False),
    (1_000_000, 1_000_000, False),
    (50_000, 100_000, True),
    (2_000_000, 1_000_000, True),
])
def test_claude_runs_at_the_ceiling_within_the_range_it_takes(compact_at, window, noted):
    result = claude_window(compact_at)
    assert result.window == window
    assert (result.note is not None) == noted


def test_a_clamped_ceiling_says_what_it_runs_at():
    assert "compacts at 100,000" in claude_window(50_000).note


def test_pi_reserves_are_keyed_by_provider_because_windows_differ_by_provider():
    assert pi_reserve_tokens(200_000, _LISTING) == {
        "anthropic/claude-opus-5-5": 800_000,
        "google-vertex-claude/claude-opus-5-5": 300_000,
        # At the ceiling: Pi's own reserve already compacts it below.
        "anthropic/claude-haiku-4-5": None,
    }


def test_capped_window_takes_the_smaller():
    assert capped_window(1_000_000, 200_000) == 200_000
    assert capped_window(128_000, 200_000) == 128_000
    assert capped_window(1_000_000, None) == 1_000_000


def test_config_context_prints_both_harnesses(tmp_path, capsys, monkeypatch):
    _write_config("context:\n  compact_at: 200000\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO(_LISTING))
    assert config_cli.main(["context", "--pi-listing", "-"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["compact_at"] == 200_000
    assert printed["claude"] == {"autoCompactWindow": 200_000, "skipped": []}
    assert printed["pi"]["reserveTokens"]["anthropic/claude-opus-5-5"] == 800_000


def test_config_context_names_no_pi_reserves_without_a_ceiling(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(_LISTING))
    assert config_cli.main(["context", "--pi-listing", "-"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"compact_at": None, "claude": {"autoCompactWindow": None, "skipped": []},
                       "pi": {"reserveTokens": {}}}


# ── The Claude pipeline ─────────────────────────────────────────────────────


def _autocompact_of(cmd: list[str]) -> str | None:
    return cmd[cmd.index("--autocompact") + 1] if "--autocompact" in cmd else None


@pytest.mark.parametrize("build", [
    agent.backend_claude._build_agent_cmd, agent.backend_claude._build_fix_cmd,
])
def test_an_agent_run_compacts_at_the_ceiling(build):
    # --bare reads no user settings, so the flag is the only way it applies.
    assert _autocompact_of(build(AgentInvocation(prompt="", compact_at=300_000))) == "300000"
    assert _autocompact_of(build(AgentInvocation(prompt="", compact_at=50_000))) == "100000"


@pytest.mark.parametrize("build", [
    agent.backend_claude._build_agent_cmd, agent.backend_claude._build_fix_cmd,
])
def test_no_ceiling_passes_no_flag(build):
    assert "--autocompact" not in build(AgentInvocation(prompt=""))


def test_a_one_turn_prompt_takes_no_flag():
    assert "--autocompact" not in agent.backend_claude._build_prompt_cmd()


def test_a_phase_resolves_its_ceiling_from_the_repo_s_config(tmp_path):
    _write_config("context:\n  compact_at: 400000\n")
    (tmp_path / ".workbench.yml").write_text("context:\n  compact_at: 300000\n")
    cfg = load_config(tmp_path)
    assert agent.phases.phase_settings(Phase.HOLISTIC, None, cfg).compact_at == 300_000
    assert agent.phases.phase_settings(Phase.HOLISTIC, None, load_config(None)).compact_at == 400_000


def test_a_review_phase_hands_its_ceiling_to_the_backend(tmp_path):
    _write_config("context:\n  compact_at: 250000\n")
    runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.HOLISTIC)
    assert runner.invocation("p").compact_at == 250_000


def test_a_fix_pass_hands_its_ceiling_to_the_backend(tmp_path, monkeypatch):
    _write_config("context:\n  compact_at: 250000\n")
    calls = []
    monkeypatch.setattr(agent.backend, "invoke_fix", lambda inv: calls.append(inv) or 0)
    agent.invoke.run_fix(Phase.CI_FIX, "fix it", cwd=tmp_path,
                         session_log=str(tmp_path / "s.jsonl"), produced=None)
    assert calls[0].compact_at == 250_000


# ── The review budget ───────────────────────────────────────────────────────


def test_the_budget_plans_against_the_ceiling_not_the_window(tmp_path):
    wide = prompt_budget_bytes("claude-sonnet-5", cwd=tmp_path)
    _write_config("context:\n  compact_at: 200000\n")
    assert model_window_tokens("claude-sonnet-5", cwd=tmp_path) == 200_000
    assert prompt_budget_bytes("claude-sonnet-5", cwd=tmp_path) < wide


def test_a_model_narrower_than_the_ceiling_keeps_its_window(tmp_path, monkeypatch):
    monkeypatch.setitem(MODEL_CONTEXT_TOKENS, "narrow", 150_000)
    _write_config("context:\n  compact_at: 200000\n")
    assert model_window_tokens("narrow", cwd=tmp_path) == 150_000


def test_an_unset_model_floor_is_capped_too(tmp_path):
    _write_config("context:\n  compact_at: 120000\n")
    assert model_window_tokens(None, cwd=tmp_path) == min(UNSET_MODEL_FLOOR_TOKENS, 120_000)


def test_a_ceiling_does_not_hide_an_unknown_model(tmp_path):
    _write_config("context:\n  compact_at: 200000\n")
    with pytest.raises(UnknownModelWindow, match="no context window on record"):
        model_window_tokens("never-heard-of-it", cwd=tmp_path)


def test_a_ceiling_the_reserves_exhaust_names_the_key(tmp_path):
    _write_config("context:\n  compact_at: 80000\n")
    with pytest.raises(UnknownModelWindow, match="context.compact_at"):
        prompt_budget_bytes("claude-sonnet-5", cwd=tmp_path)


def test_the_catalogue_window_is_capped_as_the_table_s_is(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.pi_models, "catalogue_for", lambda _cwd: _LISTING)
    _write_config("context:\n  compact_at: 300000\n")
    # The narrowest provider's window (500K) is still wider than the ceiling.
    assert model_window_tokens("claude-opus-5-5", cwd=tmp_path) == 300_000
