"""Tests for rebase.stash: auto-stashing and restoring around a rebase."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402
import rebase.stash  # noqa: E402
import core.log

from pr_rebase_support import _unconfigured


# ── _auto_stash ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status_out,expected", [
    (" M ai/lib/review_phases.py\n", True),
    ("?? scratch.txt\n", True),
    ("", False),
])
def test_auto_stash_covers_untracked_files(status_out, expected):
    """Untracked files are dirt too — they reach the hooks and the fix commit.

    Left in place they join what the pre-push hooks validate, and the recovery's
    whole-tree stage would then force-push a scratch file.
    """
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = status_out if _unconfigured(cmd)[:2] == ["git", "status"] else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase.stash.auto_stash("/fake") is expected

    stash_calls = [c for c in calls if c[:2] == ["git", "stash"]]
    assert bool(stash_calls) is expected
    assert all("-u" in c for c in stash_calls)


def test_auto_stash_refuses_when_the_worktree_cannot_be_read(tmp_path):
    """A failed status must not read as a clean tree and rebase over the work.

    git refusing a rebase on a dirty tree is a backstop that happens to catch
    this; `_auto_stash` is the guard, and its answer has to be honest whether
    or not something downstream would notice.
    """
    assert rebase.stash.auto_stash(str(tmp_path)) is None


def test_auto_stash_does_not_stash_a_tree_it_could_not_read():
    """Refusing means refusing before the stash, not stashing blind."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=128, stdout="", stderr="fatal: index file corrupt",
        )

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert rebase.stash.auto_stash("/fake") is None

    assert not [c for c in calls if c[:2] == ["git", "stash"]]


# ── _auto_unstash ──────────────────────────────────────────────────────────


def test_auto_unstash_pop_failure_without_conflicts_names_the_stash():
    """A pop that fails with no conflict markers must say the work is still stashed.

    Stashing untracked files (-u) makes git's "would be overwritten by merge"
    refusal reachable, and that failure produces no markers to resolve.
    """
    warnings = []

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="",
            stderr="error: untracked working tree files would be overwritten by merge",
        )

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.stash, "auto_stash_ref", return_value="stash@{0}"), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(core.log, "warn", side_effect=warnings.append):
        rebase.stash.auto_unstash("/fake", rebase.types.RunMode.PUSH)

    assert any(rebase.stash.STASH_MSG in w for w in warnings)
