"""Tests for eval_scoring: matching and scoring logic for model evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from eval_scoring import (
    ExpectedFinding,
    ScoringResult,
    aggregate_runs,
    compare_baselines,
    format_comparison_table,
    format_summary_table,
    match_findings,
    parse_manifest,
    score_entry,
    validate_baseline_schema,
)
from review_findings import Finding


def _finding(
    id: str = "M1",
    severity: str = "M",
    seq: int = 1,
    path: str = "handler.go",
    line: int | None = 14,
    body: str = "unchecked error return",
) -> Finding:
    return Finding(
        id=id, severity=severity, seq=seq,
        path=path, line=line, end_line=None, body=body,
    )


# ── TestParseManifest ───────────────────────────────────────────────────────


class TestParseManifest:
    def test_parses_expected_findings(self):
        manifest = {
            "expected": [
                {
                    "severity": ["M", "S"],
                    "path": "handler.go",
                    "line_range": [14, 16],
                    "category": "correctness",
                    "description_contains": "error",
                }
            ],
            "false_positives_max": 2,
            "tags": ["go"],
        }
        expected, fp_max, tags = parse_manifest(manifest)
        assert len(expected) == 1
        assert expected[0].severity == ("M", "S")
        assert expected[0].path == "handler.go"
        assert expected[0].line_range == (14, 16)
        assert expected[0].category == "correctness"
        assert expected[0].description_contains == "error"

    def test_parses_tags(self):
        _, _, tags = parse_manifest({"tags": ["security", "go"]})
        assert tags == ["security", "go"]

    def test_false_positives_max(self):
        _, fp_max, _ = parse_manifest({"false_positives_max": 5})
        assert fp_max == 5

    def test_optional_description_contains(self):
        manifest = {
            "expected": [
                {"severity": ["M"], "path": "f.go", "line_range": [1, 1], "category": "c"}
            ],
        }
        expected, _, _ = parse_manifest(manifest)
        assert expected[0].description_contains == ""

    def test_empty_manifest(self):
        expected, fp_max, tags = parse_manifest({})
        assert expected == []
        assert fp_max == 0
        assert tags == []


# ── TestMatchFindings ───────────────────────────────────────────────────────


class TestMatchFindings:
    def test_exact_match(self):
        exp = [ExpectedFinding(("M",),"handler.go", (14, 14), "correctness")]
        act = [_finding()]
        matches, fp = match_findings(exp, act)
        assert matches[0].matched
        assert matches[0].matched_finding_id == "M1"
        assert fp == []

    def test_no_match_wrong_path(self):
        exp = [ExpectedFinding(("M",),"other.go", (14, 14), "correctness")]
        act = [_finding()]
        matches, fp = match_findings(exp, act)
        assert not matches[0].matched
        assert fp == ["M1"]

    def test_no_match_wrong_line(self):
        exp = [ExpectedFinding(("M",),"handler.go", (20, 25), "correctness")]
        act = [_finding()]
        matches, fp = match_findings(exp, act)
        assert not matches[0].matched

    def test_no_match_wrong_severity(self):
        exp = [ExpectedFinding(("N",),"handler.go", (14, 14), "correctness")]
        act = [_finding()]
        matches, fp = match_findings(exp, act)
        assert not matches[0].matched

    def test_line_range_inclusive(self):
        exp = [ExpectedFinding(("M",),"handler.go", (10, 20), "correctness")]
        act = [_finding(line=10)]
        matches, _ = match_findings(exp, act)
        assert matches[0].matched

        act2 = [_finding(line=20)]
        matches2, _ = match_findings(exp, act2)
        assert matches2[0].matched

    def test_description_contains_match(self):
        exp = [ExpectedFinding(("M",),"handler.go", (14, 14), "c", "error")]
        act = [_finding(body="Unchecked ERROR return value")]
        matches, _ = match_findings(exp, act)
        assert matches[0].matched

    def test_description_contains_no_match(self):
        exp = [ExpectedFinding(("M",),"handler.go", (14, 14), "c", "sql")]
        act = [_finding(body="unchecked error return")]
        matches, _ = match_findings(exp, act)
        assert not matches[0].matched

    def test_multiple_expected(self):
        exp = [
            ExpectedFinding(("M",),"a.go", (10, 10), "c"),
            ExpectedFinding(("S",),"b.go", (20, 20), "c"),
        ]
        act = [
            _finding(id="M1", severity="M", path="a.go", line=10),
            _finding(id="S1", severity="S", path="b.go", line=20),
        ]
        matches, fp = match_findings(exp, act)
        assert all(m.matched for m in matches)
        assert fp == []

    def test_false_positives(self):
        exp = [ExpectedFinding(("M",),"a.go", (10, 10), "c")]
        act = [
            _finding(id="M1", path="a.go", line=10),
            _finding(id="N1", severity="N", path="c.go", line=5),
        ]
        matches, fp = match_findings(exp, act)
        assert matches[0].matched
        assert fp == ["N1"]

    def test_path_suffix_matching(self):
        exp = [ExpectedFinding(("M",),"handler.go", (14, 14), "c")]
        act = [_finding(path="src/handler.go")]
        matches, _ = match_findings(exp, act)
        assert matches[0].matched

    def test_path_suffix_no_partial_filename_match(self):
        exp = [ExpectedFinding(("M",),"handler.go", (14, 14), "c")]
        act = [_finding(path="src/myhandler.go")]
        matches, _ = match_findings(exp, act)
        assert not matches[0].matched

    def test_greedy_no_double_match(self):
        exp = [
            ExpectedFinding(("M",),"a.go", (10, 15), "c"),
            ExpectedFinding(("M",),"a.go", (10, 15), "c"),
        ]
        act = [_finding(id="M1", path="a.go", line=12)]
        matches, _ = match_findings(exp, act)
        assert matches[0].matched
        assert not matches[1].matched

    def test_empty_expected(self):
        act = [_finding()]
        matches, fp = match_findings([], act)
        assert matches == []
        assert fp == ["M1"]

    def test_empty_actuals(self):
        exp = [ExpectedFinding(("M",),"a.go", (10, 10), "c")]
        matches, fp = match_findings(exp, [])
        assert not matches[0].matched
        assert fp == []

    def test_line_none_no_match(self):
        exp = [ExpectedFinding(("M",),"handler.go", (14, 14), "c")]
        act = [_finding(line=None)]
        matches, _ = match_findings(exp, act)
        assert not matches[0].matched

    def test_severity_exact_first_preference(self):
        exp = [ExpectedFinding(("M", "S"),"handler.go", (14, 14), "c")]
        act = [_finding(severity="M")]
        matches, _ = match_findings(exp, act)
        assert matches[0].severity_exact

    def test_severity_not_exact_second_preference(self):
        exp = [ExpectedFinding(("M", "S"),"handler.go", (14, 14), "c")]
        act = [_finding(id="S1", severity="S")]
        matches, _ = match_findings(exp, act)
        assert matches[0].matched
        assert not matches[0].severity_exact
        assert matches[0].matched_severity == "S"


# ── TestScoreEntry ──────────────────────────────────────────────────────────


class TestScoreEntry:
    def test_perfect_score(self):
        exp = [ExpectedFinding(("M",),"a.go", (10, 10), "c")]
        act = [_finding(path="a.go", line=10)]
        result = score_entry("test", "opus", 0, exp, act, false_positives_max=2)
        assert result.recall == 1.0
        assert result.precision == 1.0
        assert result.false_positive_count == 0
        assert result.false_positive_ok

    def test_partial_recall(self):
        exp = [
            ExpectedFinding(("M",),"a.go", (10, 10), "c"),
            ExpectedFinding(("S",),"b.go", (20, 20), "c"),
        ]
        act = [_finding(path="a.go", line=10)]
        result = score_entry("test", "opus", 0, exp, act, false_positives_max=0)
        assert result.recall == 0.5
        assert result.precision == 1.0

    def test_zero_recall(self):
        exp = [ExpectedFinding(("M",),"a.go", (10, 10), "c")]
        result = score_entry("test", "opus", 0, exp, [], false_positives_max=0)
        assert result.recall == 0.0
        assert result.precision == 0.0

    def test_severity_accuracy(self):
        exp = [
            ExpectedFinding(("M", "S"),"a.go", (10, 10), "c"),
            ExpectedFinding(("M", "S"),"b.go", (20, 20), "c"),
        ]
        act = [
            _finding(id="M1", severity="M", path="a.go", line=10),
            _finding(id="S1", severity="S", path="b.go", line=20),
        ]
        result = score_entry("test", "opus", 0, exp, act, false_positives_max=2)
        assert result.severity_accuracy == 0.5

    def test_false_positive_threshold(self):
        exp = [ExpectedFinding(("M",),"a.go", (10, 10), "c")]
        act = [
            _finding(id="M1", path="a.go", line=10),
            _finding(id="N1", severity="N", path="x.go", line=1),
            _finding(id="N2", severity="N", path="y.go", line=1),
        ]
        result = score_entry("test", "opus", 0, exp, act, false_positives_max=1)
        assert not result.false_positive_ok
        assert result.false_positive_count == 2

    def test_cost_passthrough(self):
        result = score_entry(
            "test", "opus", 0, [], [], 0,
            cost_usd=1.23, duration_ms=5000,
            input_tokens=1000, output_tokens=500,
        )
        assert result.cost_usd == 1.23
        assert result.duration_ms == 5000
        assert result.input_tokens == 1000
        assert result.output_tokens == 500

    def test_no_expected_no_actuals(self):
        result = score_entry("test", "opus", 0, [], [], 0)
        assert result.recall == 0.0
        assert result.precision == 1.0


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
