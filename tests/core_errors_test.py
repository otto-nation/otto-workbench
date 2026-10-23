"""Tests for core.errors — how an entry point ends on an error.

An operator error (nothing selects an AI backend, a provider with no model) is
one line and an exit status; anything else keeps its traceback.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.pi_models
import cli.review_orchestrate
import core.errors
import review.orchestrate

AI_LIB = Path(__file__).resolve().parent.parent / "ai" / "lib"


class _Trail:
    def __init__(self):
        self.errors = []

    def error(self, action, detail, data=None):
        self.errors.append((action, detail, data))


def test_an_operator_error_is_one_line_and_an_exit_status(capsys):
    trail = _Trail()
    rc = core.errors.boundary(trail, core.errors.OperatorError("set agent.backend"))

    assert rc == core.errors.OPERATOR_ERROR_EXIT
    assert trail.errors == [("operator_error", "set agent.backend", None)]
    err = capsys.readouterr().err
    assert "set agent.backend" in err
    assert "Traceback" not in err


def test_anything_else_is_recorded_with_its_traceback_and_re_raised():
    def broken():
        raise ValueError("a real bug")

    trail = _Trail()
    try:
        broken()
    except ValueError as exc:
        original = exc
        with pytest.raises(ValueError, match="a real bug") as raised:
            core.errors.boundary(trail, exc)
    assert raised.value is original
    [(action, detail, data)] = trail.errors
    assert (action, detail) == ("unexpected_error", "a real bug")
    assert "in broken" in data["traceback"]


@pytest.mark.parametrize("error", [
    agent.backend.BackendNotSelected("no AI backend selected"),
    agent.pi_models.ModelNotConfigured("anthropic"),
    agent.pi_models.ModelNotServed("claude-x", "not listed"),
])
def test_configuration_failures_are_operator_errors(error):
    assert isinstance(error, core.errors.OperatorError)


def test_review_orchestrate_with_no_backend_exits_without_a_traceback(
        tmp_path, monkeypatch, capsys):
    """The failure a self-review on an unconfigured machine produced.

    run_orchestrate raises BackendNotSelected from its first backend call; the
    entry point used to re-raise it, so `pr review --self` ended in a
    traceback instead of the line saying what to set.
    """
    def no_backend(*_args, **_kwargs):
        agent.backend._require_backend()

    monkeypatch.delenv("AI_BACKEND", raising=False)
    monkeypatch.setattr(review.orchestrate, "run_orchestrate", no_backend)

    rc = cli.review_orchestrate.main([
        "--mode", "self", "--repo", "acme/widgets",
        "--repo-dir", str(tmp_path), "--review-file", str(tmp_path / "review.md"),
    ])

    assert rc == core.errors.OPERATOR_ERROR_EXIT
    err = capsys.readouterr().err
    assert "no AI backend selected" in err
    assert "Traceback" not in err


def test_no_entry_point_hand_rolls_its_own_error_boundary():
    """Every boundary goes through core.errors.boundary.

    One written out by hand records the error and re-raises, which turns an
    operator error back into a traceback — the defect boundary exists to end.
    """
    pattern = re.compile(r'trail\.error\(\s*"unexpected_error"')
    offenders = sorted(
        str(path.relative_to(AI_LIB))
        for path in AI_LIB.rglob("*.py")
        if "__pycache__" not in path.parts
        and path != AI_LIB / "core" / "errors.py"
        and pattern.search(path.read_text())
    )
    assert offenders == []
