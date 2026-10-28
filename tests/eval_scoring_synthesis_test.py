"""Tests for eval.scoring_synthesis: the prompt, the matcher, and the scores.

The corpus tests prove, without spending a token, that every case can score both
ways — a case with nothing to drop cannot tell a synthesis that removes noise
from one that removes nothing — and that every finding points at real code.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend
from core.phases import Phase
from eval.scoring import RunOutcome
from eval.scoring_synthesis import (
    SynthesisTask, build_prompt, parse_findings, read_findings, score_findings,
    survivors,
)
from eval.task import RunOptions, get_task
from review.artifacts import AgentFinding

CORPUS = REPO_ROOT / "eval" / "corpus"
CASES = sorted(
    p.parent for p in CORPUS.glob("*/manifest.json")
    if json.loads(p.read_text()).get("task") == "synthesis"
)
CASE_IDS = [c.name for c in CASES]


@pytest.fixture(autouse=True)
def _a_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _planted(fid, expect, **kw):
    return {"id": fid, "sid": f"sid-{fid}", "expect": expect, "path": "a.py",
            "line": 1, "body": "claim", **kw}


def _manifest(case):
    return json.loads((case / "manifest.json").read_text())


def _carried(sid="", path="a.py", line=1):
    return AgentFinding(id="M1", path=path, line=line, body="b", sid=sid)


class TestCorpus:
    def test_the_corpus_holds_synthesis_cases(self):
        assert CASES

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_case_can_score_both_ways(self, case):
        assert {p.expect for p in parse_findings(_manifest(case))} == {"keep", "drop"}

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_every_finding_points_into_the_source(self, case):
        for p in parse_findings(_manifest(case)):
            lines = (case / "src" / p.path).read_text().splitlines()
            assert p.line <= len(lines), p.id
            first = p.evidence.splitlines()[0].strip()
            assert first in lines[p.line - 1], f"{p.id}: line {p.line} is {lines[p.line - 1]!r}"

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_perfect_synthesis_scores_one(self, case):
        planted = parse_findings(_manifest(case))
        kept = {p.id for p in planted if p.expect == "keep"}
        assert score_findings(planted, kept) == (1.0, 1.0)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_synthesis_that_removes_nothing_cannot_pass(self, case):
        planted = parse_findings(_manifest(case))
        assert score_findings(planted, {p.id for p in planted}) == (0.0, 1.0)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_synthesis_that_carries_nothing_cannot_pass(self, case):
        assert score_findings(parse_findings(_manifest(case)), set()) == (1.0, 0.0)


class TestParseFindings:
    def test_a_bad_expectation_is_refused(self):
        with pytest.raises(ValueError, match="expect"):
            parse_findings({"findings": [_planted("M1", "maybe")]})

    def test_duplicate_sids_are_refused(self):
        with pytest.raises(ValueError, match="sid"):
            parse_findings({"findings": [_planted("M1", "keep", sid="x"), _planted("M2", "drop", sid="x")]})

    def test_no_findings_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            parse_findings({})


class TestSurvivors:
    def test_matches_by_sid(self):
        planted = parse_findings({"findings": [_planted("M1", "keep"), _planted("M2", "drop", line=9)]})
        assert survivors(planted, [_carried("sid-M2", line=3)]) == {"M2"}

    def test_falls_back_to_path_and_line_when_the_sid_was_dropped(self):
        planted = parse_findings({"findings": [_planted("M1", "keep", line=4)]})
        assert survivors(planted, [_carried("", line=4)]) == {"M1"}

    def test_one_unkeyed_finding_cannot_stand_for_two_planted(self):
        planted = parse_findings({"findings": [
            _planted("M1", "keep", line=4), _planted("M2", "drop", line=4),
        ]})
        assert len(survivors(planted, [_carried("", line=4)])) == 1

    def test_a_finding_the_agent_added_matches_nothing(self):
        planted = parse_findings({"findings": [_planted("M1", "keep")]})
        assert survivors(planted, [_carried("", path="other.py", line=1)]) == set()


class TestScoring:
    def test_keeping_a_duplicate_costs_recall_not_precision(self):
        planted = parse_findings({"findings": [_planted("M1", "keep"), _planted("S1", "drop")]})
        assert score_findings(planted, {"M1", "S1"}) == (0.0, 1.0)

    def test_losing_a_real_finding_costs_precision(self):
        planted = parse_findings({"findings": [_planted("M1", "keep"), _planted("M2", "keep"), _planted("S1", "drop")]})
        assert score_findings(planted, {"M1"}) == (1.0, 0.5)


class TestReadFindings:
    def test_reads_what_synthesis_wrote(self, tmp_path):
        out = tmp_path / "s.json"
        out.write_text(json.dumps({
            "summary": "ok", "verdict": "Approve", "findings": [
                {"id": "M1", "path": "a.py", "line": 1, "body": "b", "sid": "x"},
            ],
        }))
        assert [f.sid for f in read_findings(str(out))] == ["x"]

    @pytest.mark.parametrize("text", ["", "not json", '{"summary": ""}'])
    def test_an_unreadable_file_carries_nothing(self, tmp_path, text):
        out = tmp_path / "s.json"
        out.write_text(text)
        assert read_findings(str(out)) == []

    def test_a_missing_file_carries_nothing(self, tmp_path):
        assert read_findings(str(tmp_path / "nope.json")) == []


class TestPrompt:
    def test_carries_every_finding_the_output_path_and_the_production_template(self):
        planted = parse_findings(_manifest(CASES[0]))
        prompt = build_prompt(planted, "/tmp/out/synthesis.json", "/tmp/repo", 15)
        assert "completing the final review" in prompt
        assert "/tmp/out/synthesis.json" in prompt
        for p in planted:
            assert p.sid in prompt
            # The merged findings are quoted as JSON, so quotes arrive escaped.
            assert json.dumps(p.body)[1:-1] in prompt
        assert "${" not in prompt


class TestTask:
    def test_is_registered_for_the_synthesis_phase(self):
        assert get_task("synthesis").phase is Phase.SYNTHESIS

    def _run(self, monkeypatch, write):
        seen = {}

        def fake(inv):
            seen["inv"] = inv
            write(inv)
            return 0

        monkeypatch.setattr(agent.backend, "invoke_agent", fake)
        case = CASES[0]
        artifacts = SynthesisTask().run(case, RunOptions(model="claude-sonnet-5"))
        return seen["inv"], artifacts, SynthesisTask().score(artifacts, _manifest(case))

    def test_a_correct_synthesis_scores_one_and_the_phase_is_synthesis(self, monkeypatch):
        keep = [f for f in _manifest(CASES[0])["findings"] if f["expect"] == "keep"]

        def write(inv):
            Path(inv.output_path).write_text(json.dumps({
                "summary": "ok", "verdict": "Approve",
                "findings": [
                    {"id": f["id"], "path": f["path"], "line": f["line"],
                     "body": f["body"], "sid": f["sid"]} for f in keep
                ],
            }))

        inv, artifacts, result = self._run(monkeypatch, write)
        assert (result.recall, result.precision) == (1.0, 1.0)
        assert inv.phase == "synthesis"
        # A review persona, never the unrestricted agent: synthesis may not edit the tree.
        assert inv.agent
        assert inv.model == "claude-sonnet-5"
        assert inv.output_path.endswith("synthesis.json")
        assert Path(inv.cwd, keep[0]["path"]).exists()

    def test_a_synthesis_that_wrote_nothing_carries_nothing(self, monkeypatch):
        _, artifacts, result = self._run(monkeypatch, lambda inv: None)
        assert (result.recall, result.precision) == (1.0, 0.0)
        assert artifacts.outcome is RunOutcome.NOT_RUN
