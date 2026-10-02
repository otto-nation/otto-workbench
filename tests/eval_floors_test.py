"""High-water floor ratchet: comparison, save refusal, and the validator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from eval.floors import (
    FloorBreach,
    FloorRecord,
    FloorSet,
    compare_against_floors,
    format_floor_breaches,
    load_prior_entry_names,
    raise_floors,
    seed_floors,
    validate_floors_document,
)
from eval.scoring import MIN_RECALL_TOLERANCE_RUNS, entry_recall_tolerance

from eval_floors_support import STEM, MODEL, INCIDENT_ENTRY


def _record(floor: float, best: float | None = None, lowered: str | None = None) -> FloorRecord:
    return FloorRecord(floor=floor, best=best if best is not None else floor, lowered=lowered)


def _floors(entry: str, floor: float, **metrics: FloorRecord) -> FloorSet:
    recs = {"recall_mean": _record(floor), **metrics}
    return FloorSet(1, {STEM: {entry: recs}})


def _floors_with_fillers(entry: str, floor: float, n: int = 12) -> FloorSet:
    recorded = {entry: {"recall_mean": _record(floor)}}
    for i in range(n - 1):
        recorded[f"fill-{i:02d}"] = {"recall_mean": _record(1.0)}
    return FloorSet(1, {STEM: recorded})


def _baseline(entry: str, recall: float, extra_entries: dict | None = None) -> dict:
    entries = {
        entry: {
            "recall_mean": recall,
            "precision_mean": 1.0,
            "recall_std": 0.0,
        },
    }
    if extra_entries:
        entries.update(extra_entries)
    return {"backend": "claude", "model": MODEL, "entries": entries}


def _baseline_with_fillers(entry: str, recall: float, n: int = 12) -> dict:
    extra = {
        f"fill-{i:02d}": {"recall_mean": 1.0, "precision_mean": 1.0}
        for i in range(n - 1)
    }
    return _baseline(entry, recall, extra_entries=extra)


def _recall_breach(result) -> FloorBreach:
    matches = [b for b in result.breaches if b.metric == "recall_mean"]
    assert matches, result
    return matches[0]


class TestCompareAgainstFloors:
    def test_a_single_quantum_drop_passes_at_three_runs(self):
        """-0.222 is one 3-run quantum short of 0.333 — noise, not decay."""
        result = compare_against_floors(
            _floors_with_fillers(INCIDENT_ENTRY, 1.0),
            _baseline_with_fillers(INCIDENT_ENTRY, 0.778),
            3,
        )
        assert result.ok
        assert result.breaches == ()

    def test_the_incident_drop_fails_and_names_the_fields(self):
        """pr-rebase-push-refused walked 1.0 → 0.556 across two un-gated saves."""
        result = compare_against_floors(
            _floors(INCIDENT_ENTRY, 1.0),
            _baseline(INCIDENT_ENTRY, 0.556),
            3,
        )
        assert not result.ok
        breach = _recall_breach(result)
        assert breach.entry == INCIDENT_ENTRY
        assert breach.model == MODEL
        assert breach.metric == "recall_mean"
        assert breach.floor == 1.0
        assert breach.current == 0.556
        assert breach.std == 0.0
        assert breach.tolerance == pytest.approx(entry_recall_tolerance(3))

    def test_tolerance_scales_with_runs_per_entry(self):
        floors = _floors_with_fillers("case", 1.0)
        drop_20 = _baseline_with_fillers("case", 0.80)
        assert compare_against_floors(floors, drop_20, 3).ok
        # 0.20 is the 5-run quantum and stays inside 1/n + 1e-9; a hair past it fails.
        drop_past = _baseline_with_fillers("case", 0.79)
        assert compare_against_floors(floors, drop_past, 3).ok
        assert not compare_against_floors(floors, drop_past, 5).ok

    def test_single_run_refuses_a_recall_collapse(self):
        """1/n at n=1 is 1.0; a drop to 0.0 would pass if the gate ran."""
        result = compare_against_floors(
            _floors_with_fillers(INCIDENT_ENTRY, 1.0),
            _baseline_with_fillers(INCIDENT_ENTRY, 0.0),
            1,
        )
        assert not result.ok
        assert result.insufficient_runs == 1
        assert result.breaches == ()
        text = format_floor_breaches(result)
        assert "refused" in text
        assert "runs_per_entry=1" in text
        assert str(MIN_RECALL_TOLERANCE_RUNS) in text

    def test_aggregate_drop_over_twelve_entries_fails(self):
        names = [f"e{i:02d}" for i in range(12)]
        recorded = {name: {"recall_mean": _record(1.0)} for name in names}
        floors = FloorSet(1, {STEM: recorded})
        current = {
            name: {"recall_mean": 0.954, "precision_mean": 1.0} for name in names
        }
        result = compare_against_floors(
            floors, {"backend": "claude", "model": MODEL, "entries": current}, 3,
        )
        assert result.ok is False
        metrics = {b.metric for b in result.aggregate_breaches}
        assert "recall_mean" in metrics
        agg = next(b for b in result.aggregate_breaches if b.metric == "recall_mean")
        assert agg.current == pytest.approx(0.954)
        assert agg.floor == pytest.approx(1.0)
        assert agg.tolerance == pytest.approx(0.03)

    def test_an_entry_with_no_floor_record_fails(self):
        result = compare_against_floors(
            _floors("kept", 1.0),
            _baseline("kept", 1.0, extra_entries={
                "new-case": {"recall_mean": 1.0, "precision_mean": 1.0},
            }),
            3,
        )
        assert not result.ok
        assert "new-case" in result.unfloored_entries

    def test_unfloored_new_name_does_not_block_save(self):
        result = compare_against_floors(
            _floors("kept", 1.0),
            _baseline("kept", 1.0, extra_entries={
                "new-case": {"recall_mean": 1.0, "precision_mean": 1.0},
            }),
            3,
        )
        assert not result.ok
        assert not result.blocks_save(frozenset())
        assert not result.blocks_save(frozenset({"kept"}))
        assert result.blocks_save(frozenset({"kept", "new-case"}))

    def test_new_case_is_not_rendered_as_a_regression(self):
        result = compare_against_floors(
            _floors("kept", 1.0),
            _baseline("kept", 0.556, extra_entries={
                "new-case": {"recall_mean": 1.0, "precision_mean": 1.0},
            }),
            3,
        )
        assert result.blocks_save(frozenset({"kept"}))
        text = format_floor_breaches(result, frozenset({"kept"}))
        assert "kept / sonnet:" in text
        assert "current=0.556" in text
        assert "new-case" not in text
        assert "no floor record" not in text
        assert "new-case: no floor record" in format_floor_breaches(result)

    def test_deleting_a_decayed_recall_mean_still_breaches(self):
        baseline = _baseline(INCIDENT_ENTRY, 0.556)
        del baseline["entries"][INCIDENT_ENTRY]["recall_mean"]
        result = compare_against_floors(
            _floors(INCIDENT_ENTRY, 1.0), baseline, 3,
        )
        assert not result.ok
        assert (INCIDENT_ENTRY, "recall_mean") in result.missing_metrics

    def test_deleting_cache_read_ratio_mean_still_breaches(self):
        cache_floor = 0.9011053140137101
        floors = FloorSet(1, {STEM: {INCIDENT_ENTRY: {
            "recall_mean": _record(1.0),
            "cache_read_ratio_mean": _record(cache_floor),
        }}})
        result = compare_against_floors(
            floors, _baseline(INCIDENT_ENTRY, 1.0), 3,
        )
        assert not result.ok
        assert (INCIDENT_ENTRY, "cache_read_ratio_mean") in result.missing_metrics

    def test_deleting_a_decayed_entry_still_breaches(self):
        baseline = _baseline(INCIDENT_ENTRY, 0.556)
        del baseline["entries"][INCIDENT_ENTRY]
        result = compare_against_floors(
            _floors(INCIDENT_ENTRY, 1.0), baseline, 3,
        )
        assert not result.ok
        assert INCIDENT_ENTRY in result.dropped_entries

    def test_deleting_an_entry_without_decay_still_breaches(self):
        baseline = _baseline(INCIDENT_ENTRY, 1.0)
        del baseline["entries"][INCIDENT_ENTRY]
        result = compare_against_floors(
            _floors(INCIDENT_ENTRY, 1.0), baseline, 3,
        )
        assert not result.ok
        assert INCIDENT_ENTRY in result.dropped_entries


class TestLoadPriorEntryNames:
    def test_corrupt_file_raises_rather_than_returning_empty(self, tmp_path):
        (tmp_path / "claude-sonnet.json").write_text("{partial\n")
        with pytest.raises(ValueError, match="unreadable baseline"):
            load_prior_entry_names(tmp_path, "claude", "sonnet")


class TestFloorDocumentGrammar:
    def test_floor_above_best_fails(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"recall_mean": {"floor": 1.1, "best": 1.0}}}},
        })
        assert errors
        assert any("exceeds best" in e for e in errors)

    def test_floor_below_best_without_lowered_fails(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"recall_mean": {"floor": 0.5, "best": 1.0}}}},
        })
        assert errors
        assert any("no lowered reason" in e for e in errors)

    def test_floor_below_best_with_a_valid_lowered_passes(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"recall_mean": {
                "floor": 0.5, "best": 1.0,
                "lowered": "case retired — scorer no longer fits",
            }}}},
        })
        assert errors == []

    def test_lowered_with_no_reason_after_the_separator_fails(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"recall_mean": {
                "floor": 0.5, "best": 1.0, "lowered": "case retired — ",
            }}}},
        })
        assert errors
        assert any("declares nothing" in e for e in errors)

    def test_lowered_without_a_separator_fails(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"recall_mean": {
                "floor": 0.5, "best": 1.0, "lowered": "case retired",
            }}}},
        })
        assert any("declares nothing" in e for e in errors)

    def test_lower_is_better_floor_below_best_fails(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"billed_input_mean": {
                "floor": 100.0, "best": 200.0,
            }}}},
        })
        assert errors
        assert any("is below best" in e for e in errors)

    def test_lower_is_better_floor_above_best_needs_lowered(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"billed_input_mean": {
                "floor": 200.0, "best": 100.0,
            }}}},
        })
        assert errors
        assert any("no lowered reason" in e for e in errors)

    def test_lower_is_better_floor_above_best_with_lowered_passes(self):
        errors = validate_floors_document({
            "schema_version": 1,
            "backends": {STEM: {"e": {"billed_input_mean": {
                "floor": 200.0, "best": 100.0,
                "lowered": "prompt growth — Claude-era cost cannot be re-recorded",
            }}}},
        })
        assert errors == []


class TestSeedFloors:
    def test_later_worse_revision_keeps_the_earlier_best(self):
        """e5383b3a recorded 1.0; a8dd81c8 recorded 0.778. best is 1.0."""
        earlier = _baseline(INCIDENT_ENTRY, 1.0)
        later = _baseline(INCIDENT_ENTRY, 0.7777777777777778)
        floors = seed_floors([earlier, later])
        rec = floors.backends[STEM][INCIDENT_ENTRY]["recall_mean"]
        assert rec.best == 1.0
        assert rec.floor == 1.0
        drop = compare_against_floors(
            floors, _baseline(INCIDENT_ENTRY, 0.556), 3,
        )
        assert not drop.ok
        assert _recall_breach(drop).floor == 1.0
        last_wins = seed_floors([later])
        last = last_wins.backends[STEM][INCIDENT_ENTRY]["recall_mean"]
        assert last.best == 0.7777777777777778
        silent = compare_against_floors(
            last_wins, _baseline(INCIDENT_ENTRY, 0.556), 3,
        )
        assert not any(
            b.entry == INCIDENT_ENTRY and b.metric == "recall_mean"
            for b in silent.breaches
        ), "last-wins seed must miss the per-entry 0.556 incident"


class TestRaiseFloors:
    def test_raises_best_on_improvement_and_never_lowers_it(self):
        start = _floors("e", 0.5)
        raised = raise_floors(start, _baseline("e", 0.8))
        rec = raised.backends[STEM]["e"]["recall_mean"]
        assert rec.best == 0.8
        assert rec.floor == 0.8
        lowered = raise_floors(raised, _baseline("e", 0.4))
        rec = lowered.backends[STEM]["e"]["recall_mean"]
        assert rec.best == 0.8
        assert rec.floor == 0.8
