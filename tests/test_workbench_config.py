"""Tests for workbench_config — the typed, layered workbench configuration."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import workbench_config as wc
from review_common import Effort, Phase, Thinking


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A sandboxed config root plus an empty project directory."""
    config_root = tmp_path / "config"
    config_root.mkdir()
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_root))
    project = tmp_path / "project"
    project.mkdir()
    return config_root, project


def _write(path: Path, text: str) -> None:
    path.write_text(text.lstrip("\n"))


# ── Loading and merging ─────────────────────────────────────────────────────


def test_missing_files_give_built_in_defaults(roots):
    _, project = roots
    cfg = wc.load_config(project)
    assert cfg.reuse.default is wc.ReuseLevel.FULL
    assert cfg.reuse.level is None
    assert cfg.review.model is None
    assert cfg.review.phases == {}
    assert cfg.review.issue_tracker.provider is wc.IssueProvider.LINEAR


def test_global_config_is_typed(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
reuse:
  level: ultra
review:
  effort: high
  thinking: medium
  phases:
    scout:
      model: haiku
""")
    cfg = wc.load_config(project)
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA
    assert cfg.review.effort is Effort.HIGH
    assert cfg.review.thinking is Thinking.MEDIUM
    assert cfg.review.phases[Phase.SCOUT].model == "haiku"


def test_project_config_wins_over_global(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n")
    _write(project / ".workbench.yml", "review:\n  model: opus\n")
    assert wc.load_config(project).review.model == "opus"


def test_project_config_does_not_discard_global_siblings(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
review:
  model: sonnet
  issue_tracker:
    provider: github
    team: ENG
""")
    _write(project / ".workbench.yml", "review:\n  phases:\n    fix:\n      model: opus\n")
    cfg = wc.load_config(project)
    assert cfg.review.model == "sonnet"
    assert cfg.review.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.review.issue_tracker.team == "ENG"
    assert cfg.review.phases[Phase.FIX].model == "opus"


def test_an_empty_file_is_not_an_error(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "")
    assert wc.load_config(project) == wc.WorkbenchConfig()


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_reads_the_same_config(roots, monkeypatch):
    """PyYAML is optional, so the yq path has to produce the same answer."""
    monkeypatch.setattr(wc, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n  effort: high\n")
    cfg = wc.load_config(project)
    assert cfg.review.model == "sonnet"
    assert cfg.review.effort is Effort.HIGH


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_rejects_malformed_yaml(roots, monkeypatch):
    monkeypatch.setattr(wc, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: [unclosed\n")
    with pytest.raises(wc.ConfigError):
        wc.load_config(project)


# ── Error handling ──────────────────────────────────────────────────────────


def test_unknown_enum_value_is_rejected_by_file_name(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  thinking: turbo\n")
    with pytest.raises(wc.ConfigError) as excinfo:
        wc.load_config(project)
    assert "config.yml" in str(excinfo.value)
    assert "turbo" in str(excinfo.value)


def test_unknown_phase_key_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  phases:\n    scoot:\n      model: haiku\n")
    with pytest.raises(wc.ConfigError, match="scoot"):
        wc.load_config(project)


def test_load_config_or_default_survives_a_bad_file(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  thinking: turbo\n")
    assert wc.load_config_or_default(project) == wc.WorkbenchConfig()


def test_malformed_yaml_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: [unclosed\n")
    with pytest.raises(wc.ConfigError):
        wc.load_config(project)


def test_a_non_mapping_file_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "- one\n- two\n")
    with pytest.raises(wc.ConfigError, match="mapping"):
        wc.load_config(project)


# ── Writing ─────────────────────────────────────────────────────────────────


def test_set_value_creates_and_updates_the_global_file(roots):
    config_root, _ = roots
    wc.set_value("reuse.level", "ultra")
    assert wc.load_config().reuse.level is wc.ReuseLevel.ULTRA
    wc.set_value("reuse.level", "lite")
    assert wc.load_config().reuse.level is wc.ReuseLevel.LITE
    assert (config_root / "config.yml").is_file()


def test_set_value_preserves_unrelated_keys(roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n")
    wc.set_value("reuse.level", "ultra")
    cfg = wc.load_config()
    assert cfg.review.model == "sonnet"
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA
