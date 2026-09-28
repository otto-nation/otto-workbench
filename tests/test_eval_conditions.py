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
