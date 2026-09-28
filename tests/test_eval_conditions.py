"""Tests for eval.conditions: rule-prefix classification and config-tree seeding."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from eval import conditions


def test_every_installed_rule_is_classified_into_exactly_one_arm():
    both = conditions.KEPT_RULES & conditions.DROPPED_RULES
    assert both == frozenset(), f"classified into both arms: {sorted(both)}"
    assert len(conditions.KEPT_RULES) == 12
    assert len(conditions.DROPPED_RULES) == 15


def test_an_unrecognised_rule_fails_the_run_rather_than_joining_an_arm():
    installed = sorted(conditions.KEPT_RULES | conditions.DROPPED_RULES) + ["brand-new"]
    with pytest.raises(conditions.UnclassifiedRule, match="brand-new"):
        conditions.classify(installed)


def test_a_rule_in_both_arms_fails_the_run(monkeypatch):
    monkeypatch.setattr(conditions, "KEPT_RULES", frozenset({"shared", "kept"}))
    monkeypatch.setattr(conditions, "DROPPED_RULES", frozenset({"shared", "dropped"}))
    with pytest.raises(conditions.UnclassifiedRule, match="shared"):
        conditions.classify(["kept", "dropped", "shared"])


def test_the_full_arm_seeds_every_rule_and_the_trimmed_arm_only_the_kept(tmp_path):
    source = tmp_path / "real"
    (source / "rules").mkdir(parents=True)
    for name in conditions.KEPT_RULES | conditions.DROPPED_RULES:
        (source / "rules" / f"{name}.md").write_text(f"# {name}\n")
    (source / "settings.json").write_text("{}")

    full = conditions.seed_config_tree(source, tmp_path / "cc-full", "full")
    trimmed = conditions.seed_config_tree(source, tmp_path / "cc-trimmed", "trimmed")

    assert len(list((full / "rules").glob("*.md"))) == 27
    assert {p.stem for p in (trimmed / "rules").glob("*.md")} == set(conditions.KEPT_RULES)
    assert (trimmed / "settings.json").is_file()


def test_seeding_copies_and_leaves_the_source_tree_untouched(tmp_path):
    source = tmp_path / "real"
    (source / "rules").mkdir(parents=True)
    for name in conditions.KEPT_RULES | conditions.DROPPED_RULES:
        (source / "rules" / f"{name}.md").write_text("x")
    (source / "settings.json").write_text("{}")
    before = sorted(p.name for p in (source / "rules").iterdir())

    dest = conditions.seed_config_tree(source, tmp_path / "cc-trimmed", "trimmed")

    assert sorted(p.name for p in (source / "rules").iterdir()) == before
    assert len(before) == 27
    assert {p.stem for p in (dest / "rules").glob("*.md")} == set(conditions.KEPT_RULES)
    assert (dest / "settings.json").is_file()


def test_the_seeded_path_is_absolute_because_the_cli_rejects_a_relative_one(
    tmp_path, monkeypatch,
):
    source = tmp_path / "real"
    (source / "rules").mkdir(parents=True)
    (source / "settings.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    dest = conditions.seed_config_tree(source, Path("cc-full"), "full")
    assert dest.is_absolute()


def _pi_workbench(tmp_path, files: dict[str, str]) -> Path:
    wb = tmp_path / "wb"
    rules = wb / "ai" / "guidelines" / "rules"
    rules.mkdir(parents=True)
    for name, body in files.items():
        (rules / name).write_text(body)
    return wb


def test_pi_source_stages_layers_and_reuses_the_same_trim_set(tmp_path, monkeypatch):
    wb = _pi_workbench(tmp_path, {
        "general.md": "# general\n",
        "git-operations.md": "# git\n",
    })
    monkeypatch.setattr(conditions, "state_dir", lambda: tmp_path / "no-state")
    monkeypatch.setattr(conditions, "config_dir", lambda: tmp_path / "no-config")
    source = conditions.stage_pi_seed_source(tmp_path / "pi-source", workbench_dir=wb)
    assert {p.stem for p in (source / "rules").glob("*.md")} == {
        "general", "git-operations",
    }
    trimmed = conditions.seed_config_tree(source, tmp_path / "cc-trimmed", "trimmed")
    assert {p.stem for p in (trimmed / "rules").glob("*.md")} == {"general"}


def test_pi_source_later_layer_wins_and_disabled_drops(tmp_path, monkeypatch):
    wb = _pi_workbench(tmp_path, {"general.md": "# repo\n"})
    state = tmp_path / "state" / "rules"
    state.mkdir(parents=True)
    (state / "general.md").write_text("# generated\n")
    user = tmp_path / "config" / "overrides" / "ai" / "guidelines" / "rules"
    user.mkdir(parents=True)
    (user / "testing.md").write_text("# user\n")
    (user / "general.disabled").write_text("")
    monkeypatch.setattr(conditions, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(conditions, "config_dir", lambda: tmp_path / "config")
    source = conditions.stage_pi_seed_source(tmp_path / "pi-source", workbench_dir=wb)
    names = {p.stem: p.read_text() for p in (source / "rules").glob("*.md")}
    assert "general" not in names
    assert names["testing"] == "# user\n"


def test_pi_source_missing_layers_fail_loudly(tmp_path):
    wb = tmp_path / "empty-wb"
    wb.mkdir()
    with pytest.raises(conditions.MissingRuleSource, match="Pi rule layers"):
        conditions.stage_pi_seed_source(tmp_path / "dest", workbench_dir=wb)


def test_claude_source_missing_rules_fail_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    with pytest.raises(conditions.MissingRuleSource, match="Claude rule prefix"):
        conditions.claude_seed_source()


def test_prepare_seed_source_dispatches_on_kind(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    (claude / "rules").mkdir(parents=True)
    (claude / "rules" / "general.md").write_text("# g\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    assert conditions.prepare_seed_source("claude", tmp_path / "unused") == claude

    wb = _pi_workbench(tmp_path, {"general.md": "# g\n"})
    monkeypatch.setattr(conditions, "state_dir", lambda: tmp_path / "no-state")
    monkeypatch.setattr(conditions, "config_dir", lambda: tmp_path / "no-config")
    dest = tmp_path / "pi-dest"
    source = conditions.prepare_seed_source("pi", dest, workbench_dir=wb)
    assert source == dest.resolve()
    assert (source / "rules" / "general.md").is_file()
