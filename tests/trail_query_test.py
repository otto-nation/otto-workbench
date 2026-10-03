"""Tests for the trail read path — `core.trail_query`."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import core.workbench_paths  # noqa: E402
from core.trail import Trail  # noqa: E402
from core.trail_query import (  # noqa: E402
    FALLBACK_WINDOW,
    discover_trails,
    filter_events,
    load_events,
    parse_since,
)
from trail_support import (  # noqa: E402
    PR_REVIEW,
    make_command,
    make_trail,
    raw_record,
    write_raw,
)


class TestTrailDiscovery:
    def test_finds_the_month_file_every_writer_appends_to(self):
        make_trail("ci-check", [("fetch", "fetched")])
        trails = discover_trails()
        assert len(trails) == 1
        assert trails[0].parent == core.workbench_paths.trail_dir()

    def test_an_empty_root_has_no_trails(self):
        assert discover_trails() == []

    def test_every_script_lands_in_the_same_file(self):
        make_trail("ci-check", [("a", "first")])
        make_trail("review", [("b", "second")])
        assert len(discover_trails()) == 1


class TestQueryFiltering:
    def test_filter_by_script(self):
        make_trail("ci-check", [("a", "first")])
        make_trail("pr-rebase", [("b", "second")])
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
        inv1 = make_trail("test", [("a", "first")])
        make_trail("test", [("b", "second")])
        events = load_events(discover_trails())
        filtered = filter_events(events, invocation=inv1)
        assert filtered
        assert all(e["invocation"] == inv1 for e in filtered)

    def test_a_pre_cutover_narrow_invocation_still_resolves(self):
        """IDs minted before the width grew are 8 hex characters and live in the
        same root forever. The match is on the whole field, so both widths select
        their own run and neither one prefix-matches the other."""
        new_inv = make_trail("test", [("a", "first")])
        write_raw("legacy.jsonl", raw_record(invocation=new_inv[:8]))

        events = load_events(discover_trails())
        old = filter_events(events, invocation=new_inv[:8])
        assert [e["script"] for e in old] == ["old-run"]
        assert all(e["script"] == "test" for e in
                   filter_events(events, invocation=new_inv))


class TestCommandCorrelation:
    """A user command spans several processes; the reader treats it as one."""

    def test_filter_by_root_selects_every_process_in_the_command(self):
        root, _, _ = make_command(*PR_REVIEW)
        make_trail("pr", [("other", "unrelated run")])
        events = load_events(discover_trails())
        filtered = filter_events(events, root=root)
        assert {e["script"] for e in filtered} == set(PR_REVIEW)

    def test_filter_by_invocation_still_selects_one_process(self):
        """The narrow question is still askable — `--root` is an addition."""
        _, child, _ = make_command(*PR_REVIEW)
        events = load_events(discover_trails())
        filtered = filter_events(events, invocation=child)
        assert {e["script"] for e in filtered} == {"review"}

    def test_a_record_predating_the_root_field_is_its_own_command(self):
        """Every grouping goes through `root_of`, so history written before the
        field existed still answers a root query — as a command of one."""
        inv = make_trail("ci-check", [("a", "first")])
        events = load_events(discover_trails())
        assert all(e["script"] == "ci-check"
                   for e in filter_events(events, root=inv))


class TestSinceSkipsFilesByName:
    def _write(self, name: str, script: str):
        write_raw(name, raw_record(script=script))

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
