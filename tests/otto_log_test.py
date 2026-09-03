"""Tests for otto-log query CLI."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import load_script

BIN_DIR = Path(__file__).resolve().parent.parent / "ai" / "bin"
LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(BIN_DIR))

import ai_usage
import trail as trail_module
import workbench_paths
from trail import Trail

otto_log = load_script("otto_log", BIN_DIR / "otto-log")


def _make_trail(script: str, events: list[tuple[str, str]]) -> str:
    """Write a trail with the given action/detail pairs, return invocation ID."""
    trail = Trail.start(script=script, context={"repo": "org/repo", "pr": 42})
    for action, detail in events:
        trail.info(action, detail)
    trail.finish()
    return trail.invocation


class TestTrailDiscovery:
    def test_finds_the_month_file_every_writer_appends_to(self):
        _make_trail("ci-check", [("fetch", "fetched")])
        trails = otto_log.discover_trails()
        assert len(trails) == 1
        assert trails[0].parent == workbench_paths.trail_dir()

    def test_an_empty_root_has_no_trails(self):
        assert otto_log.discover_trails() == []

    def test_every_script_lands_in_the_same_file(self):
        _make_trail("ci-check", [("a", "first")])
        _make_trail("claude-review", [("b", "second")])
        assert len(otto_log.discover_trails()) == 1


class TestQueryFiltering:
    def test_filter_by_script(self):
        _make_trail("ci-check", [("a", "first")])
        _make_trail("pr-rebase", [("b", "second")])
        events = otto_log.load_events(otto_log.discover_trails())
        filtered = otto_log.filter_events(events, script="ci-check")
        assert filtered
        assert all(e["script"] == "ci-check" for e in filtered)

    def test_filter_by_level(self):
        trail = Trail.start(script="test", context={})
        trail.info("ok", "fine")
        trail.error("bad", "broken")
        trail.finish()
        events = otto_log.load_events(otto_log.discover_trails())
        filtered = otto_log.filter_events(events, level="error")
        assert filtered
        assert all(e["level"] == "error" for e in filtered)

    def test_filter_by_invocation(self):
        inv1 = _make_trail("test", [("a", "first")])
        _make_trail("test", [("b", "second")])
        events = otto_log.load_events(otto_log.discover_trails())
        filtered = otto_log.filter_events(events, invocation=inv1)
        assert filtered
        assert all(e["invocation"] == inv1 for e in filtered)

    def test_a_pre_cutover_narrow_invocation_still_resolves(self):
        """IDs minted before the width grew are 8 hex characters and live in the
        same root forever. The match is on the whole field, so both widths select
        their own run and neither one prefix-matches the other."""
        new_inv = _make_trail("test", [("a", "first")])
        root = workbench_paths.trail_dir()
        root.mkdir(parents=True, exist_ok=True)
        (root / "legacy.jsonl").write_text(json.dumps({
            "ts": "2026-01-01T00:00:00Z", "script": "old-run",
            "invocation": new_inv[:8], "level": "info", "event_type": "action",
            "action": "x", "detail": "", "context": {},
        }) + "\n")

        events = otto_log.load_events(otto_log.discover_trails())
        old = otto_log.filter_events(events, invocation=new_inv[:8])
        assert [e["script"] for e in old] == ["old-run"]
        assert all(e["script"] == "test" for e in
                   otto_log.filter_events(events, invocation=new_inv))


class TestSinceSkipsFilesByName:
    def _write(self, name: str, script: str):
        root = workbench_paths.trail_dir()
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps({
            "ts": "2026-01-01T00:00:00Z", "script": script, "invocation": "a1b2c3d4",
            "level": "info", "event_type": "action", "action": "x", "detail": "",
            "context": {},
        }) + "\n")

    def test_drops_a_month_below_the_cutoff(self):
        self._write("2026-01.jsonl", "old")
        self._write("2026-08.jsonl", "new")
        names = [p.name for p in otto_log.discover_trails(
            since=datetime(2026, 8, 1, tzinfo=timezone.utc))]
        assert names == ["2026-08.jsonl"]

    def test_always_reads_a_stem_that_is_not_a_month(self):
        """`legacy.jsonl` holds every pre-cutover record; its stem names no month."""
        self._write("legacy.jsonl", "carried")
        self._write("2026-01.jsonl", "old")
        names = [p.name for p in otto_log.discover_trails(
            since=datetime(2026, 8, 1, tzinfo=timezone.utc))]
        assert names == ["legacy.jsonl"]

    def test_no_cutoff_reads_everything(self):
        self._write("2026-01.jsonl", "old")
        self._write("legacy.jsonl", "carried")
        assert len(otto_log.discover_trails()) == 2


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


class TestSummaryIsNotAlwaysFinish:
    def test_show_reports_the_runs_duration(self, capsys):
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.info("act", "did")
        trail.finish()
        otto_log.cmd_show(argparse.Namespace(invocation=trail.invocation, json=False))
        # A duration, not merely a line that happens to end in "s" — the point of
        # the test is that `finish` is found and its duration_ms rendered.
        assert re.search(r"\d+\.\d+s", capsys.readouterr().out)

    def test_show_survives_a_summary_with_no_duration(self, capsys):
        """A terminal `pr_outcome` event carries no duration and must not raise."""
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.summary("pr_outcome", "org/repo#7 merged", data={"outcome": "MERGED"})
        otto_log.cmd_show(argparse.Namespace(invocation=trail.invocation, json=False))
        assert "pr_outcome" in capsys.readouterr().out

    def test_list_survives_a_summary_with_no_duration(self, capsys):
        trail = Trail.start(script="pr", context={"repo": "org/repo"})
        trail.summary("pr_outcome", "org/repo#7 merged")
        otto_log.cmd_list(argparse.Namespace(
            script=None, since=None, repo=None, json=True))
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows[0]["duration_ms"] is None
