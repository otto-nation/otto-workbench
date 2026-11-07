"""PATH shim that refuses mutating git subcommands in the fix worktree.

Every ``agent.backend.invoke_fix`` call — every FIX-shaped phase, verify
gates included, both backends, eval callers included — runs with a temporary
directory first on ``PATH`` holding ``git`` → ``git-guard.sh``. The shim
refuses a guarded subcommand only when the target repo's absolute git dir
equals the fix worktree's. Everything else is exec'd to the real git
unchanged. The engine's own git calls, ``land``, and the verify-command
suite never see the shim: they use ``os.environ``, not the agent env.

Not a sandbox: an absolute git path, a ``PATH=`` override, or a ``!`` shell
alias bypasses it.
"""

# doc-group: backend

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import core.log
import git.client

GUARDED_SUBCOMMANDS: tuple[str, ...] = (
    "commit", "push", "rebase", "reset", "checkout", "switch", "restore",
    "stash", "clean", "merge", "cherry-pick", "revert", "apply", "am", "pull",
)
ENV_REAL_GIT = "WORKBENCH_REAL_GIT"
ENV_GUARDED_GIT_DIR = "WORKBENCH_GUARDED_GIT_DIR"
ENV_GUARDED_SUBCOMMANDS = "WORKBENCH_GUARDED_GIT_SUBCOMMANDS"
SHIM = Path(__file__).resolve().with_name("git-guard.sh")


@contextmanager
def guarded(env: Mapping[str, str], cwd: str) -> Iterator[dict[str, str]]:
    """Yield *env* with this module's ``git`` shim first on ``PATH``, or unchanged.

    Unchanged when ``git`` is missing from *env*'s ``PATH`` or *cwd* is not a
    repository, as with eval fixtures. The temp dir lives for the ``with``
    block, which is the agent invocation.
    """
    real = shutil.which("git", path=env.get("PATH", ""))
    gd = git.client.run("rev-parse", "--absolute-git-dir", cwd=cwd)
    if real is None or not gd.ok:
        why = "no git on PATH" if real is None else f"{cwd} is not a git repo"
        core.log.dim(f"git-guard: {why}; agent env unguarded")
        yield dict(env)
        return
    with tempfile.TemporaryDirectory(prefix="agent-git-guard-") as tmp:
        os.symlink(SHIM, Path(tmp) / "git")
        yield {
            **env,
            "PATH": tmp + os.pathsep + env.get("PATH", ""),
            ENV_REAL_GIT: real,
            ENV_GUARDED_GIT_DIR: os.path.realpath(gd.stdout.strip()),
            ENV_GUARDED_SUBCOMMANDS: " ".join(GUARDED_SUBCOMMANDS),
        }
