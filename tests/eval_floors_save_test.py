"""Tests for saving eval baselines: a save that would lower a floor is refused unless accepted."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import eval.baselines
from eval.floors import parse_accept_regression, save_floor_gate_messages

from eval_floors_support import STEM, INCIDENT_ENTRY


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
    def test_bare_save_baselines_refuses_and_leaves_the_file(self, tmp_path):
        """Previous file already decayed to 0.556; floors still hold 1.0."""
        results = tmp_path / "results"
        results.mkdir()
        baseline = eval.baselines._baseline_document(
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
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert path.read_text() == original

    def test_single_run_save_refuses_a_recall_collapse(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        baseline = eval.baselines._baseline_document(
            "sonnet", "low", 1,
            {INCIDENT_ENTRY: _complete_metrics(0.0)},
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
            "backend": "claude", "effort": "low", "runs_per_entry": 1,
            "entries": {INCIDENT_ENTRY: {"sonnet": _complete_metrics(0.0)}},
        }
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert path.read_text() == original

    def test_accept_regression_without_a_reason_writes_nothing(self, tmp_path):
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
        code = eval.baselines.run_post_eval(args, output, tmp_path)
        assert code != 0
        assert path.read_text() == "{}\n"

    def test_save_does_not_recreate_deleted_floors_when_baselines_exist(
        self, tmp_path,
    ):
        results = tmp_path / "results"
        results.mkdir()
        baseline = eval.baselines._baseline_document(
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
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert not (results / "floors.json").exists()
        assert path.read_text() == original

    def test_save_establishes_a_first_floor_for_a_new_case(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        kept = _complete_metrics(1.0)
        path = results / "claude-sonnet.json"
        path.write_text(json.dumps(eval.baselines._baseline_document(
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
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
        assert code == 0
        floors = json.loads((results / "floors.json").read_text())
        rec = floors["backends"][STEM]["new-case"]["recall_mean"]
        assert rec["floor"] == 1.0
        assert rec["best"] == 1.0
        saved = json.loads(path.read_text())
        assert "new-case" in saved["entries"]

    def test_save_establishes_floors_for_a_new_backend(self, tmp_path):
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
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
        assert code == 0
        floors = json.loads((results / "floors.json").read_text())
        rec = floors["backends"]["pi-opus"][INCIDENT_ENTRY]["recall_mean"]
        assert rec["floor"] == 1.0
        assert rec["best"] == 1.0
        assert STEM in floors["backends"]
        assert (results / "pi-opus.json").is_file()

    def test_unfloored_entry_already_on_disk_still_refuses_save(
        self, tmp_path,
    ):
        results = tmp_path / "results"
        results.mkdir()
        kept = _complete_metrics(1.0)
        orphan = _complete_metrics(1.0)
        path = results / "claude-sonnet.json"
        original_doc = eval.baselines._baseline_document(
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
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
        assert code != 0
        assert path.read_text() == original
        floors = json.loads((results / "floors.json").read_text())
        assert "orphan" not in floors["backends"][STEM]

    def test_corrupt_on_disk_baseline_refuses_save(self, tmp_path):
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
        code = eval.baselines.run_post_eval(_save_args(results), output, tmp_path)
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
        self, tmp_path, capsys,
    ):
        results = tmp_path / "results"
        results.mkdir()
        path = results / "claude-sonnet.json"
        path.write_text(json.dumps(eval.baselines._baseline_document(
            "sonnet", "low", 3,
            {INCIDENT_ENTRY: _complete_metrics(1.0)}, "claude",
        ), indent=2) + "\n")
        output = {
            "backend": "claude", "effort": "low", "runs_per_entry": 3,
            "entries": {INCIDENT_ENTRY: {"sonnet": _complete_metrics(1.0)}},
        }
        args = _save_args(results, seed_floors=True)
        code = eval.baselines.run_post_eval(args, output, tmp_path)
        assert code == 0
        err = capsys.readouterr().err
        assert "--seed-floors is reseeding floors.json from this run" in err
        assert "recovered from history" in err
        assert (results / "floors.json").is_file()


class TestParseAcceptRegression:
    def test_a_spec_without_a_reason_is_refused(self):
        with pytest.raises(ValueError, match="reason"):
            parse_accept_regression(f"{INCIDENT_ENTRY}/recall_mean=0.556")
