"""Tests for otto-log query CLI."""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import load_script, reset_trail_root

BIN_DIR = Path(__file__).resolve().parent.parent / "ai" / "bin"
LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(BIN_DIR))

from agent import usage as ai_usage
from core import trail as trail_module
from core import workbench_paths
from core.trail import Trail
# The read path is `core.trail_query`'s. Reaching for it here rather than
# through the CLI keeps `otto-log` importing only the names it calls.
from core.trail_query import (
    FALLBACK_WINDOW,
    discover_trails,
    filter_events,
    load_events,
    parse_since,
)

otto_log = load_script("otto_log", BIN_DIR / "otto-log")


def _make_trail(script: str, events: list[tuple[str, str]]) -> str:
    """Write a trail with the given action/detail pairs, return invocation ID."""
    trail = Trail.start(script=script, context={"repo": "org/repo", "pr": 42})
    for action, detail in events:
        trail.info(action, detail)
    trail.finish()
    return trail.invocation


def _make_command(*scripts: str) -> list[str]:
    """One user command as the process tree it really is; return its invocations.

    Each trail is opened while the one before it is still the published root, so
    the records land exactly as `pr` → `claude-review` → `review-orchestrate`
    writes them — without paying for three subprocesses per test. The spawn
    itself is covered in `trail_test.py`, which is where that mechanism lives.

    `reset_trail_root` is what makes each call a separate command, the way the
    outermost process exiting does in a real tree: without it the next call
    would adopt this one's root and the two would read as one command.
    """
    with reset_trail_root():
        trails = []
        for script in scripts:
            trail = Trail.start(script=script,
                                context={"repo": "org/repo", "pr": 42})
            trail.info("work", f"{script} ran")
            trails.append(trail)
        for trail in reversed(trails):
            trail.finish()
    return [t.invocation for t in trails]


# The command every correlation test is about: what `pr review` really runs.
# Named once so the three-process shape is a single source of truth rather than
# a literal repeated down the class.
_PR_REVIEW = ("pr", "claude-review", "review-orchestrate")


def _raw_event(**fields) -> dict:
    """One trail record, with every required field defaulted.

    For the tests that cannot go through `Trail` — history from before a field
    existed, or a stamp hours in the past. Each names only what it is about and
    inherits the rest, so a new required field is added here rather than in
    every literal that predates it.

    The record rather than its line, for the readers that take events as they
    come off the loader. `_raw_record` is the same thing serialized.
    """
    return {
        "ts": "2026-01-01T00:00:00Z", "script": "old-run",
        "invocation": "a1b2c3d4", "level": "info", "event_type": "action",
        "action": "x", "detail": "", "context": {},
        **fields,
    }


def _raw_record(**fields) -> str:
    """One trail record as a line, for the tests that write a trail file."""
    return json.dumps(_raw_event(**fields)) + "\n"


def _finish_event_fields(**fields) -> dict:
    """The summary that closes a run, carrying the duration it measured."""
    return dict(event_type="summary", action="finish", **fields)


def _raw_finish(**fields) -> str:
    """`_raw_record`'s finish form, as a line."""
    return _raw_record(**_finish_event_fields(**fields))


def _finish(**fields) -> dict:
    """`_raw_event`'s finish form, as a record."""
    return _raw_event(**_finish_event_fields(**fields))


def _write_raw(name: str, *records: str) -> Path:
    """Put pre-built *records* in the trail root under *name*."""
    root = workbench_paths.trail_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text("".join(records))
    return path


class TestTrailDiscovery:
    def test_finds_the_month_file_every_writer_appends_to(self):
        _make_trail("ci-check", [("fetch", "fetched")])
        trails = discover_trails()
        assert len(trails) == 1
        assert trails[0].parent == workbench_paths.trail_dir()

    def test_an_empty_root_has_no_trails(self):
        assert discover_trails() == []

    def test_every_script_lands_in_the_same_file(self):
        _make_trail("ci-check", [("a", "first")])
        _make_trail("claude-review", [("b", "second")])
        assert len(discover_trails()) == 1


