"""Omitted --models resolves through agent.phases.phase_model."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.phases
import eval.run
from core.phases import Phase

from eval_task_support import _args, _make_case


def test_omitted_models_resolves_through_phase_model(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    seen = []

    def fake_phase_model(phase, explicit, cfg=None):
        seen.append((phase, explicit))
        return "resolved-from-phase"

    monkeypatch.setattr(agent.phases, "phase_model", fake_phase_model)
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    assert seen == [(Phase.CI_FIX, None)]
    err = capsys.readouterr().err
    assert "resolved-from-phase" in err
    assert "(default)" not in err


def test_explicit_models_still_wins(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")

    def boom(phase, explicit, cfg=None):
        raise AssertionError("phase_model must not run when --models is set")

    monkeypatch.setattr(agent.phases, "phase_model", boom)
    eval.run.run_eval(_args(tmp_path, models="opus,haiku", dry_run=True), tmp_path)
    err = capsys.readouterr().err
    assert "opus, haiku" in err


def test_empty_model_tokens_are_dropped(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    monkeypatch.setattr(
        agent.phases, "phase_model", lambda phase, explicit, cfg=None: "resolved-empty")

    assert eval.run._explicit_models(_args(tmp_path, models=", opus,")) == ["opus"]
    assert eval.run._explicit_models(_args(tmp_path, models=",")) is None
    eval.run.run_eval(_args(tmp_path, models=",", dry_run=True), tmp_path)
    err = capsys.readouterr().err
    assert "Models: resolved-empty" in err


def test_ci_fix_env_override_is_honoured(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    monkeypatch.setenv("WORKBENCH_AI_CI_FIX_MODEL", "env-ci-fix-model")
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    err = capsys.readouterr().err
    assert "env-ci-fix-model" in err
    assert "(default)" not in err


def test_review_omitted_models_resolves_single(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="review")
    seen = []

    def fake_phase_model(phase, explicit, cfg=None):
        seen.append(phase)
        return "resolved-review"

    monkeypatch.setattr(agent.phases, "phase_model", fake_phase_model)
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    assert seen == [Phase.SINGLE]
    assert "resolved-review" in capsys.readouterr().err


def test_skill_omitted_models_resolves_comments_fix(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="skill")
    seen = []

    def fake_phase_model(phase, explicit, cfg=None):
        seen.append(phase)
        return "resolved-skill"

    monkeypatch.setattr(agent.phases, "phase_model", fake_phase_model)
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    assert seen == [Phase.COMMENTS_FIX]
    assert "resolved-skill" in capsys.readouterr().err
