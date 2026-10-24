"""Omitted --models resolves through agent.phases.phase_ref."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.phases
import eval.run
from agent.models import ModelRef
from core.phases import Phase

from eval_task_support import _args, _make_case


def test_omitted_models_resolves_through_phase_ref(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    seen = []

    def fake_phase_ref(phase, explicit, cfg=None):
        seen.append((phase, explicit))
        return ModelRef("resolved-from-phase")

    monkeypatch.setattr(agent.phases, "phase_ref", fake_phase_ref)
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    assert seen == [(Phase.CI_FIX, None)]
    err = capsys.readouterr().err
    assert "resolved-from-phase" in err
    assert "(default)" not in err


def test_explicit_models_still_wins(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")

    monkeypatch.setenv("WORKBENCH_AI_CI_FIX_MODEL", "must-not-win")
    eval.run.run_eval(_args(tmp_path, models="opus,haiku", dry_run=True), tmp_path)
    err = capsys.readouterr().err
    assert "opus, haiku" in err
    assert "must-not-win" not in err


def test_explicit_models_drops_empty_tokens(tmp_path):
    assert eval.run._explicit_models(_args(tmp_path, models=", opus,")) == ["opus"]
    assert eval.run._explicit_models(_args(tmp_path, models=",")) is None


def test_all_empty_models_resolves_through_phase(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    monkeypatch.setattr(
        agent.phases, "phase_ref", lambda phase, explicit, cfg=None: ModelRef("resolved-empty"))
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

    def fake_phase_ref(phase, explicit, cfg=None):
        seen.append(phase)
        return ModelRef("resolved-review")

    monkeypatch.setattr(agent.phases, "phase_ref", fake_phase_ref)
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    assert seen == [Phase.SINGLE]
    assert "resolved-review" in capsys.readouterr().err


def test_skill_omitted_models_resolves_comments_fix(tmp_path, monkeypatch, capsys):
    _make_case(tmp_path / "corpus", "a", task="skill")
    seen = []

    def fake_phase_ref(phase, explicit, cfg=None):
        seen.append(phase)
        return ModelRef("resolved-skill")

    monkeypatch.setattr(agent.phases, "phase_ref", fake_phase_ref)
    eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    assert seen == [Phase.COMMENTS_FIX]
    assert "resolved-skill" in capsys.readouterr().err


def test_unknown_manifest_task_exits_with_a_message(tmp_path, capsys):
    _make_case(tmp_path / "corpus", "a", task="nope")
    with pytest.raises(SystemExit) as exc:
        eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    # A KeyError's str() is repr(msg); the exit text must be the bare message.
    assert str(exc.value).startswith("error: unknown eval task 'nope'")


def test_omitted_models_with_nothing_configured_refuses(tmp_path, monkeypatch):
    """An eval row must carry the model that served it.

    With no model configured anywhere, the backend would pick its own default
    and the results could not be labelled or compared, so the run refuses and
    names what to set.
    """
    _make_case(tmp_path / "corpus", "a", task="ci-fix")
    monkeypatch.setattr(agent.phases, "phase_ref", lambda phase, explicit, cfg=None: None)
    with pytest.raises(SystemExit) as caught:
        eval.run.run_eval(_args(tmp_path, dry_run=True), tmp_path)
    msg = str(caught.value)
    assert "--models" in msg
    assert "models.<tier>" in msg
