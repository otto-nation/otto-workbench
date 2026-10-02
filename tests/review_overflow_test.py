"""API `prompt is too long` parsing and in-phase recovery."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from agent.diagnosis import Diagnosis, DiagnosisKind
from review.budget import COMPLETION_RESERVE_TOKENS
from review.overflow import (
    MAX_OVERFLOW_RECOVERY, overflow_ladder_bytes, parse_prompt_overflow,
    recover_overflow_prompt, run_with_overflow_recovery,
)


class TestParsePromptOverflow:
    def test_claude_cli_wording(self):
        assert parse_prompt_overflow(
            "prompt is too long: 200000 tokens > 168000 maximum"
        ) == (200000, 168000)

    def test_case_insensitive_and_tolerant(self):
        assert parse_prompt_overflow(
            "Prompt is too long — 10 token > 8 maximum"
        ) == (10, 8)

    def test_unparseable_is_none(self):
        assert parse_prompt_overflow("permission denied") is None
        assert parse_prompt_overflow("") is None
        assert parse_prompt_overflow("prompt is too long but no numbers") is None


class TestOverflowLadder:
    def test_scales_rendered_bytes_by_the_api_ratio(self):
        rendered = 100_000
        sent, maximum = 200_000, 100_000
        scaled = overflow_ladder_bytes(sent, maximum, rendered)
        usable = maximum - COMPLETION_RESERVE_TOKENS
        # The fraction that fit, less a margin so the next render is not on the line.
        fit_fraction = usable / sent
        assert scaled < rendered * fit_fraction
        assert scaled == pytest.approx(rendered * fit_fraction, rel=0.1)

    def test_is_proportional_to_the_rendered_bytes(self):
        small = overflow_ladder_bytes(200_000, 100_000, 50_000)
        large = overflow_ladder_bytes(200_000, 100_000, 100_000)
        assert large == pytest.approx(2 * small, abs=2)

    def test_a_window_inside_the_completion_reserve_leaves_one_byte(self):
        assert overflow_ladder_bytes(
            200_000, COMPLETION_RESERVE_TOKENS, 100_000,
        ) == 1


class TestRecoverOverflowPrompt:
    def test_rebuilds_on_a_parseable_rejection(self):
        diagnosis = Diagnosis(
            DiagnosisKind.AGENT_ERROR,
            detail="prompt is too long: 100 tokens > 80 maximum",
        )
        rebuilt = recover_overflow_prompt(
            diagnosis, "x" * 1000, rebuild=lambda n: f"small-{n}", attempt=0,
        )
        assert rebuilt is not None
        assert rebuilt.startswith("small-")

    def test_a_rebuild_no_smaller_than_the_rejected_prompt_is_not_resent(self):
        diagnosis = Diagnosis(
            DiagnosisKind.AGENT_ERROR,
            detail="prompt is too long: 100 tokens > 80 maximum",
        )
        assert recover_overflow_prompt(
            diagnosis, "x" * 100, rebuild=lambda n: "y" * 100, attempt=0,
        ) is None
        assert recover_overflow_prompt(
            diagnosis, "x" * 100, rebuild=lambda n: "y" * 150, attempt=0,
        ) is None

    def test_unparseable_is_not_retried(self):
        diagnosis = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="boom")
        assert recover_overflow_prompt(
            diagnosis, "prompt", rebuild=lambda n: "x", attempt=0,
        ) is None

    def test_local_prompt_too_large_is_not_retried(self):
        diagnosis = Diagnosis(DiagnosisKind.PROMPT_TOO_LARGE, detail="512KB")
        assert recover_overflow_prompt(
            diagnosis, "prompt", rebuild=lambda n: "x", attempt=0,
        ) is None

    def test_caps_recovery_attempts(self):
        diagnosis = Diagnosis(
            DiagnosisKind.AGENT_ERROR,
            detail="prompt is too long: 100 tokens > 80 maximum",
        )
        assert recover_overflow_prompt(
            diagnosis, "prompt", rebuild=lambda n: "x",
            attempt=MAX_OVERFLOW_RECOVERY,
        ) is None


class TestRunWithOverflowRecovery:
    def test_retries_with_a_smaller_prompt_then_succeeds(self):
        prompts = []
        output = {"ready": False}

        def invoke(text):
            prompts.append(text)
            if len(prompts) >= 2:
                output["ready"] = True

        def after(_text):
            if output["ready"]:
                return None
            return Diagnosis(
                DiagnosisKind.AGENT_ERROR,
                detail="prompt is too long: 200 tokens > 100 maximum",
            )

        prompt, diagnosis = run_with_overflow_recovery(
            "B" * 100,
            invoke=invoke,
            after=after,
            has_output=lambda: output["ready"],
            rebuild=lambda n: f"SMALL-{n}",
        )
        assert diagnosis is None
        assert len(prompts) == 2
        assert prompts[0] == "B" * 100
        assert prompts[1].startswith("SMALL-")
        assert prompt == prompts[1]

    def test_unparseable_is_not_retried(self):
        prompts = []

        def invoke(text):
            prompts.append(text)

        prompt, diagnosis = run_with_overflow_recovery(
            "BIG",
            invoke=invoke,
            after=lambda _t: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="nope"),
            has_output=lambda: False,
            rebuild=lambda n: "SMALL",
        )
        assert prompts == ["BIG"]
        assert diagnosis.detail == "nope"
        assert prompt == "BIG"

    def test_stops_after_the_attempt_cap_when_every_rebuild_still_overflows(self):
        prompts = []
        sizes = iter(range(900, 0, -100))

        def rebuild(_target):
            return "r" * next(sizes)

        prompt, diagnosis = run_with_overflow_recovery(
            "B" * 1000,
            invoke=prompts.append,
            after=lambda _t: Diagnosis(
                DiagnosisKind.AGENT_ERROR,
                detail="prompt is too long: 200 tokens > 100 maximum",
            ),
            has_output=lambda: False,
            rebuild=rebuild,
        )
        assert len(prompts) == 1 + MAX_OVERFLOW_RECOVERY
        assert diagnosis is not None
        assert prompt == prompts[-1]

    def test_an_artifact_on_disk_ends_recovery_despite_a_diagnosis(self):
        prompts = []
        rebuilds = []

        prompt, diagnosis = run_with_overflow_recovery(
            "BIG",
            invoke=prompts.append,
            after=lambda _t: Diagnosis(
                DiagnosisKind.AGENT_ERROR,
                detail="prompt is too long: 200 tokens > 100 maximum",
            ),
            has_output=lambda: True,
            rebuild=lambda n: rebuilds.append(n) or "S",
        )
        assert prompts == ["BIG"]
        assert rebuilds == []
        assert prompt == "BIG"
        assert diagnosis is not None
