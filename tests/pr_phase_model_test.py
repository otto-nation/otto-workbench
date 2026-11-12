"""`pr --phase-model PHASE=MODEL`: a per-run, per-phase model override."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phases
import cli.pr
from core.phases import Phase


def test_a_spec_becomes_that_phases_environment_entry():
    got = agent.phases.phase_model_overrides(["scout=claude-sonnet-5-5", "disprove=google-vertex/gemini-3.1-pro-preview:high"])
    assert got == {
        Phase.SCOUT.model_env_key: "claude-sonnet-5-5",
        Phase.DISPROVE.model_env_key: "google-vertex/gemini-3.1-pro-preview:high",
    }


def test_a_later_spec_for_one_phase_wins():
    got = agent.phases.phase_model_overrides(["scout=a-model", "scout=b-model"])
    assert got == {Phase.SCOUT.model_env_key: "b-model"}


@pytest.mark.parametrize("spec", ["scout", "=x", "scout=", "nope=claude-sonnet-5-5"])
def test_a_malformed_spec_or_unknown_phase_is_refused(spec):
    with pytest.raises(agent.phases.BadPhaseModel):
        agent.phases.phase_model_overrides([spec])


@pytest.mark.parametrize("model", ["claude-haiku-4-5", "anthropic/claude-haiku-4-5:low"])
def test_a_review_phase_cannot_be_overridden_onto_haiku(model):
    with pytest.raises(agent.phases.ReviewModelTooLight):
        agent.phases.phase_model_overrides([f"group={model}"])


def test_a_non_review_phase_may_take_a_light_model():
    got = agent.phases.phase_model_overrides(["describe=claude-haiku-4-5"])
    assert got == {Phase.DESCRIBE.model_env_key: "claude-haiku-4-5"}


def test_the_flag_is_global_and_repeatable():
    args, _ = cli.pr.build_global_parser().parse_known_args(
        ["--phase-model", "scout=a", "--phase-model", "group=b", "review"])
    assert args.phase_model == ["scout=a", "group=b"]


def test_a_refused_override_exits_2_before_any_command_runs(capsys):
    rc = cli.pr.main(["--phase-model", "group=claude-haiku-4-5", "status"], bin_dir=Path("."))
    assert rc == 2
    assert "Haiku" in capsys.readouterr().err
