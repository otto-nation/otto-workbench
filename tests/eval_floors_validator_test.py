"""Tests for bin/local/validate-eval-floors, run against fixture baselines and floors."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from eval_floors_support import STEM, MODEL, INCIDENT_ENTRY

VALIDATOR = REPO_ROOT / "bin" / "local" / "validate-eval-floors"


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

    def test_single_run_baseline_refuses_rather_than_holding(self, tmp_path):
        _write_validator_fixture(tmp_path, current=0.0)
        baseline = json.loads((tmp_path / "claude-sonnet.json").read_text())
        baseline["runs_per_entry"] = 1
        (tmp_path / "claude-sonnet.json").write_text(
            json.dumps(baseline) + "\n",
        )
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        combined = proc.stdout + proc.stderr
        assert "refused" in proc.stderr
        assert "runs_per_entry=1" in proc.stderr
        assert "floors hold" not in combined
        assert "Traceback" not in combined

    def test_non_numeric_runs_per_entry_is_a_clean_failure(self, tmp_path):
        _write_validator_fixture(tmp_path, current=1.0)
        baseline = json.loads((tmp_path / "claude-sonnet.json").read_text())
        baseline["runs_per_entry"] = "three"
        (tmp_path / "claude-sonnet.json").write_text(
            json.dumps(baseline) + "\n",
        )
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        combined = proc.stdout + proc.stderr
        assert "Traceback" not in combined
        assert proc.stderr.startswith("✗")
        assert "claude-sonnet.json" in proc.stderr
        assert "runs_per_entry" in proc.stderr
        assert "three" in proc.stderr

    def test_non_positive_runs_per_entry_is_a_clean_failure(self, tmp_path):
        _write_validator_fixture(tmp_path, current=1.0)
        baseline = json.loads((tmp_path / "claude-sonnet.json").read_text())
        baseline["runs_per_entry"] = 0
        (tmp_path / "claude-sonnet.json").write_text(
            json.dumps(baseline) + "\n",
        )
        proc = subprocess.run(
            [str(VALIDATOR), str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 1
        combined = proc.stdout + proc.stderr
        assert "Traceback" not in combined
        assert proc.stderr.startswith("✗")
        assert "claude-sonnet.json" in proc.stderr
        assert "runs_per_entry" in proc.stderr
