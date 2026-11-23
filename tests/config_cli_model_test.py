"""`otto-workbench config model` — set or clear one phase's or tier's model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_write

config_cli = _load_lib("config_cli")

GEMINI = "google-vertex/gemini-3.1-pro-preview:medium"


def _loaded() -> config.workbench_config.WorkbenchConfig:
    return config.workbench_config.load_config()


def test_a_phase_model_lands_under_agent_phases():
    assert config_cli.main(["model", "scout", GEMINI]) == 0
    assert _loaded().agent.phases["scout"].model == GEMINI


def test_a_tier_model_lands_under_models():
    assert config_cli.main(["model", "light", "claude-haiku-4-5"]) == 0
    assert _loaded().models.light == "claude-haiku-4-5"


@pytest.mark.parametrize("target", ["scout", "group", "standard", "deep"])
def test_haiku_is_refused_where_a_review_would_read_it(target, capsys):
    assert config_cli.main(["model", target, "claude-haiku-4-5"]) == 1
    assert "Haiku" in capsys.readouterr().err
    assert not config.workbench_config.global_config_path().exists() or "haiku" not in config.workbench_config.global_config_path().read_text()


@pytest.mark.parametrize("alias", ["sonnet", "opus", "haiku", "opusplan", "default", "sonnet[1m]", "Opus"])
@pytest.mark.parametrize("argv_for", [
    lambda alias: ["set", "models.light", alias],
    lambda alias: ["model", "light", alias],
])
def test_a_tier_named_by_an_alias_is_refused_and_not_written(alias, argv_for, capsys):
    assert config_cli.main(argv_for(alias)) == 1
    err = capsys.readouterr().err
    assert "Claude alias" in err and "claude-sonnet-5-5" in err
    path = config.workbench_config.global_config_path()
    assert not path.exists() or "models" not in path.read_text()


def test_a_tier_named_by_a_full_id_is_still_written():
    assert config_cli.main(["set", "models.standard", "claude-sonnet-5-5[1m]"]) == 0
    assert _loaded().models.standard == "claude-sonnet-5-5[1m]"


def test_a_session_may_still_be_an_alias():
    assert config_cli.main(["set", "models.session", "opusplan"]) == 0
    assert _loaded().models.session == "opusplan"


def test_an_unknown_target_is_refused(capsys):
    assert config_cli.main(["model", "bogus", GEMINI]) == 1
    assert "neither a phase" in capsys.readouterr().err


def test_unset_removes_the_key_and_the_emptied_parents():
    config_cli.main(["model", "scout", GEMINI])
    assert config_cli.main(["model", "scout", "--unset"]) == 0
    text = config.workbench_config.global_config_path().read_text()
    assert "scout" not in text and "phases" not in text


def test_unset_leaves_sibling_keys_alone():
    config_cli.main(["model", "scout", GEMINI])
    config_cli.main(["model", "group", GEMINI])
    config_cli.main(["model", "scout", "--unset"])
    assert _loaded().agent.phases["group"].model == GEMINI
    assert "scout" not in _loaded().agent.phases


def test_unsetting_what_is_not_set_is_not_an_error(capsys):
    assert config_cli.main(["model", "scout", "--unset"]) == 0
    assert "was not set" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["model", "scout"], ["model", "scout", GEMINI, "--unset"]])
def test_a_model_xor_unset_is_required(argv):
    assert config_cli.main(argv) == 2


def test_a_tier_name_is_refused_as_a_tier_value(capsys):
    assert config_cli.main(["model", "standard", "light"]) == 1
    assert "tier name" in capsys.readouterr().err


def test_unset_prunes_emptied_parents_in_one_write():
    config_cli.main(["model", "scout", GEMINI])
    path = config.workbench_config.global_config_path()
    assert config_cli.main(["model", "scout", "--unset"]) == 0
    assert "scout" not in path.read_text()
    assert not list(path.parent.glob(".*.tmp"))


def test_unset_without_yq_refuses_rather_than_rewriting_the_file(monkeypatch):
    config_cli.main(["model", "scout", GEMINI])
    before = config.workbench_config.global_config_path().read_text()
    monkeypatch.setattr(config.workbench_config_write.shutil, "which", lambda _name: None)
    with pytest.raises(config.workbench_config.ConfigError, match="yq is required"):
        config.workbench_config_write.unset_value("agent.phases.scout.model", config.workbench_config.global_config_path())
    assert config.workbench_config.global_config_path().read_text() == before
