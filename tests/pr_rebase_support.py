"""Helpers and constants shared by the suites split out of pr_rebase_test.py."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

from conftest import git_out, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.land  # noqa: E402
import rebase.types  # noqa: E402
import rebase.land  # noqa: E402
import rebase.lease  # noqa: E402
from git.land import CommitStatus  # noqa: E402


def _unconfigured(cmd):
    """A git argv with `git.client`'s `-c key=value` prefixes stripped.

    The client decides `core.quotePath=false` for every path-listing subcommand
    and `core.editor=true` for a `rebase --continue`, so the argv git receives
    no longer starts with the subcommand. A stub keyed on `cmd[:2]` then stops
    firing and answers from its catch-all instead — which reads as the tested
    behaviour changing, or as nothing at all.
    """
    if not cmd or cmd[0] != "git":
        return cmd
    rest = list(cmd[1:])
    while rest[:1] == ["-c"]:
        del rest[:2]
    return ["git", *rest]


# The sha the stubbed owner reports for whatever it landed. Any value does; it
# is here so the tests that read it back are reading one thing.
_LANDED_SHA = "1a2b3c4"

_LEASE = rebase.lease.PushLease(branch="isaac/feat/x", expect="abc123")

# What `push.resume_command` renders for this script's force-push, which is the
# line `--no-push` prints and a refusal offers.
_RESUME = f"git -C '/fake' push {_LEASE.args[0]}"


def _pushed(sha: str = _LANDED_SHA) -> git.land.LandResult:
    """The owner's answer when the remote took the force-push."""
    return git.land.LandResult(CommitStatus.PUSHED, sha=sha)


def _lands(result: git.land.LandResult):
    """Patch `_land` so the caller under test sees exactly this outcome."""
    return mock.patch.object(rebase.land, "land_rebased", return_value=result)


# The base a run resolved to, threaded into every helper that derives a signal
# from it. Named here rather than repeated as a literal so a test that cares
# which ref reached git can pass its own instead.
_TARGET = "origin/main"

# The non-default base a release-branch PR reports, and the ref resolution
# prefixes it into. Paired here because the tests that assert one against the
# other are asserting exactly that relationship.
_OTHER_BASE = "release/1.2"
_OTHER_TARGET = f"origin/{_OTHER_BASE}"


# ── Chunked conflict resolution ──────────────────────────────────────────


def _make_large_file(num_lines, conflicts):
    """Build a file with num_lines of filler and conflict blocks at given positions.

    conflicts: list of (line_index, ours_text, theirs_text)
    """
    lines = [f"line {i}\n" for i in range(num_lines)]
    offset = 0
    for pos, ours, theirs in conflicts:
        block = [
            "<<<<<<< HEAD\n",
            f"{ours}\n",
            "=======\n",
            f"{theirs}\n",
            ">>>>>>> abc123\n",
        ]
        lines[pos + offset:pos + offset + 1] = block
        offset += len(block) - 1
    return "".join(lines)


def _block(index=1, start=0, end=0, conflict="", context_before="", context_after=""):
    """Build a ConflictBlock with defaults for fields the test doesn't care about."""
    return rebase.types.ConflictBlock(
        index=index, start=start, end=end, conflict=conflict,
        context_before=context_before, context_after=context_after,
    )


def _git(repo, *args):
    """The shared git runner, stripped — see conftest.run_checked."""
    return git_out(repo, *args).strip()


# ── already-landed preflight ────────────────────────────────────────────────


_LANDED_BRANCH = "feat/landed"
_LANDED_PR = 726


def _completed(cmd, returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout=stdout, stderr="")


def _landed_ctx(**overrides):
    """A context for the branch every preflight test asks about."""
    defaults = dict(branch=_LANDED_BRANCH, pr_number=_LANDED_PR)
    defaults.update(overrides)
    return make_ctx(**defaults)
