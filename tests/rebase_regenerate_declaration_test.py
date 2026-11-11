"""SSOT check: this repo's rebase.regenerate declaration vs generated files."""

from __future__ import annotations

import fnmatch
import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import config.workbench_config
import core.serde

# Generated files this repo cannot rebuild without inputs a rebase does not
# have. Marking a new linguist-generated file forces a decision: add a
# generator and a glob, or add the path here with a reason.
UNCOVERED = {
    "CHANGELOG.md": "release-please writes this; no input-free generator",
    "ai/claude/CHANGELOG.md": (
        "release-please writes the otto-ai-tools changelog; no input-free generator"
    ),
    ".github/.release-please-manifest.json": (
        "release-please writes this; no input-free generator"
    ),
    "tests/weights.tsv": (
        "generate-test-weights needs junit reports; --run is a full suite"
    ),
}


def _rebase_config() -> config.workbench_config.RebaseConfig:
    data = config.workbench_config.read_yaml(
        config.workbench_config.project_config_path(REPO_ROOT),
    )
    return core.serde.from_dict(
        config.workbench_config.RebaseConfig, data["rebase"],
    )


def _git(*args: str, input: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=True, capture_output=True, text=True, input=input,
    ).stdout


def _tracked() -> list[str]:
    return [path for path in _git("ls-files").splitlines() if path]


def _linguist_generated(paths: list[str]) -> set[str]:
    if not paths:
        return set()
    stdout = _git(
        "check-attr", "--stdin", "-z", "linguist-generated",
        input="\0".join(paths) + "\0",
    )
    matched = set()
    parts = stdout.split("\0")
    index = 0
    while index + 2 < len(parts):
        path, _attr, value = parts[index], parts[index + 1], parts[index + 2]
        if value == "true":
            matched.add(path)
        index += 3
    return matched


def _covered(paths: set[str], patterns: list[str]) -> set[str]:
    return {
        path for path in paths
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    }


def test_each_regenerate_command_is_an_executable_in_the_repo():
    cfg = _rebase_config()
    assert cfg.regenerate
    for command in cfg.regenerate:
        first = shlex.split(command)[0]
        path = REPO_ROOT / first
        assert path.is_file(), f"{first} is not a file under the repo root"
        assert os.access(path, os.X_OK), f"{first} is not executable"


def test_each_regenerate_paths_glob_matches_a_generated_file():
    cfg = _rebase_config()
    tracked = _tracked()
    generated = _linguist_generated(tracked)
    for pattern in cfg.regenerate_paths:
        hits = [path for path in tracked if fnmatch.fnmatchcase(path, pattern)]
        assert hits, f"{pattern} matches no tracked path"
        for path in hits:
            assert path in generated, (
                f"{pattern} matches {path}, which is not linguist-generated"
            )


def test_uncovered_generated_files_are_exactly_the_known_set():
    cfg = _rebase_config()
    generated = _linguist_generated(_tracked())
    uncovered = generated - _covered(generated, cfg.regenerate_paths)
    assert uncovered == set(UNCOVERED), (
        "uncovered generated files changed: "
        f"{sorted(uncovered ^ set(UNCOVERED))}. "
        "Add a generator and a glob, or add the path to UNCOVERED with a reason."
    )
