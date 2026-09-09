"""Tests for the trail structured logging module."""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from conftest import run_checked

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

from core import workbench_paths
from core.trail import (
    ARTIFACT_LIMIT,
    EXCERPT_LIMIT,
    FINISH_ACTION,
    INVOCATION_HEX_WIDTH,
    SCHEMA_VERSION,
    TRAIL_KEEP_MONTHS,
    EventType,
    Level,
    Trail,
    add_trail_args,
    artifacts_dir,
    billed_to,
    prune_trail,
    tdecision,
    terr,
    tfail,
    tinfo,
    tspan,
)


def _months_ago(n: int) -> str:
    """The stem of the month *n* months before this one.

    Walked back a month at a time from the first of this one, so the test does
    not restate the arithmetic it is checking.
    """
    day = datetime.now(timezone.utc).replace(day=1)
    for _ in range(n):
        day = (day - timedelta(days=1)).replace(day=1)
    return day.strftime("%Y-%m")


def _seed_month(stem: str) -> Path:
    """A trail file for one month, holding one record."""
    root = workbench_paths.trail_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stem}.jsonl"
    path.write_text('{"action":"old"}\n')
    return path


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


def _artifact_files() -> list[Path]:
    """Every artifact under the trail root, oldest month first."""
    root = artifacts_dir()
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.log"))


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
        assert len(trail.invocation) == INVOCATION_HEX_WIDTH
        assert all(c in "0123456789abcdef" for c in trail.invocation)
        trail.finish()

    def test_start_writes_no_gitignore(self, tmp_path, monkeypatch):
        """The trail no longer lands in anyone's working tree, so it ignores nothing."""
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        run_checked(["git", "init", "-q", str(tmp_path)])
        Trail.start(script="pr", context={}).finish()
        assert not (tmp_path / ".gitignore").exists()


class TestTrailRetention:
    def test_a_month_past_the_horizon_goes_as_a_trail_opens(self):
        """The sweep is the trail's own, not a chore anyone has to run: a root
        nobody prunes grows without bound under a writer that is polled."""
        stale = _seed_month(_months_ago(TRAIL_KEEP_MONTHS))
        kept = _seed_month(_months_ago(TRAIL_KEEP_MONTHS - 1))

        Trail.start(script="test", context={}).finish()

        assert not stale.exists()
        assert kept.is_file()

    def test_the_month_being_written_survives_any_horizon(self):
        """A horizon that excluded the current month would delete the records
        of the invocation doing the deleting."""
        Trail.start(script="test", context={}).finish()
        current = workbench_paths.trail_dir() / f"{_months_ago(0)}.jsonl"
        assert current.is_file()

        assert prune_trail(0) == []

        assert current.is_file()

    def test_a_stem_that_names_no_month_is_never_dropped(self):
        """`legacy.jsonl` cannot be placed in time by its name, and nothing
        appends to it — a fixed size, not a source of growth."""
        root = workbench_paths.trail_dir()
        root.mkdir(parents=True, exist_ok=True)
        legacy = root / "legacy.jsonl"
        legacy.write_text('{"action":"pre-cutover"}\n')

        assert prune_trail(1) == []

        assert legacy.is_file()

    def test_prune_reports_every_month_it_dropped(self):
        older = _seed_month(_months_ago(TRAIL_KEEP_MONTHS + 1))
        newer = _seed_month(_months_ago(TRAIL_KEEP_MONTHS))

        assert [p.name for p in prune_trail()] == [older.name, newer.name]

    def test_prune_without_a_root_yet_is_not_an_error(self):
        assert prune_trail() == []
        assert not workbench_paths.trail_dir().exists()


class TestUnrecordedTrail:
    def test_it_writes_nothing(self):
        trail = Trail.start(script="test", context={}, record=False)
        trail.info("read", "answered from the state root")
        trail.finish()
        assert _read_events() == []

    def test_it_creates_no_root(self):
        Trail.start(script="test", context={}, record=False).finish()
        assert not workbench_paths.trail_dir().exists()

    def test_it_sweeps_nothing(self):
        """A run that writes no history has no business deleting any."""
        stale = _seed_month(_months_ago(TRAIL_KEEP_MONTHS))

        Trail.start(script="test", context={}, record=False).finish()

        assert stale.is_file()

    def test_debug_still_echoes(self, capsys):
        """The flag is about watching what a run decided, which does not depend
        on whether the decision was worth keeping."""
        trail = Trail.start(script="test", context={}, debug=True, record=False)
        trail.info("fetch", "fetched items")
        trail.finish()
        assert "[trail]" in capsys.readouterr().err
        assert _read_events() == []


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
            from core.trail import Trail
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


