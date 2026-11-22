"""Skills whose body is rule text rather than a procedure.

A rule in ``ai/guidelines/rules/`` is loaded into every session, by both Claude
Code and Pi. Guidance that applies only sometimes is written as a skill
instead, because description matching is the one conditional load both
harnesses share. A skill that carries a rule's text declares it with
``kind: rule`` in its frontmatter.

Tools that read the rule set — to match review comments against it, or to
decide whether a memory is already covered — must read these skills too, or
moving a rule into a skill reads as the rule having been deleted. This module
is the one place that says which skills those are, so the marker is read one way.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

SKILLS_REL = Path("ai") / "skills"

# The frontmatter line that marks a skill as rule text.
RULE_MARKER = "kind: rule"


def _frontmatter_lines(text: str) -> list[str]:
    """The lines between the opening and closing ``---``; empty when there is none."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return []
    for end, line in enumerate(lines[1:], start=1):
        if line == "---":
            return lines[1:end]
    return []


def is_rule_skill(text: str) -> bool:
    """True when *text*, a SKILL.md, declares itself rule text."""
    return RULE_MARKER in _frontmatter_lines(text)


def rule_skill_files(workbench: Path) -> list[Path]:
    """Every ``ai/skills/<name>/SKILL.md`` under *workbench* that declares ``kind: rule``."""
    skills_dir = workbench / SKILLS_REL
    if not skills_dir.is_dir():
        return []
    return [
        path for path in sorted(skills_dir.glob("*/SKILL.md"))
        if is_rule_skill(path.read_text(encoding="utf-8"))
    ]


def display_name(path: Path) -> str:
    """The name a report shows for a rule skill: ``<skill>/SKILL.md``.

    Every skill file is called SKILL.md, so the basename alone would label six
    different rules the same.
    """
    return f"{path.parent.name}/{path.name}"