class TestQueryFiltering:
    def test_filter_by_script(self):
        _make_trail("ci-check", [("a", "first")])
        _make_trail("pr-rebase", [("b", "second")])
        events = load_events(discover_trails())
        filtered = filter_events(events, script="ci-check")
        assert filtered
        assert all(e["script"] == "ci-check" for e in filtered)

    def test_filter_by_level(self):
        trail = Trail.start(script="test", context={})
        trail.info("ok", "fine")
        trail.error("bad", "broken")
        trail.finish()
        events = load_events(discover_trails())
        filtered = filter_events(events, level="error")
        assert filtered
        assert all(e["level"] == "error" for e in filtered)

    def test_filter_by_invocation(self):
        inv1 = _make_trail("test", [("a", "first")])
        _make_trail("test", [("b", "second")])
        events = load_events(discover_trails())
        filtered = filter_events(events, invocation=inv1)
        assert filtered
        assert all(e["invocation"] == inv1 for e in filtered)

    def test_a_pre_cutover_narrow_invocation_still_resolves(self):
        """IDs minted before the width grew are 8 hex characters and live in the
        same root forever. The match is on the whole field, so both widths select
        their own run and neither one prefix-matches the other."""
        new_inv = _make_trail("test", [("a", "first")])
        _write_raw("legacy.jsonl", _raw_record(invocation=new_inv[:8]))

        events = load_events(discover_trails())
        old = filter_events(events, invocation=new_inv[:8])
        assert [e["script"] for e in old] == ["old-run"]
        assert all(e["script"] == "test" for e in
                   filter_events(events, invocation=new_inv))


