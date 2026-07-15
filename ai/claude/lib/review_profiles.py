"""Review profiles: per-domain review doctrine routed by file paths.

Profiles live in `.claude/review/profiles/*.yml` and contain structured
review rules matched against changed file paths. Group reviewers receive
only the profiles relevant to their files.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import log

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass
class ReviewRule:
    severity: str
    rule: str
    evidence: str = ""


@dataclass
class ReviewProfile:
    name: str
    description: str
    paths: list[str] = field(default_factory=list)
    rules: list[ReviewRule] = field(default_factory=list)


def _parse_rules(data: dict) -> list[ReviewRule]:
    rules: list[ReviewRule] = []
    for r in data.get("rules", []):
        if not isinstance(r, dict) or "rule" not in r:
            continue
        rules.append(ReviewRule(
            severity=r.get("severity", "should-fix"),
            rule=r["rule"],
            evidence=r.get("evidence", ""),
        ))
    return rules


def load_profiles(wt_path: str) -> list[ReviewProfile]:
    if yaml is None:
        return []
    profiles_dir = Path(wt_path) / ".claude" / "review" / "profiles"
    if not profiles_dir.is_dir():
        return []

    profiles: list[ReviewProfile] = []
    for path in sorted(profiles_dir.glob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text())
        except Exception:
            log.warn(f"Skipping malformed profile: {path.name}")
            continue
        if not isinstance(data, dict):
            continue

        profiles.append(ReviewProfile(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            paths=data.get("paths", []),
            rules=_parse_rules(data),
        ))
    return profiles


def match_profiles(
    profiles: list[ReviewProfile], changed_files: list[str],
) -> list[ReviewProfile]:
    matched: list[ReviewProfile] = []
    for profile in profiles:
        if not profile.paths:
            matched.append(profile)
            continue
        if any(any(fnmatch.fnmatch(f, p) for f in changed_files) for p in profile.paths):
            matched.append(profile)
    return matched


def match_profiles_for_group(
    profiles: list[ReviewProfile], group_files: list[str],
) -> list[ReviewProfile]:
    return match_profiles(profiles, group_files)


_SEVERITY_ORDER = {"must-fix": 0, "should-fix": 1, "nit": 2}


def format_profiles_section(profiles: list[ReviewProfile]) -> str:
    if not profiles:
        return ""

    parts: list[str] = [
        "#### Review profiles",
        "",
        "These rules are derived from domain-specific review doctrine —",
        "incident history, design decisions, and patterns senior engineers flag.",
        "Treat each rule as a review criterion at the stated severity.",
        "",
    ]

    for profile in profiles:
        parts.append(f"**{profile.name}**")
        if profile.description:
            parts.append(f": {profile.description}")
        parts.append("")
        sorted_rules = sorted(
            profile.rules,
            key=lambda r: _SEVERITY_ORDER.get(r.severity, 9),
        )
        for rule in sorted_rules:
            parts.extend(_format_rule_lines(rule))
        parts.append("")

    return "\n".join(parts)


def _format_rule_lines(rule: ReviewRule) -> list[str]:
    lines = [f"- [{rule.severity}] {rule.rule}"]
    if rule.evidence:
        lines.append(f"  Evidence: {rule.evidence}")
    return lines
