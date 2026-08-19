"""Tests for eval_scoring: aggregation, baseline schema, and baseline comparison."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import eval_scoring
from eval_scoring import (
    ScoringResult,
    aggregate_runs,
    compare_baselines,
    format_comparison_table,
    format_summary_table,
    validate_baseline_schema,
)


# ── TestAggregateRuns ───────────────────────────────────────────────────────


class TestAggregateRuns:
    def test_single_run(self):
        r = ScoringResult("e", "m", 0, recall=0.8, precision=0.6, cost_usd=0.05)
        agg = aggregate_runs([r])
        assert agg["recall_mean"] == 0.8
        assert agg["recall_std"] == 0.0
        assert agg["cost_mean"] == 0.05

    def test_multiple_runs(self):
        r1 = ScoringResult("e", "m", 0, recall=1.0, precision=0.5,
                           cost_usd=0.03, duration_ms=10000)
        r2 = ScoringResult("e", "m", 1, recall=0.5, precision=1.0,
                           cost_usd=0.05, duration_ms=20000)
        agg = aggregate_runs([r1, r2])
        assert agg["recall_mean"] == 0.75
        assert agg["recall_std"] > 0
        assert agg["cost_mean"] == 0.04

    def test_empty_runs(self):
        agg = aggregate_runs([])
        assert agg["recall_mean"] == 0.0
        assert agg["cost_mean"] == 0.0


# ── TestFormatSummaryTable ──────────────────────────────────────────────────


class TestFormatSummaryTable:
    def test_produces_header_row(self):
        table = format_summary_table({})
        assert "Entry" in table
        assert "Model" in table
        assert "Recall" in table

    def test_includes_all_entries(self):
        r1 = ScoringResult("entry-a", "opus", 0, recall=1.0, cost_usd=0.10)
        r2 = ScoringResult("entry-b", "sonnet", 0, recall=0.5, cost_usd=0.05)
        table = format_summary_table({
            ("entry-a", "opus"): [r1],
            ("entry-b", "sonnet"): [r2],
        })
        assert "entry-a" in table
        assert "entry-b" in table
        assert "opus" in table
        assert "sonnet" in table

    def test_shows_cost(self):
        r = ScoringResult("e", "m", 0, cost_usd=1.23)
        table = format_summary_table({("e", "m"): [r]})
        assert "$1.23" in table


# ── TestValidateBaselineSchema ─────────────────────────────────────────────


def _valid_baseline() -> dict:
    return {
        "schema_version": 1,
        "model": "sonnet",
        "effort": "low",
        "runs_per_entry": 1,
        "updated_at": "2026-08-03T12:00:00+00:00",
        "entries": {
            "unchecked-error-go": {
                "recall_mean": 1.0,
                "recall_std": 0.0,
                "precision_mean": 0.5,
                "precision_std": 0.0,
                "severity_accuracy_mean": 1.0,
                "false_positive_mean": 1.0,
                "cost_mean": 0.03,
                "duration_mean_ms": 5000,
                "runs": [],
            },
        },
    }


class TestValidateBaselineSchema:
    def test_valid_baseline(self):
        assert validate_baseline_schema(_valid_baseline()) == []

    def test_missing_schema_version(self):
        data = _valid_baseline()
        del data["schema_version"]
        errors = validate_baseline_schema(data)
        assert any("schema_version" in e for e in errors)

    def test_wrong_schema_version(self):
        data = _valid_baseline()
        data["schema_version"] = 99
        errors = validate_baseline_schema(data)
        assert any("unsupported" in e for e in errors)

    def test_missing_model(self):
        data = _valid_baseline()
        del data["model"]
        errors = validate_baseline_schema(data)
        assert any("model" in e for e in errors)

    def test_missing_entries(self):
        data = _valid_baseline()
        del data["entries"]
        errors = validate_baseline_schema(data)
        assert any("entries" in e for e in errors)

    def test_entry_missing_recall_mean(self):
        data = _valid_baseline()
        del data["entries"]["unchecked-error-go"]["recall_mean"]
        errors = validate_baseline_schema(data)
        assert any("recall_mean" in e for e in errors)

    def test_entry_bad_metric_type(self):
        data = _valid_baseline()
        data["entries"]["unchecked-error-go"]["precision_mean"] = "high"
        errors = validate_baseline_schema(data)
        assert any("precision_mean" in e and "number" in e for e in errors)

    def test_empty_entries_valid(self):
        data = _valid_baseline()
        data["entries"] = {}
        assert validate_baseline_schema(data) == []


# ── TestCompareBaselines ───────────────────────────────────────────────────


def _baseline_data(recall: float = 0.8, precision: float = 0.9) -> dict:
    return {
        "schema_version": 1,
        "model": "sonnet",
        "entries": {
            "test-entry": {
                "recall_mean": recall,
                "precision_mean": precision,
                "severity_accuracy_mean": 0.5,
                "false_positive_mean": 1.0,
                "cost_mean": 0.03,
            },
        },
    }


def _current_output(model: str, recall: float, precision: float) -> dict:
    return {
        "entries": {
            "test-entry": {
                model: {
                    "recall_mean": recall,
                    "precision_mean": precision,
                    "severity_accuracy_mean": 0.5,
                    "false_positive_mean": 1.0,
                    "cost_mean": 0.03,
                },
            },
        },
    }


class TestCompareBaselines:
    def test_no_regressions(self):
        baselines = {"sonnet": _baseline_data(0.8, 0.9)}
        current = _current_output("sonnet", 0.85, 0.95)
        result = compare_baselines(baselines, current)
        assert result["regressions"] == []

    def test_recall_regression(self):
        baselines = {"sonnet": _baseline_data(0.8, 0.9)}
        current = _current_output("sonnet", 0.5, 0.9)
        result = compare_baselines(baselines, current, threshold=0.05)
        assert len(result["regressions"]) >= 1
        assert any(r[2] == "recall_mean" for r in result["regressions"])

    def test_precision_improvement_not_flagged(self):
        baselines = {"sonnet": _baseline_data(0.8, 0.5)}
        current = _current_output("sonnet", 0.8, 0.9)
        result = compare_baselines(baselines, current)
        assert not any(r[2] == "precision_mean" for r in result["regressions"])

    def test_new_entry_detected(self):
        baselines = {"sonnet": {"entries": {}}}
        current = _current_output("sonnet", 0.8, 0.9)
        result = compare_baselines(baselines, current)
        assert ("test-entry", "sonnet") in result["new_entries"]

    def test_missing_entry_detected(self):
        baselines = {"sonnet": _baseline_data(0.8, 0.9)}
        current = {"entries": {"test-entry": {}}}
        result = compare_baselines(baselines, current)
        assert ("test-entry", "sonnet") in result["missing_entries"]

    def test_empty_baseline(self):
        baselines = {}
        current = _current_output("sonnet", 0.8, 0.9)
        result = compare_baselines(baselines, current)
        assert result["regressions"] == []
        assert result["comparisons"] == {}

    def test_empty_current(self):
        baselines = {"sonnet": _baseline_data(0.8, 0.9)}
        result = compare_baselines(baselines, {})
        assert ("test-entry", "sonnet") in result["missing_entries"]


# ── TestFormatComparisonTable ──────────────────────────────────────────────


class TestFormatComparisonTable:
    def test_produces_header(self):
        table = format_comparison_table({"comparisons": {}, "new_entries": [], "missing_entries": []})
        assert "Entry" in table
        assert "Model" in table
        assert "Metric" in table

    def test_shows_regression_marker(self):
        comparison = {
            "comparisons": {
                ("e", "m"): {
                    "recall_mean": {
                        "baseline": 0.8, "current": 0.5,
                        "delta": -0.3, "regression": True,
                    },
                },
            },
            "new_entries": [],
            "missing_entries": [],
        }
        table = format_comparison_table(comparison)
        assert "REGRESSED" in table

    def test_empty_comparison(self):
        table = format_comparison_table({"comparisons": {}, "new_entries": [], "missing_entries": []})
        lines = table.strip().split("\n")
        assert len(lines) == 2

    def test_reports_whether_a_metric_was_gated(self):
        """A gate that hides what it ignored trains people to ignore the gate."""
        comparison = {
            "comparisons": {
                ("e", "m"): {
                    "cost_mean": {
                        "baseline": 0.03, "current": 0.30,
                        "delta": 0.27, "regression": False, "gated": False,
                    },
                    "recall_mean": {
                        "baseline": 0.8, "current": 0.8,
                        "delta": 0.0, "regression": False, "gated": True,
                    },
                },
            },
            "new_entries": [],
            "missing_entries": [],
        }
        table = format_comparison_table(comparison)
        assert "Gate" in table
        assert "ungated" in table
        assert "pass" in table


# ── TestTokenRatchet ───────────────────────────────────────────────────────


def _token_baseline(**overrides) -> dict:
    entry = {
        "recall_mean": 0.8,
        "precision_mean": 0.9,
        "severity_accuracy_mean": 0.5,
        "false_positive_mean": 1.0,
        "cost_mean": 0.03,
        "billed_input_mean": 1000.0,
        "output_tokens_mean": 1000.0,
        "cache_read_ratio_mean": 0.85,
    }
    entry.update(overrides)
    return {"schema_version": 2, "model": "sonnet", "entries": {"test-entry": entry}}


def _token_current(**overrides) -> dict:
    entry = {
        "recall_mean": 0.8,
        "precision_mean": 0.9,
        "severity_accuracy_mean": 0.5,
        "false_positive_mean": 1.0,
        "cost_mean": 0.03,
        "billed_input_mean": 1000.0,
        "output_tokens_mean": 1000.0,
        "cache_read_ratio_mean": 0.85,
    }
    entry.update(overrides)
    return {"entries": {"test-entry": {"sonnet": entry}}}


def _regressed(result: dict) -> set[str]:
    return {r[2] for r in result["regressions"]}


class TestTokenRatchet:
    def test_flat_token_usage_is_not_a_regression(self):
        result = compare_baselines({"sonnet": _token_baseline()}, _token_current())
        assert result["regressions"] == []

    def test_billed_input_exactly_at_the_threshold_is_allowed(self):
        """15% is the budget, not the trigger — the gate fires past it."""
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(billed_input_mean=1150.0))
        assert "billed_input_mean" not in _regressed(result)

    def test_billed_input_just_past_the_threshold_regresses(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(billed_input_mean=1151.0))
        assert "billed_input_mean" in _regressed(result)

    def test_output_tokens_exactly_at_the_threshold_is_allowed(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(output_tokens_mean=1150.0))
        assert "output_tokens_mean" not in _regressed(result)

    def test_output_tokens_just_past_the_threshold_regresses(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(output_tokens_mean=1151.0))
        assert "output_tokens_mean" in _regressed(result)

    def test_fewer_tokens_is_never_a_regression(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()},
            _token_current(billed_input_mean=10.0, output_tokens_mean=10.0))
        assert result["regressions"] == []

    def test_cache_read_ratio_below_the_floor_regresses(self):
        """The failure mode: caching silently stops and the bill triples."""
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(cache_read_ratio_mean=0.59))
        assert "cache_read_ratio_mean" in _regressed(result)

    def test_cache_read_ratio_at_the_floor_is_allowed(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(cache_read_ratio_mean=0.60))
        assert "cache_read_ratio_mean" not in _regressed(result)

    def test_cache_ratio_drop_above_the_floor_is_allowed(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(cache_read_ratio_mean=0.70))
        assert "cache_read_ratio_mean" not in _regressed(result)

    def test_cost_is_reported_but_never_gates(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(cost_mean=3.0))
        assert "cost_mean" not in _regressed(result)
        metrics = result["comparisons"][("test-entry", "sonnet")]
        assert metrics["cost_mean"]["current"] == 3.0
        assert metrics["cost_mean"]["gated"] is False

    def test_a_baseline_without_token_metrics_is_ungated(self):
        """Old baselines keep working — a missing metric is unknown, not passing."""
        baseline = _token_baseline()
        for key in ("billed_input_mean", "output_tokens_mean", "cache_read_ratio_mean"):
            del baseline["entries"]["test-entry"][key]
        result = compare_baselines(
            {"sonnet": baseline},
            _token_current(billed_input_mean=99999.0, cache_read_ratio_mean=0.0))
        assert result["regressions"] == []

    def test_a_zero_baseline_is_ungated(self):
        """A zero means the run was never measured, so there is nothing to ratchet."""
        result = compare_baselines(
            {"sonnet": _token_baseline(billed_input_mean=0.0)},
            _token_current(billed_input_mean=50000.0))
        assert "billed_input_mean" in result["comparisons"][("test-entry", "sonnet")] \
            or "billed_input_mean" not in _regressed(result)
        assert "billed_input_mean" not in _regressed(result)

    def test_a_finding_score_drop_still_gates(self):
        result = compare_baselines(
            {"sonnet": _token_baseline()}, _token_current(recall_mean=0.5))
        assert "recall_mean" in _regressed(result)


class TestTokenAggregation:
    def test_aggregate_exposes_the_gated_token_metrics(self):
        runs = [
            ScoringResult("e", "m", 0, billed_input=1000, output_tokens=100,
                          cache_read_ratio=0.8),
            ScoringResult("e", "m", 1, billed_input=2000, output_tokens=200,
                          cache_read_ratio=0.9),
        ]
        agg = aggregate_runs(runs)
        assert agg["billed_input_mean"] == 1500.0
        assert agg["output_tokens_mean"] == 150.0
        assert agg["cache_read_ratio_mean"] == pytest.approx(0.85)

    def test_no_runs_still_reports_the_token_keys(self):
        agg = aggregate_runs([])
        assert agg["billed_input_mean"] == 0.0
        assert agg["output_tokens_mean"] == 0.0
        assert agg["cache_read_ratio_mean"] == 0.0


class TestSchemaVersion:
    def test_current_version_is_accepted(self):
        data = _valid_baseline()
        data["schema_version"] = eval_scoring.SCHEMA_VERSION
        assert validate_baseline_schema(data) == []

    def test_the_previous_version_still_loads(self):
        """Baselines predating the token metrics must not be rejected outright."""
        data = _valid_baseline()
        data["schema_version"] = 1
        assert validate_baseline_schema(data) == []

    def test_token_metrics_must_be_numbers_when_present(self):
        data = _valid_baseline()
        data["entries"]["unchecked-error-go"]["billed_input_mean"] = "lots"
        errors = validate_baseline_schema(data)
        assert any("billed_input_mean" in e and "number" in e for e in errors)
