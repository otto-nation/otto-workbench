"""Pin the split of the revert-check guidance between testing.md and its skill."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.rule_skills  # noqa: E402

TESTING = REPO_ROOT / "ai" / "guidelines" / "rules" / "testing.md"
SKILL = REPO_ROOT / "ai" / "skills" / "test-verification" / "SKILL.md"
HEADING = "A Test Must Fail When Its Subject Breaks"


def _section(text: str, heading: str) -> str:
    match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", text)
    assert match, heading
    start = match.end()
    nxt = re.search(r"(?m)^## ", text[start:])
    return text[start:start + nxt.start()] if nxt else text[start:]


def _core() -> str:
    return " ".join(_section(TESTING.read_text(), HEADING).split())


def test_the_skill_exists_and_declares_kind_rule():
    assert core.rule_skills.is_rule_skill(SKILL.read_text()) is True


def test_the_skill_keeps_the_heading_and_the_procedure():
    body = SKILL.read_text()
    assert f"## {HEADING}" in body
    assert "passes-at-base:" in body
    assert "check-new-tests" in body


def test_the_core_forbids_restoring_with_git_checkout():
    assert "Never restore with `git checkout <file>`" in _core()


def test_the_core_requires_the_whole_file_not_a_subset():
    core_text = _core()
    assert "run the whole test file" in core_text
    assert "`-k`" in core_text


def test_the_core_reverts_the_subject_line_not_the_expected_value():
    assert "never the expected value" in _core()


def test_the_core_points_at_the_skill():
    assert "ai/skills/test-verification/SKILL.md" in _core()


def test_the_procedure_narrative_is_not_duplicated_in_testing_md():
    assert "passes-at-base" not in TESTING.read_text()
