"""Tests for eval_scoring: aggregation, baseline schema, and baseline comparison."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

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
