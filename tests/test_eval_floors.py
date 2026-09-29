"""High-water floor ratchet: comparison, save refusal, and the validator."""

from __future__ import annotations

import argparse
import json
import subprocess
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
    parse_accept_regression,
    raise_floors,
    save_floor_gate_messages,
    seed_floors,
    validate_floors_document,
)
from eval.scoring import entry_recall_tolerance

VALIDATOR = REPO_ROOT / "bin" / "local" / "validate-eval-floors"
STEM = "claude-sonnet"
MODEL = "sonnet"
INCIDENT_ENTRY = "pr-rebase-push-refused"


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


def _save_args(results: Path, **overrides):
    ns = dict(
        save_baselines=True, compare=False,
        results_dir=str(results), accept_regression=[],
    )
    ns.update(overrides)
    return argparse.Namespace(**ns)


def _complete_metrics(recall: float) -> dict:
    return {
        "recall_mean": recall, "precision_mean": 1.0,
        "severity_accuracy_mean": 0.0, "false_positive_mean": 0.0,
        "runs_measured": 3, "runs_attempted": 3,
    }


class TestSaveBaselinesRefusesRegression:
    def test_bare_save_baselines_refuses_and_leaves_the_file(self, em, tmp_path):
        """Previous file already decayed to 0.556; floors still hold 1.0."""
        results = tmp_path / "results"
        results.mkdir()
        baseline = em._baseline_document(
            "sonnet", "low", 3,
            {INCIDENT_ENTRY: _complete_metrics(0.556)},
            "claude",
        )
        path = results / "claude-sonnet.json"
        original = json.dumps(baseline, indent=2) + "\n"
        path.write_text(original)
        (results / "floors.json").write_text(json.dumps({
            "schema_version": 1,
            "backends": {STEM: {INCIDENT_ENTRY: {
                "recall_mean": {"floor": 1.0, "best": 1.0},
            }}},
        }) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {INCIDENT_ENTRY: {"sonnet": _complete_metrics(0.556)}},
        }
        code = em._run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert path.read_text() == original

    def test_accept_regression_without_a_reason_writes_nothing(self, em, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        path = results / "claude-sonnet.json"
        path.write_text("{}\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {INCIDENT_ENTRY: {"sonnet": _complete_metrics(1.0)}},
        }
        args = _save_args(
            results,
            accept_regression=[f"{INCIDENT_ENTRY}/recall_mean=0.556"],
        )
        code = em._run_post_eval(args, output, tmp_path)
        assert code != 0
        assert path.read_text() == "{}\n"

    def test_save_does_not_recreate_deleted_floors_when_baselines_exist(
        self, em, tmp_path,
    ):
        results = tmp_path / "results"
        results.mkdir()
        baseline = em._baseline_document(
            "sonnet", "low", 3,
            {INCIDENT_ENTRY: _complete_metrics(1.0)},
            "claude",
        )
        path = results / "claude-sonnet.json"
        original = json.dumps(baseline, indent=2) + "\n"
        path.write_text(original)
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {INCIDENT_ENTRY: {"sonnet": _complete_metrics(1.0)}},
        }
        code = em._run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert not (results / "floors.json").exists()
        assert path.read_text() == original

    def test_save_establishes_a_first_floor_for_a_new_case(self, em, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        kept = _complete_metrics(1.0)
        path = results / "claude-sonnet.json"
        path.write_text(json.dumps(em._baseline_document(
            "sonnet", "low", 3, {INCIDENT_ENTRY: kept}, "claude",
        ), indent=2) + "\n")
        (results / "floors.json").write_text(json.dumps({
            "schema_version": 1,
            "backends": {STEM: {INCIDENT_ENTRY: {
                "recall_mean": {"floor": 1.0, "best": 1.0},
            }}},
        }) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {
                INCIDENT_ENTRY: {"sonnet": kept},
                "new-case": {"sonnet": _complete_metrics(1.0)},
            },
        }
        code = em._run_post_eval(_save_args(results), output, tmp_path)
        assert code == 0
        floors = json.loads((results / "floors.json").read_text())
        rec = floors["backends"][STEM]["new-case"]["recall_mean"]
        assert rec["floor"] == 1.0
        assert rec["best"] == 1.0
        saved = json.loads(path.read_text())
        assert "new-case" in saved["entries"]

    def test_save_establishes_floors_for_a_new_backend(self, em, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        (results / "floors.json").write_text(json.dumps({
            "schema_version": 1,
            "backends": {STEM: {INCIDENT_ENTRY: {
                "recall_mean": {"floor": 1.0, "best": 1.0},
            }}},
        }) + "\n")
        output = {
            "backend": "pi", "effort": "low", "runs_per_entry": 3,
            "entries": {INCIDENT_ENTRY: {"opus": _complete_metrics(1.0)}},
        }
        code = em._run_post_eval(_save_args(results), output, tmp_path)
        assert code == 0
        floors = json.loads((results / "floors.json").read_text())
        rec = floors["backends"]["pi-opus"][INCIDENT_ENTRY]["recall_mean"]
        assert rec["floor"] == 1.0
        assert rec["best"] == 1.0
        assert STEM in floors["backends"]
        assert (results / "pi-opus.json").is_file()

    def test_unfloored_entry_already_on_disk_still_refuses_save(
        self, em, tmp_path,
    ):
        results = tmp_path / "results"
        results.mkdir()
        kept = _complete_metrics(1.0)
        orphan = _complete_metrics(1.0)
        path = results / "claude-sonnet.json"
        original_doc = em._baseline_document(
            "sonnet", "low", 3,
            {INCIDENT_ENTRY: kept, "orphan": orphan},
            "claude",
        )
        original = json.dumps(original_doc, indent=2) + "\n"
        path.write_text(original)
        (results / "floors.json").write_text(json.dumps({
            "schema_version": 1,
            "backends": {STEM: {INCIDENT_ENTRY: {
                "recall_mean": {"floor": 1.0, "best": 1.0},
            }}},
        }) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {
                INCIDENT_ENTRY: {"sonnet": kept},
                "orphan": {"sonnet": orphan},
            },
        }
        code = em._run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert path.read_text() == original
        floors = json.loads((results / "floors.json").read_text())
        assert "orphan" not in floors["backends"][STEM]

    def test_corrupt_on_disk_baseline_refuses_save(self, em, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        path = results / "claude-sonnet.json"
        original = "{partial\n"
        path.write_text(original)
        (results / "floors.json").write_text(json.dumps({
            "schema_version": 1,
            "backends": {STEM: {INCIDENT_ENTRY: {
                "recall_mean": {"floor": 1.0, "best": 1.0},
            }}},
        }) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {
                INCIDENT_ENTRY: {"sonnet": _complete_metrics(1.0)},
                "new-case": {"sonnet": _complete_metrics(1.0)},
            },
        }
        code = em._run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert path.read_text() == original
        floors = json.loads((results / "floors.json").read_text())
        assert "new-case" not in floors["backends"][STEM]

    def test_save_messages_partition_first_floors_from_regressions(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        kept = _complete_metrics(1.0)
        (results / "claude-sonnet.json").write_text(json.dumps({
            "backend": "claude", "model": "sonnet",
            "entries": {INCIDENT_ENTRY: kept},
        }) + "\n")
        (results / "floors.json").write_text(json.dumps({
            "schema_version": 1,
            "backends": {STEM: {INCIDENT_ENTRY: {
                "recall_mean": {"floor": 1.0, "best": 1.0},
            }}},
        }) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {
                INCIDENT_ENTRY: {"sonnet": _complete_metrics(0.556)},
                "new-case": {"sonnet": kept},
            },
        }
        code, lines = save_floor_gate_messages(results, output, [])
        text = "\n".join(lines)
        assert code == 2
        assert "floor regressions:" in text
        regress, _, rest = text.partition("establishing first floors:")
        assert rest, text
        assert "new-case" not in regress
        assert "current=0.556" in regress
        assert "new-case" in rest

    def test_seed_floors_prints_a_warning_when_it_reseeds(
        self, em, tmp_path, capsys,
    ):
        results = tmp_path / "results"
        results.mkdir()
        path = results / "claude-sonnet.json"
        path.write_text(json.dumps(em._baseline_document(
            "sonnet", "low", 3,
            {INCIDENT_ENTRY: _complete_metrics(1.0)}, "claude",
        ), indent=2) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {INCIDENT_ENTRY: {"sonnet": _complete_metrics(1.0)}},
        }
        args = _save_args(results, seed_floors=True)
        code = em._run_post_eval(args, output, tmp_path)
        assert code == 0
        err = capsys.readouterr().err
        assert "--seed-floors is reseeding floors.json from this run" in err
        assert "recovered from history" in err
        assert (results / "floors.json").is_file()


class TestParseAcceptRegression:
    def test_a_spec_without_a_reason_is_refused(self):
        with pytest.raises(ValueError, match="reason"):
            parse_accept_regression(f"{INCIDENT_ENTRY}/recall_mean=0.556")


def _write_validator_fixture(tmp_path: Path, *, current: float) -> None:
    (tmp_path / "floors.json").write_text(json.dumps({
        "schema_version": 1,
        "backends": {STEM: {INCIDENT_ENTRY: {
            "recall_mean": {"floor": 1.0, "best": 1.0},
        }}},
    }) + "\n")
    (tmp_path / "claude-sonnet.json").write_text(json.dumps({
        "schema_version": 3,
        "backend": "claude",
        "model": MODEL,
        "runs_per_entry": 3,
        "entries": {INCIDENT_ENTRY: {
            "recall_mean": current, "precision_mean": 1.0,
        }},
    }) + "\n")


class TestValidateEvalFloors:
    def test_exits_1_on_breach_and_names_the_entry_and_metric(self, tmp_path):
        _write_validator_fixture(tmp_path, current=0.556)
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        err = proc.stderr
        assert INCIDENT_ENTRY in err
        assert "recall_mean" in err

    def test_quiet_still_exits_1_on_breach(self, tmp_path):
        _write_validator_fixture(tmp_path, current=0.556)
        proc = subprocess.run(
            [str(VALIDATOR), "--quiet", str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert INCIDENT_ENTRY in proc.stderr
        assert "recall_mean" in proc.stderr

    def test_committed_floors_catch_the_recorded_incident(self, tmp_path):
        """0.556 vs the Aug 25 high-water of 1.0 must fail the real floors.json."""
        src = REPO_ROOT / "eval" / "results"
        floors = json.loads((src / "floors.json").read_text())
        rec = floors["backends"][STEM][INCIDENT_ENTRY]["recall_mean"]
        assert rec["best"] == 1.0
        assert rec["floor"] == 1.0
        baseline = json.loads((src / "claude-sonnet.json").read_text())
        baseline["entries"][INCIDENT_ENTRY]["recall_mean"] = 0.556
        (tmp_path / "floors.json").write_text(json.dumps(floors) + "\n")
        (tmp_path / "claude-sonnet.json").write_text(json.dumps(baseline) + "\n")
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode != 0
        assert INCIDENT_ENTRY in proc.stderr
        assert "recall_mean" in proc.stderr

    def test_deleting_a_decayed_recall_mean_fails_the_validator(self, tmp_path):
        src = REPO_ROOT / "eval" / "results"
        floors = json.loads((src / "floors.json").read_text())
        baseline = json.loads((src / "claude-sonnet.json").read_text())
        baseline["entries"][INCIDENT_ENTRY]["recall_mean"] = 0.556
        del baseline["entries"][INCIDENT_ENTRY]["recall_mean"]
        (tmp_path / "floors.json").write_text(json.dumps(floors) + "\n")
        (tmp_path / "claude-sonnet.json").write_text(json.dumps(baseline) + "\n")
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert INCIDENT_ENTRY in proc.stderr
        assert "recall_mean" in proc.stderr

    def test_deleting_cache_read_ratio_mean_fails_the_validator(self, tmp_path):
        src = REPO_ROOT / "eval" / "results"
        floors = json.loads((src / "floors.json").read_text())
        baseline = json.loads((src / "claude-sonnet.json").read_text())
        rec = floors["backends"][STEM][INCIDENT_ENTRY]["cache_read_ratio_mean"]
        assert rec["floor"] == 0.9011053140137101
        del baseline["entries"][INCIDENT_ENTRY]["cache_read_ratio_mean"]
        (tmp_path / "floors.json").write_text(json.dumps(floors) + "\n")
        (tmp_path / "claude-sonnet.json").write_text(json.dumps(baseline) + "\n")
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert INCIDENT_ENTRY in proc.stderr
        assert "cache_read_ratio_mean" in proc.stderr

    def test_deleting_a_decayed_entry_fails_the_validator(self, tmp_path):
        src = REPO_ROOT / "eval" / "results"
        floors = json.loads((src / "floors.json").read_text())
        baseline = json.loads((src / "claude-sonnet.json").read_text())
        baseline["entries"][INCIDENT_ENTRY]["recall_mean"] = 0.556
        del baseline["entries"][INCIDENT_ENTRY]
        (tmp_path / "floors.json").write_text(json.dumps(floors) + "\n")
        (tmp_path / "claude-sonnet.json").write_text(json.dumps(baseline) + "\n")
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert INCIDENT_ENTRY in proc.stderr

    def test_deleting_an_entry_without_decay_fails_the_validator(self, tmp_path):
        src = REPO_ROOT / "eval" / "results"
        floors = json.loads((src / "floors.json").read_text())
        baseline = json.loads((src / "claude-sonnet.json").read_text())
        del baseline["entries"][INCIDENT_ENTRY]
        (tmp_path / "floors.json").write_text(json.dumps(floors) + "\n")
        (tmp_path / "claude-sonnet.json").write_text(json.dumps(baseline) + "\n")
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert INCIDENT_ENTRY in proc.stderr

    def test_absent_floors_with_baselines_exits_1(self, tmp_path):
        src = REPO_ROOT / "eval" / "results"
        baseline = json.loads((src / "claude-sonnet.json").read_text())
        (tmp_path / "claude-sonnet.json").write_text(json.dumps(baseline) + "\n")
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert "floors.json" in proc.stderr

    def test_absent_floors_with_no_baselines_exits_0(self, tmp_path):
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0

    def test_unfloored_entry_on_disk_fails_the_validator(self, tmp_path):
        _write_validator_fixture(tmp_path, current=1.0)
        baseline = json.loads((tmp_path / "claude-sonnet.json").read_text())
        baseline["entries"]["orphan"] = {
            "recall_mean": 1.0, "precision_mean": 1.0,
        }
        (tmp_path / "claude-sonnet.json").write_text(
            json.dumps(baseline) + "\n",
        )
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        assert "orphan" in proc.stderr
