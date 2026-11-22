"""Tests for skills that carry rule text, and the scanners that must still see them."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import core.rule_skills  # noqa: E402
import memory.promote  # noqa: E402
from retro.rules import load_rules  # noqa: E402

RULE_SKILL = "---\nname: demo\nkind: rule\n---\n\n# Demo\n\n- Never amend a pushed commit\n"
PLAIN_SKILL = "---\nname: other\n---\n\n# Other\n\n- Run the thing\n"


def _workbench(tmp_path: Path) -> Path:
    wb = tmp_path / "wb"
    for name, text in (("demo", RULE_SKILL), ("other", PLAIN_SKILL)):
        skill = wb / "ai" / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(text)
    rules = wb / "ai" / "guidelines" / "rules"
    rules.mkdir(parents=True)
    (rules / "general.md").write_text("# General\n\n- Keep functions small and focused\n")
    return wb


def test_only_a_skill_declaring_kind_rule_is_rule_text(tmp_path):
    wb = _workbench(tmp_path)
    assert [p.parent.name for p in core.rule_skills.rule_skill_files(wb)] == ["demo"]


def test_kind_rule_in_the_body_is_not_the_marker():
    body_only = "---\nname: x\n---\n\nkind: rule\n"
    assert core.rule_skills.is_rule_skill(body_only) is False
    assert core.rule_skills.is_rule_skill("kind: rule\n") is False


def test_an_unclosed_frontmatter_is_not_a_marker():
    assert core.rule_skills.is_rule_skill("---\nname: x\nkind: rule\n") is False


def test_a_workbench_without_skills_has_no_rule_skills(tmp_path):
    assert core.rule_skills.rule_skill_files(tmp_path) == []


def test_the_display_name_keeps_the_skill_directory(tmp_path):
    wb = _workbench(tmp_path)
    (path,) = core.rule_skills.rule_skill_files(wb)
    assert core.rule_skills.display_name(path) == "demo/SKILL.md"


def test_retro_loads_a_rule_skill_beside_the_rule_files(tmp_path):
    rules = load_rules(_workbench(tmp_path))
    assert [r["filename"] for r in rules] == ["general.md", "demo/SKILL.md"]
    assert "amend" in rules[1]["keywords"]


def test_retro_loads_a_rule_skill_when_there_is_no_rules_directory(tmp_path):
    wb = _workbench(tmp_path)
    (wb / "ai" / "guidelines" / "rules" / "general.md").unlink()
    (wb / "ai" / "guidelines" / "rules").rmdir()
    assert [r["filename"] for r in load_rules(wb)] == ["demo/SKILL.md"]


def test_the_repos_issue_tracker_skill_is_still_a_rule_retro_can_match():
    repo = Path(__file__).resolve().parent.parent
    assert "issue-tracker/SKILL.md" in {r["filename"] for r in load_rules(repo)}
    assert "issue-tracker/SKILL.md" in {r["filename"] for r in memory.promote.scan_rules(repo)}


def test_promote_scan_lists_a_rule_skill_beside_the_rule_files(tmp_path):
    scanned = memory.promote.scan_rules(_workbench(tmp_path))
    assert [r["filename"] for r in scanned] == ["general.md", "demo/SKILL.md"]
    assert scanned[1]["heading"] == "Demo"
