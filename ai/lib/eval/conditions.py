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

import os
import shutil
from pathlib import Path

from core.workbench_paths import config_dir, state_dir

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


class MissingRuleSource(Exception):
    """The backend's rule-prefix source is not on disk, so an arm cannot be seeded."""


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


def prepare_seed_source(
    kind: str, dest: Path, *, workbench_dir: Path | None = None,
) -> Path:
    """The on-disk tree ``seed_config_tree`` copies from, for this backend.

    Claude's source is already a ``~/.claude``-shaped tree with ``rules/``.
    Pi has no such tree: interactive sessions read one concatenated
    ``AGENTS.md``. The three rule layers are staged into ``dest/rules/`` so
    the membership decision in ``seed_config_tree`` still runs over individual
    files — the trim set is not forked, only the source of those files.
    """
    if kind == "pi":
        if workbench_dir is None:
            raise MissingRuleSource(
                "Pi rule layers need a workbench checkout to seed from"
            )
        return stage_pi_seed_source(dest, workbench_dir=workbench_dir)
    return claude_seed_source()


def claude_seed_source() -> Path:
    """``CLAUDE_CONFIG_DIR`` or ``~/.claude``, required to hold ``rules/``."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    rules = home / "rules"
    if not rules.is_dir():
        raise MissingRuleSource(
            f"Claude rule prefix not found at {rules} "
            f"(set CLAUDE_CONFIG_DIR or install rules with otto-workbench sync)"
        )
    return home


def stage_pi_seed_source(dest: Path, *, workbench_dir: Path) -> Path:
    """Merge the three Pi rule layers into ``dest/rules/`` and return dest.

    Later layers win for a same-named file, matching ``resolve_rules``:
    repo defaults, then the generated machine layer, then operator overrides.
    A ``.disabled`` sentinel in the generated or override layer drops the
    name. Fails loudly when the merge yields no files — an empty arm would
    score as a successful trim.
    """
    dest = dest.resolve()
    rules_dir = dest / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    layers = _pi_rule_layers(workbench_dir)
    if not layers[0].is_dir():
        raise MissingRuleSource(
            f"Pi rule layers not found at {layers[0]} "
            f"(this workbench checkout has no ai/guidelines/rules)"
        )

    mapping: dict[str, Path] = {}
    disabled: set[str] = set()
    for layer in layers:
        _apply_rule_layer(mapping, disabled, layer)
    copied = 0
    for name, src in sorted(mapping.items()):
        if name in disabled or Path(name).stem in disabled:
            continue
        shutil.copy2(src, rules_dir / name)
        copied += 1
    if copied == 0:
        raise MissingRuleSource(
            f"Pi rule layers produced no files from {', '.join(str(p) for p in layers)}"
        )
    return dest


def _pi_rule_layers(workbench_dir: Path) -> tuple[Path, Path, Path]:
    return (
        workbench_dir / "ai" / "guidelines" / "rules",
        state_dir() / "rules",
        config_dir() / "overrides" / "ai" / "guidelines" / "rules",
    )


def _apply_rule_layer(
    mapping: dict[str, Path], disabled: set[str], layer: Path,
) -> None:
    if not layer.is_dir():
        return
    for path in sorted(layer.glob("*.md")):
        mapping[path.name] = path
    for path in sorted(layer.glob("*.disabled")):
        stem = path.name[: -len(".disabled")]
        disabled.add(stem)
        disabled.add(f"{stem}.md")


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
