"""Tests for ai_usage — session log parsing, usage normalization, aggregation."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from ai_usage import SessionUsage, merge, normalize_usage, parse_session_log


def _write_result(path, **rec):
    rec.setdefault("type", "result")
    Path(path).write_text(json.dumps(rec) + "\n")


# ── normalize_usage ───────────────────────────────────────────────────────────


def test_normalize_usage_camel_case():
    got = normalize_usage({
        "inputTokens": 8, "outputTokens": 2386,
        "cacheReadInputTokens": 84671, "cacheCreationInputTokens": 16065,
    })
    assert got == {
        "input_tokens": 8, "output_tokens": 2386,
        "cache_read_tokens": 84671, "cache_write_tokens": 16065,
    }


def test_normalize_usage_snake_case():
    got = normalize_usage({
        "input_tokens": 8, "output_tokens": 2386,
        "cache_read_input_tokens": 84671, "cache_creation_input_tokens": 16065,
    })
    assert got == {
        "input_tokens": 8, "output_tokens": 2386,
        "cache_read_tokens": 84671, "cache_write_tokens": 16065,
    }


def test_normalize_usage_missing_keys_default_to_zero():
    assert normalize_usage({}) == {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
    }


def test_normalize_usage_absent_input_key_still_reads_cache():
    """A model with no fresh input must not lose its cache fields.

    Picking a key spelling per-dict off a single probe key drops the rest when
    that probe is absent; each field resolves independently instead.
    """
    got = normalize_usage({"outputTokens": 5, "cacheReadInputTokens": 900})
    assert got["cache_read_tokens"] == 900
    assert got["input_tokens"] == 0


def test_normalize_usage_null_values_treated_as_zero():
    assert normalize_usage({"inputTokens": None})["input_tokens"] == 0


# ── parse_session_log ─────────────────────────────────────────────────────────


def test_parse_session_log_prefers_camel_case_model_usage(tmp_path):
    log = tmp_path / "session.jsonl"
    _write_result(
        log,
        total_cost_usd=2.0,
        duration_ms=30000,
        usage={"input_tokens": 100, "output_tokens": 200,
               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        modelUsage={
            "claude-sonnet-4-6": {
                "inputTokens": 500, "outputTokens": 300,
                "cacheReadInputTokens": 1000, "cacheCreationInputTokens": 200,
                "costUSD": 1.75,
            },
            "claude-haiku-4-5@20251001": {
                "inputTokens": 100, "outputTokens": 50,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "costUSD": 0.25,
            },
        },
    )
    usage = parse_session_log(str(log))
    assert usage.input_tokens == 600
    assert usage.output_tokens == 350
    assert usage.cache_read_tokens == 1000
    assert usage.cache_write_tokens == 200
    assert usage.cost == pytest.approx(2.0)
    assert usage.cost_by_model == pytest.approx({
        "claude-sonnet-4-6": 1.75, "claude-haiku-4-5@20251001": 0.25,
    })


def test_parse_session_log_falls_back_to_snake_case_usage(tmp_path):
    """Backends without modelUsage (Pi) emit a snake_case top-level usage block."""
    log = tmp_path / "session.jsonl"
    _write_result(
        log,
        total_cost_usd=1.0,
        duration_ms=60000,
        usage={"input_tokens": 100, "output_tokens": 200,
               "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 300},
    )
    usage = parse_session_log(str(log))
    assert usage.input_tokens == 100
    assert usage.cache_read_tokens == 5000
    assert usage.cache_write_tokens == 300
    assert usage.cost_by_model == {}


def test_parse_session_log_sums_multiple_result_records(tmp_path):
    log = tmp_path / "session.jsonl"
    log.write_text("".join(
        json.dumps({
            "type": "result", "total_cost_usd": c, "duration_ms": 1000,
            "usage": {"input_tokens": i, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        }) + "\n"
        for c, i in ((1.0, 100), (2.0, 300))
    ))
    usage = parse_session_log(str(log))
    assert usage.cost == pytest.approx(3.0)
    assert usage.input_tokens == 400
    assert usage.duration_ms == 2000


def test_parse_session_log_skips_malformed_lines(tmp_path):
    log = tmp_path / "session.jsonl"
    log.write_text(
        '{"type":"result" TRUNCATED\n'
        + json.dumps({"type": "result", "total_cost_usd": 1.0,
                      "usage": {"input_tokens": 5}}) + "\n"
    )
    assert parse_session_log(str(log)).input_tokens == 5


def test_parse_session_log_missing_file_returns_empty(tmp_path):
    assert parse_session_log(str(tmp_path / "nope.jsonl")) == SessionUsage()


# ── derived properties ────────────────────────────────────────────────────────


def test_billed_input_excludes_output():
    u = SessionUsage(input_tokens=100, output_tokens=999,
                     cache_read_tokens=5000, cache_write_tokens=300)
    assert u.billed_input == 5400


def test_cache_read_ratio():
    u = SessionUsage(input_tokens=100, cache_read_tokens=800, cache_write_tokens=100)
    assert u.cache_read_ratio == pytest.approx(0.8)


def test_cache_read_ratio_zero_when_no_input():
    assert SessionUsage().cache_read_ratio == 0.0


# ── merge ─────────────────────────────────────────────────────────────────────


def test_merge_sums_fields_and_per_model_cost():
    got = merge([
        SessionUsage(cost=1.0, input_tokens=10, cache_read_tokens=100,
                     cost_by_model={"sonnet": 1.0}),
        SessionUsage(cost=2.0, input_tokens=20, cache_read_tokens=200,
                     cost_by_model={"sonnet": 1.5, "haiku": 0.5}),
    ])
    assert got.cost == pytest.approx(3.0)
    assert got.input_tokens == 30
    assert got.cache_read_tokens == 300
    assert got.cost_by_model == pytest.approx({"sonnet": 2.5, "haiku": 0.5})


def test_merge_empty_returns_zero_usage():
    assert merge([]) == SessionUsage()
