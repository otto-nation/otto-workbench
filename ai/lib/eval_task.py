"""Task-agnostic evaluation plumbing.

An eval case is a corpus directory with a manifest. The manifest's `task` field
picks how the case is run and scored; everything here is what is common to all
tasks — the fixture repo, the run options, the artifacts a run leaves behind.

Task implementations live in `eval_scoring_<task>.py` and are resolved lazily so
that adding a task does not make every other task's dependencies load.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ai_usage import SessionUsage
from eval_scoring import ScoringResult

DEFAULT_TASK = "review"

# Inherited git env vars point at the *calling* repo. A fixture repo built with
# them set silently becomes a worktree of this checkout.
_GIT_ENV_SANITIZE = [
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
    "GIT_CEILING_DIRECTORIES", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
]


# Seconds one eval case gets before it is scored as a failure. Not a tier from
# `timeouts`: those bound a subprocess that should have answered by now, while
# this is a budget for an agent that could reasonably keep working — the number
# says how long a case is worth, and `--timeout` exists so a slow model can be
# given more. The eval job's own `timeout-minutes` is the real backstop.
EVAL_CASE_BUDGET = 300


@dataclass(frozen=True)
class RunOptions:
    """Everything a task needs from the command line, model included."""
    model: str = ""
    effort: str = "low"
    timeout: int = EVAL_CASE_BUDGET
    verbose: bool = False


@dataclass
class RunArtifacts:
    """What one run left behind, for the scorer and for cleanup."""
    exit_code: int = 0
    usage: SessionUsage = field(default_factory=SessionUsage)
    temp_dirs: list[str] = field(default_factory=list)
    # Task-specific outputs: findings for review, command results for ci-fix.
    data: dict = field(default_factory=dict)


class EvalTask(Protocol):
    name: str

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        ...

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        ...


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in _GIT_ENV_SANITIZE:
        env.pop(key, None)
    return env


def _copy_into(src_dir: Path, dest_dir: Path) -> None:
    for item in src_dir.iterdir():
        dest = dest_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def create_temp_repo(src_dir: str, prefix: str = "eval-") -> str:
    """Copy a case's sources into a throwaway git repo with an `eval` branch."""
    tmpdir = tempfile.mkdtemp(prefix=prefix)
    env = clean_env()
    git = [
        "git", "-C", tmpdir,
        "-c", "user.email=eval@eval.local",
        "-c", "user.name=eval",
    ]
    steps = [
        ["init", "-b", "main"],
        ["commit", "--allow-empty", "-m", "initial"],
        ["checkout", "-b", "eval"],
    ]
    for step in steps:
        subprocess.run(git + step, capture_output=True, check=True, env=env)

    _copy_into(Path(src_dir), Path(tmpdir))

    for step in (["add", "-A"], ["commit", "-m", "add buggy code"],
                 ["remote", "add", "origin", tmpdir], ["fetch", "origin"]):
        subprocess.run(git + step, capture_output=True, check=True, env=env)

    return tmpdir


def task_name(manifest: dict) -> str:
    """The task a manifest declares. Absent means review — the field is additive."""
    return manifest.get("task") or DEFAULT_TASK


def _review_task() -> EvalTask:
    from eval_scoring_review import ReviewTask
    return ReviewTask()


def _cifix_task() -> EvalTask:
    from eval_scoring_cifix import CiFixTask
    return CiFixTask()


def _skill_task() -> EvalTask:
    from eval_scoring_skill import SkillTask
    return SkillTask()


_TASK_FACTORIES = {
    DEFAULT_TASK: _review_task,
    "ci-fix": _cifix_task,
    "skill": _skill_task,
}


def get_task(name: str) -> EvalTask:
    if name not in _TASK_FACTORIES:
        known = ", ".join(sorted(_TASK_FACTORIES))
        raise KeyError(f"unknown eval task {name!r} — known tasks: {known}")
    return _TASK_FACTORIES[name]()
