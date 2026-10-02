"""Tests for how otto-log renders the trail — `core.trail_view`."""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import core.trail  # noqa: E402
import core.trail_view  # noqa: E402
import core.workbench_paths  # noqa: E402
from core.trail import Trail  # noqa: E402
from trail_support import (  # noqa: E402
    PR_REVIEW,
    make_command,
    make_trail,
    raw_event,
    raw_record,
    write_raw,
)


def _finish_event_fields(**fields) -> dict:
    """The summary that closes a run, carrying the duration it measured."""
    return dict(event_type="summary", action="finish", **fields)


def _raw_finish(**fields) -> str:
    """`raw_record`'s finish form, as a line."""
    return raw_record(**_finish_event_fields(**fields))


def _finish(**fields) -> dict:
    """`raw_event`'s finish form, as a record."""
    return raw_event(**_finish_event_fields(**fields))


class TestCommandCorrelation:
    """A user command spans several processes; the reader treats it as one."""

    def test_show_renders_the_whole_command_from_its_root(self, capsys):
        root, _, _ = make_command(*PR_REVIEW)
        core.trail_view.show(root, only=False, as_json=False)
        out = capsys.readouterr().out
        assert "pr → review → review-orchestrate" in out
        assert "review-orchestrate ran" in out

    def test_show_names_who_started_the_run(self, capsys):
        """The question somebody has when they open a trail for a run they did
        not expect. A fix pass wrote into a worktree and there was no process
        left to ask, so the header answers it without a --json detour."""
        root, _ = make_command("pr", "review")
        core.trail_view.show(root, only=False, as_json=False)
        out = capsys.readouterr().out
        assert "Started by:" in out
        assert f"(ppid {os.getppid()})" in out

    def test_show_says_nothing_of_a_record_predating_attribution(self, capsys):
        """A row of unknowns would read as a probe that failed rather than as
        history written before the field existed."""
        root = make_trail("pr", [("a", "first")])
        path = next(iter(core.workbench_paths.trail_dir().glob("*.jsonl")))
        kept = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for e in kept:
            e.pop("origin", None)
        path.write_text("".join(json.dumps(e) + "\n" for e in kept))
        core.trail_view.show(root, only=False, as_json=False)
        assert "Started by:" not in capsys.readouterr().out

    def test_show_finds_the_command_from_a_child_id(self, capsys):
        """A user has one ID in hand and does not know which end it came from."""
        root, child, _ = make_command(*PR_REVIEW)
        core.trail_view.show(child, only=False, as_json=False)
        out = capsys.readouterr().out
        assert f"Invocation {root}" in out
        assert "review-orchestrate ran" in out

    def test_show_reports_the_whole_commands_duration(self, capsys):
        """The root's own finish, not whichever child happened to end first."""
        root, _ = make_command("pr", "review")
        core.trail_view.show(root, only=False, as_json=False)
        header = capsys.readouterr().out.splitlines()[0]
        assert re.search(r"\d+\.\d+s", header)

    def test_show_labels_each_event_with_the_script_that_wrote_it(self, capsys):
        root, _ = make_command("pr", "review")
        core.trail_view.show(root, only=False, as_json=False)
        body = capsys.readouterr().out.splitlines()[3:]
        assert any("review" in line for line in body)

    def test_a_single_process_command_keeps_the_unlabelled_layout(self, capsys):
        """Nothing to tell apart, so the column would be the same on every line."""
        inv = make_trail("ci-check", [("fetch", "fetched")])
        core.trail_view.show(inv, only=False, as_json=False)
        body = capsys.readouterr().out.splitlines()[3:]
        assert not any("ci-check" in line for line in body)

    def test_only_narrows_back_to_one_process(self, capsys):
        _, child, _ = make_command(*PR_REVIEW)
        core.trail_view.show(child, only=True, as_json=False)
        out = capsys.readouterr().out
        assert f"Invocation {child}" in out
        assert "review-orchestrate ran" not in out

    def test_only_reports_that_processes_own_duration(self, capsys):
        """The header must not go looking for a finish under the root's ID when
        no event in the narrowed listing carries it."""
        _, child = make_command("pr", "review")
        core.trail_view.show(child, only=True, as_json=False)
        header = capsys.readouterr().out.splitlines()[0]
        assert re.search(r"\d+\.\d+s", header)

    def test_list_prints_one_row_per_command(self, capsys):
        make_command(*PR_REVIEW)
        core.trail_view.list_commands(script=None, since=None, repo=None, as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1
        assert rows[0]["scripts"] == list(PR_REVIEW)
        assert rows[0]["script"] == "pr"

    def test_list_counts_every_event_in_the_command(self, capsys):
        root, _ = make_command("pr", "review")
        core.trail_view.list_commands(script=None, since=None, repo=None, as_json=True)
        row = json.loads(capsys.readouterr().out.splitlines()[0])
        assert row["invocation"] == root
        # Two `work` events and two `finish`es, from the two processes.
        assert row["event_count"] == 4

    def test_list_by_script_reports_the_commands_that_reached_it(self, capsys):
        """Filtering on an inner script still yields whole commands: the row is
        the `pr review` that got there, not the fragment one process logged."""
        root, _, _ = make_command(*PR_REVIEW)
        make_trail("ci-check", [("a", "unrelated")])
        core.trail_view.list_commands(
            script="review-orchestrate", since=None, repo=None, as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1
        assert rows[0]["invocation"] == root
        assert rows[0]["scripts"] == list(PR_REVIEW)

    def test_list_keeps_a_command_that_started_before_the_window(self, capsys):
        """A window selects commands, not events.

        A `pr review` that has been running an hour began before `--since 1h`,
        and half its timeline answers no question anyone asks of it — so the
        row is the whole command, stamped with when it really started.
        """
        now = datetime.now(timezone.utc)
        started = now - timedelta(hours=10)
        write_raw(
            f"{now:%Y-%m}.jsonl",
            raw_record(ts=f"{started:%Y-%m-%dT%H:%M:%SZ}",
                        invocation="aaaa", script="pr", action="dispatch"),
            raw_record(ts=f"{now - timedelta(minutes=5):%Y-%m-%dT%H:%M:%SZ}",
                        invocation="bbbb", root="aaaa",
                        script="review", action="work"),
        )

        core.trail_view.list_commands(script=None, since="1h", repo=None, as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1
        assert rows[0]["scripts"] == ["pr", "review"]
        assert rows[0]["ts"].startswith(f"{started:%Y-%m-%dT%H}")

    def test_list_names_the_command_by_its_outermost_script(self, capsys):
        make_command(*PR_REVIEW)
        core.trail_view.list_commands(script=None, since=None, repo=None, as_json=False)
        assert "pr +2" in capsys.readouterr().out


class TestRepoScoping:
    def _trail(self, repo: str):
        trail = Trail.start(script="pr", context={"repo": repo, "pr": 1})
        trail.info("act", "did")
        trail.finish()

    def test_recent_narrows_to_one_repo(self, capsys):
        self._trail("org/alpha")
        self._trail("org/beta")
        core.trail_view.recent("1d", repo="org/alpha", as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows
        assert all(r["context"]["repo"] == "org/alpha" for r in rows)

    def test_list_narrows_to_one_repo(self, capsys):
        self._trail("org/alpha")
        self._trail("org/beta")
        core.trail_view.list_commands(script=None, since=None, repo="org/alpha", as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1


class TestPruneCommand:
    def _write(self, name: str):
        root = core.workbench_paths.trail_dir()
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_text('{"action":"old"}\n')
        return path

    def _run(self, keep):
        core.trail_view.prune(keep)

    def test_a_horizon_the_caller_names_overrides_the_default(self, capsys):
        """The reason to run it by hand at all: taking history down past the
        default after a burst, without waiting for the next trail to open."""
        stale = self._write("2026-01.jsonl")
        self._write(f"{datetime.now(timezone.utc):%Y-%m}.jsonl")

        self._run(1)

        assert not stale.exists()
        assert "2026-01.jsonl" in capsys.readouterr().out

    def test_an_already_bounded_root_says_so(self, capsys):
        self._write(f"{datetime.now(timezone.utc):%Y-%m}.jsonl")
        self._run(core.trail.TRAIL_KEEP_MONTHS)
        assert "nothing older" in capsys.readouterr().out


class TestPrune:
    def test_it_names_a_swept_artifact_month_under_its_directory(self, capsys):
        stem = "2020-01"
        month = core.trail.artifacts_dir() / stem
        month.mkdir(parents=True)
        (month / "aaaaaaaaaaaa-1-push.log").write_text("old\n")

        core.trail_view.prune(1)

        assert f"artifacts/{stem}" in capsys.readouterr().out


class TestSummaryIsNotAlwaysFinish:
    def test_show_reports_the_runs_duration(self, capsys):
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.info("act", "did")
        trail.finish()
        core.trail_view.show(trail.invocation, only=False, as_json=False)
        # A duration, not merely a line that happens to end in "s" — the point of
        # the test is that `finish` is found and its duration_ms rendered.
        assert re.search(r"\d+\.\d+s", capsys.readouterr().out)

    def test_show_survives_a_summary_with_no_duration(self, capsys):
        """A terminal `pr_outcome` event carries no duration and must not raise."""
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.summary("pr_outcome", "org/repo#7 merged", data={"outcome": "MERGED"})
        core.trail_view.show(trail.invocation, only=False, as_json=False)
        assert "pr_outcome" in capsys.readouterr().out

    def test_list_survives_a_summary_with_no_duration(self, capsys):
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.summary("pr_outcome", "org/repo#7 merged")
        core.trail_view.list_commands(script=None, since=None, repo=None, as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows[0]["duration_ms"] is None


class TestCommandDuration:
    """What a command's duration covers once records can outlive its root.

    `otto-log record` files an event under an inherited root, so a command can
    gain records after the process that opened it has exited. The root's own
    `duration_ms` stops measuring the command at that point.
    """

    def test_a_run_that_ends_when_its_root_exits_reports_what_it_measured(self):
        """Every command written before `record` existed is this one."""
        events = [
            raw_event(ts="2026-09-01T00:00:00Z", invocation="aaaaaaaaaaaa"),
            _finish(ts="2026-09-01T00:00:07Z", invocation="aaaaaaaaaaaa",
                        duration_ms=7000),
        ]

        assert core.trail_view._command_duration_ms(events, "aaaaaaaaaaaa") == 7000

    def test_a_long_run_that_logged_late_keeps_its_measured_duration(self):
        """`duration_ms` runs from process start, the first event from whenever it
        was written. A 22-minute run that wrote both records in its last second
        is real history, and the span between its events is not its duration."""
        events = [
            raw_event(ts="2026-09-09T00:25:17Z", invocation="bbbbbbbbbbbb",
                        action="unexpected_error"),
            _finish(ts="2026-09-09T00:25:17Z", invocation="bbbbbbbbbbbb",
                        duration_ms=1368728),
        ]

        assert core.trail_view._command_duration_ms(events, "bbbbbbbbbbbb") == 1368728

    def test_a_record_after_the_root_exits_extends_the_command(self):
        """The dream case: a scan, then an agent's phases minutes later."""
        events = [
            raw_event(ts="2026-09-01T00:00:00Z", script="dream-scan",
                        invocation="cccccccccccc"),
            _finish(ts="2026-09-01T00:00:02Z", script="dream-scan",
                        invocation="cccccccccccc", duration_ms=2000),
            raw_event(ts="2026-09-01T00:05:02Z", script="dream",
                        invocation="dddddddddddd", root="cccccccccccc",
                        action="consolidate"),
        ]

        # Five minutes of agent work after a two-second scan, not two seconds.
        assert core.trail_view._command_duration_ms(events, "cccccccccccc") == 302000

    def test_a_trailing_record_never_shortens_a_run(self):
        """A later process whose clock reads behind the root's cannot subtract
        from a duration the root measured."""
        events = [
            raw_event(ts="2026-09-01T00:00:10Z", invocation="eeeeeeeeeeee"),
            _finish(ts="2026-09-01T00:00:10Z", invocation="eeeeeeeeeeee",
                        duration_ms=10000),
            raw_event(ts="2026-09-01T00:00:05Z", invocation="ffffffffffff",
                        root="eeeeeeeeeeee"),
        ]

        assert core.trail_view._command_duration_ms(events, "eeeeeeeeeeee") == 10000

    def test_a_killed_run_reports_nothing_rather_than_a_span(self):
        """No finish survives, so the duration is unknown — and a span between
        whatever records it managed to write would be a guess."""
        events = [
            raw_event(ts="2026-09-01T00:00:00Z", invocation="bbbbccccdddd"),
            raw_event(ts="2026-09-01T00:04:00Z", invocation="bbbbccccdddd"),
        ]

        assert core.trail_view._command_duration_ms(events, "bbbbccccdddd") is None

    def test_show_reports_the_whole_command_not_just_its_root(self, capsys):
        write_raw(
            "2026-09.jsonl",
            raw_record(ts="2026-09-01T00:00:00Z", script="dream-scan",
                        invocation="cccccccccccc"),
            _raw_finish(ts="2026-09-01T00:00:02Z", script="dream-scan",
                        invocation="cccccccccccc", duration_ms=2000),
            raw_record(ts="2026-09-01T00:05:02Z", script="dream",
                        invocation="dddddddddddd", root="cccccccccccc",
                        action="consolidate"),
        )

        core.trail_view.show("cccccccccccc", only=False, as_json=False)

        assert "302.0s" in capsys.readouterr().out


class TestLogPointerRendering:
    def test_an_event_with_an_artifact_names_it_absolutely(self):
        event = {"ts": "2026-09-04T20:07:24Z", "level": "error",
                 "event_type": "error", "action": "push", "detail": "refused",
                 "data": {"log": "artifacts/2026-09/214e9758c739-1-push.log"}}

        rendered = core.trail_view._format_event_line(event)

        expected = core.workbench_paths.trail_dir() / "artifacts/2026-09/214e9758c739-1-push.log"
        assert f"log: {expected}" in rendered

    def test_an_event_without_one_renders_unchanged(self):
        event = {"ts": "2026-09-04T20:07:24Z", "level": "info",
                 "event_type": "action", "action": "push", "detail": "pushed"}

        assert "log:" not in core.trail_view._format_event_line(event)

    def test_the_duration_stays_on_the_header_line(self):
        """The pointer goes last, so a run's timing is not pushed onto its own
        line by an artifact that arrived after it."""
        event = {"ts": "2026-09-04T20:07:24Z", "level": "error",
                 "event_type": "error", "action": "push", "detail": "refused",
                 "duration_ms": 42, "data": {"log": "artifacts/2026-09/a-1-push.log"}}

        header, pointer = core.trail_view._format_event_line(event).split("\n")
        assert "(42ms)" in header
        assert "log:" in pointer