class TestCommandCorrelation:
    """A user command spans several processes; the reader treats it as one."""

    def test_filter_by_root_selects_every_process_in_the_command(self):
        root, _, _ = _make_command(*_PR_REVIEW)
        _make_trail("pr", [("other", "unrelated run")])
        events = load_events(discover_trails())
        filtered = filter_events(events, root=root)
        assert {e["script"] for e in filtered} == set(_PR_REVIEW)

    def test_filter_by_invocation_still_selects_one_process(self):
        """The narrow question is still askable — `--root` is an addition."""
        _, child, _ = _make_command(*_PR_REVIEW)
        events = load_events(discover_trails())
        filtered = filter_events(events, invocation=child)
        assert {e["script"] for e in filtered} == {"claude-review"}

    def test_a_record_predating_the_root_field_is_its_own_command(self):
        """Every grouping goes through `_root_of`, so history written before the
        field existed still answers a root query — as a command of one."""
        inv = _make_trail("ci-check", [("a", "first")])
        events = load_events(discover_trails())
        assert all(e["script"] == "ci-check"
                   for e in filter_events(events, root=inv))

    def test_show_renders_the_whole_command_from_its_root(self, capsys):
        root, _, _ = _make_command(*_PR_REVIEW)
        otto_log.cmd_show(argparse.Namespace(
            invocation=root, only=False, json=False))
        out = capsys.readouterr().out
        assert "pr → claude-review → review-orchestrate" in out
        assert "review-orchestrate ran" in out

    def test_show_finds_the_command_from_a_child_id(self, capsys):
        """A user has one ID in hand and does not know which end it came from."""
        root, child, _ = _make_command(*_PR_REVIEW)
        otto_log.cmd_show(argparse.Namespace(
            invocation=child, only=False, json=False))
        out = capsys.readouterr().out
        assert f"Invocation {root}" in out
        assert "review-orchestrate ran" in out

    def test_show_reports_the_whole_commands_duration(self, capsys):
        """The root's own finish, not whichever child happened to end first."""
        root, _ = _make_command("pr", "claude-review")
        otto_log.cmd_show(argparse.Namespace(
            invocation=root, only=False, json=False))
        header = capsys.readouterr().out.splitlines()[0]
        assert re.search(r"\d+\.\d+s", header)

    def test_show_labels_each_event_with_the_script_that_wrote_it(self, capsys):
        root, _ = _make_command("pr", "claude-review")
        otto_log.cmd_show(argparse.Namespace(
            invocation=root, only=False, json=False))
        body = capsys.readouterr().out.splitlines()[3:]
        assert any("claude-review" in line for line in body)

    def test_a_single_process_command_keeps_the_unlabelled_layout(self, capsys):
        """Nothing to tell apart, so the column would be the same on every line."""
        inv = _make_trail("ci-check", [("fetch", "fetched")])
        otto_log.cmd_show(argparse.Namespace(
            invocation=inv, only=False, json=False))
        body = capsys.readouterr().out.splitlines()[3:]
        assert not any("ci-check" in line for line in body)

    def test_only_narrows_back_to_one_process(self, capsys):
        _, child, _ = _make_command(*_PR_REVIEW)
        otto_log.cmd_show(argparse.Namespace(
            invocation=child, only=True, json=False))
        out = capsys.readouterr().out
        assert f"Invocation {child}" in out
        assert "review-orchestrate ran" not in out

    def test_only_reports_that_processes_own_duration(self, capsys):
        """The header must not go looking for a finish under the root's ID when
        no event in the narrowed listing carries it."""
        _, child = _make_command("pr", "claude-review")
        otto_log.cmd_show(argparse.Namespace(
            invocation=child, only=True, json=False))
        header = capsys.readouterr().out.splitlines()[0]
        assert re.search(r"\d+\.\d+s", header)

    def test_list_prints_one_row_per_command(self, capsys):
        _make_command(*_PR_REVIEW)
        otto_log.cmd_list(argparse.Namespace(
            script=None, since=None, repo=None, json=True))
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1
        assert rows[0]["scripts"] == list(_PR_REVIEW)
        assert rows[0]["script"] == "pr"

    def test_list_counts_every_event_in_the_command(self, capsys):
        root, _ = _make_command("pr", "claude-review")
        otto_log.cmd_list(argparse.Namespace(
            script=None, since=None, repo=None, json=True))
        row = json.loads(capsys.readouterr().out.splitlines()[0])
        assert row["invocation"] == root
        # Two `work` events and two `finish`es, from the two processes.
        assert row["event_count"] == 4

    def test_list_by_script_reports_the_commands_that_reached_it(self, capsys):
        """Filtering on an inner script still yields whole commands: the row is
        the `pr review` that got there, not the fragment one process logged."""
        root, _, _ = _make_command(*_PR_REVIEW)
        _make_trail("ci-check", [("a", "unrelated")])
        otto_log.cmd_list(argparse.Namespace(
            script="review-orchestrate", since=None, repo=None, json=True))
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1
        assert rows[0]["invocation"] == root
        assert rows[0]["scripts"] == list(_PR_REVIEW)

    def test_list_keeps_a_command_that_started_before_the_window(self, capsys):
        """A window selects commands, not events.

        A `pr review` that has been running an hour began before `--since 1h`,
        and half its timeline answers no question anyone asks of it — so the
        row is the whole command, stamped with when it really started.
        """
        now = datetime.now(timezone.utc)
        started = now - timedelta(hours=10)
        _write_raw(
            f"{now:%Y-%m}.jsonl",
            _raw_record(ts=f"{started:%Y-%m-%dT%H:%M:%SZ}",
                        invocation="aaaa", script="pr", action="dispatch"),
            _raw_record(ts=f"{now - timedelta(minutes=5):%Y-%m-%dT%H:%M:%SZ}",
                        invocation="bbbb", root="aaaa",
                        script="claude-review", action="work"),
        )

        otto_log.cmd_list(argparse.Namespace(
            script=None, since="1h", repo=None, json=True))
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1
        assert rows[0]["scripts"] == ["pr", "claude-review"]
        assert rows[0]["ts"].startswith(f"{started:%Y-%m-%dT%H}")

    def test_list_names_the_command_by_its_outermost_script(self, capsys):
        _make_command(*_PR_REVIEW)
        otto_log.cmd_list(argparse.Namespace(
            script=None, since=None, repo=None, json=False))
        assert "pr +2" in capsys.readouterr().out


