"""Tests for ai_usage — session log parsing, usage normalization, aggregation."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import ai_usage
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


# ── ledger ────────────────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ai_usage, "_warned", False)
    return tmp_path / ai_usage.LEDGER_DIRNAME


def _records(ledger_dir):
    return [
        json.loads(line)
        for f in sorted(ledger_dir.glob("*.jsonl"))
        for line in f.read_text().splitlines()
    ]


def test_record_appends_line(ledger):
    ai_usage.record(
        script="pr-rebase", entry_point="prompt", backend="claude",
        model="claude-sonnet-4-6", usage=SessionUsage(cost=0.42, input_tokens=1200),
        exit_code=0,
    )
    recs = _records(ledger)
    assert len(recs) == 1
    assert recs[0]["script"] == "pr-rebase"
    assert recs[0]["entry_point"] == "prompt"
    assert recs[0]["cost"] == pytest.approx(0.42)
    assert recs[0]["input_tokens"] == 1200
    assert recs[0]["exit_code"] == 0
    assert recs[0]["ts"].endswith("Z")


def test_record_appends_rather_than_truncates(ledger):
    for i in range(3):
        ai_usage.record(
            script="ci-check", entry_point="fix", backend="claude", model=None,
            usage=SessionUsage(input_tokens=i), exit_code=0,
        )
    assert [r["input_tokens"] for r in _records(ledger)] == [0, 1, 2]


def test_record_writes_monthly_file(ledger):
    ai_usage.record(
        script="s", entry_point="prompt", backend="claude", model=None,
        usage=SessionUsage(), exit_code=0,
    )
    names = [p.name for p in ledger.glob("*.jsonl")]
    assert len(names) == 1
    assert re.fullmatch(r"\d{4}-\d{2}\.jsonl", names[0]), names[0]


def test_record_creates_ledger_dir(ledger):
    assert not ledger.exists()
    ai_usage.record(
        script="s", entry_point="prompt", backend="claude", model=None,
        usage=SessionUsage(), exit_code=0,
    )
    assert ledger.is_dir()


def test_record_includes_optional_context(ledger):
    ai_usage.record(
        script="review-threads", entry_point="fix", backend="claude", model=None,
        usage=SessionUsage(), exit_code=1, task="comment-triage",
        repo="otto-workbench", pr="596",
    )
    rec = _records(ledger)[0]
    assert rec["task"] == "comment-triage"
    assert rec["repo"] == "otto-workbench"
    assert rec["pr"] == "596"
    assert rec["exit_code"] == 1


def test_record_omits_absent_optional_context(ledger):
    ai_usage.record(
        script="s", entry_point="prompt", backend="claude", model=None,
        usage=SessionUsage(), exit_code=0,
    )
    rec = _records(ledger)[0]
    assert "task" not in rec
    assert "repo" not in rec
    assert "pr" not in rec


def test_record_carries_per_model_cost(ledger):
    ai_usage.record(
        script="s", entry_point="agent", backend="claude", model=None,
        usage=SessionUsage(cost_by_model={"sonnet": 1.5}), exit_code=0,
    )
    assert _records(ledger)[0]["cost_by_model"] == {"sonnet": 1.5}


def test_record_never_raises_when_dir_unwritable(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(blocked))
    monkeypatch.setattr(ai_usage, "_warned", False)
    ai_usage.record(
        script="s", entry_point="prompt", backend="claude", model=None,
        usage=SessionUsage(), exit_code=0,
    )
    assert "usage ledger" in capsys.readouterr().err


def test_record_warns_only_once_per_process(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(blocked))
    monkeypatch.setattr(ai_usage, "_warned", False)
    for _ in range(3):
        ai_usage.record(
            script="s", entry_point="prompt", backend="claude", model=None,
            usage=SessionUsage(), exit_code=0,
        )
    assert capsys.readouterr().err.count("usage ledger") == 1


def test_record_line_is_newline_terminated_and_single_line(ledger):
    ai_usage.record(
        script="s", entry_point="prompt", backend="claude", model=None,
        usage=SessionUsage(), exit_code=0,
    )
    content = next(ledger.glob("*.jsonl")).read_text()
    assert content.endswith("\n")
    assert content.count("\n") == 1


def test_read_ledger_returns_records_across_months(ledger):
    ledger.mkdir(parents=True)
    (ledger / "2026-07.jsonl").write_text(json.dumps({"script": "a"}) + "\n")
    (ledger / "2026-08.jsonl").write_text(json.dumps({"script": "b"}) + "\n")
    assert [r["script"] for r in ai_usage.read_ledger()] == ["a", "b"]


def test_read_ledger_missing_dir_returns_empty(ledger):
    assert ai_usage.read_ledger() == []


def test_read_ledger_skips_malformed_lines(ledger):
    ledger.mkdir(parents=True)
    (ledger / "2026-08.jsonl").write_text("{bad\n" + json.dumps({"script": "b"}) + "\n")
    assert [r["script"] for r in ai_usage.read_ledger()] == ["b"]


def _write_month(ledger, month, *records):
    ledger.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r) + "\n" for r in records)
    (ledger / f"{month}.jsonl").write_text(body)


def test_read_ledger_since_drops_older_records(ledger):
    _write_month(
        ledger, "2026-08",
        {"script": "old", "ts": "2026-08-01T00:00:00Z"},
        {"script": "new", "ts": "2026-08-20T00:00:00Z"},
    )
    since = datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert [r["script"] for r in ai_usage.read_ledger(since=since)] == ["new"]


def test_read_ledger_since_skips_month_files_before_cutoff(ledger):
    """Selection is by filename — the point of the monthly split."""
    _write_month(ledger, "2026-06", {"script": "stale", "ts": "2026-08-20T00:00:00Z"})
    _write_month(ledger, "2026-08", {"script": "current", "ts": "2026-08-20T00:00:00Z"})
    since = datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert [r["script"] for r in ai_usage.read_ledger(since=since)] == ["current"]


def test_read_ledger_since_keeps_records_without_timestamps(ledger):
    """A record with no ts predates nothing knowable — dropping it would hide cost."""
    _write_month(ledger, "2026-08", {"script": "untimed"})
    since = datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert [r["script"] for r in ai_usage.read_ledger(since=since)] == ["untimed"]
