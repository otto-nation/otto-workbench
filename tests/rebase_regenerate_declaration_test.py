"""SSOT check: this repo's regenerate= gitattributes vs generated files."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# linguist-generated patterns this repo cannot rebuild without inputs a rebase
# does not have. Marking a new linguist-generated pattern forces a decision:
# add regenerate=<command>, or add the pattern here with a reason.
EXCLUDED = {
    "CHANGELOG.md": "release-please writes this; no input-free generator",
    ".github/.release-please-manifest.json": (
        "release-please writes this; no input-free generator"
    ),
    "tests/weights.tsv": (
        "generate-test-weights needs junit reports; --run is a full suite"
    ),
}

_NONE = frozenset({None, "set", "unset", "unspecified"})


def _parse_attr(item: str) -> tuple[str, str]:
    if item.startswith("-"):
        return item[1:], "unset"
    name, sep, value = item.partition("=")
    return (name, value) if sep else (name, "set")


def _attr_lines() -> list[tuple[str, dict[str, str]]]:
    entries: list[tuple[str, dict[str, str]]] = []
    for raw in (REPO_ROOT / ".gitattributes").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *items = line.split()
        entries.append((pattern, dict(_parse_attr(i) for i in items)))
    return entries


def test_each_regenerate_value_is_an_executable_in_the_repo():
    values = [
        (pattern, attrs["regenerate"])
        for pattern, attrs in _attr_lines()
        if attrs.get("regenerate") not in _NONE
    ]
    assert values, "no regenerate= attributes in .gitattributes"
    for pattern, command in values:
        path = REPO_ROOT / command
        assert path.is_file(), f"{pattern}: {command} is not a file under the repo root"
        assert os.access(path, os.X_OK), f"{pattern}: {command} is not executable"


def test_linguist_generated_patterns_are_attributed_or_excluded():
    generated = [
        (pattern, attrs)
        for pattern, attrs in _attr_lines()
        if attrs.get("linguist-generated") == "true"
    ]
    attributed = {
        pattern
        for pattern, attrs in generated
        if attrs.get("regenerate") not in _NONE
    }
    unattributed = {pattern for pattern, _ in generated if pattern not in attributed}
    assert unattributed == set(EXCLUDED), (
        "unattributed linguist-generated patterns changed: "
        f"{sorted(unattributed ^ set(EXCLUDED))}. "
        "Add regenerate=<command>, or add the pattern to EXCLUDED with a reason."
    )
    extra = attributed & set(EXCLUDED)
    assert not extra, f"EXCLUDED patterns also have regenerate=: {sorted(extra)}"