class TestTrailSummary:
    def test_summary_writes_a_second_kind_of_summary_event(self):
        trail = Trail.start(script="pr", context={"repo": "org/repo", "pr": 1})
        trail.summary("pr_outcome", "org/repo#7 merged", data={"outcome": "MERGED"})
        trail.finish()
        summaries = [e for e in _read_events() if e["event_type"] == "summary"]
        actions = {e["action"] for e in summaries}
        assert actions == {"pr_outcome", FINISH_ACTION}
        outcome = next(e for e in summaries if e["action"] == "pr_outcome")
        assert outcome["data"] == {"outcome": "MERGED"}
        assert "duration_ms" not in outcome

    def test_per_event_context_overrides_the_runs(self):
        """`pr gc` prunes other PRs than its own; the record must name theirs."""
        trail = Trail.start(script="pr", context={"repo": "org/repo", "pr": 1})
        trail.summary("pr_outcome", "", context={"pr": 7, "branch": "feat/x"})
        trail.finish()
        outcome = next(e for e in _read_events() if e["action"] == "pr_outcome")
        assert outcome["context"] == {"repo": "org/repo", "pr": 7, "branch": "feat/x"}

    def test_the_runs_context_is_not_mutated(self):
        trail = Trail.start(script="pr", context={"repo": "org/repo", "pr": 1})
        trail.summary("pr_outcome", "", context={"pr": 7})
        trail.info("after", "")
        after = next(e for e in _read_events() if e["action"] == "after")
        assert after["context"] == {"repo": "org/repo", "pr": 1}


class TestTrailContext:
    """The subject a run opened against, readable by whoever else needs it.

    A usage ledger entry bills to the same repo and PR the trail names, and the
    helpers that write one sit too far below `main` to have been handed either.
    """

    def test_the_runs_subject_is_readable(self):
        trail = Trail.start(script="pr", context={"repo": "org/repo", "pr": 1})
        assert trail.context == {"repo": "org/repo", "pr": 1}

    def test_a_reader_cannot_edit_what_later_events_will_carry(self):
        trail = Trail.start(script="pr", context={"repo": "org/repo", "pr": 1})
        trail.context["pr"] = 7
        trail.info("after", "")
        after = next(e for e in _read_events() if e["action"] == "after")
        assert after["context"] == {"repo": "org/repo", "pr": 1}


class TestOptionalTrailHelpers:
    """A library function below an entry point records only if one is recording.

    Every helper takes the trail it might not have been given, so the modules
    calling them do not each carry the same None check.
    """

    def test_the_guard_delegates_to_the_trail(self):
        trail = mock.MagicMock()
        tfail(trail, "unstash", "stash pop failed", output="boom")
        trail.failure.assert_called_once_with(
            "unstash", "stash pop failed", output="boom")

    def test_no_trail_is_not_a_failure(self):
        assert tfail(None, "unstash", "failed", output="boom") is None

    def test_each_guard_forwards_to_its_own_method(self):
        trail = mock.MagicMock()
        terr(trail, "act", "detail")
        tinfo(trail, "act", "detail")
        tdecision(trail, "act", "detail", reason="why")
        trail.error.assert_called_once_with("act", "detail")
        trail.info.assert_called_once_with("act", "detail")
        trail.decision.assert_called_once_with("act", "detail", reason="why")

    def test_no_trail_is_not_an_error(self):
        terr(None, "act", "detail")
        tinfo(None, "act", "detail")
        tdecision(None, "act", "detail", reason="why")

    def test_a_span_without_a_trail_still_runs_its_body(self):
        ran = False
        with tspan(None, "work"):
            ran = True
        assert ran

    def test_a_span_with_a_trail_is_recorded(self):
        trail = Trail.start(script="test", context={})
        with tspan(trail, "work"):
            pass
        assert [e["event_type"] for e in _read_events() if e["span"] == "work"] == [
            EventType.SPAN_START.value, EventType.SPAN_END.value,
        ]

    def test_the_runs_subject_is_what_a_call_bills_to(self):
        trail = Trail.start(script="test", context={"repo": "org/repo", "pr": 7})
        assert billed_to(trail) == {"repo": "org/repo", "pr": "7"}

    def test_a_branch_with_no_pr_bills_to_the_repo_alone(self):
        trail = Trail.start(script="test", context={"repo": "org/repo", "pr": None})
        assert billed_to(trail) == {"repo": "org/repo", "pr": None}

    def test_no_trail_bills_to_nothing(self):
        """`--help` and the unit tests below run with no trail opened."""
        assert billed_to(None) == {"repo": None, "pr": None}


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


