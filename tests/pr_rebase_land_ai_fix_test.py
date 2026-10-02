"""Tests for rebase.land: the force-push and which outcomes reach the AI fix."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.land  # noqa: E402
import rebase.prepush  # noqa: E402
import rebase.types  # noqa: E402
import rebase.land  # noqa: E402
import git.push  # noqa: E402
from git.land import CommitStatus  # noqa: E402

from pr_rebase_support import _LANDED_SHA, _LEASE, _RESUME, _pushed

# Ssh's half of what a push killed by a mid-transfer reset prints. Nothing in it
# is a complaint about the worktree, which is the point of the test below.
_RESET_DUMP_SSH = (
    "Read from remote host github.com: Connection reset by peer\n"
    "client_loop: send disconnect: Broken pipe\n"
)


def _refused(error: str = "✗ gofmt: server.go") -> git.land.LandResult:
    """The owner's answer when a pre-push hook rejected the branch."""
    return git.land.LandResult(
        CommitStatus.PUSH_FAILED, sha=_LANDED_SHA, error=error, resume=_RESUME,
        push=git.push.PushResult(
            git.push.PushStatus.REFUSED, sha=_LANDED_SHA, branch="isaac/feat/x",
            refusal=git.push.Refusal.HOOK, output=f"{error}\n",
        ),
    )


# ── _land ──────────────────────────────────────────────────────────────────


def _owner_reports(result: git.land.LandResult):
    """Patch the land owner, whose own behaviour is `tests/land_test.py`'s subject.

    What is left here is the composition: which flags this script asks the owner
    for, and which of the owner's answers is worth handing to the AI fix.
    """
    return mock.patch.object(git.land, "land_head", return_value=result)


def test_land_asks_the_owner_for_a_force_push_with_the_regen_recovery():
    """The rebase replayed the branch, so every push here rewrites the remote.

    `gated=True` whatever the mode: this is the one entry point where pushing is
    the command rather than an accident, and `main` opens the publishing gate
    for the modes that reach the remote. The gate, not this argument, is what
    `--no-push` shuts.
    """
    landed = _pushed()

    with _owner_reports(landed) as owner:
        assert rebase.land.land_rebased("/fake", args=_LEASE.args) is landed

    assert owner.call_args[0][0] == "/fake"
    kwargs = owner.call_args.kwargs
    assert kwargs["gated"] is True
    assert kwargs["args"] == _LEASE.args
    assert kwargs["regen"] == rebase.types.REGEN_MESSAGE


def test_a_landed_push_never_reaches_the_ai_fix():
    with _owner_reports(_pushed()), \
         mock.patch.object(rebase.prepush, "fix_push_failures") as mock_fix:
        assert rebase.land.land_rebased(
            "/fake", resolved_files=["server.go"], args=_LEASE.args).ok

    mock_fix.assert_not_called()


def test_a_refusal_hands_the_hook_output_to_the_ai_fix():
    """The second recovery: `land` committed what the hook regenerated and the
    checks still failed, so the output is a complaint an agent can act on."""
    repaired = _pushed(sha="9f8e7d6")

    with _owner_reports(_refused("gofmt: server.go")), \
         mock.patch.object(rebase.prepush, "fix_push_failures",
                           return_value=repaired) as mock_fix:
        assert rebase.land.land_rebased(
            "/fake", resolved_files=["server.go"], args=_LEASE.args) is repaired

    mock_fix.assert_called_once_with(
        "/fake", "gofmt: server.go", ["server.go"], args=_LEASE.args, trail=None)


def test_a_fix_that_produced_nothing_leaves_the_refusal_standing():
    """No backend, or an agent that changed nothing — the push is still the answer."""
    refusal = _refused()

    with _owner_reports(refusal), \
         mock.patch.object(rebase.prepush, "fix_push_failures", return_value=None):
        assert rebase.land.land_rebased(
            "/fake", resolved_files=["server.go"], args=_LEASE.args) is refusal


@pytest.mark.parametrize("result", [
    git.land.LandResult(CommitStatus.PUSH_HELD, sha=_LANDED_SHA, resume=_RESUME,
                    push=git.push.PushResult(git.push.PushStatus.HELD, sha=_LANDED_SHA,
                                         branch="isaac/feat/x")),
    git.land.LandResult(CommitStatus.PUSH_LOST, sha=_LANDED_SHA,
                    push=git.push.PushResult(git.push.PushStatus.LOST, sha=_LANDED_SHA,
                                         branch="isaac/feat/x")),
    git.land.LandResult(CommitStatus.PUSH_UNVERIFIED, sha=_LANDED_SHA,
                    push=git.push.PushResult(git.push.PushStatus.UNVERIFIED, sha=_LANDED_SHA,
                                         branch="isaac/feat/x")),
])
def test_only_a_refusal_reaches_the_ai_fix(result):
    """A held, lost, or unverified push says nothing is wrong with the worktree.

    Handing one to the fix pass asks an agent to rewrite code that passed every
    check — and under `--no-push` it would do that on every run.
    """
    with _owner_reports(result), \
         mock.patch.object(rebase.prepush, "fix_push_failures") as mock_fix:
        assert rebase.land.land_rebased(
            "/fake", resolved_files=["server.go"], args=_LEASE.args) is result

    mock_fix.assert_not_called()


def test_a_dropped_refusal_is_not_handed_to_the_ai_fix():
    """A dropped connection is a refusal with nothing to repair.

    The gates passed — git only reaches the transfer once `pre-push` returns
    zero — so handing this to the fix pass asks an agent to rewrite code nobody
    rejected.
    """
    dropped = git.land.LandResult(
        CommitStatus.PUSH_FAILED, sha=_LANDED_SHA, error=_RESET_DUMP_SSH,
        resume=_RESUME,
        push=git.push.PushResult(
            git.push.PushStatus.REFUSED, sha=_LANDED_SHA, branch="isaac/feat/x",
            refusal=git.push.Refusal.DROPPED, output=_RESET_DUMP_SSH,
        ),
    )

    with _owner_reports(dropped), \
         mock.patch.object(rebase.prepush, "fix_push_failures") as mock_fix:
        assert rebase.land.land_rebased(
            "/fake", resolved_files=["server.go"], args=_LEASE.args) is dropped

    mock_fix.assert_not_called()


def test_a_refusal_with_no_resolved_files_skips_the_ai_fix():
    """Nothing the AI resolved means nothing it has standing to repair."""
    with _owner_reports(_refused()), \
         mock.patch.object(rebase.prepush, "fix_push_failures") as mock_fix:
        assert not rebase.land.land_rebased("/fake", args=_LEASE.args).ok

    mock_fix.assert_not_called()


def test_a_refusal_that_said_nothing_skips_the_ai_fix():
    """An empty complaint is not a prompt — the agent would be guessing."""
    with _owner_reports(_refused(error="")), \
         mock.patch.object(rebase.prepush, "fix_push_failures") as mock_fix:
        rebase.land.land_rebased("/fake", resolved_files=["server.go"], args=_LEASE.args)

    mock_fix.assert_not_called()