class TestSinceSkipsFilesByName:
    def _write(self, name: str, script: str):
        _write_raw(name, _raw_record(script=script))

    def test_drops_a_month_below_the_cutoff(self):
        self._write("2026-01.jsonl", "old")
        self._write("2026-08.jsonl", "new")
        names = [p.name for p in discover_trails(
            since=datetime(2026, 8, 1, tzinfo=timezone.utc))]
        assert names == ["2026-08.jsonl"]

    def test_always_reads_a_stem_that_is_not_a_month(self):
        """`legacy.jsonl` holds every pre-cutover record; its stem names no month."""
        self._write("legacy.jsonl", "carried")
        self._write("2026-01.jsonl", "old")
        names = [p.name for p in discover_trails(
            since=datetime(2026, 8, 1, tzinfo=timezone.utc))]
        assert names == ["legacy.jsonl"]

    def test_no_cutoff_reads_everything(self):
        self._write("2026-01.jsonl", "old")
        self._write("legacy.jsonl", "carried")
        assert len(discover_trails()) == 2


class TestRepoScoping:
    def _trail(self, repo: str):
        trail = Trail.start(script="pr", context={"repo": repo, "pr": 1})
        trail.info("act", "did")
        trail.finish()

    def test_recent_narrows_to_one_repo(self, capsys):
        self._trail("org/alpha")
        self._trail("org/beta")
        otto_log.cmd_recent(argparse.Namespace(since="1d", repo="org/alpha", json=True))
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows
        assert all(r["context"]["repo"] == "org/alpha" for r in rows)

    def test_list_narrows_to_one_repo(self, capsys):
        self._trail("org/alpha")
        self._trail("org/beta")
        otto_log.cmd_list(argparse.Namespace(
            script=None, since=None, repo="org/alpha", json=True))
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(rows) == 1


# ── stats ─────────────────────────────────────────────────────────────────────


def _usage(**overrides):
    rec = {
        "ts": "2026-08-20T12:00:00Z", "script": "pr", "entry_point": "prompt",
        "backend": "claude", "model": None, "cost": 1.0, "input_tokens": 100,
        "output_tokens": 10, "cache_read_tokens": 900, "cache_write_tokens": 0,
        "duration_ms": 1000, "exit_code": 0,
    }
    rec.update(overrides)
    return rec


def _by_group(rows):
    return {r.group: r for r in rows}


class TestStatsAggregation:
    def test_groups_by_script(self):
        rows = otto_log.aggregate_usage(
            [_usage(script="pr", cost=1.0), _usage(script="pr", cost=2.0),
             _usage(script="ci-check", cost=0.5)],
            by="script",
        )
        groups = _by_group(rows)
        assert groups["pr"].calls == 2
        assert groups["pr"].cost == pytest.approx(3.0)
        assert groups["ci-check"].calls == 1

    def test_sorts_by_cost_descending(self):
        rows = otto_log.aggregate_usage(
            [_usage(script="cheap", cost=0.1), _usage(script="pricey", cost=9.0)],
            by="script",
        )
        assert [r.group for r in rows] == ["pricey", "cheap"]

    def test_groups_by_task(self):
        rows = otto_log.aggregate_usage(
            [_usage(task="pr-review"), _usage(task="pr-review"), _usage(task="ci-fix")],
            by="task",
        )
        assert _by_group(rows)["pr-review"].calls == 2

    def test_records_without_the_group_field_land_in_one_bucket(self):
        rows = otto_log.aggregate_usage([_usage(), _usage()], by="task")
        assert len(rows) == 1
        assert rows[0].calls == 2

    def test_groups_by_day_chronologically(self):
        rows = otto_log.aggregate_usage(
            [_usage(ts="2026-08-20T01:00:00Z"), _usage(ts="2026-08-18T01:00:00Z")],
            by="day",
        )
        assert [r.group for r in rows] == ["2026-08-18", "2026-08-20"]

    def test_by_model_splits_cost_across_models(self):
        rows = otto_log.aggregate_usage(
            [_usage(cost=3.0, cost_by_model={"opus-5": 2.0, "haiku-4-5": 1.0})],
            by="model",
        )
        groups = _by_group(rows)
        assert groups["opus-5"].cost == pytest.approx(2.0)
        assert groups["haiku-4-5"].cost == pytest.approx(1.0)

    def test_by_model_leaves_tokens_unattributed(self):
        """The CLI reports cost per model but tokens per session — don't invent a split."""
        rows = otto_log.aggregate_usage(
            [_usage(cost_by_model={"opus-5": 1.0})], by="model",
        )
        assert rows[0].billed_input is None
        assert rows[0].cache_read_ratio is None

    def test_by_model_falls_back_to_requested_model(self):
        rows = otto_log.aggregate_usage([_usage(model="sonnet-5")], by="model")
        assert rows[0].group == "sonnet-5"

    def test_billed_input_sums_input_and_cache(self):
        rows = otto_log.aggregate_usage(
            [_usage(input_tokens=100, cache_read_tokens=900, cache_write_tokens=50)],
            by="script",
        )
        assert rows[0].billed_input == 1050
        assert rows[0].cache_read_ratio == pytest.approx(900 / 1050)

    def test_median_duration_ignores_unmeasured_calls(self):
        rows = otto_log.aggregate_usage(
            [_usage(duration_ms=1000), _usage(duration_ms=3000), _usage(duration_ms=0)],
            by="script",
        )
        assert rows[0].median_duration_ms == 2000

    def test_median_duration_is_none_when_nothing_measured(self):
        rows = otto_log.aggregate_usage([_usage(duration_ms=0)], by="script")
        assert rows[0].median_duration_ms is None