class TestFailure:
    def test_it_writes_the_whole_output_to_an_artifact(self):
        trail = Trail.start(script="test", context={})
        output = "\n".join(f"line {n}" for n in range(500))

        path = trail.failure("push", "git refused the push", output=output)

        assert path is not None
        assert path.read_text() == output
        assert path.parent.name == datetime.now(timezone.utc).strftime("%Y-%m")
        assert path.parent.parent == artifacts_dir()

    def test_the_record_points_at_the_artifact_by_relative_path(self):
        """An absolute path does not survive a state root that moves."""
        trail = Trail.start(script="test", context={})

        path = trail.failure("push", "refused", output="boom")

        event = _read_events()[-1]
        assert event["data"]["log"] == str(path.relative_to(workbench_paths.trail_dir()))
        assert event["data"]["log"].startswith("artifacts/")

    def test_the_excerpt_is_the_tail_not_the_head(self):
        """The line naming which gate failed is the last one a hook prints."""
        output = "banner\n" + "x" * 2000 + "\n✗ lint:ts failed"
        Trail.start(script="test", context={}).failure("push", "refused", output=output)

        recorded = _read_events()[-1]["data"]["error"]
        assert "✗ lint:ts failed" in recorded
        assert "banner" not in recorded
        assert len(recorded) <= EXCERPT_LIMIT

    def test_it_counts_the_lines_of_the_whole_output(self):
        Trail.start(script="test", context={}).failure(
            "push", "refused", output="a\nb\nc")

        assert _read_events()[-1]["data"]["output_lines"] == 3

    def test_caller_data_is_kept_beside_the_excerpt(self):
        Trail.start(script="test", context={}).failure(
            "push", "refused", output="boom", data={"sha": "1a2b3c4d"})

        data = _read_events()[-1]["data"]
        assert data["sha"] == "1a2b3c4d"
        assert data["error"] == "boom"

    def test_it_records_an_error_event(self):
        Trail.start(script="test", context={}).failure("push", "refused", output="boom")

        event = _read_events()[-1]
        assert event["level"] == "error"
        assert event["event_type"] == "error"
        assert event["action"] == "push"
        assert event["detail"] == "refused"

    def test_two_failures_in_one_run_do_not_collide(self):
        trail = Trail.start(script="test", context={})

        first = trail.failure("push", "refused", output="first")
        second = trail.failure("push", "refused again", output="second")

        assert first != second
        assert first.read_text() == "first"
        assert second.read_text() == "second"

    def test_an_action_that_is_not_a_filename_is_sanitised(self):
        trail = Trail.start(script="test", context={})

        path = trail.failure("push/force --lease", "refused", output="boom")

        assert path.parent == artifacts_dir() / datetime.now(timezone.utc).strftime("%Y-%m")
        assert "/" not in path.name.removeprefix(f"{trail.invocation}-")

    def test_output_over_the_cap_keeps_its_tail_under_a_banner(self):
        trail = Trail.start(script="test", context={})
        output = "H" * ARTIFACT_LIMIT + "TAIL"

        written = trail.failure("push", "refused", output=output).read_text()

        assert written.endswith("TAIL")
        assert len(written.encode()) <= ARTIFACT_LIMIT + len(written.splitlines()[0]) + 1
        assert "dropped" in written.splitlines()[0]

    def test_an_unwritable_root_still_records_the_event(self, monkeypatch):
        """A full disk must not turn a refused push into a crash."""
        def _refuse(*args, **kwargs):
            raise OSError("no space left on device")

        trail = Trail.start(script="test", context={})
        monkeypatch.setattr(Path, "mkdir", _refuse)

        assert trail.failure("push", "refused", output="boom") is None

        data = _read_events()[-1]["data"]
        assert data["error"] == "boom"
        assert "log" not in data

    def test_empty_output_writes_no_artifact(self):
        """A blank AI response would otherwise get a path to an empty file."""
        trail = Trail.start(script="test", context={})

        assert trail.failure("triage", "no answer", output="") is None

        data = _read_events()[-1]["data"]
        assert "log" not in data
        assert data["output_lines"] == 0
        assert _artifact_files() == []

    def test_whitespace_only_output_writes_no_artifact(self):
        trail = Trail.start(script="test", context={})

        assert trail.failure("triage", "no answer", output="  \n\n\t") is None

        assert "log" not in _read_events()[-1]["data"]
        assert _artifact_files() == []


class TestUnrecordedFailure:
    def test_it_writes_no_artifact(self):
        trail = Trail.start(script="test", context={}, record=False)

        assert trail.failure("push", "refused", output="boom") is None

        assert _read_events() == []
        assert not artifacts_dir().exists()


class TestArtifactRetention:
    def _seed_artifact(self, stem: str) -> Path:
        month = artifacts_dir() / stem
        month.mkdir(parents=True, exist_ok=True)
        path = month / "aaaaaaaaaaaa-1-push.log"
        path.write_text("old output\n")
        return month

    def test_it_drops_an_artifact_month_below_the_cutoff(self):
        stale = self._seed_artifact(_months_ago(TRAIL_KEEP_MONTHS + 1))

        assert stale in prune_trail()

        assert not stale.exists()

    def test_it_keeps_an_artifact_month_inside_the_horizon(self):
        fresh = self._seed_artifact(_months_ago(1))

        prune_trail()

        assert fresh.is_dir()

    def test_a_directory_that_names_no_month_is_never_dropped(self):
        odd = artifacts_dir() / "scratch"
        odd.mkdir(parents=True, exist_ok=True)

        prune_trail(1)

        assert odd.is_dir()

    def test_no_artifacts_root_is_not_an_error(self):
        assert prune_trail() == []
