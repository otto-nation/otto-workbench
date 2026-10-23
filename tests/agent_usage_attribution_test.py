"""Tests for what a usage-ledger row says about a call: where it went, how hard
it thought, how much context it carried, and whether it failed.

Not: token and cost parsing (`ai_usage_test`), the first-turn reading
(`agent_usage_first_turn_test`), the rest of the stats table (`usage_stats_test`).
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.backend_claude
import agent.backend_pi
import agent.usage
import agent.usage_stats


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent.usage, "_warned", False)
    return tmp_path / agent.usage.LEDGER_DIRNAME


def _records(ledger_dir):
    return [json.loads(line)
            for f in sorted(ledger_dir.glob("*.jsonl"))
            for line in f.read_text().splitlines()]


def _write_jsonl(path, *records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _claude_turn(input_tokens, cache_read=0, cache_write=0):
    return {"type": "assistant", "message": {"model": "claude-sonnet-5-5", "usage": {
        "input_tokens": input_tokens, "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }}}


def _pi_turn(input_tokens, cache_read=0):
    return {"type": "message_end", "message": {
        "role": "assistant", "model": "claude-sonnet-5-5",
        "usage": {"input": input_tokens, "cacheRead": cache_read, "cacheWrite": 0},
    }}


_RESULT = {"type": "result", "total_cost_usd": 0.5, "num_turns": 3,
           "usage": {"input_tokens": 10, "output_tokens": 5}}


# ── Peak context ──────────────────────────────────────────────────────────────


def test_a_claude_session_log_reports_its_largest_request(tmp_path):
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _claude_turn(1_000, cache_read=40_000),
                 _claude_turn(2_000, cache_read=150_000, cache_write=8_000),
                 _claude_turn(500, cache_read=60_000), _RESULT)
    assert agent.usage.parse_session_log(str(log)).peak_context_tokens == 160_000


def test_a_pi_session_log_reports_its_largest_request(tmp_path):
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _pi_turn(3_000, cache_read=20_000), _pi_turn(1_000, cache_read=90_000),
                 _RESULT)
    assert agent.usage.parse_session_log(str(log)).peak_context_tokens == 91_000


def test_an_errored_request_with_no_usage_is_not_the_peak(tmp_path):
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _claude_turn(0), _claude_turn(7_000), _RESULT)
    assert agent.usage.parse_session_log(str(log)).peak_context_tokens == 7_000


def test_a_log_with_only_a_result_has_no_peak(tmp_path):
    """The `--output-format json` envelope carries no per-request usage."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _RESULT)
    usage = agent.usage.parse_session_log(str(log))
    assert usage.peak_context_tokens is None
    assert usage.cost == pytest.approx(0.5)


def test_the_first_turn_reading_is_unchanged_by_the_peak():
    records = [_claude_turn(1_000), _claude_turn(9_000)]
    assert agent.usage.first_turn_usage(records).input_tokens == 1_000
    assert agent.usage.peak_context_tokens(records) == 9_000


# ── The ledger row ────────────────────────────────────────────────────────────


def test_a_row_carries_provider_thinking_and_peak(ledger):
    agent.usage.record(
        script="s", entry_point="agent", backend="pi", model="m", exit_code=0,
        usage=agent.usage.SessionUsage(cost=1.0, peak_context_tokens=120_000),
        provider="anthropic", thinking="low",
    )
    [rec] = _records(ledger)
    assert (rec["provider"], rec["thinking"], rec["peak_context_tokens"]) == (
        "anthropic", "low", 120_000)


def test_a_row_leaves_out_what_was_not_sent(ledger):
    agent.usage.record(
        script="s", entry_point="agent", backend="claude", model="m", exit_code=0,
        usage=agent.usage.SessionUsage(cost=1.0),
    )
    [rec] = _records(ledger)
    assert {"provider", "thinking", "peak_context_tokens"}.isdisjoint(rec)


