"""Tests for the trail structured logging module."""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths
from trail import (
    FINISH_ACTION,
    SCHEMA_VERSION,
    EventType,
    Level,
    Trail,
    add_trail_args,
)


def _read_events() -> list[dict]:
    """Every record in the trail root, oldest file first.

    One run writes one file, so the test does not need to know its name.
    """
    root = workbench_paths.trail_dir()
    if not root.is_dir():
        return []
    events = []
    for path in sorted(root.glob("*.jsonl")):
        events += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return events


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
        assert SCHEMA_VERSION == 1
        assert FINISH_ACTION == "finish"


class TestTrailRoot:
    def test_start_creates_the_root(self):
        Trail.start(script="test-script", context={"repo": "org/repo"}).finish()
        assert workbench_paths.trail_dir().is_dir()

    def test_events_land_in_this_months_file(self):
        trail = Trail.start(script="test-script", context={})
        trail.info("fetch", "fetched")
        trail.finish()
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        names = [p.name for p in workbench_paths.trail_dir().glob("*.jsonl")]
        assert names == [f"{month}.jsonl"]

    def test_the_events_own_month_picks_the_file(self):
        """A run crossing midnight on the 31st writes each event where its ts says."""
        now = datetime.now(timezone.utc)
        # +32 days always overshoots the longest possible month by at least a
        # day, so this lands in the immediately following month every time,
        # including a December run rolling into next January.
        next_month = (now.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
        this_month = now.strftime("%Y-%m")
        trail = Trail.start(script="test-script", context={})
        event = trail._make_event(Level.INFO, EventType.ACTION, "late", "after midnight")
        event.ts = f"{next_month}-01T00:00:01Z"
        trail._emit(event)
        assert (workbench_paths.trail_dir() / f"{next_month}.jsonl").is_file()
        assert not (workbench_paths.trail_dir() / f"{this_month}.jsonl").is_file()

    def test_start_generates_invocation_id(self):
        trail = Trail.start(script="test-script", context={})
        assert len(trail.invocation) == 8
        assert all(c in "0123456789abcdef" for c in trail.invocation)
        trail.finish()

    def test_start_writes_no_gitignore(self, tmp_path, monkeypatch):
        """The trail no longer lands in anyone's working tree, so it ignores nothing."""
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        Trail.start(script="pr", context={}).finish()
        assert not (tmp_path / ".gitignore").exists()


class TestTrailEvents:
    def test_info_writes_action_event(self):
        trail = Trail.start(script="test", context={"repo": "r"})
        trail.info("fetch", "fetched 3 items", data={"count": 3})
        trail.finish()
        action_events = [e for e in _read_events() if e["event_type"] == "action"]
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
        trail = Trail.start(script="test", context={})
        trail.decision("classify", "chose A", reason="B was worse")
        trail.finish()
        decisions = [e for e in _read_events() if e["event_type"] == "decision"]
        assert len(decisions) == 1
        assert decisions[0]["reason"] == "B was worse"

    def test_error_sets_both_level_and_event_type(self):
        trail = Trail.start(script="test", context={})
        trail.error("api_call", "rate limited", data={"status": 429})
        trail.finish()
        errors = [e for e in _read_events() if e["event_type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["level"] == "error"

    def test_warn_writes_warn_level(self):
        trail = Trail.start(script="test", context={})
        trail.warn("stale_cache", "cache is 2 days old")
        trail.finish()
        assert len([e for e in _read_events() if e["level"] == "warn"]) == 1

    def test_debug_writes_debug_level(self):
        trail = Trail.start(script="test", context={})
        trail.debug("lookup", "checking cache")
        trail.finish()
        assert len([e for e in _read_events() if e["level"] == "debug"]) == 1


class TestTrailSpan:
    def test_span_writes_start_and_end(self):
        trail = Trail.start(script="test", context={})
        with trail.span("post_review"):
            trail.info("post_inline", "posted 4 comments")
        trail.finish()
        events = _read_events()
        starts = [e for e in events if e["event_type"] == "span_start"]
        ends = [e for e in events if e["event_type"] == "span_end"]
        assert len(starts) == 1
        assert starts[0]["span"] == "post_review"
        assert len(ends) == 1
        assert ends[0]["duration_ms"] >= 0


class TestTrailFinish:
    def test_finish_writes_summary(self):
        trail = Trail.start(script="test", context={})
        trail.info("do_thing", "did it")
        trail.finish()
        summaries = [e for e in _read_events() if e["event_type"] == "summary"]
        assert len(summaries) == 1
        assert summaries[0]["action"] == FINISH_ACTION
        assert summaries[0]["duration_ms"] >= 0


class TestTrailAppend:
    def test_multiple_invocations_append(self):
        t1 = Trail.start(script="test", context={})
        t1.info("a", "first")
        t1.finish()
        t2 = Trail.start(script="test", context={})
        t2.info("b", "second")
        t2.finish()
        assert len(set(e["invocation"] for e in _read_events())) == 2

    def test_two_processes_append_intact_lines(self, tmp_path):
        """One file now takes appends from `pr` and the script it spawned.

        A short write splits a record across two write() calls — this happens
        for real on NFS-mounted homes, on signal interruption, and at rlimit
        boundaries — and without the flock the other process's append can
        land in the gap between them. Every raw write is forced short here
        (4 KiB) so the interleaving window opens on every record, on every
        filesystem, deterministically.
        """
        writer = tmp_path / "writer.py"
        writer.write_text(textwrap.dedent(f"""
            # `_io.FileIO` is a static type on this interpreter and refuses
            # attribute assignment, so the short write is forced one layer up:
            # `open()` itself is swapped for a version whose raw layer is a
            # pure-Python RawIOBase that truncates every write to 4 KiB. trail.py
            # opens the trail file with a bare `open(path, "a")`, so patching
            # only that call, by mode and suffix, leaves every other open alone.
            import builtins
            import io
            import os

            _real_open = builtins.open

            class _ShortRawIO(io.RawIOBase):
                def __init__(self, fd):
                    self._fd = fd

                def writable(self):
                    return True

                def write(self, b):
                    return os.write(self._fd, bytes(b)[:4096])

                def fileno(self):
                    return self._fd

                def close(self):
                    if not self.closed:
                        os.close(self._fd)
                    super().close()

            def _short_open(file, mode="r", *args, **kwargs):
                if mode == "a" and str(file).endswith(".jsonl"):
                    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                    return io.TextIOWrapper(io.BufferedWriter(_ShortRawIO(fd)))
                return _real_open(file, mode, *args, **kwargs)

            builtins.open = _short_open

            import sys
            sys.path.insert(0, {str(LIB_DIR)!r})
            from trail import Trail
            trail = Trail.start(script=sys.argv[1], context={{}})
            for _ in range(50):
                trail.info("bulk", "x" * 9000, data={{"pad": "y" * 9000}})
            trail.finish()
        """))
        procs = [
            subprocess.Popen([sys.executable, str(writer), name], env=dict(os.environ))
            for name in ("alpha", "beta")
        ]
        for p in procs:
            assert p.wait() == 0
        events = _read_events()
        assert len([e for e in events if e["action"] == "bulk"]) == 100
        assert {e["script"] for e in events} == {"alpha", "beta"}


class TestTrailDebugMode:
    def test_debug_mode_via_flag(self, capsys):
        trail = Trail.start(script="test", context={}, debug=True)
        trail.info("fetch", "fetched items")
        trail.finish()
        captured = capsys.readouterr()
        assert "[trail]" in captured.err
        assert "fetch" in captured.err

    def test_normal_mode_no_stderr(self, capsys):
        trail = Trail.start(script="test", context={})
        trail.info("fetch", "fetched items")
        trail.finish()
        assert "[trail]" not in capsys.readouterr().err

    def test_debug_mode_via_env(self, capsys, monkeypatch):
        monkeypatch.setenv("WORKBENCH_DEBUG", "1")
        trail = Trail.start(script="test", context={})
        trail.info("fetch", "fetched items")
        trail.finish()
        assert "[trail]" in capsys.readouterr().err


class TestAddTrailArgs:
    def test_adds_debug_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        add_trail_args(parser)
        assert parser.parse_args(["--debug"]).debug is True

    def test_debug_defaults_false(self):
        import argparse
        parser = argparse.ArgumentParser()
        add_trail_args(parser)
        assert parser.parse_args([]).debug is False
