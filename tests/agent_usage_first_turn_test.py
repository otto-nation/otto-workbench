"""First-turn billed input and served model from both session-log formats."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from agent.usage import first_turn_usage


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


def test_record_prompt_overhead_writes_served_model_and_warns(tmp_path, monkeypatch, capsys):
    import json
    from core.phases import Phase
    from review.prompt_fit import record_prompt_overhead
    from review_prompt_support import _make_job, _make_preflight

    job = _make_job(_make_preflight())
    job.review_file = str(tmp_path / "review.md")
    stats = tmp_path / "prompt-stats.json"
    stats.write_text(json.dumps([{
        "template": "scout.md",
        "prompt_tokens": 100,
    }]))
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
