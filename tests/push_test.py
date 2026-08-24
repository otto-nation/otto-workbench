"""Tests for the push owner.

Against a real remote rather than a mocked `subprocess`, for the reason
`git_client_test` gives: every assertion here is a claim about what git actually
does, and the central one — that a push can exit zero and land nothing — is a
claim a mock cannot make honestly.

A lost push is fabricated with a `post-receive` hook on the bare remote that
rewinds the ref it was just handed. post-receive runs after the refs move and
its exit code cannot fail the push, so the client sees a clean success while the
remote ends up holding what it held before. That is the failure #962 describes,
reproduced rather than simulated.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git_client  # noqa: E402
import push  # noqa: E402
import timeouts  # noqa: E402

from conftest import GIT_TIMEOUT, git_in, seed_repo  # noqa: E402

_LOSING_HOOK = """#!/usr/bin/env bash
while read -r old new ref; do
  if [ "$old" = "0000000000000000000000000000000000000000" ]; then
    git update-ref -d "$ref"
  else
    git update-ref "$ref" "$old"
  fi
done
"""


def _commit(wt: Path, message: str) -> str:
    """An empty commit in *wt*, returning its SHA."""
    result = git_client.run(
        "commit", "-q", "--allow-empty", "-m", message, cwd=wt,
        config={"user.email": "t@t", "user.name": "t"},
    )
    assert result.ok, f"commit failed: {result.detail}"
    return git_client.head_sha(cwd=wt)


@pytest.fixture
def pushable(tmp_path, live_git_hooks) -> tuple[Path, Path]:
    """A one-commit repo on `main`, with `origin` a bare repo alongside it.

    `live_git_hooks` because half of what this suite asserts is a hook firing —
    the losing post-receive below, and the refusal a pre-push produces. The
    autouse sandbox points `core.hooksPath` at /dev/null, which would disable
    both and quietly turn every lost-push test into a passing one.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True, timeout=GIT_TIMEOUT)
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


# ── the timeout tier ────────────────────────────────────────────────────────


def test_ls_remote_takes_the_transfer_tier():
    """A network read on the 10s local budget would expire on a slow remote."""
    assert git_client._timeout_for(("ls-remote",)) == timeouts.TRANSFER


# ── remote_head ─────────────────────────────────────────────────────────────


def test_remote_head_reports_the_pushed_commit(pushable):
    wt, _ = pushable
    assert push.remote_head(wt, "main") == git_client.head_sha(cwd=wt)


def test_remote_head_distinguishes_absent_from_unaskable(pushable):
    """"" means the remote has no such ref; None means it could not be asked."""
    wt, _ = pushable
    assert push.remote_head(wt, "no-such-branch") == ""
    git_in(wt, "remote", "set-url", "origin", str(wt / "nope.git"))
    assert push.remote_head(wt, "main") is None


# ── the five outcomes ───────────────────────────────────────────────────────


def test_push_that_lands_is_pushed(pushable):
    wt, _ = pushable
    sha = _commit(wt, "work")
    result = push.push(wt, gated=False)
    assert result.status is push.PushStatus.PUSHED
    assert result.ok
    assert result.sha == sha
    assert result.branch == "main"
    assert result.remote_sha == sha


def test_push_that_vanishes_is_lost(pushable):
    """git exits zero and the remote does not hold the commit."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    _lose_pushes(remote)
    result = push.push(wt, gated=False)
    assert result.status is push.PushStatus.LOST
    assert not result.ok
    assert result.sha == sha
    assert result.remote_sha != sha


def test_rejected_push_is_refused_not_lost(pushable):
    """Nothing left the machine, so it must not read as a lost push."""
    wt, _ = pushable
    _commit(wt, "theirs")
    git_in(wt, "push", "-q", "origin", "main")
    git_in(wt, "reset", "-q", "--hard", "HEAD~1")
    _commit(wt, "mine")
    result = push.push(wt, gated=False)
    assert result.status is push.PushStatus.REFUSED
    assert result.refusal is push.Refusal.DIVERGED


def test_unreachable_remote_is_unverified_not_lost(pushable, monkeypatch):
    """A remote that could not answer has not answered "no"."""
    wt, _ = pushable
    sha = _commit(wt, "work")
    monkeypatch.setattr(push, "remote_head", lambda *a, **k: None)
    result = push.push(wt, gated=False)
    assert result.status is push.PushStatus.UNVERIFIED
    assert not result.ok
    assert result.sha == sha


def test_gated_push_is_held_and_attempts_nothing(pushable):
    """The publishing gate is shut by default — see conftest's _drafts_only."""
    wt, _ = pushable
    sha = _commit(wt, "work")
    result = push.push(wt, gated=True)
    assert result.status is push.PushStatus.HELD
    assert result.sha == sha
    assert push.remote_head(wt, "main") != sha


# ── the refusal classifier ──────────────────────────────────────────────────


@pytest.mark.parametrize("output,expected", [
    ("! [rejected]  main -> main (non-fast-forward)", push.Refusal.DIVERGED),
    ("Updates were rejected because the remote contains work.\nfetch first",
     push.Refusal.DIVERGED),
    ("! [rejected] main -> main (stale info)", push.Refusal.DIVERGED),
    ("ssh: Could not resolve host: github.com", push.Refusal.TRANSPORT),
    ("fatal: Could not read from remote repository.", push.Refusal.TRANSPORT),
    ("Permission denied (publickey).", push.Refusal.TRANSPORT),
    ("validate-all failed\nerror: failed to push some refs to 'origin'",
     push.Refusal.HOOK),
    ("something nobody has seen before", push.Refusal.OTHER),
])
def test_classify_names_the_refusal(output, expected):
    assert push.classify(output) == expected


def test_a_hook_rejection_outranks_nothing_it_shares_words_with():
    """A transport failure also prints the generic refusal line; it wins."""
    output = ("fatal: Could not read from remote repository.\n"
              "error: failed to push some refs to 'origin'")
    assert push.classify(output) is push.Refusal.TRANSPORT
