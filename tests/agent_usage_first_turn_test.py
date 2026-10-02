"""First-turn billed input and served model from both session-log formats."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from agent.usage import first_turn_usage
from core.phases import Phase
from review.prompt_fit import record_prompt_overhead
from review_prompt_support import _make_job, _make_preflight


def test_claude_stream_json_first_assistant_usage():
    records = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
        {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-5",
                "usage": {
                    "input_tokens": 1000,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 30,
                },
            },
        },
        {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 5000,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.served_model == "claude-sonnet-5"
    assert first.input_tokens == 1050


def test_pi_message_end_assistant_usage():
    records = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "usage": {"input": 800, "cacheRead": 10, "cacheWrite": 40},
            },
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "usage": {"input": 9000, "cacheRead": 0, "cacheWrite": 0},
            },
        },
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.served_model == "claude-sonnet-5"
    assert first.input_tokens == 850


def test_empty_log_is_none():
    assert first_turn_usage([]) is None
    assert first_turn_usage([{"type": "result"}]) is None


def test_claude_assistant_without_usage_is_skipped():
    records = [
        {"type": "assistant", "message": {"model": "claude-sonnet-5"}},
        {
            "type": "assistant",
            "message": {"usage": {"input_tokens": 700}},
        },
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.input_tokens == 700


def test_claude_synthetic_error_message_with_zero_usage_is_skipped():
    """An API error surfaces as a `<synthetic>` assistant message of zeros."""
    zeros = {
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    records = [
        {"type": "assistant", "message": {"model": "<synthetic>", "usage": zeros}},
        {
            "type": "assistant",
            "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 900}},
        },
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.served_model == "claude-sonnet-5"
    assert first.input_tokens == 900

    assert first_turn_usage(records[:1]) is None


def test_claude_falls_back_to_the_init_model_when_the_message_names_none():
    records = [
        {"type": "system", "subtype": "init", "model": "claude-init-model"},
        {"type": "assistant", "message": {"usage": {"input_tokens": 10}}},
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.served_model == "claude-init-model"


def test_pi_non_assistant_message_end_is_skipped():
    records = [
        {
            "type": "message_end",
            "message": {"role": "user", "usage": {"input": 5_000}},
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant", "model": "m", "usage": {"input": 300},
            },
        },
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.input_tokens == 300


def test_pi_errored_turn_without_usage_is_skipped():
    """A rejected turn carries no usage; reporting 0 makes overhead very negative."""
    records = [
        {"type": "message_end", "message": {"role": "assistant", "model": "m"}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant", "model": "m", "usage": {"input": 400},
            },
        },
    ]
    first = first_turn_usage(records)
    assert first is not None
    assert first.input_tokens == 400


def test_pi_with_only_usageless_turns_is_none():
    records = [
        {"type": "message_end", "message": {"role": "assistant", "usage": {}}},
    ]
    assert first_turn_usage(records) is None


def _stats_job(tmp_path, rows):
    job = _make_job(_make_preflight())
    job.review_file = str(tmp_path / "review.md")
    stats = tmp_path / "prompt-stats.json"
    stats.write_text(json.dumps(rows))
    return job, stats


def _claude_log(path, input_tokens):
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": input_tokens},
        },
    }) + "\n")


def test_record_prompt_overhead_writes_served_model_and_warns(tmp_path, capsys):
    job, stats = _stats_job(
        tmp_path, [{"template": "scout.md", "prompt_tokens": 100}],
    )
    log = tmp_path / "session.jsonl"
    log.write_text(json.dumps({
        "type": "system", "subtype": "init", "model": "claude-sonnet-5",
    }) + "\n" + json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": 80_000,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }) + "\n")
    job.session_log = str(log)
    record_prompt_overhead(job, str(log), Phase.SCOUT)
    row = json.loads(stats.read_text())[-1]
    assert row["served_model"] == "claude-sonnet-5"
    assert row["overhead_tokens"] == 79_900
    assert "exceeds the backend reserve" in capsys.readouterr().err


class TestRecordPromptOverheadLeavesTheRecordAlone:
    def test_a_session_with_no_usage(self, tmp_path):
        rows = [{"template": "scout.md", "prompt_tokens": 100}]
        job, stats = _stats_job(tmp_path, rows)
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result"}) + "\n")
        record_prompt_overhead(job, str(log), Phase.SCOUT)
        assert json.loads(stats.read_text()) == rows

    def test_a_prompt_that_was_never_counted(self, tmp_path):
        job, stats = _stats_job(tmp_path, [{"template": "scout.md"}])
        log = tmp_path / "session.jsonl"
        _claude_log(log, 5_000)
        record_prompt_overhead(job, str(log), Phase.SCOUT)
        row = json.loads(stats.read_text())[-1]
        assert "overhead_tokens" not in row

    def test_a_missing_log(self, tmp_path):
        rows = [{"template": "scout.md", "prompt_tokens": 100}]
        job, stats = _stats_job(tmp_path, rows)
        record_prompt_overhead(job, str(tmp_path / "absent.jsonl"), Phase.SCOUT)
        record_prompt_overhead(job, "", Phase.SCOUT)
        assert json.loads(stats.read_text()) == rows
