"""Tests for eval_scoring_review: manifest parsing, finding matching, scoring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from eval_scoring_review import (
    ExpectedFinding,
    ReviewTask,
    match_findings,
    parse_manifest,
    score_entry,
)
from eval_task import RunArtifacts
from ai_usage import SessionUsage
from review_findings import Finding


def _finding(
    id: str = "M1",
    severity: str = "M",
    seq: int = 1,
    path: str = "handler.go",
    line: int | None = 14,
    body: str = "unchecked error return",
    end_line: int | None = None,
) -> Finding:
    return Finding(
        id=id, severity=severity, seq=seq,
        path=path, line=line, end_line=end_line, body=body,
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

    def test_range_starting_before_the_window_still_matches(self):
        """A finding anchored at the enclosing declaration covers the bug."""
        exp = [ExpectedFinding(("M",), "service.go", (13, 19), "correctness")]
        act = [_finding(path="service.go", line=12, end_line=14)]
        matches, fp = match_findings(exp, act)
        assert matches[0].matched
        assert fp == []

    def test_range_ending_after_the_window_still_matches(self):
        exp = [ExpectedFinding(("M",), "service.go", (10, 13), "correctness")]
        act = [_finding(path="service.go", line=13, end_line=25)]
        matches, _ = match_findings(exp, act)
        assert matches[0].matched

    def test_range_entirely_outside_the_window_does_not_match(self):
        exp = [ExpectedFinding(("M",), "service.go", (30, 40), "correctness")]
        act = [_finding(path="service.go", line=12, end_line=14)]
        matches, fp = match_findings(exp, act)
        assert not matches[0].matched
        assert fp == ["M1"]

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


# ── TestReviewTask ──────────────────────────────────────────────────────────


class TestReviewTask:
    def test_scores_artifacts_against_manifest(self):
        manifest = {
            "expected": [
                {"severity": ["M"], "path": "a.go", "line_range": [10, 10],
                 "category": "correctness"},
            ],
            "false_positives_max": 0,
        }
        artifacts = RunArtifacts(
            data={"findings": [_finding(path="a.go", line=10)]},
        )
        result = ReviewTask().score(artifacts, manifest)
        assert result.recall == 1.0

    def test_carries_token_metrics_off_the_session_usage(self):
        """Billed input and cache-read ratio are what the CI ratchet gates on."""
        artifacts = RunArtifacts(usage=SessionUsage(
            cost=0.5, input_tokens=100, output_tokens=20, cache_read_tokens=900,
        ))
        result = ReviewTask().score(artifacts, {})
        assert result.billed_input == 1000
        assert result.cache_read_ratio == 0.9
        assert result.output_tokens == 20


# ── TestCorpusExpectations ──────────────────────────────────────────────────


CORPUS = REPO_ROOT / "eval" / "corpus"
_REVIEW_MANIFESTS = [
    p for p in sorted(CORPUS.glob("*/manifest.json"))
    if json.loads(p.read_text()).get("expected")
]


@pytest.mark.parametrize(
    "manifest_path", _REVIEW_MANIFESTS, ids=lambda p: p.parent.name,
)
class TestCorpusExpectations:
    """An expectation nobody can satisfy scores as a permanent 0% recall.

    Cheap structural checks only — that the target file exists and the window
    lies inside it. Whether the window covers the planted defect is the eval
    run's job, not this suite's.
    """

    def test_expected_paths_and_ranges_resolve(self, manifest_path):
        manifest = json.loads(manifest_path.read_text())
        expected, _, _ = parse_manifest(manifest)
        for exp in expected:
            src = manifest_path.parent / "src" / exp.path
            assert src.is_file(), f"{exp.path} not in {manifest_path.parent.name}/src"
            line_count = len(src.read_text().splitlines())
            lo, hi = exp.line_range
            assert 1 <= lo <= hi <= line_count, (
                f"{exp.path}: line_range {exp.line_range} outside 1..{line_count}"
            )
