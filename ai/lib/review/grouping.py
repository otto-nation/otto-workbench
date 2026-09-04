"""How a review's changed files are divided, and what doctrine applies to each.

Tier classification ranks a path by the scrutiny it warrants; grouping turns
the ranked file list into the review groups one agent each is given; profiles
are the per-domain doctrine `.claude/review/profiles/*.yml` declares, routed to
a group by the paths it holds.

The three answer one question between them — what a given file is worth to a
reviewer — which is why the prompt's byte budget asks here before deciding what
it can afford to carry.
"""

# doc-group: pipeline

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

from core import log
from gh.types import PRMetadata
from review.types import Group

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_GROUP_LINES = 800
MAX_GROUP_FILES = 15
HOLISTIC_MIN_GROUPS = 8

GROUP_TIER1 = "tier1-critical"
GROUP_TIER3 = "tier3-generated"

TIER1_BASENAMES = {
    "CLAUDE.md", ".cursorrules", "AGENTS.md", "GEMINI.md",
    "go.mod", "package.json", "package-lock.json", "requirements.txt", "Gemfile",
}
TIER1_EXTENSIONS = {".proto", ".graphql"}
TIER1_PATH_SEGMENTS = {
    "migrations", "auth", "crypto", "permissions",
    "vault", "network-policies", "authorization-policies",
}
TIER3_BASENAMES = {"go.sum"}
TIER3_BASENAMES_SUFFIXES = (
    ".pb.go", "_pb2.py", "_pb.ts", "_pb2_grpc.py",
    ".latest.sql", ".ko.yaml",
)
TIER3_PATH_SEGMENTS = {"gen", "testdata"}


# ── Tier classification ──────────────────────────────────────────────────────

def classify_tier(path: str) -> int:
    """How much scrutiny ``path`` warrants: 1 critical, 2 ordinary, 3 generated.

    Generated wins over critical, so a `.pb.go` under `auth/` is tier 3 rather
    than tier 1 — nobody wrote it and nobody will fix it in this review.
    """
    parts = path.split("/")
    basename = parts[-1]

    if any(seg in TIER3_PATH_SEGMENTS for seg in parts):
        return 3
    if basename in TIER3_BASENAMES or basename.endswith(TIER3_BASENAMES_SUFFIXES):
        return 3
    if basename in TIER1_BASENAMES:
        return 1
    if any(basename.endswith(ext) for ext in TIER1_EXTENSIONS):
        return 1
    if any(seg in TIER1_PATH_SEGMENTS for seg in parts):
        return 1
    return 2


# ── File grouping ─────────────────────────────────────────────────────────────

def _split_large_dir(name: str, files: list[str], file_lines: dict[str, int]) -> list[Group]:
    groups: list[Group] = []
    sub_files: list[str] = []
    sub_lines = 0
    sub_idx = 1
    for f in files:
        fl = file_lines[f]
        if sub_files and (sub_lines + fl > MAX_GROUP_LINES or len(sub_files) >= MAX_GROUP_FILES):
            groups.append(Group(f"{name}-{sub_idx}", sub_files, sub_lines))
            sub_files = []
            sub_lines = 0
            sub_idx += 1
        sub_files.append(f)
        sub_lines += fl
    if sub_files:
        groups.append(Group(f"{name}-{sub_idx}", sub_files, sub_lines))
    return groups


def group_files(pr: PRMetadata) -> list[Group]:
    """The PR's changed files divided into the groups one agent each reviews.

    Tier 1 and tier 3 each become a single group; tier 2 is grouped by
    top-level directory, and a directory over `MAX_GROUP_LINES` or
    `MAX_GROUP_FILES` is split into numbered sub-groups.
    """
    file_lines = {f["path"]: f["additions"] + f["deletions"] for f in pr.files}

    tiers: dict[int, list[str]] = {1: [], 2: [], 3: []}
    tier_lines: dict[int, int] = {1: 0, 2: 0, 3: 0}

    for path, lines in file_lines.items():
        t = classify_tier(path)
        tiers[t].append(path)
        tier_lines[t] += lines

    groups: list[Group] = []

    if tiers[1]:
        groups.append(Group(GROUP_TIER1, tiers[1], tier_lines[1]))

    dir_files: dict[str, list[str]] = {}
    dir_lines: dict[str, int] = {}
    dir_order: list[str] = []
    for f in tiers[2]:
        d = f.split("/")[0]
        if d not in dir_files:
            dir_files[d] = []
            dir_lines[d] = 0
            dir_order.append(d)
        dir_files[d].append(f)
        dir_lines[d] += file_lines[f]

    for d in dir_order:
        files = dir_files[d]
        total = dir_lines[d]
        if total > MAX_GROUP_LINES or len(files) > MAX_GROUP_FILES:
            groups.extend(_split_large_dir(d, files, file_lines))
        else:
            groups.append(Group(d, files, total))

    if tiers[3]:
        groups.append(Group(GROUP_TIER3, tiers[3], tier_lines[3]))

    return groups


def _merge_score(a: Group, b: Group) -> tuple[int, int]:
    """Lower score = better merge: longest shared name prefix, then smallest combined size."""
    # os.path.commonprefix is character-based (not path-component-based), which is
    # intentional here — we want a quick name-similarity heuristic, not strict path ancestry.
    shared = len(os.path.commonprefix([a.name, b.name]))
    return (-shared, a.lines + b.lines)


def _find_best_merge_pair(groups: list[Group]) -> tuple[int, int]:
    pairs = [(i, j) for i in range(len(groups)) for j in range(i + 1, len(groups))]
    return min(pairs, key=lambda p: _merge_score(groups[p[0]], groups[p[1]]))


def merge_smallest_groups(groups: list[Group], max_groups: int) -> list[Group]:
    """``groups`` reduced to at most ``max_groups`` by repeatedly merging a pair.

    Each round merges the pair sharing the longest name prefix, breaking ties on
    combined size, so a cap is spent on neighbouring directories before it costs
    an unrelated group its own agent.
    """
    groups = list(groups)
    while len(groups) > max_groups:
        i, j = _find_best_merge_pair(groups)
        a, b = groups[i], groups[j]
        merged = Group(
            name=f"{a.name}+{b.name}",
            files=a.files + b.files,
            lines=a.lines + b.lines,
        )
        groups = [g for k, g in enumerate(groups) if k not in (i, j)]
        groups.append(merged)
    return groups


# ── Review profiles ───────────────────────────────────────────────────────────

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
    """Every profile under ``wt_path``'s `.claude/review/profiles/`, by filename.

    A malformed profile is skipped with a warning rather than failing the run,
    and no profiles at all — including on a machine without PyYAML — is an
    empty list.
    """
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
    """The profiles whose paths match any of ``changed_files``.

    A profile declaring no paths is doctrine for the whole repository and
    matches whatever it is given, an empty file list included.
    """
    matched: list[ReviewProfile] = []
    for profile in profiles:
        if not profile.paths:
            matched.append(profile)
            continue
        if any(any(fnmatch.fnmatch(f, p) for f in changed_files) for p in profile.paths):
            matched.append(profile)
    return matched


_SEVERITY_ORDER = {"must-fix": 0, "should-fix": 1, "nit": 2}


def format_profiles_section(profiles: list[ReviewProfile]) -> str:
    """``profiles`` rendered as the prompt's "Review profiles" section.

    Rules are ordered by severity within each profile so the agent reads the
    must-fix doctrine first. No profiles renders to the empty string, which the
    caller drops rather than emitting an empty heading.
    """
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