class TestStatsTable:
    def _rows(self):
        return otto_log.aggregate_usage(
            [_usage(script="pr", cost=1.5), _usage(script="a-much-longer-name", cost=0.5)],
            by="script",
        )

    def test_the_total_row_sums_the_groups(self):
        assert "$2.0000" in otto_log.format_stats_table(self._rows()).splitlines()[-1]

    def test_columns_line_up_across_rows(self):
        """Every cell is padded to its column's width, so the body lines match.

        The header is excluded because its bold escape adds invisible bytes.
        """
        body = otto_log.format_stats_table(self._rows()).splitlines()[1:]
        assert len({len(line) for line in body}) == 1


class TestStatsCommand:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
        d = tmp_path / ai_usage.LEDGER_DIRNAME
        d.mkdir()
        return d

    def _write(self, ledger, *records):
        body = "".join(json.dumps(r) + "\n" for r in records)
        (ledger / "2026-08.jsonl").write_text(body)

    def _run(self, monkeypatch, since="7d", by="script", as_json=False):
        """Pin 'now' so fixture timestamps stay inside the window."""
        monkeypatch.setattr(
            otto_log, "_parse_since", lambda _: datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        otto_log.cmd_stats(argparse.Namespace(since=since, by=by, json=as_json))

    def test_prints_a_row_per_group(self, ledger, monkeypatch, capsys):
        self._write(ledger, _usage(script="pr", cost=1.5), _usage(script="ci-check"))
        self._run(monkeypatch)
        out = capsys.readouterr().out
        assert "pr" in out
        assert "ci-check" in out

    def test_json_emits_one_object_per_group(self, ledger, monkeypatch, capsys):
        self._write(ledger, _usage(script="pr", cost=1.5))
        self._run(monkeypatch, as_json=True)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows[0]["group"] == "pr"
        assert rows[0]["cost"] == pytest.approx(1.5)

    def test_json_keys_are_the_stable_wire_format(self, ledger, monkeypatch, capsys):
        """The row is serialized field-by-field, so its declaration order is the schema.

        Consumers read these keys; a rename or a reorder is a break they see.
        """
        self._write(ledger, _usage(script="pr"))
        self._run(monkeypatch, as_json=True)
        row = json.loads(capsys.readouterr().out.splitlines()[0])
        assert list(row) == [
            "group", "calls", "cost", "billed_input", "output_tokens",
            "cache_read_tokens", "cache_read_ratio", "median_duration_ms",
        ]

    def test_empty_ledger_says_so(self, ledger, monkeypatch, capsys):
        self._run(monkeypatch)
        assert "No AI usage recorded" in capsys.readouterr().out

    def test_since_excludes_older_records(self, ledger, monkeypatch, capsys):
        self._write(
            ledger,
            _usage(script="stale", ts="2026-08-01T00:00:00Z"),
            _usage(script="fresh", ts="2026-08-20T00:00:00Z"),
        )
        monkeypatch.setattr(
            otto_log, "_parse_since", lambda _: datetime(2026, 8, 15, tzinfo=timezone.utc),
        )
        otto_log.cmd_stats(argparse.Namespace(since="7d", by="script", json=True))
        out = capsys.readouterr().out
        assert "fresh" in out
        assert "stale" not in out


class TestPruneCommand:
    def _write(self, name: str):
        root = workbench_paths.trail_dir()
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_text('{"action":"old"}\n')
        return path

    def _run(self, keep):
        otto_log.cmd_prune(argparse.Namespace(keep=keep))

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
        self._run(trail_module.TRAIL_KEEP_MONTHS)
        assert "nothing older" in capsys.readouterr().out


class TestPrune:
    def test_it_names_a_swept_artifact_month_under_its_directory(self, capsys):
        stem = "2020-01"
        month = trail_module.artifacts_dir() / stem
        month.mkdir(parents=True)
        (month / "aaaaaaaaaaaa-1-push.log").write_text("old\n")

        otto_log.cmd_prune(argparse.Namespace(keep=1))

        assert f"artifacts/{stem}" in capsys.readouterr().out


class TestSummaryIsNotAlwaysFinish:
    def test_show_reports_the_runs_duration(self, capsys):
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.info("act", "did")
        trail.finish()
        otto_log.cmd_show(argparse.Namespace(
            invocation=trail.invocation, only=False, json=False))
        # A duration, not merely a line that happens to end in "s" — the point of
        # the test is that `finish` is found and its duration_ms rendered.
        assert re.search(r"\d+\.\d+s", capsys.readouterr().out)

    def test_show_survives_a_summary_with_no_duration(self, capsys):
        """A terminal `pr_outcome` event carries no duration and must not raise."""
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.summary("pr_outcome", "org/repo#7 merged", data={"outcome": "MERGED"})
        otto_log.cmd_show(argparse.Namespace(
            invocation=trail.invocation, only=False, json=False))
        assert "pr_outcome" in capsys.readouterr().out

    def test_list_survives_a_summary_with_no_duration(self, capsys):
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.summary("pr_outcome", "org/repo#7 merged")
        otto_log.cmd_list(argparse.Namespace(
            script=None, since=None, repo=None, json=True))
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
            _raw_event(ts="2026-09-01T00:00:00Z", invocation="aaaaaaaaaaaa"),
            _finish(ts="2026-09-01T00:00:07Z", invocation="aaaaaaaaaaaa",
                        duration_ms=7000),
        ]

        assert otto_log._command_duration_ms(events, "aaaaaaaaaaaa") == 7000

    def test_a_long_run_that_logged_late_keeps_its_measured_duration(self):
        """`duration_ms` runs from process start, the first event from whenever it
        was written. A 22-minute run that wrote both records in its last second
        is real history, and the span between its events is not its duration."""
        events = [
            _raw_event(ts="2026-09-09T00:25:17Z", invocation="bbbbbbbbbbbb",
                        action="unexpected_error"),
            _finish(ts="2026-09-09T00:25:17Z", invocation="bbbbbbbbbbbb",
                        duration_ms=1368728),
        ]

        assert otto_log._command_duration_ms(events, "bbbbbbbbbbbb") == 1368728

    def test_a_record_after_the_root_exits_extends_the_command(self):
        """The dream case: a scan, then an agent's phases minutes later."""
        events = [
            _raw_event(ts="2026-09-01T00:00:00Z", script="dream-scan",
                        invocation="cccccccccccc"),
            _finish(ts="2026-09-01T00:00:02Z", script="dream-scan",
                        invocation="cccccccccccc", duration_ms=2000),
            _raw_event(ts="2026-09-01T00:05:02Z", script="dream",
                        invocation="dddddddddddd", root="cccccccccccc",
                        action="consolidate"),
        ]

        # Five minutes of agent work after a two-second scan, not two seconds.
        assert otto_log._command_duration_ms(events, "cccccccccccc") == 302000

    def test_a_trailing_record_never_shortens_a_run(self):
        """A later process whose clock reads behind the root's cannot subtract
        from a duration the root measured."""
        events = [
            _raw_event(ts="2026-09-01T00:00:10Z", invocation="eeeeeeeeeeee"),
            _finish(ts="2026-09-01T00:00:10Z", invocation="eeeeeeeeeeee",
                        duration_ms=10000),
            _raw_event(ts="2026-09-01T00:00:05Z", invocation="ffffffffffff",
                        root="eeeeeeeeeeee"),
        ]

        assert otto_log._command_duration_ms(events, "eeeeeeeeeeee") == 10000

    def test_a_killed_run_reports_nothing_rather_than_a_span(self):
        """No finish survives, so the duration is unknown — and a span between
        whatever records it managed to write would be a guess."""
        events = [
            _raw_event(ts="2026-09-01T00:00:00Z", invocation="gggggggggggg"),
            _raw_event(ts="2026-09-01T00:04:00Z", invocation="gggggggggggg"),
        ]

        assert otto_log._command_duration_ms(events, "gggggggggggg") is None

    def test_show_reports_the_whole_command_not_just_its_root(self, capsys):
        _write_raw(
            "2026-09.jsonl",
            _raw_record(ts="2026-09-01T00:00:00Z", script="dream-scan",
                        invocation="cccccccccccc"),
            _raw_finish(ts="2026-09-01T00:00:02Z", script="dream-scan",
                        invocation="cccccccccccc", duration_ms=2000),
            _raw_record(ts="2026-09-01T00:05:02Z", script="dream",
                        invocation="dddddddddddd", root="cccccccccccc",
                        action="consolidate"),
        )

        otto_log.cmd_show(argparse.Namespace(
            invocation="cccccccccccc", only=False, json=False))

        assert "302.0s" in capsys.readouterr().out


