"""Tests for the full/trimmed A/B arms: the session output, the baseline save, and the arm table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import eval.baselines
import eval.scoring
from eval.scoring import RunOutcome, ScoringResult, format_ab_table


def _r(**overrides) -> ScoringResult:
    """A scored run. The briefs call _r(); ScoringResult is positional on identity."""
    kwargs = {
        "entry_name": "e",
        "model": "m",
        "run_index": 0,
        "recall": 0.8,
    }
    kwargs.update(overrides)
    return ScoringResult(
        kwargs.pop("entry_name"),
        kwargs.pop("model"),
        kwargs.pop("run_index"),
        **kwargs,
    )


def test_the_session_output_nests_condition_under_model():
    out = eval.baselines.build_output(
        {("e", "sonnet", "full"): [_r()], ("e", "sonnet", "trimmed"): [_r()]},
        "low", 1,
    )
    assert out["schema_version"] == eval.scoring.SESSION_SCHEMA_VERSION
    assert set(out["entries"]["e"]["sonnet"]) == {"full", "trimmed"}
    assert "billed_input_mean" in out["entries"]["e"]["sonnet"]["full"]


def test_a_per_run_record_carries_the_tokens_an_ab_needs():
    row = eval.baselines._serialize_run(_r(billed_input=40000, output_tokens=2800))
    assert row["billed_input"] == 40000
    assert row["output_tokens"] == 2800


def _save_baseline_args(tmp_path):
    return argparse.Namespace(
        save_baselines=True, compare=False,
        results_dir=str(tmp_path / "results"),
    )


def test_a_baseline_file_keeps_the_flat_schema_3_shape(tmp_path):
    session = eval.baselines.build_output(
        {
            ("e", "sonnet", "full"): [_r(model="sonnet", recall=0.9)],
            ("e", "sonnet", "trimmed"): [
                _r(model="sonnet", recall=0.1, condition="trimmed"),
            ],
        },
        "low", 1,
    )
    session["backend"] = "claude"
    assert eval.baselines.run_post_eval(_save_baseline_args(tmp_path), session, tmp_path) == 0
    base = json.loads((tmp_path / "results" / "claude-sonnet.json").read_text())
    assert base["backend"] == "claude"
    assert base["schema_version"] == eval.scoring.SCHEMA_VERSION
    assert base["entries"]["e"]["recall_mean"] == 0.9
    assert "full" not in base["entries"]["e"]
    assert "trimmed" not in base["entries"]["e"]


def test_save_baselines_refuses_a_trimmed_only_session(tmp_path, capsys):
    session = eval.baselines.build_output(
        {("e", "sonnet", "trimmed"): [
            _r(model="sonnet", recall=0.1, condition="trimmed"),
        ]},
        "low", 1,
    )
    results = tmp_path / "results"
    results.mkdir()
    good = results / "sonnet.json"
    good.write_text('{"keep": true}\n')
    code = eval.baselines.run_post_eval(_save_baseline_args(tmp_path), session, tmp_path)
    assert code == 3
    assert good.read_text() == '{"keep": true}\n'
    err = capsys.readouterr().err
    assert "no full arm" in err
    assert "e / sonnet" in err


def test_the_table_reports_each_arm_and_the_delta_between_them():
    results = {
        ("e", "sonnet", "full"): [_r(recall=1.0, billed_input=40000, output_tokens=3000)],
        ("e", "sonnet", "trimmed"): [_r(recall=1.0, billed_input=22000, output_tokens=2800)],
    }
    table = format_ab_table(results)
    assert "full" in table and "trimmed" in table
    assert "-18000" in table or "-18,000" in table


def test_an_unmeasured_arm_is_named_not_printed_as_a_zero():
    results = {
        ("e", "sonnet", "full"): [
            _r(recall=1.0, billed_input=40000, outcome=RunOutcome.MEASURED),
        ],
        ("e", "sonnet", "trimmed"): [_r(recall=1.0, outcome=RunOutcome.NOT_RUN)],
    }
    table = format_ab_table(results)
    assert "0%" not in table.split("trimmed")[1].split("\n")[0], \
        "an arm that never ran must not read as a 0% pass rate"
    assert "unmeasured" in table.lower()
    assert "-40000" not in table and "-40,000" not in table, \
        "an unmeasured arm must not be differenced against as a zero"


def test_a_measured_zero_is_printed_as_zero_and_still_gets_a_delta():
    results = {
        ("e", "sonnet", "full"): [
            _r(recall=1.0, billed_input=40000, output_tokens=3000),
        ],
        ("e", "sonnet", "trimmed"): [
            _r(recall=0.0, billed_input=22000, output_tokens=2800),
        ],
    }
    table = format_ab_table(results)
    trimmed_row = table.split("trimmed")[1].split("\n")[0]
    assert "0%" in trimmed_row, "a measured miss must render 0%, not unmeasured"
    assert "unmeasured" not in trimmed_row.lower()
    assert "delta" in table


def test_a_partially_measured_arm_still_emits_a_delta():
    results = {
        ("e", "sonnet", "full"): [
            _r(recall=1.0, billed_input=40000, output_tokens=3000),
        ],
        ("e", "sonnet", "trimmed"): [
            _r(recall=1.0, billed_input=22000, output_tokens=2800, run_index=0),
            _r(recall=1.0, billed_input=22000, output_tokens=2800,
               run_index=1, outcome=RunOutcome.NOT_RUN),
            _r(recall=1.0, billed_input=22000, output_tokens=2800,
               run_index=2, outcome=RunOutcome.NOT_RUN),
        ],
    }
    table = format_ab_table(results)
    assert "1/3" in table.split("trimmed")[1].split("\n")[0]
    assert "delta" in table


def test_both_unmeasured_arms_emit_no_delta():
    results = {
        ("e", "sonnet", "full"): [_r(recall=1.0, outcome=RunOutcome.NOT_RUN)],
        ("e", "sonnet", "trimmed"): [_r(recall=1.0, outcome=RunOutcome.NOT_RUN)],
    }
    table = format_ab_table(results)
    assert "delta" not in table
    assert "unmeasured" in table.lower()


def test_a_positive_delta_carries_an_explicit_sign():
    results = {
        ("e", "sonnet", "full"): [
            _r(recall=0.5, billed_input=20000, output_tokens=2000),
        ],
        ("e", "sonnet", "trimmed"): [
            _r(recall=1.0, billed_input=40000, output_tokens=3000),
        ],
    }
    delta_row = format_ab_table(results).split("delta")[1].split("\n")[0]
    assert "+50%" in delta_row
    assert "+20000" in delta_row or "+20,000" in delta_row
