"""Plan-render-verify-replan: the loop that checks a prompt before sending it."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.phases import Phase
from review.prompt import PromptTooLarge
from review.prompt_fit import (
    MAX_PROMPT_RENDERS, PromptVerification, ratchet_target, verify_prompt,
)
from review.registry import build_prompt

from conftest import TEST_MODEL
from review_prompt_support import _make_job, _make_preflight


def _job(tmp_path):
    job = _make_job(_make_preflight())
    job.review_file = str(tmp_path / "review.md")
    return job


def _stats(tmp_path):
    return json.loads((tmp_path / "prompt-stats.json").read_text())


class TestVerifyPrompt:
    def test_bytes_over_budget_is_not_ok(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "0")
        result = verify_prompt(
            "x" * 200, model=TEST_MODEL, phase=Phase.SCOUT,
            backend=None, budget_bytes=50,
        )
        assert not result.ok
        assert result.byte_overshoot > 0
        assert result.token_verified is False
        assert result.reason == "disabled"

    def test_a_missing_count_is_unverified_not_a_fail(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=None):
            result = verify_prompt(
                "hello", model=TEST_MODEL, phase=Phase.SCOUT,
                backend=None, budget_bytes=10_000,
            )
        assert result.ok
        assert result.token_verified is False
        assert result.reason == "unavailable"
        assert result.tokens is None

    def test_token_overshoot_fails_when_counted(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=10_000_000):
            result = verify_prompt(
                "hello", model=TEST_MODEL, phase=Phase.SCOUT,
                backend=None, budget_bytes=10_000,
            )
        assert result.token_verified is True
        assert result.token_overshoot > 0
        assert not result.ok


class TestRatchetNeverGrows:
    def test_shrinks_by_byte_overshoot_with_margin(self):
        verification = PromptVerification(
            prompt_bytes=1_000, budget_bytes=800, tokens=None,
            token_verified=False, reason="unavailable", ok=False,
            byte_overshoot=200, token_overshoot=0,
        )
        nxt = ratchet_target(900, verification)
        assert nxt < 900
        assert nxt == 900 - int(200 * 1.05)

    def test_token_overshoot_converts_at_measured_density(self):
        verification = PromptVerification(
            prompt_bytes=1_000, budget_bytes=10_000, tokens=100,
            token_verified=True, reason="", ok=False,
            byte_overshoot=0, token_overshoot=50,
        )
        nxt = ratchet_target(900, verification)
        # 50 tokens * (1000/100) bytes/token = 500 bytes, plus 5% margin
        assert nxt == 900 - int(500 * 1.05)

    def test_never_returns_a_larger_target(self):
        verification = PromptVerification(
            prompt_bytes=10, budget_bytes=10, tokens=None,
            token_verified=False, reason="unavailable", ok=False,
            byte_overshoot=0, token_overshoot=0,
        )
        nxt = ratchet_target(100, verification)
        assert nxt < 100


class TestTheFitLoop:
    def test_first_render_that_fits_counts_once(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=100) as counter:
            build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        counter.assert_called_once()
        record = _stats(tmp_path)[-1]
        assert record["token_verified"] is True
        assert record["prompt_tokens"] == 100
        assert record["renders"] == 1

    def test_byte_overshoot_replans_then_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        monkeypatch.setattr(
            "review.prompt_fit.prompt_budget_bytes", lambda *a, **k: 80,
        )
        monkeypatch.setattr(
            "review.prompt_fit.ladder_target_bytes", lambda *a, **k: 60,
        )
        renders = []

        real_render = __import__("agent.templates", fromlist=["render"]).render

        def counting_render(*args, **kwargs):
            renders.append(1)
            return real_render(*args, **kwargs)

        with patch("review.prompt.count_tokens", return_value=10), \
             patch("agent.templates.render", side_effect=counting_render):
            with pytest.raises(PromptTooLarge):
                build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        assert 2 <= len(renders) <= MAX_PROMPT_RENDERS
        ladders = [row["ladder_bytes"] for row in _stats(tmp_path)]
        assert all(later < earlier for earlier, later in zip(ladders, ladders[1:]))

    def test_three_renders_then_prompt_too_large(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        renders = []
        real_render = __import__("agent.templates", fromlist=["render"]).render

        def counting_render(*args, **kwargs):
            renders.append(1)
            return real_render(*args, **kwargs)

        with patch("review.prompt.count_tokens", return_value=10_000_000), \
             patch("agent.templates.render", side_effect=counting_render):
            with pytest.raises(PromptTooLarge):
                build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        assert len(renders) == MAX_PROMPT_RENDERS

    def test_token_overshoot_replans(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        # First count overflows; later counts fit so the loop can stop.
        counts = iter([10_000_000, 100, 100])
        with patch("review.prompt.count_tokens", side_effect=lambda *a, **k: next(counts)):
            prompt = build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        assert prompt
        records = _stats(tmp_path)
        assert len(records) >= 2
        assert records[0]["token_verified"] is True
        assert records[-1]["token_verified"] is True
        assert records[-1]["ladder_bytes"] < records[0]["ladder_bytes"]

    def test_unverified_is_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=None):
            build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        record = _stats(tmp_path)[-1]
        assert record["token_verified"] is False
        assert record["token_unverified_reason"] == "unavailable"
        assert "prompt_tokens" not in record

    def test_opt_out_is_unverified_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "0")
        with patch("review.prompt.count_tokens") as counter:
            build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        counter.assert_not_called()
        record = _stats(tmp_path)[-1]
        assert record["token_verified"] is False
        assert record["token_unverified_reason"] == "disabled"

    def test_retry_hint_is_part_of_the_counted_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        seen = []

        def capture(prompt, model, **kwargs):
            seen.append(prompt)
            return 10

        with patch("review.prompt.count_tokens", side_effect=capture):
            built = build_prompt(
                Phase.SCOUT, _job(tmp_path), max_turns=10,
                prefix="RETRY HINT\n",
            )
        assert seen
        assert seen[0].startswith("RETRY HINT\n")
        assert built.startswith("RETRY HINT\n")
