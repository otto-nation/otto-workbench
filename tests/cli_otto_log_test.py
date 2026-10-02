"""Tests for `otto-log`'s argument parsing and dispatch — `cli.otto_log`.

The library behind each subcommand has its own suite; these pin the wiring
between a flag and the library call it reaches, and the exits the CLI owns.
"""

import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import cli.otto_log  # noqa: E402
import core.trail_query  # noqa: E402
from conftest import reset_trail_root  # noqa: E402
from trail_support import PR_REVIEW, make_command  # noqa: E402


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))


def test_no_subcommand_prints_help_and_returns_1(capsys):
    assert cli.otto_log.main([]) == 1
    assert "usage:" in capsys.readouterr().out


def test_show_only_reaches_the_library(capsys):
    root, child, _ = make_command(*PR_REVIEW)
    assert cli.otto_log.main(["show", child, "--only"]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert f"Invocation {child}" in header
    assert root not in header


def test_record_stores_numeric_data_as_numbers(capsys):
    with reset_trail_root():
        code = cli.otto_log.main([
            "record", "--script", "dream", "--action", "consolidate",
            "--data", "added=3", "--data", "ratio=0.5", "--data", "note=x",
        ])
    assert code == 0
    invocation = capsys.readouterr().out.strip()
    events = core.trail_query.filter_events(
        core.trail_query.load_trail_events(None), invocation=invocation)
    recorded = [e for e in events if e.get("action") == "consolidate"]
    assert len(recorded) == 1
    assert recorded[0]["data"] == {"added": 3, "ratio": 0.5, "note": "x"}


def test_record_refuses_data_without_an_equals_sign(capsys):
    with reset_trail_root(), pytest.raises(SystemExit) as exc:
        cli.otto_log.main([
            "record", "--script", "dream", "--action", "consolidate", "--data", "bad",
        ])
    assert exc.value.code == 2
    assert "--data expects key=value" in capsys.readouterr().err


def test_stats_defaults_to_a_week(capsys):
    assert cli.otto_log.main(["stats"]) == 0
    assert capsys.readouterr().out == "No AI usage recorded in the last 7d.\n"
