"""Helpers shared by the suites split out of the former push_test.py."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402

from conftest import git_in, run_checked, seed_repo  # noqa: E402


_LOSING_HOOK = """#!/usr/bin/env bash
while read -r old new ref; do
  if [ "$old" = "0000000000000000000000000000000000000000" ]; then
    git update-ref -d "$ref"
  else
    git update-ref "$ref" "$old"
  fi
done
"""


def _never_runs(*cmd, **kwargs):
    """A `git.client.run` that fails the test if anything reaches it."""
    raise AssertionError(f"git ran when it should not have: {cmd}")


def _commit(wt: Path, message: str) -> str:
    """An empty commit in *wt*, returning its SHA."""
    result = git.client.run(
        "commit", "-q", "--allow-empty", "-m", message, cwd=wt,
        config={"user.email": "t@t", "user.name": "t"},
    )
    assert result.ok, f"commit failed: {result.detail}"
    return git.client.head_sha(cwd=wt)


@pytest.fixture
def pushable(tmp_path, live_git_hooks) -> tuple[Path, Path]:
    """A one-commit repo on `main`, with `origin` a bare repo alongside it.

    `live_git_hooks` because half of what this suite asserts is a hook firing —
    the losing post-receive below, and the refusal a pre-push produces. The
    autouse sandbox points `core.hooksPath` at /dev/null, which would disable
    both and quietly turn every lost-push test into a passing one.
    """
    remote = tmp_path / "remote.git"
    run_checked(["git", "init", "-q", "--bare", "-b", "main", str(remote)])
    wt = seed_repo(tmp_path / "wt")
    git_in(wt, "remote", "add", "origin", str(remote))
    git_in(wt, "push", "-q", "-u", "origin", "main")
    return wt, remote


def _lose_pushes(remote: Path) -> Path:
    """Make *remote* accept every push and then rewind the ref."""
    hook = remote / "hooks" / "post-receive"
    hook.write_text(_LOSING_HOOK)
    hook.chmod(0o755)
    return hook


_HOOK_DUMP = (
    "Running pre-push checks for: .claude,dev-ci,lib-go,svc-product\n"
    + "\n".join(f"pkl-drift: generated file {n} matches" for n in range(80))
    + "\n✗ pre-commit: lint:ts failed\n"
)


# Everything a push killed by a mid-transfer reset prints — ssh's diagnostic,
# then git's. Every other classification in this module matches something in it,
# which is why the ordering in `classify` is the whole fix: the ssh diagnostic
# reads as transport and the trailing line reads as a hook rejection.
_RESET_DUMP_FULL = (
    "Read from remote host github.com: Connection reset by peer\n"
    "client_loop: send disconnect: Broken pipe\n"
    "fatal: Could not read from remote repository.\n"
    "error: failed to push some refs to 'origin'\n"
)
