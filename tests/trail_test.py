"""Tests for the trail structured logging module."""

import json
import os
import sys
import tempfile
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"
sys.path.insert(0, str(LIB_DIR))

from trail import (
    SCHEMA_VERSION,
    TRAIL_FILENAME,
    EventType,
    Level,
    Trail,
    add_trail_args,
)


def _read_events(trail_dir: str) -> list[dict]:
    trail_file = Path(trail_dir) / TRAIL_FILENAME
    if not trail_file.exists():
        return []
    return [json.loads(line) for line in trail_file.read_text().splitlines() if line.strip()]


class TestTrailEvent:
    def test_level_enum_values(self):
        assert Level.DEBUG == "debug"
        assert Level.INFO == "info"
        assert Level.WARN == "warn"
        assert Level.ERROR == "error"

    def test_event_type_enum_values(self):
        assert EventType.ACTION == "action"
        assert EventType.DECISION == "decision"
        assert EventType.SPAN_START == "span_start"
        assert EventType.SPAN_END == "span_end"
        assert EventType.ERROR == "error"
        assert EventType.SUMMARY == "summary"

    def test_constants(self):
        assert TRAIL_FILENAME == "trail.jsonl"
        assert SCHEMA_VERSION == 1


class TestTrailStart:
    def test_start_creates_trail_file(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test-script", artifact_dir=d, context={"repo": "org/repo"})
            trail.finish()
            assert (Path(d) / TRAIL_FILENAME).exists()

    def test_start_generates_invocation_id(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test-script", artifact_dir=d, context={})
            assert len(trail.invocation) == 8
            assert all(c in "0123456789abcdef" for c in trail.invocation)
            trail.finish()

    def test_start_creates_artifact_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "sub", "dir")
            trail = Trail.start(script="test-script", artifact_dir=nested, context={})
            trail.finish()
            assert (Path(nested) / TRAIL_FILENAME).exists()


class TestTrailEvents:
    def test_info_writes_action_event(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={"repo": "r"})
            trail.info("fetch", "fetched 3 items", data={"count": 3})
            trail.finish()
            events = _read_events(d)
            action_events = [e for e in events if e["event_type"] == "action"]
            assert len(action_events) == 1
            e = action_events[0]
            assert e["level"] == "info"
            assert e["action"] == "fetch"
            assert e["detail"] == "fetched 3 items"
            assert e["data"] == {"count": 3}
            assert e["schema_version"] == SCHEMA_VERSION
            assert e["script"] == "test"
            assert e["context"] == {"repo": "r"}
            assert e["invocation"] == trail.invocation

    def test_decision_requires_reason(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.decision("classify", "chose A", reason="B was worse")
            trail.finish()
            events = _read_events(d)
            decisions = [e for e in events if e["event_type"] == "decision"]
            assert len(decisions) == 1
            assert decisions[0]["reason"] == "B was worse"

    def test_error_sets_both_level_and_event_type(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.error("api_call", "rate limited", data={"status": 429})
            trail.finish()
            events = _read_events(d)
            errors = [e for e in events if e["event_type"] == "error"]
            assert len(errors) == 1
            assert errors[0]["level"] == "error"
            assert errors[0]["event_type"] == "error"

    def test_warn_writes_warn_level(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.warn("stale_cache", "cache is 2 days old")
            trail.finish()
            events = _read_events(d)
            warns = [e for e in events if e["level"] == "warn"]
            assert len(warns) == 1

    def test_debug_writes_debug_level(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.debug("lookup", "checking cache")
            trail.finish()
            events = _read_events(d)
            debugs = [e for e in events if e["level"] == "debug"]
            assert len(debugs) == 1


class TestTrailSpan:
    def test_span_writes_start_and_end(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            with trail.span("post_review"):
                trail.info("post_inline", "posted 4 comments")
            trail.finish()
            events = _read_events(d)
            starts = [e for e in events if e["event_type"] == "span_start"]
            ends = [e for e in events if e["event_type"] == "span_end"]
            assert len(starts) == 1
            assert starts[0]["span"] == "post_review"
            assert len(ends) == 1
            assert ends[0]["span"] == "post_review"
            assert ends[0]["duration_ms"] is not None
            assert ends[0]["duration_ms"] >= 0


class TestTrailFinish:
    def test_finish_writes_summary(self):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.info("do_thing", "did it")
            trail.finish()
            events = _read_events(d)
            summaries = [e for e in events if e["event_type"] == "summary"]
            assert len(summaries) == 1
            assert summaries[0]["duration_ms"] is not None
            assert summaries[0]["duration_ms"] >= 0


class TestTrailAppend:
    def test_multiple_invocations_append(self):
        with tempfile.TemporaryDirectory() as d:
            t1 = Trail.start(script="test", artifact_dir=d, context={})
            t1.info("a", "first")
            t1.finish()
            t2 = Trail.start(script="test", artifact_dir=d, context={})
            t2.info("b", "second")
            t2.finish()
            events = _read_events(d)
            invocations = set(e["invocation"] for e in events)
            assert len(invocations) == 2


class TestTrailDebugMode:
    def test_debug_mode_via_flag(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={}, debug=True)
            trail.info("fetch", "fetched items")
            trail.finish()
            captured = capsys.readouterr()
            assert "[trail]" in captured.err
            assert "fetch" in captured.err

    def test_normal_mode_no_stderr(self, capsys):
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.info("fetch", "fetched items")
            trail.finish()
            captured = capsys.readouterr()
            assert "[trail]" not in captured.err

    def test_debug_mode_via_env(self, capsys, monkeypatch):
        monkeypatch.setenv("WORKBENCH_DEBUG", "1")
        with tempfile.TemporaryDirectory() as d:
            trail = Trail.start(script="test", artifact_dir=d, context={})
            trail.info("fetch", "fetched items")
            trail.finish()
            captured = capsys.readouterr()
            assert "[trail]" in captured.err


class TestAddTrailArgs:
    def test_adds_debug_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        add_trail_args(parser)
        args = parser.parse_args(["--debug"])
        assert args.debug is True

    def test_debug_defaults_false(self):
        import argparse
        parser = argparse.ArgumentParser()
        add_trail_args(parser)
        args = parser.parse_args([])
        assert args.debug is False
