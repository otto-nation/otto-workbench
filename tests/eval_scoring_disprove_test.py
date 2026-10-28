"""Tests for eval.scoring_disprove: the prompt, the verdict reader, and the scores.

The corpus tests prove, without spending a token, that every case can score both
ways — a case whose findings are all one kind cannot tell a gate that falsifies
everything from one that falsifies nothing.
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
from eval.scoring_disprove import (
    DisproveTask, build_prompt, parse_findings, read_verdicts, score_verdicts,
)
from eval.task import RunOptions, get_task
from review.artifacts import DisproveOutcome

CORPUS = REPO_ROOT / "eval" / "corpus"
CASES = sorted(
    p.parent for p in CORPUS.glob("*/manifest.json")
    if json.loads(p.read_text()).get("task") == "disprove"
)
CASE_IDS = [c.name for c in CASES]


@pytest.fixture(autouse=True)
def _a_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-sonnet-5")
    # The fixture repo and log dir a run builds go under the test's own dir.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _finding(fid, truth, **kw):
    return {"id": fid, "truth": truth, "path": "a.py", "line": 1, "body": "claim", **kw}


class TestCorpus:
    def test_the_corpus_holds_disprove_cases(self):
        assert CASES

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_case_can_score_both_ways(self, case):
        findings = parse_findings(json.loads((case / "manifest.json").read_text()))
        truths = {f.truth for f in findings}
        assert truths == {"real", "false"}

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_every_finding_points_into_the_source(self, case):
        findings = parse_findings(json.loads((case / "manifest.json").read_text()))
        for f in findings:
            lines = (case / "src" / f.path).read_text().splitlines()
            assert f.line <= len(lines), f.id
            # The quoted evidence is the code the finding sits on.
            if f.evidence:
                first = f.evidence.splitlines()[0].strip()
                assert first in lines[f.line - 1], f"{f.id}: line {f.line} is {lines[f.line - 1]!r}"

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_perfect_gate_scores_one(self, case):
        findings = parse_findings(json.loads((case / "manifest.json").read_text()))
        perfect = {
            f.id: DisproveOutcome.FALSIFIED if f.truth == "false" else DisproveOutcome.SURVIVES
            for f in findings
        }
        assert score_verdicts(findings, perfect) == (1.0, 1.0)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_gate_that_challenges_nothing_cannot_pass(self, case):
        findings = parse_findings(json.loads((case / "manifest.json").read_text()))
        assert score_verdicts(findings, {}) == (0.0, 1.0)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_gate_that_falsifies_everything_cannot_pass(self, case):
        findings = parse_findings(json.loads((case / "manifest.json").read_text()))
        everything = {f.id: DisproveOutcome.FALSIFIED for f in findings}
        assert score_verdicts(findings, everything) == (1.0, 0.0)


class TestParseFindings:
    def test_a_bad_truth_is_refused(self):
        with pytest.raises(ValueError, match="truth"):
            parse_findings({"findings": [_finding("M1", "maybe")]})

    def test_a_nit_is_refused_because_the_gate_skips_it(self):
        with pytest.raises(ValueError, match="M and S"):
            parse_findings({"findings": [_finding("N1", "real")]})

    def test_duplicate_ids_are_refused(self):
        with pytest.raises(ValueError, match="unique"):
            parse_findings({"findings": [_finding("M1", "real"), _finding("M1", "false")]})

    def test_no_findings_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            parse_findings({})


class TestScoring:
    def test_a_case_with_one_kind_scores_the_other_field_one(self):
        only_real = parse_findings({"findings": [_finding("M1", "real")]})
        assert score_verdicts(only_real, {}) == (1.0, 1.0)
        only_false = parse_findings({"findings": [_finding("M1", "false")]})
        assert score_verdicts(only_false, {}) == (0.0, 1.0)

    def test_survives_and_falsified_are_not_the_same_to_a_real_finding(self):
        findings = parse_findings({"findings": [_finding("M1", "real"), _finding("M2", "real")]})
        verdicts = {"M1": DisproveOutcome.FALSIFIED, "M2": DisproveOutcome.SURVIVES}
        assert score_verdicts(findings, verdicts) == (1.0, 0.5)


class TestReadVerdicts:
    def test_reads_the_verdicts_the_gate_wrote(self, tmp_path):
        out = tmp_path / "d.json"
        out.write_text(json.dumps({"verdicts": [
            {"id": "M1", "outcome": "falsified", "reason": "guarded at line 3"},
        ]}))
        assert read_verdicts(str(out)) == {"M1": DisproveOutcome.FALSIFIED}

    @pytest.mark.parametrize("text", ["", "not json", '{"verdicts": [{"id": "N1", "outcome": "survives", "reason": "x"}]}'])
    def test_an_unreadable_file_is_no_verdicts(self, tmp_path, text):
        out = tmp_path / "d.json"
        out.write_text(text)
        assert read_verdicts(str(out)) == {}

    def test_a_missing_file_is_no_verdicts(self, tmp_path):
        assert read_verdicts(str(tmp_path / "nope.json")) == {}


class TestPrompt:
    def test_carries_the_findings_the_output_path_and_the_production_template(self):
        findings = parse_findings(json.loads((CASES[0] / "manifest.json").read_text()))
        prompt = build_prompt(findings, "/tmp/out/disprove.json", 15)
        assert "adversarial reviewer" in prompt
        assert "/tmp/out/disprove.json" in prompt
        assert findings[0].body.splitlines()[0] in prompt
        assert findings[0].evidence.splitlines()[0] in prompt
        assert "${" not in prompt


class TestTask:
    def test_is_registered_for_the_disprove_phase(self):
        assert get_task("disprove").phase is Phase.DISPROVE

    def _run(self, monkeypatch, write):
        seen = {}

        def fake(inv):
            seen["inv"] = inv
            write(inv)
            return 0

        monkeypatch.setattr(agent.backend, "invoke_agent", fake)
        case = CASES[0]
        artifacts = DisproveTask().run(case, RunOptions(model="claude-sonnet-5"))
        manifest = json.loads((case / "manifest.json").read_text())
        return seen["inv"], artifacts, DisproveTask().score(artifacts, manifest)

    def test_a_correct_gate_scores_one_and_the_agent_is_the_phases(self, monkeypatch):
        manifest = json.loads((CASES[0] / "manifest.json").read_text())

        def write(inv):
            Path(inv.output_path).write_text(json.dumps({"verdicts": [
                {"id": f["id"], "reason": "r",
                 "outcome": "falsified" if f["truth"] == "false" else "survives"}
                for f in manifest["findings"]
            ]}))

        inv, artifacts, result = self._run(monkeypatch, write)
        assert (result.recall, result.precision) == (1.0, 1.0)
        assert inv.phase == "disprove"
        assert inv.model == "claude-sonnet-5"
        assert inv.output_path.endswith("disprove.json")
        assert inv.max_turns == 15
        # The agent works in the fixture repo, and may read the artifact dir.
        planted = manifest["findings"][0]["path"]
        assert Path(inv.cwd, planted).exists()

    def test_a_gate_that_wrote_nothing_is_not_a_perfect_score(self, monkeypatch):
        _, artifacts, result = self._run(monkeypatch, lambda inv: None)
        assert result.recall == 0.0
        # No usage was billed, so this is a run that never happened, not a zero.
        assert artifacts.outcome is RunOutcome.NOT_RUN
