"""Tests for `otto-log stats` — `agent.usage_stats`."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import agent.usage
import agent.usage_stats
import core.trail_query


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
        rows = agent.usage_stats.aggregate_usage(
            [_usage(script="pr", cost=1.0), _usage(script="pr", cost=2.0),
             _usage(script="ci-check", cost=0.5)],
            by="script",
        )
        groups = _by_group(rows)
        assert groups["pr"].calls == 2
        assert groups["pr"].cost == pytest.approx(3.0)
        assert groups["ci-check"].calls == 1

    def test_sorts_by_cost_descending(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(script="cheap", cost=0.1), _usage(script="pricey", cost=9.0)],
            by="script",
        )
        assert [r.group for r in rows] == ["pricey", "cheap"]

    def test_groups_by_task(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(task="pr-review"), _usage(task="pr-review"), _usage(task="ci-fix")],
            by="task",
        )
        assert _by_group(rows)["pr-review"].calls == 2

    def test_records_without_the_group_field_land_in_one_bucket(self):
        rows = agent.usage_stats.aggregate_usage([_usage(), _usage()], by="task")
        assert len(rows) == 1
        assert rows[0].calls == 2

    # passes-at-base: aggregate_usage groups by any key; what this change adds is phase as an accepted choice, pinned by the test below
    def test_groups_by_phase(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(phase="fix"), _usage(phase="fix"), _usage(phase="group")],
            by="phase",
        )
        assert _by_group(rows)["fix"].calls == 2

    def test_the_turn_grouping_is_one_the_cli_accepts(self):
        # `stats_columns` compares `--by` against this literal, so a value
        # missing from STATS_GROUPINGS is unreachable and the turn columns
        # would never render — silently, since every other grouping is
        # supposed to omit them. Held here rather than by a module-level
        # `assert`, which `python -O` removes.
        assert agent.usage_stats._TURN_GROUPING in agent.usage_stats.STATS_GROUPINGS

    def test_phase_is_a_grouping_the_cli_will_accept(self):
        # `aggregate_usage` groups by whatever key it is handed, so the test
        # above passed before `phase` was a choice anyone could pass — the
        # argument parser rejected it and the function was never reached.
        # This is the half that was actually missing.
        assert "phase" in agent.usage_stats.STATS_GROUPINGS

    def test_groups_by_day_chronologically(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(ts="2026-08-20T01:00:00Z"), _usage(ts="2026-08-18T01:00:00Z")],
            by="day",
        )
        assert [r.group for r in rows] == ["2026-08-18", "2026-08-20"]

    def test_turn_percentiles_come_from_what_runs_actually_spent(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(phase="fix", num_turns=n) for n in (5, 10, 40)], by="phase",
        )
        row = _by_group(rows)["fix"]
        # Exact values: a percentile is one of the readings, so an assertion
        # that only bounded it would pass for an interpolated 27.5 — a turn
        # count no run took.
        assert row.p50_turns == 10
        assert row.p95_turns == 40

    def test_a_group_whose_records_carry_no_cap_reports_no_ratio(self):
        """Unknown is not zero, and this is the day-one state of the ledger.

        Every record written before the budget became its own key has turns
        and no cap. Reporting those as 0.0 would show every phase as never
        reaching its cap — a confident answer drawn from records that cannot
        support one, and the reading a person would act on.
        """
        rows = agent.usage_stats.aggregate_usage(
            [_usage(phase="fix", num_turns=40)], by="phase",
        )
        assert _by_group(rows)["fix"].at_cap_ratio is None

    def test_the_cap_ratio_counts_only_the_runs_that_declared_one(self):
        rows = agent.usage_stats.aggregate_usage([
            _usage(phase="fix", num_turns=20, max_turns=20),
            _usage(phase="fix", num_turns=20, max_turns=20),
            _usage(phase="fix", num_turns=5, max_turns=20),
            # No cap declared: it contributes to the distribution and must not
            # dilute the ratio, which would otherwise read 2/4.
            _usage(phase="fix", num_turns=5),
        ], by="phase")
        assert _by_group(rows)["fix"].at_cap_ratio == pytest.approx(2 / 3)

    def test_a_run_that_reported_no_turns_is_not_counted_as_zero(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(phase="fix", num_turns=30), _usage(phase="fix")], by="phase",
        )
        # A record with no turn count says nothing about turns. Reading it as
        # zero would halve every median in the table.
        assert _by_group(rows)["fix"].p50_turns == 30

    def test_by_model_splits_cost_across_models(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(cost=3.0, cost_by_model={"opus-5": 2.0, "haiku-4-5": 1.0})],
            by="model",
        )
        groups = _by_group(rows)
        assert groups["opus-5"].cost == pytest.approx(2.0)
        assert groups["haiku-4-5"].cost == pytest.approx(1.0)

    def test_by_model_leaves_tokens_unattributed(self):
        """The CLI reports cost per model but tokens per session — don't invent a split."""
        rows = agent.usage_stats.aggregate_usage(
            [_usage(cost_by_model={"opus-5": 1.0})], by="model",
        )
        assert rows[0].billed_input is None
        assert rows[0].cache_read_ratio is None

    def test_by_model_falls_back_to_requested_model(self):
        rows = agent.usage_stats.aggregate_usage([_usage(model="sonnet-5")], by="model")
        assert rows[0].group == "sonnet-5"

    def test_billed_input_sums_input_and_cache(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(input_tokens=100, cache_read_tokens=900, cache_write_tokens=50)],
            by="script",
        )
        assert rows[0].billed_input == 1050
        assert rows[0].cache_read_ratio == pytest.approx(900 / 1050)

    def test_median_duration_ignores_unmeasured_calls(self):
        rows = agent.usage_stats.aggregate_usage(
            [_usage(duration_ms=1000), _usage(duration_ms=3000), _usage(duration_ms=0)],
            by="script",
        )
        assert rows[0].median_duration_ms == 2000

    def test_median_duration_is_none_when_nothing_measured(self):
        rows = agent.usage_stats.aggregate_usage([_usage(duration_ms=0)], by="script")
        assert rows[0].median_duration_ms is None


