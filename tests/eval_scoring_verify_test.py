"""Tests for eval.scoring_verify: the prompt, the verdict reader, and the scores.

The corpus tests prove, without spending a token, that every case can score both
ways — a case whose fixes are all one kind cannot tell a gate that clears
everything from one that clears nothing — and that the repo a case ships shows
what its manifest says it does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.backend
import fix.tracking
from core.phases import Phase
from eval.scoring import RunOutcome
from eval.scoring_verify import (
    VerifyTask, build_items, build_prompt, overlay_files, parse_fixes,
    read_verdicts, score_verdicts,
)
from eval.task import RunOptions, get_task

CORPUS = REPO_ROOT / "eval" / "corpus"
CASES = sorted(
    p.parent for p in CORPUS.glob("*/manifest.json")
    if json.loads(p.read_text()).get("task") == "verify"
)
CASE_IDS = [c.name for c in CASES]


@pytest.fixture(autouse=True)
def _a_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _fix(fid, truth, **kw):
    return {"id": fid, "truth": truth, "file": "a.py", "line": 1,
            "ask": "ask", "claim": "claim", **kw}


def _manifest(case):
    return json.loads((case / "manifest.json").read_text())


class TestCorpus:
    def test_the_corpus_holds_verify_cases(self):
        assert CASES

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_case_can_score_both_ways(self, case):
        assert {f.truth for f in parse_fixes(_manifest(case))} == {"holds", "broken"}

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_every_fix_points_at_a_file_the_repo_or_the_edit_holds(self, case):
        for f in parse_fixes(_manifest(case)):
            assert (case / "src" / f.file).exists() or (case / "fix" / f.file).exists(), f.id

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_the_edit_changes_something_in_every_file_it_names(self, case):
        for rel in overlay_files(case):
            base = case / "src" / rel
            assert not base.exists() or base.read_text() != (case / "fix" / rel).read_text(), rel

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_perfect_gate_scores_one(self, case):
        fixes = parse_fixes(_manifest(case))
        perfect = {f.id: f.truth == "holds" for f in fixes}
        assert score_verdicts(fixes, perfect) == (1.0, 1.0)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_gate_that_answers_nothing_cannot_pass(self, case):
        assert score_verdicts(parse_fixes(_manifest(case)), {}) == (1.0, 0.0)

    @pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
    def test_a_gate_that_verifies_everything_cannot_pass(self, case):
        fixes = parse_fixes(_manifest(case))
        assert score_verdicts(fixes, {f.id: True for f in fixes}) == (0.0, 1.0)


class TestParseFixes:
    def test_a_bad_truth_is_refused(self):
        with pytest.raises(ValueError, match="truth"):
            parse_fixes({"items": [_fix("R1", "maybe")]})

    def test_duplicate_ids_are_refused(self):
        with pytest.raises(ValueError, match="unique"):
            parse_fixes({"items": [_fix("R1", "holds"), _fix("R1", "broken")]})

    def test_an_empty_ask_is_refused(self):
        with pytest.raises(ValueError, match="ask"):
            parse_fixes({"items": [_fix("R1", "holds", ask="")]})

    def test_a_case_without_fix_dir_is_refused(self, tmp_path):
        case = tmp_path / "nofix"
        case.mkdir()
        (case / "manifest.json").write_text(json.dumps({"items": [_fix("R1", "holds")]}))
        with pytest.raises(ValueError, match="nofix.*fix/"):
            VerifyTask().run(case, RunOptions(model="claude-sonnet-5", effort="medium"))

    def test_no_items_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            parse_fixes({})


class TestScoring:
    def test_not_verified_stops_a_broken_fix_but_does_not_clear_a_good_one(self):
        fixes = parse_fixes({"items": [_fix("R1", "broken"), _fix("R2", "holds")]})
        assert score_verdicts(fixes, {"R1": None, "R2": None}) == (1.0, 0.0)

    def test_calling_a_broken_fix_verified_is_the_costly_miss(self):
        fixes = parse_fixes({"items": [_fix("R1", "broken"), _fix("R2", "broken")]})
        assert score_verdicts(fixes, {"R1": True, "R2": False}) == (0.5, 1.0)

    def test_a_case_with_one_kind_scores_the_other_field_one(self):
        assert score_verdicts(parse_fixes({"items": [_fix("R1", "holds")]}), {"R1": True}) == (1.0, 1.0)


class TestReadVerdicts:
    def _tracking(self, tmp_path, ticks):
        items = build_items(parse_fixes({"items": [_fix("R1", "holds"), _fix("R2", "holds")]}), [])
        path = tmp_path / "v.md"
        fix.tracking.write(path, "Verify Fixes", items, fix.tracking.VERIFY_BOXES)
        text = path.read_text()
        for item_id, box in ticks.items():
            head, tail = text.split(f"fix:{item_id} -->", 1)
            tail = tail.replace(f"- [ ] {box}", f"- [x] {box} — evidence", 1)
            text = f"{head}fix:{item_id} -->{tail}"
        path.write_text(text)
        return path

    def test_reads_all_three_answers_and_leaves_an_unanswered_item_out(self, tmp_path):
        path = self._tracking(tmp_path, {"R1": "verified"})
        assert read_verdicts(path) == {"R1": True}
        path = self._tracking(tmp_path, {"R1": "broken", "R2": "not verified"})
        assert read_verdicts(path) == {"R1": False, "R2": None}

    def test_a_missing_file_is_no_verdicts(self, tmp_path):
        assert read_verdicts(tmp_path / "nope.md") == {}


class TestPrompt:
    def test_carries_the_ask_the_claim_the_changed_files_and_the_production_template(self, tmp_path):
        case = CASES[0]
        fixes = parse_fixes(_manifest(case))
        items = build_items(fixes, overlay_files(case))
        prompt = build_prompt(items, tmp_path / "v.md", "/tmp/repo", 15)
        assert "verifying what another agent claims it did" in prompt
        assert fixes[0].ask in prompt
        assert fixes[0].claim in prompt
        assert "The fix pass claims this change is held by" in prompt
        # What the worktree shows: the edits the case's fix pass made.
        for rel in overlay_files(case):
            assert f"`{rel}`" in prompt
        assert str(tmp_path / "v.md") in prompt
        assert "${" not in prompt


class TestTask:
    def test_is_registered_for_the_verify_gate_phase(self):
        assert get_task("verify").phase is Phase.FIX_VERIFY

    def _run(self, monkeypatch, write):
        seen = {}

        def fake(inv):
            seen["inv"] = inv
            write(inv)
            return 0

        monkeypatch.setattr(agent.backend, "invoke_fix", fake)
        case = CASES[0]
        artifacts = VerifyTask().run(case, RunOptions(model="claude-sonnet-5"))
        return seen["inv"], artifacts, VerifyTask().score(artifacts, _manifest(case))

    def test_the_fix_passes_edit_is_in_the_worktree_but_uncommitted(self, monkeypatch):
        def write(inv):
            status = subprocess.run(
                ["git", "-C", inv.cwd, "status", "--porcelain"],
                capture_output=True, text=True, check=True,
            ).stdout
            seen.append(status)

        seen: list[str] = []
        inv, _, _ = self._run(monkeypatch, write)
        changed = sorted(line[3:] for line in seen[0].splitlines())
        assert changed == overlay_files(CASES[0])
        assert inv.phase == "fix_verify"

    def test_a_correct_gate_scores_one(self, monkeypatch):
        truths = {f.id: f.truth == "holds" for f in parse_fixes(_manifest(CASES[0]))}

        def write(inv):
            tracking = next(Path(d) for d in inv.add_dirs if (Path(d) / "verify.md").exists()) / "verify.md"
            text = tracking.read_text()
            for item_id, ok in truths.items():
                box = "verified" if ok else "broken"
                head, tail = text.split(f"fix:{item_id} -->", 1)
                tail = tail.replace(f"- [ ] {box}", f"- [x] {box} — evidence", 1)
                text = f"{head}fix:{item_id} -->{tail}"
            tracking.write_text(text)

        inv, artifacts, result = self._run(monkeypatch, write)
        assert (result.recall, result.precision) == (1.0, 1.0)
        assert inv.model == "claude-sonnet-5"
        assert inv.task == "eval-verify"

    def test_a_gate_that_ticked_nothing_clears_nothing(self, monkeypatch):
        _, artifacts, result = self._run(monkeypatch, lambda inv: None)
        assert (result.recall, result.precision) == (1.0, 0.0)
        assert artifacts.outcome is RunOutcome.NOT_RUN
