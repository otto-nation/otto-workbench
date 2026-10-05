"""Tests for `eval-models`' argument parsing and checkout guard — `cli.eval_models`."""

from __future__ import annotations

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import cli.eval_models  # noqa: E402
import core.workbench_paths  # noqa: E402
import eval.run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _capture(monkeypatch, code=0):
    seen = []

    def fake(args, repo_root):
        seen.append((args, repo_root))
        return {}, code

    monkeypatch.setattr(eval.run, "run_eval", fake)
    return seen


def test_relative_defaults_resolve_against_the_checkout_this_runs_from(monkeypatch):
    seen = _capture(monkeypatch)
    assert cli.eval_models.main(["--dry-run"]) == 0
    assert seen[0][1] == REPO_ROOT


def test_the_runs_exit_code_is_returned(monkeypatch):
    _capture(monkeypatch, code=2)
    assert cli.eval_models.main([]) == 2


def test_a_relative_corpus_outside_a_checkout_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(core.workbench_paths, "source_checkout", lambda: None)
    seen = _capture(monkeypatch)
    assert cli.eval_models.main([]) == 1
    assert "--corpus" in capsys.readouterr().err
    assert seen == []


def test_absolute_paths_run_without_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(core.workbench_paths, "source_checkout", lambda: None)
    seen = _capture(monkeypatch)
    corpus = tmp_path / "c"
    results = tmp_path / "r"
    assert cli.eval_models.main([
        "--corpus", str(corpus), "--results-dir", str(results),
    ]) == 0
    assert seen[0][1] is None