class TestStatsTable:
    def _rows(self):
        return agent.usage_stats.aggregate_usage(
            [_usage(script="pr", cost=1.5), _usage(script="a-much-longer-name", cost=0.5)],
            by="script",
        )

    def test_the_total_row_sums_the_groups(self):
        assert "$2.0000" in agent.usage_stats.format_stats_table(self._rows()).splitlines()[-1]

    def test_columns_line_up_across_rows(self):
        """Every cell is padded to its column's width, so the body lines match.

        The header is excluded because its bold escape adds invisible bytes.
        """
        body = agent.usage_stats.format_stats_table(self._rows()).splitlines()[1:]
        assert len({len(line) for line in body}) == 1

    def _phase_rows(self):
        return agent.usage_stats.aggregate_usage(
            [_usage(phase="fix", num_turns=20, max_turns=20)], by="phase",
        )

    def test_the_turn_columns_appear_under_by_phase(self):
        header = agent.usage_stats.format_stats_table(self._phase_rows(), "phase")
        assert "P95 TURNS" in header
        assert "AT CAP" in header

    def test_the_turn_columns_stay_out_of_the_other_groupings(self):
        table = agent.usage_stats.format_stats_table(self._rows(), "script")
        assert "P95 TURNS" not in table
        assert "AT CAP" not in table

    def test_phase_rows_line_up_across_the_wider_table(self):
        # The render loop zips cells against columns, and zip truncates in
        # silence: pairing the wider rows against the narrower column tuple
        # dropped the three new columns from the output while every other
        # test passed.
        body = agent.usage_stats.format_stats_table(self._phase_rows(), "phase").splitlines()
        assert len({len(line) for line in body[1:]}) == 1
        assert body[0].count("TURNS") == 2


class TestStatsCommand:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
        d = tmp_path / agent.usage.LEDGER_DIRNAME
        d.mkdir()
        return d

    def _write(self, ledger, *records):
        body = "".join(json.dumps(r) + "\n" for r in records)
        (ledger / "2026-08.jsonl").write_text(body)

    def _run(self, monkeypatch, since="7d", by="script", as_json=False):
        """Pin 'now' so fixture timestamps stay inside the window."""
        monkeypatch.setattr(
            core.trail_query, "parse_since", lambda _: datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        agent.usage_stats.stats(since, by, as_json=as_json)

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
        # The turn fields are appended, which is the one change this schema
        # tolerates: a consumer reading by key is unaffected, and the eight
        # before them are still in their original positions.
        assert list(row) == [
            "group", "calls", "cost", "billed_input", "output_tokens",
            "cache_read_tokens", "cache_read_ratio", "median_duration_ms",
            "p50_turns", "p95_turns", "at_cap_ratio",
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
            core.trail_query, "parse_since", lambda _: datetime(2026, 8, 15, tzinfo=timezone.utc),
        )
        agent.usage_stats.stats("7d", "script", as_json=True)
        out = capsys.readouterr().out
        assert "fresh" in out
        assert "stale" not in out
