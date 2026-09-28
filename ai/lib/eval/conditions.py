"""The two rule prefixes an A/B run compares, and the trees they are served from.

The prefix under test is the 27 files installed at ``~/.claude/rules/`` — the
merge of repo defaults, generated files and operator overrides — not the 18 in
``ai/guidelines/rules/``. A trim set written against the repo sources would
leave the five generated files identical in both arms and quietly shrink the
contrast the experiment exists to measure.

Membership is explicit on both sides rather than "kept is everything not
dropped": a rule added later would otherwise join the kept arm silently and
change what the two conditions mean without anyone editing this file.
"""

# doc-group: eval

from __future__ import annotations

import shutil
from pathlib import Path

CONDITIONS: tuple[str, ...] = ("full", "trimmed")

# Rules a fix agent acts on: language and project conventions, testing, shell
# portability, secret handling.
KEPT_RULES = frozenset({
    "general", "testing", "testing.local", "bash", "security-secrets", "ci",
    "go", "kotlin", "ansible", "docker",
    "tools.generated.go", "tools.generated.infra",
})

# Rules addressed to a human operator or to harness mechanics. A fix agent
# neither opens a PR nor files an issue nor authors a skill.
DROPPED_RULES = frozenset({
    "git-operations", "git.generated", "self-review", "issue-tracker",
    "issue-tracker.local", "skills", "artifacts", "output", "bash-tool",
    "rules-authoring", "claude-review-dev", "workbench",
    "tools.generated", "tools.generated.workbench", "tools.generated.java",
})


class UnclassifiedRule(Exception):
    """An installed rule belongs to neither arm, so the trim set is stale."""


def classify(installed: list[str]) -> None:
    """Raise unless every installed rule name is in exactly one arm."""
    both = sorted(KEPT_RULES & DROPPED_RULES)
    if both:
        raise UnclassifiedRule(
            f"rules in both arms, so the trim set is contradictory: "
            f"{', '.join(both)}",
        )
    unknown = sorted(set(installed) - KEPT_RULES - DROPPED_RULES)
    if unknown:
        raise UnclassifiedRule(
            f"rules in neither arm, so the trim set no longer describes the "
            f"prefix: {', '.join(unknown)}",
        )


def seed_config_tree(source: Path, dest: Path, condition: str) -> Path:
    """Copy *source* to *dest*, keeping the rules *condition* calls for.

    Returns the absolute destination: the CLI rejects a relative
    ``CLAUDE_CONFIG_DIR`` outright.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")

    dest = dest.resolve()
    rules_dir = dest / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        if item.name == "rules":
            continue
        if item.is_file():
            shutil.copy2(item, dest / item.name)

    src_rules = source / "rules"
    if not src_rules.is_dir():
        return dest

    rules = sorted(src_rules.glob("*.md"))
    classify([rule.stem for rule in rules])
    wanted = rules if condition == "full" else [r for r in rules if r.stem in KEPT_RULES]
    for rule in wanted:
        shutil.copy2(rule, rules_dir / rule.name)

    return dest
