"""Tests for `pr.branch_sync` — the push decision `pr create` makes.

Ported from the bats suite that covered the bash `push_branch` this replaced,
plus D8 (exact ls-remote), D9 (UNVERIFIED is ok) and `--no-verify` reaching the
push args.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import git_in, git_out, init_repo, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import git.push  # noqa: E402
from core.proc import CmdResult  # noqa: E402
from git.push import PushResult, PushStatus  # noqa: E402
from pr.branch_sync import SyncOutcome, sync_branch  # noqa: E402


MSG_NEW = "→ Pushing new branch to remote..."
MSG_UP_TO_DATE = "✓ Branch is up to date with remote"
MSG_BEHIND = "✗ Remote has commits not in local branch — please pull first: git pull"
MSG_AHEAD = "→ Local has unpushed commits, pushing..."
MSG_DIVERGED = (
    "✗ Branch has diverged from remote\n"
    "→ Fix with: git pull --rebase or git reset"
)


def _repo(tmp_path: Path, branch: str = "feature/test") -> tuple[Path, Path]:
    """A `main` pushed to a bare origin, plus an unpushed local *branch*."""
    remote = tmp_path / "remote.git"
    run_checked(["git", "init", "-q", "--bare", "-b", "main", str(remote)])
    wt = init_repo(tmp_path / "wt")
    git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
    git_in(wt, "remote", "add", "origin", str(remote))
    git_in(wt, "push", "-q", "-u", "origin", "main")
    git_in(wt, "checkout", "-q", "-b", branch)
    return wt, remote


def _commit(wt: Path, message: str, rel: str | None = None) -> None:
    if rel is None:
        git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
        return
    path = wt / rel
    path.write_text(message)
    git_in(wt, "add", "--", rel)
    git_in(wt, "commit", "-q", "--no-verify", "-m", message)


def _track(wt: Path, branch: str) -> None:
    git_in(wt, "push", "-q", "-u", "origin", branch)


def _other(tmp_path: Path, remote: Path, branch: str) -> Path:
    other = tmp_path / "other"
    run_checked(["git", "clone", "-q", str(remote), str(other)])
    git_in(other, "config", "user.email", "other@example.com")
    git_in(other, "config", "user.name", "Other")
    git_in(other, "checkout", "-q", branch)
    return other


def _pushed(status: PushStatus, branch: str, output: str = "") -> PushResult:
    return PushResult(status, sha="abc", branch=branch, output=output, remote="origin")


# ── ported from the bash push_branch suite ─────────────────────────────────


def test_pushes_new_branch_to_remote(tmp_path):
    wt, remote = _repo(tmp_path)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert result.ok
    assert result.outcome is SyncOutcome.PUSHED_NEW
    assert result.message == MSG_NEW
    assert git.push.remote_head(wt, "feature/test") == git.client.head_sha(cwd=wt)
    assert git_out(wt, "rev-parse", "--abbrev-ref", "@{u}").strip() == "origin/feature/test"
    # The remote still has main; this is the first push of feature/test.
    assert git.push.remote_head(wt, "main")


def test_reports_up_to_date_when_already_pushed(tmp_path):
    wt, _ = _repo(tmp_path)
    _track(wt, "feature/test")
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert result.ok
    assert result.outcome is SyncOutcome.UP_TO_DATE
    assert result.message == MSG_UP_TO_DATE
    assert result.push is None


def test_pushes_when_local_is_ahead_of_remote(tmp_path):
    wt, _ = _repo(tmp_path)
    _track(wt, "feature/test")
    _commit(wt, "feat: add more", "more.txt")
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert result.ok
    assert result.outcome is SyncOutcome.PUSHED
    assert result.message == MSG_AHEAD
    assert git.push.remote_head(wt, "feature/test") == git.client.head_sha(cwd=wt)


def test_fails_when_remote_is_ahead_of_local(tmp_path):
    wt, remote = _repo(tmp_path)
    _track(wt, "feature/test")
    other = _other(tmp_path, remote, "feature/test")
    _commit(other, "feat: remote commit", "remote.txt")
    git_in(other, "push", "-q", "origin", "feature/test")

    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.BEHIND
    assert result.message == MSG_BEHIND
    assert result.push is None
    # Local HEAD did not move onto the remote commit.
    assert git.push.remote_head(wt, "feature/test") != git.client.head_sha(cwd=wt)


def test_fails_when_branches_have_diverged(tmp_path):
    wt, remote = _repo(tmp_path)
    _track(wt, "feature/test")
    other = _other(tmp_path, remote, "feature/test")
    _commit(other, "feat: remote", "remote.txt")
    git_in(other, "push", "-q", "origin", "feature/test")
    _commit(wt, "feat: local", "local.txt")

    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.DIVERGED
    assert result.message == MSG_DIVERGED
    assert result.push is None
    # The remote still holds the other clone's commit, not ours.
    assert git.push.remote_head(wt, "feature/test") != git.client.head_sha(cwd=wt)


def test_lost_push_on_a_new_branch_is_failed(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    lost = _pushed(PushStatus.LOST, "feature/test", output="the remote did not move")

    def fake_push(*_a, **_k):
        return lost

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.FAILED
    assert result.push is lost
    assert "the remote did not move" in result.message


def test_lost_push_on_a_branch_the_remote_already_has_is_failed(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    _track(wt, "feature/test")
    _commit(wt, "feat: add more", "more.txt")
    lost = _pushed(PushStatus.LOST, "feature/test", output="the remote did not move")

    def fake_push(*_a, **_k):
        return lost

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.FAILED
    assert result.push is lost
    assert "the remote did not move" in result.message


# ── deltas ──────────────────────────────────────────────────────────────────


def test_a_branch_whose_name_is_a_prefix_of_a_remote_ref_is_new(tmp_path, monkeypatch):
    """D8: `ls-remote` of `refs/heads/feat` must not match remote `feat/auth`."""
    wt, _ = _repo(tmp_path, branch="feat/auth")
    _track(wt, "feat/auth")
    git_in(wt, "checkout", "-q", "main")
    # A repo cannot hold both `feat` and `feat/auth` as local *or* remote
    # branches — the D8 case is the probe, so the push is stubbed.
    git_in(wt, "branch", "-D", "feat/auth")
    git_in(wt, "checkout", "-q", "-b", "feat")
    captured: dict = {}

    def fake_push(wt_path, *, gated, sha="", branch="", remote="origin",
                  args=(), trail=None, env=None):
        captured["args"] = tuple(args)
        return _pushed(PushStatus.PUSHED, branch)

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feat", no_verify=False)
    assert result.ok
    assert result.outcome is SyncOutcome.PUSHED_NEW
    assert result.message == MSG_NEW
    assert captured["args"] == ("--set-upstream", "origin", "feat")


def test_an_unverified_push_counts_as_ok(tmp_path, monkeypatch):
    """D9: UNVERIFIED is a warning, not a refusal to open the PR."""
    wt, _ = _repo(tmp_path)
    unverified = _pushed(PushStatus.UNVERIFIED, "feature/test", output="could not ask")

    def fake_push(*_a, **_k):
        return unverified

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert result.ok
    assert result.outcome is SyncOutcome.PUSHED_NEW
    assert result.push is unverified


def test_no_verify_reaches_the_push_args(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    captured: dict = {}

    def fake_push(wt_path, *, gated, sha="", branch="", remote="origin", args=(), trail=None, env=None):
        captured["gated"] = gated
        captured["args"] = tuple(args)
        captured["branch"] = branch
        captured["remote"] = remote
        return _pushed(PushStatus.PUSHED, branch)

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=True)
    assert result.ok
    assert captured["gated"] is False
    assert captured["branch"] == "feature/test"
    assert captured["remote"] == "origin"
    assert "--no-verify" in captured["args"]
    assert "--set-upstream" in captured["args"]
    assert "origin" in captured["args"]
    assert "feature/test" in captured["args"]
    # args is the whole `git push` argv — remote/branch live here, not twice.
    assert captured["args"].count("origin") == 1
    assert captured["args"].count("feature/test") == 1


def test_push_runs_with_the_scrubbed_git_env(tmp_path, monkeypatch):
    """`git.push.push` must get the same GIT_DIR-stripped env as the probe
    reads — a hook's inherited GIT_DIR would otherwise push to its repo rather
    than *wt*."""
    wt, _ = _repo(tmp_path)
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    captured: dict = {}

    def fake_push(wt_path, *, gated, sha="", branch="", remote="origin", args=(), trail=None, env=None):
        captured["env"] = env
        return _pushed(PushStatus.PUSHED, branch)

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert result.ok
    assert captured["env"] is not None
    assert "GIT_DIR" not in captured["env"]


def test_first_push_sets_upstream_in_args(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    captured: dict = {}

    def fake_push(wt_path, *, gated, sha="", branch="", remote="origin", args=(), trail=None, env=None):
        captured["args"] = tuple(args)
        captured["gated"] = gated
        return _pushed(PushStatus.PUSHED, branch)

    monkeypatch.setattr(git.push, "push", fake_push)
    sync_branch(wt, "feature/test", no_verify=False)
    assert captured["gated"] is False
    assert captured["args"] == ("--set-upstream", "origin", "feature/test")


def test_ahead_push_does_not_set_upstream(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    _track(wt, "feature/test")
    _commit(wt, "feat: add more", "more.txt")
    captured: dict = {}

    def fake_push(wt_path, *, gated, sha="", branch="", remote="origin", args=(), trail=None, env=None):
        captured["args"] = tuple(args)
        return _pushed(PushStatus.PUSHED, branch)

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert result.outcome is SyncOutcome.PUSHED
    assert "--set-upstream" not in captured["args"]
    assert captured["args"] == ("origin", "feature/test")


def test_progress_is_logged_before_the_push(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    events: list[str] = []

    def log(msg: str) -> None:
        events.append(f"log:{msg}")

    def fake_push(*_a, **_k):
        events.append("push")
        return _pushed(PushStatus.PUSHED, "feature/test")

    monkeypatch.setattr(git.push, "push", fake_push)
    result = sync_branch(wt, "feature/test", no_verify=False, log=log)
    assert events == [f"log:{MSG_NEW}", "push"]
    assert result.message == MSG_NEW


# ── probe failures: a git that could not answer is not a git that said "no" ─


def _no_push(*_a, **_k):
    raise AssertionError("sync_branch pushed after a failed probe")


def _failing(monkeypatch, prefix: tuple[str, ...], stderr: str) -> None:
    """Make `git.client.run` fail for argv starting with *prefix* only."""
    real = git.client.run

    def fake_run(*args, **kwargs):
        if args[:len(prefix)] == prefix:
            return CmdResult(returncode=128, stderr=stderr)
        return real(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", fake_run)


def test_an_unreachable_remote_fails_without_pushing(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    git_in(wt, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    monkeypatch.setattr(git.push, "push", _no_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.FAILED
    assert result.push is None
    assert result.message.startswith("✗ Could not reach origin: ")
    assert "nowhere.git" in result.message


def test_a_failed_fetch_fails_without_pushing(tmp_path, monkeypatch):
    wt, _ = _repo(tmp_path)
    _track(wt, "feature/test")
    _commit(wt, "feat: add more", "more.txt")
    _failing(monkeypatch, ("fetch",), "fatal: the fetch broke")
    monkeypatch.setattr(git.push, "push", _no_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.FAILED
    assert result.push is None
    assert result.message == (
        "✗ Could not fetch origin/feature/test: fatal: the fetch broke")


@pytest.mark.parametrize("argv", [
    ("rev-parse", "HEAD"),
    ("rev-parse", "@{u}"),
    ("merge-base", "HEAD", "@{u}"),
])
def test_a_failed_comparison_read_fails_without_pushing(tmp_path, monkeypatch, argv):
    """Two failed reads both come back empty; they must not compare equal."""
    wt, _ = _repo(tmp_path)
    _track(wt, "feature/test")
    _commit(wt, "feat: add more", "more.txt")
    _failing(monkeypatch, argv, "fatal: bad revision")
    monkeypatch.setattr(git.push, "push", _no_push)
    result = sync_branch(wt, "feature/test", no_verify=False)
    assert not result.ok
    assert result.outcome is SyncOutcome.FAILED
    assert result.push is None
    assert result.message == (
        f"✗ git {' '.join(argv)} failed: fatal: bad revision")


def test_a_first_push_runs_git_push_with_the_refspec_once(tmp_path, monkeypatch):
    """`git.push.push` hands `args` to `git push` verbatim; branch/remote are
    only for verification, so the refspec must appear exactly once."""
    wt, _ = _repo(tmp_path)
    real = git.client.run
    pushes: list[tuple[str, ...]] = []

    def spy(*args, **kwargs):
        if args[:1] == ("push",):
            pushes.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", spy)
    result = sync_branch(wt, "feature/test", no_verify=True)
    assert result.outcome is SyncOutcome.PUSHED_NEW
    assert pushes == [
        ("push", "--set-upstream", "origin", "feature/test", "--no-verify")]