class TestParseSince:
    """The one window parser, in both of the modes its callers need.

    A glance (`otto-log --since`) prefers the last hour to an error; a scan
    parameter (`retro-scan --since`) prefers the error, because silently
    narrowing the window makes a report claim there was nothing to find.
    """

    @pytest.mark.parametrize("window,seconds", [
        ("30m", 1800), ("24h", 86400), ("7d", 604800),
    ])
    def test_each_unit_names_its_own_span(self, window, seconds):
        before = datetime.now(timezone.utc)
        cutoff = parse_since(window)
        assert abs((before - cutoff).total_seconds() - seconds) < 60

    def test_an_unreadable_window_falls_back_to_the_hour(self):
        before = datetime.now(timezone.utc)
        cutoff = parse_since("garbage")
        assert abs((before - cutoff) - FALLBACK_WINDOW).total_seconds() < 60

    def test_strict_refuses_an_unreadable_window_instead(self):
        with pytest.raises(SystemExit):
            parse_since("garbage", strict=True)

    def test_strict_refuses_a_count_that_is_not_a_number(self):
        with pytest.raises(SystemExit):
            parse_since("xd", strict=True)


class TestLogPointerRendering:
    def test_an_event_with_an_artifact_names_it_absolutely(self):
        event = {"ts": "2026-09-04T20:07:24Z", "level": "error",
                 "event_type": "error", "action": "push", "detail": "refused",
                 "data": {"log": "artifacts/2026-09/214e9758c739-1-push.log"}}

        rendered = otto_log._format_event_line(event)

        expected = workbench_paths.trail_dir() / "artifacts/2026-09/214e9758c739-1-push.log"
        assert f"log: {expected}" in rendered

    def test_an_event_without_one_renders_unchanged(self):
        event = {"ts": "2026-09-04T20:07:24Z", "level": "info",
                 "event_type": "action", "action": "push", "detail": "pushed"}

        assert "log:" not in otto_log._format_event_line(event)

    def test_the_duration_stays_on_the_header_line(self):
        """The pointer goes last, so a run's timing is not pushed onto its own
        line by an artifact that arrived after it."""
        event = {"ts": "2026-09-04T20:07:24Z", "level": "error",
                 "event_type": "error", "action": "push", "detail": "refused",
                 "duration_ms": 42, "data": {"log": "artifacts/2026-09/a-1-push.log"}}

        header, pointer = otto_log._format_event_line(event).split("\n")
        assert "(42ms)" in header
        assert "log:" in pointer
