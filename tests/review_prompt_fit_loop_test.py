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
from review.prompt import PromptTooLarge, unverified_reason
from review.prompt_fit import (
    MAX_PROMPT_RENDERS, _RATCHET_MARGIN, PromptVerification, ratchet_target,
    verify_prompt,
)
from review.registry import build_prompt

from conftest import TEST_MODEL
from review_prompt_support import _make_job, _make_preflight

import agent.templates


def _counting_render(renders):
    """A `render` that counts calls. Patched on the module, so the code under
    test must look `render` up through `agent.templates` for it to count."""
    real_render = agent.templates.render

    def counting_render(*args, **kwargs):
        renders.append(1)
        return real_render(*args, **kwargs)

    return counting_render


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


class TestUnverifiedReason:
    def test_one_owner_names_each_reason(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "0")
        assert unverified_reason(Phase.SCOUT) == "disabled"
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        assert unverified_reason(None) == "no_phase"
        assert unverified_reason(Phase.SCOUT) == "unavailable"

    def test_the_direct_log_path_uses_the_same_vocabulary(self, tmp_path, monkeypatch):
        from review.prompt import log_prompt_size

        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        log_prompt_size(
            "scout.md", "text", {}, _job(tmp_path),
            budget_bytes=1_000, model=TEST_MODEL, phase=None,
        )
        assert _stats(tmp_path)[-1]["token_unverified_reason"] == "no_phase"


class TestRatchetNeverGrows:
    def test_shrinks_by_byte_overshoot_with_margin(self):
        verification = PromptVerification(
            prompt_bytes=1_000, budget_bytes=800, tokens=None,
            reason="unavailable", ok=False,
            byte_overshoot=200, token_overshoot=0,
        )
        nxt = ratchet_target(900, verification)
        assert nxt < 900
        assert nxt == 900 - int(200 * (1 + _RATCHET_MARGIN))

    def test_token_overshoot_converts_at_measured_density(self):
        verification = PromptVerification(
            prompt_bytes=1_000, budget_bytes=10_000, tokens=100,
            reason="", ok=False,
            byte_overshoot=0, token_overshoot=50,
        )
        nxt = ratchet_target(900, verification)
        # 50 tokens * (1000/100) bytes/token = 500 bytes, plus 5% margin
        assert nxt == 900 - int(500 * (1 + _RATCHET_MARGIN))

    def test_never_returns_a_larger_target(self):
        verification = PromptVerification(
            prompt_bytes=10, budget_bytes=10, tokens=None,
            reason="unavailable", ok=False,
            byte_overshoot=0, token_overshoot=0,
        )
        nxt = ratchet_target(100, verification)
        # No overshoot to measure still steps down by the minimum of one byte.
        assert nxt == 99

    def test_a_tiny_overshoot_still_steps_by_at_least_one(self):
        verification = PromptVerification(
            prompt_bytes=11, budget_bytes=10, tokens=None,
            reason="unavailable", ok=False,
            byte_overshoot=1, token_overshoot=0,
        )
        assert ratchet_target(100, verification) == 100 - max(
            1, int(1 * (1 + _RATCHET_MARGIN)),
        )


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

    def test_byte_overshoot_at_the_diff_floor_refuses_without_replanning(
        self, tmp_path, monkeypatch,
    ):
        """The floor means every lever is spent; a second render is the same prompt."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        monkeypatch.setattr(
            "review.prompt_fit.prompt_budget_bytes", lambda *a, **k: 80,
        )
        monkeypatch.setattr(
            "review.prompt_fit.ladder_target_bytes", lambda *a, **k: 60,
        )
        renders = []

        with patch("review.prompt.count_tokens", return_value=10), \
             patch("agent.templates.render", side_effect=_counting_render(renders)):
            with pytest.raises(PromptTooLarge):
                build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        assert renders, "render was never looked up through agent.templates"
        assert len(renders) == 1
        assert len(_stats(tmp_path)) == 1

    def test_three_renders_then_prompt_too_large(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        renders = []

        with patch("review.prompt.count_tokens", return_value=10_000_000), \
             patch("agent.templates.render", side_effect=_counting_render(renders)):
            with pytest.raises(PromptTooLarge):
                build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        assert renders, "render was never looked up through agent.templates"
        assert len(renders) == MAX_PROMPT_RENDERS

    def test_a_token_only_overshoot_says_tokens_not_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=10_000_000):
            with pytest.raises(PromptTooLarge) as exc:
                build_prompt(Phase.SCOUT, _job(tmp_path), max_turns=10)
        assert exc.value.token_overshoot > 0
        assert exc.value.prompt_bytes <= exc.value.budget_bytes
        assert "tokens over" in str(exc.value)

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


class TestTokenVerifiedIsDerived:
    def test_it_follows_the_count_and_is_not_a_constructor_field(self):
        base = dict(
            prompt_bytes=1, budget_bytes=1, reason="", ok=True,
            byte_overshoot=0, token_overshoot=0,
        )
        assert PromptVerification(tokens=0, **base).token_verified is True
        assert PromptVerification(tokens=None, **base).token_verified is False
        with pytest.raises(TypeError):
            PromptVerification(tokens=None, token_verified=True, **base)
