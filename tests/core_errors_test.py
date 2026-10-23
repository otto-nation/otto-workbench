"""Tests for core.errors — how a run ends on an error.

An operator error (nothing selects an AI backend, no pi installed) reaches the
operator as one line; a bug keeps its traceback; either way an in-process
caller stops, as it would have on a spawned child.
"""

import os
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.machine_env
import agent.pi_binary
import agent.pi_models
import cli.review_orchestrate
import core.errors
import core.publishing
import core.trail
import review.orchestrate

AI_LIB = Path(__file__).resolve().parent.parent / "ai" / "lib"


class _Trail:
    def __init__(self):
        self.errors = []

    def error(self, action, detail, data=None):
        self.errors.append((action, detail, data))


def _raise_through(trail, exc):
    """Raise *exc* and hand it to reraise from an except clause, as an entry point does."""
    try:
        raise exc
    except Exception as caught:
        core.errors.reraise(trail, caught)


def test_an_operator_error_is_recorded_and_re_raised():
    trail = _Trail()
    error = core.errors.OperatorError("set agent.backend")
    with pytest.raises(core.errors.OperatorError) as raised:
        _raise_through(trail, error)
    assert raised.value is error
    assert trail.errors == [("operator_error", "set agent.backend", None)]


def test_a_bug_is_recorded_with_a_traceback_that_still_ends_where_it_failed():
    def broken():
        raise ValueError("a real bug")

    trail = _Trail()
    with pytest.raises(ValueError, match="a real bug") as raised:
        try:
            broken()
        except Exception as exc:
            core.errors.reraise(trail, exc)

    [(action, detail, data)] = trail.errors
    assert (action, detail) == ("unexpected_error", "a real bug")
    assert "in broken" in data["traceback"]
    assert traceback.extract_tb(raised.value.__traceback__)[-1].name == "broken"


def test_nested_boundaries_record_one_failure_once():
    """`pr` runs `review` in-process, which runs `review-orchestrate`: three boundaries."""
    inner, outer = _Trail(), _Trail()
    with pytest.raises(ValueError):
        try:
            _raise_through(inner, ValueError("once"))
        except Exception as exc:
            core.errors.reraise(outer, exc)
    assert [e[0] for e in inner.errors] == ["unexpected_error"]
    assert outer.errors == []


@pytest.mark.parametrize("error", [
    agent.backend.BackendNotSelected("no AI backend selected"),
    agent.pi_models.ModelNotConfigured("anthropic"),
    agent.pi_models.ModelNotServed("claude-x", "not listed"),
    agent.pi_binary.PiUnresolvable("nothing installed"),
    agent.machine_env.MachineEnvUnavailable("mise could not undo the repo env"),
])
def test_configuration_failures_are_operator_errors(error):
    assert isinstance(error, core.errors.OperatorError)


def test_an_uncaught_operator_error_reaches_the_operator_as_one_line(tmp_path):
    """The real interpreter's exit, which is where the hook acts: no traceback, status 1."""
    env = {k: v for k, v in os.environ.items() if k != "AI_BACKEND"}
    env["WORKBENCH_CONFIG_DIR"] = str(tmp_path / "config")
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(AI_LIB)!r})
        import agent.backend
        agent.backend._require_backend()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1
    assert "no AI backend selected" in result.stderr
    assert "Traceback" not in result.stderr


def test_an_uncaught_bug_still_prints_its_traceback(tmp_path):
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(AI_LIB)!r})
        import core.errors
        raise ValueError("a real bug")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" in result.stderr
    assert "ValueError: a real bug" in result.stderr


def test_an_operator_error_stops_an_in_process_caller(tmp_path, monkeypatch):
    """`pr fix` runs its passes through call_entry_point and must not continue past one.

    An exit status would read as "this pass found problems" and the next pass —
    the CI pass, which rebases and can push — would run on a machine that
    cannot run AI at all.
    """
    (tmp_path / "fake_entry.py").write_text(textwrap.dedent("""
        import agent.backend
        def main(argv):
            agent.backend._require_backend()
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("AI_BACKEND", raising=False)
    with pytest.raises(agent.backend.BackendNotSelected):
        core.publishing.call_entry_point("fake_entry:main", [])


def test_review_orchestrate_records_a_missing_backend_and_lets_it_through(tmp_path, monkeypatch):
    """The failure `pr review --self` hit on a machine with no backend selected."""
    def no_backend(*_args, **_kwargs):
        agent.backend._require_backend()

    recorded = []
    real_error = core.trail.Trail.error

    def spy(self, action, detail, data=None):
        recorded.append(action)
        return real_error(self, action, detail, data)

    monkeypatch.delenv("AI_BACKEND", raising=False)
    monkeypatch.setattr(review.orchestrate, "run_orchestrate", no_backend)
    monkeypatch.setattr(core.trail.Trail, "error", spy)

    with pytest.raises(agent.backend.BackendNotSelected):
        cli.review_orchestrate.main([
            "--mode", "self", "--repo", "acme/widgets",
            "--repo-dir", str(tmp_path), "--review-file", str(tmp_path / "review.md"),
        ])
    assert recorded == ["operator_error"]
