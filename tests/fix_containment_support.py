"""A real git repo with hooks neutralised, for the fix containment suites."""

import sys
from pathlib import Path

from conftest import git_out


def _repo(tmp_path, files):
    wt = tmp_path / "wt"
    wt.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(wt, "init", "-q", "-b", "main")
    git_out(wt, "config", "user.email", "test@example.com")
    git_out(wt, "config", "user.name", "Test")
    git_out(wt, "config", "commit.gpgsign", "false")
    git_out(wt, "config", "core.hooksPath", str(hooks))
    for rel, text in files.items():
        path = wt / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git_out(wt, "add", "-A")
    git_out(wt, "commit", "-qm", "initial")
    return wt