def test_claude_reports_neither_provider_nor_thinking():
    """The Claude CLI is given neither; recording the requested level would claim it ran."""
    got = agent.backend_claude.attribution(provider="anthropic", thinking="high")
    assert got == agent.backend.Attribution()


def test_pi_reports_its_effective_provider_and_the_level(monkeypatch):
    monkeypatch.setattr(agent.backend_pi, "effective_provider",
                        lambda explicit: explicit or "settings-default")
    assert agent.backend_pi.attribution(provider=None, thinking="low") == \
        agent.backend.Attribution(provider="settings-default", thinking="low")
    assert agent.backend_pi.attribution(provider="openai", thinking="") == \
        agent.backend.Attribution(provider="openai", thinking=None)


def test_an_agent_call_records_what_its_backend_sent(ledger, tmp_path, monkeypatch):
    """End to end through invoke_agent: the row names what the backend reported."""
    def attribution(*, provider, thinking):
        return agent.backend.Attribution(provider=f"sent-{provider}", thinking=thinking)

    backend = types.SimpleNamespace(invoke_agent=lambda inv: 0, attribution=attribution)
    monkeypatch.setattr(agent.backend, "_get_module", lambda: backend)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _claude_turn(4_000), _RESULT)

    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log),
        provider="openai", thinking="high", phase="scout",
    ))
    [rec] = _records(ledger)
    assert (rec["provider"], rec["thinking"], rec["phase"], rec["peak_context_tokens"]) == (
        "sent-openai", "high", "scout", 4_000)


def test_a_backend_that_cannot_attribute_still_records_the_call(ledger, tmp_path, monkeypatch):
    def broken(**_kwargs):
        raise RuntimeError("cannot say")

    backend = types.SimpleNamespace(invoke_agent=lambda inv: 0, attribution=broken)
    monkeypatch.setattr(agent.backend, "_get_module", lambda: backend)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _RESULT)

    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log), provider="openai",
    ))
    [rec] = _records(ledger)
    assert rec["cost"] == pytest.approx(0.5)
    assert "provider" not in rec


# ── Stats ─────────────────────────────────────────────────────────────────────


def _row(**fields):
    base = {"ts": "2026-10-23T00:00:00Z", "script": "review", "cost": 0.0, "exit_code": 0}
    return {**base, **fields}


def test_stats_count_failed_calls_so_crashes_do_not_read_as_free():
    rows = agent.usage_stats.aggregate_usage(
        [_row(exit_code=1), _row(exit_code=1), _row(cost=2.0)], "script")
    [row] = rows
    assert (row.calls, row.failed, row.cost) == (3, 2, pytest.approx(2.0))


def test_stats_report_the_largest_context_in_a_group():
    rows = agent.usage_stats.aggregate_usage(
        [_row(peak_context_tokens=90_000), _row(peak_context_tokens=210_000), _row()],
        "script")
    assert rows[0].peak_context_tokens == 210_000


def test_stats_have_no_peak_for_records_that_never_measured_one():
    [row] = agent.usage_stats.aggregate_usage([_row()], "script")
    assert row.peak_context_tokens is None


def test_stats_group_by_provider():
    rows = agent.usage_stats.aggregate_usage(
        [_row(provider="anthropic", cost=1.0), _row(provider="openai", cost=3.0), _row()],
        "provider")
    assert [(r.group, r.cost) for r in rows] == [
        ("openai", pytest.approx(3.0)), ("anthropic", pytest.approx(1.0)), ("—", 0.0)]


def test_the_table_shows_failed_and_peak_columns_and_totals():
    table = agent.usage_stats.format_stats_table(agent.usage_stats.aggregate_usage(
        [_row(exit_code=1, peak_context_tokens=50_000),
         _row(script="describe", peak_context_tokens=150_000)], "script"), "script")
    header, *body = table.splitlines()
    assert "FAILED" in header and "PEAK CTX" in header
    total = body[-1].split()
    assert total[0] == "TOTAL"
    assert total[2] == "1"
    assert "150.0k" in body[-1]
