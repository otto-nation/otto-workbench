"""Tests for the push owner.

Against a real remote rather than a mocked `subprocess`, for the reason
`git_client_test` gives: every assertion here is a claim about what git actually
does, and the central one — that a push can exit zero and land nothing — is a
claim a mock cannot make honestly.

A lost push is fabricated with a `post-receive` hook on the bare remote that
rewinds the ref it was just handed. post-receive runs after the refs move and
its exit code cannot fail the push, so the client sees a clean success while the
remote ends up holding what it held before. That is the failure the verification
step exists to catch, reproduced rather than simulated.
"""

import signal
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import core.proc  # noqa: E402
import git.push  # noqa: E402
import core.timeouts  # noqa: E402
from core.trail import Trail  # noqa: E402

from conftest import _last_event, _load_lib, git_in  # noqa: E402

gitenv = _load_lib("gitenv")

# `pushable` is a fixture: imported so pytest finds it here, never called by name.
from push_support import _never_runs, _commit, pushable, _lose_pushes, _HOOK_DUMP, _RESET_DUMP_FULL


# ── the timeout tier ────────────────────────────────────────────────────────


def test_ls_remote_takes_the_transfer_tier():
    """A network read on the 10s local budget would expire on a slow remote."""
    assert git.client._timeout_for(("ls-remote",)) == core.timeouts.TRANSFER


# ── remote_head ─────────────────────────────────────────────────────────────


def test_remote_head_reports_the_pushed_commit(pushable):
    wt, _ = pushable
    assert git.push.remote_head(wt, "main") == git.client.head_sha(cwd=wt)


def test_remote_head_distinguishes_absent_from_unaskable(pushable):
    """"" means the remote has no such ref; None means it could not be asked."""
    wt, _ = pushable
    assert git.push.remote_head(wt, "no-such-branch") == ""
    git_in(wt, "remote", "set-url", "origin", str(wt / "nope.git"))
    assert git.push.remote_head(wt, "main") is None


def test_remote_head_is_not_fooled_by_a_branch_ending_in_the_same_name(pushable):
    """An `ls-remote` pattern matches any ref whose tail spells it.

    `alt/main` answers a query for `main`, and git sorts its output — which is
    why the impostor is named to sort ahead of the real branch. Reading the
    first line would hand back a SHA belonging to something else, reporting a
    landed push as lost or, worse, the reverse.
    """
    wt, _ = pushable
    on_main = git.client.head_sha(cwd=wt)
    git_in(wt, "checkout", "-q", "-b", "alt/main")
    _commit(wt, "the impostor")
    git_in(wt, "push", "-q", "origin", "alt/main")

    assert git.push.remote_head(wt, "main") == on_main


# ── holds ───────────────────────────────────────────────────────────────────


def test_holds_answers_for_the_commit_the_remote_has(pushable):
    wt, _ = pushable
    assert git.push.holds(wt, git.client.head_sha(cwd=wt)) is True


def test_holds_answers_for_an_earlier_commit_a_later_push_carried_out(pushable):
    """Ancestry, not equality — a round's commit rides out on the next one's push."""
    wt, _ = pushable
    earlier = git.client.head_sha(cwd=wt)
    _commit(wt, "a later round")
    git_in(wt, "push", "-q", "origin", "main")

    assert git.push.holds(wt, earlier) is True


def test_holds_declines_a_commit_that_never_left(pushable):
    wt, _ = pushable
    local = _commit(wt, "not pushed")
    assert git.push.holds(wt, local) is False


def test_holds_declines_a_branch_the_remote_does_not_have(pushable):
    wt, _ = pushable
    git_in(wt, "checkout", "-q", "-b", "feat/unpushed")
    assert git.push.holds(wt, _commit(wt, "on a new branch")) is False


def test_an_unreachable_remote_reads_as_pending(pushable):
    """Deferring a citation is the safe answer; publishing a dead link is not."""
    wt, _ = pushable
    sha = git.client.head_sha(cwd=wt)
    git_in(wt, "remote", "set-url", "origin", str(wt / "nope.git"))
    assert git.push.holds(wt, sha) is False


def test_holds_reads_the_remote_rather_than_the_tracking_ref(pushable):
    """A lost push leaves `origin/main` pointing at the commit that never arrived.

    Which is exactly the failure a caller asks this to rule out, so answering
    from the local tracking ref would answer yes for it.
    """
    wt, remote = pushable
    _lose_pushes(remote)
    lost = _commit(wt, "the push that vanishes")
    git_in(wt, "push", "-q", "origin", "main")

    assert git.client.out("rev-parse", "origin/main", cwd=wt) == lost
    assert git.push.holds(wt, lost) is False


# ── the five outcomes ───────────────────────────────────────────────────────


def test_push_that_lands_is_pushed(pushable):
    wt, _ = pushable
    sha = _commit(wt, "work")
    result = git.push.push(wt, gated=False)
    assert result.status is git.push.PushStatus.PUSHED
    assert result.ok
    assert result.sha == sha
    assert result.branch == "main"
    assert result.remote_sha == sha


def test_push_that_vanishes_is_lost(pushable):
    """git exits zero and the remote does not hold the commit."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    _lose_pushes(remote)
    result = git.push.push(wt, gated=False)
    assert result.status is git.push.PushStatus.LOST
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
    result = git.push.push(wt, gated=False)
    assert result.status is git.push.PushStatus.REFUSED
    assert result.refusal is git.push.Refusal.DIVERGED


def test_unreachable_remote_is_unverified_not_lost(pushable, monkeypatch):
    """A remote that could not answer has not answered "no"."""
    wt, _ = pushable
    sha = _commit(wt, "work")
    monkeypatch.setattr(git.push, "remote_head", lambda *a, **k: None)
    result = git.push.push(wt, gated=False)
    assert result.status is git.push.PushStatus.UNVERIFIED
    assert not result.ok
    assert result.sha == sha


def test_gated_push_is_held_and_attempts_nothing(pushable):
    """The publishing gate is shut by default — see conftest's _drafts_only."""
    wt, _ = pushable
    sha = _commit(wt, "work")
    result = git.push.push(wt, gated=True)
    assert result.status is git.push.PushStatus.HELD
    assert git.push.remote_head(wt, "main") != sha


def test_a_held_push_reads_nothing_from_the_repository(pushable, monkeypatch):
    """The gate is asked first, so a held run does not shell out at all.

    It carries only what the caller passed in — callers that report a SHA for a
    held commit have one of their own, and the draft names the command rather
    than the commit.
    """
    wt, _ = pushable
    _commit(wt, "work")
    monkeypatch.setattr(git.client, "run", _never_runs)
    result = git.push.push(wt, gated=True)
    assert result.status is git.push.PushStatus.HELD
    assert result.sha == ""
    assert git.push.push(wt, gated=True, sha="abc1234").sha == "abc1234"


# ── a push the connection dropped ───────────────────────────────────────────


@pytest.fixture
def drops_the_connection(monkeypatch):
    """Let the push run for real, then report it as killed by a reset.

    The ref moves (or does not, with the losing hook in place) exactly as it
    would in production while the client sees a drop — which is the situation
    that cannot be reproduced by killing a real git, since then the ref never
    moves at all. Faking the `CmdResult` also keeps `proc.MACHINE_KILLS` empty,
    so `conftest` does not staple a contention section onto an unrelated
    failure here.
    """
    real_run = git.client.run
    seen: list[tuple[str, ...]] = []

    def dropping(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args and args[0] == "push":
            seen.append(args)
            return core.proc.CmdResult(-signal.SIGPIPE, "", _RESET_DUMP_FULL)
        return result

    monkeypatch.setattr(git.client, "run", dropping)
    return seen


def test_a_dropped_push_the_remote_took_is_pushed(pushable, drops_the_connection):
    """git could not say it landed; `ls-remote` can, and it did."""
    wt, _ = pushable
    sha = _commit(wt, "work")

    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.PUSHED
    assert result.refusal is git.push.Refusal.DROPPED
    assert result.remote_sha == sha
    assert result.retry is git.push.Retry.NONE
    assert len(drops_the_connection) == 1


def test_a_dropped_push_the_remote_never_took_is_lost_and_retried(
        pushable, drops_the_connection):
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)

    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.LOST
    assert result.retry is git.push.Retry.ATTEMPTED
    assert len(drops_the_connection) == 2


def test_a_dropped_push_recovers_when_the_retry_lands(pushable, monkeypatch):
    """The gates passed seconds ago for this commit, so the retry costs a transfer."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    hook = _lose_pushes(remote)
    seen: list[tuple[str, ...]] = []
    real_run = git.client.run

    def drop_then_heal(*args, **kwargs):
        if args and args[0] == "push":
            seen.append(args)
            if len(seen) == 1:
                real_run(*args, **kwargs)
                hook.unlink()
                return core.proc.CmdResult(-signal.SIGPIPE, "", _RESET_DUMP_FULL)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", drop_then_heal)
    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.PUSHED
    assert result.retry is git.push.Retry.ATTEMPTED
    assert result.remote_sha == sha
    assert "--no-verify" in seen[1]


def test_a_dropped_push_the_remote_could_not_be_asked_about_is_unverified(
        pushable, drops_the_connection, monkeypatch):
    """Both accounts failed: git died mid-transfer and `ls-remote` answered nothing.

    Neither REFUSED nor LOST is honest about that — the refusal would claim
    nothing reached the remote and the loss would claim the remote said no.
    """
    wt, _ = pushable
    sha = _commit(wt, "work")
    monkeypatch.setattr(git.push, "remote_head", lambda *a, **k: None)

    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.UNVERIFIED
    assert result.refusal is git.push.Refusal.DROPPED
    assert result.sha == sha
    assert result.retry is git.push.Retry.NONE


def test_a_retry_the_remote_could_not_be_asked_about_records_the_attempt(
        pushable, drops_the_connection, monkeypatch):
    """The second transfer happened whether or not anything could confirm it."""
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)
    answers = ["", None]
    monkeypatch.setattr(git.push, "remote_head", lambda *a, **k: answers.pop(0))

    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.UNVERIFIED
    assert result.retry is git.push.Retry.ATTEMPTED
    assert len(drops_the_connection) == 2


def test_an_auth_failure_is_refused_without_asking_the_remote(monkeypatch):
    """Asking a remote the credentials just failed against reports UNVERIFIED.

    That is a legibility regression, not a fix: a push that never happened would
    be reported as one that could not be confirmed.
    """
    monkeypatch.setattr(
        git.client, "run",
        lambda *a, **k: core.proc.CmdResult(128, "", "Permission denied (publickey)."))
    monkeypatch.setattr(git.push, "remote_head", _never_runs)

    result = git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x")

    assert result.status is git.push.PushStatus.REFUSED
    assert result.refusal is git.push.Refusal.AUTH


def test_a_hook_rejection_is_refused_without_asking_the_remote(monkeypatch):
    refused = f"{_HOOK_DUMP}error: failed to push some refs to 'origin'\n"
    monkeypatch.setattr(git.client, "run",
                        lambda *a, **k: core.proc.CmdResult(1, "", refused))
    monkeypatch.setattr(git.push, "remote_head", _never_runs)

    result = git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x")

    assert result.status is git.push.PushStatus.REFUSED
    assert result.refusal is git.push.Refusal.HOOK


def test_a_dropped_push_reaches_the_trail(pushable, drops_the_connection):
    """`otto-log` should hold the event, since the console line says it recovered."""
    wt, _ = pushable
    sha = _commit(wt, "work")
    trail = Trail.start(script="test", context={})

    git.push.push(wt, gated=False, trail=trail)

    event = _last_event()
    assert event["data"]["sha"] == sha
    assert "dropped" in event["detail"]


def test_a_dropped_refusal_is_not_repairable():
    """Nothing in the tree was rejected, so there is nothing for a fix pass."""
    assert not git.push.PushResult(
        git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.DROPPED).repairable


@pytest.mark.parametrize("refusal", [git.push.Refusal.HOOK, git.push.Refusal.DIVERGED,
                                     git.push.Refusal.TRANSPORT, git.push.Refusal.AUTH,
                                     git.push.Refusal.UNREACHABLE, git.push.Refusal.OTHER])
def test_every_other_refusal_is_repairable(refusal):
    assert git.push.PushResult(
        git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=refusal).repairable


@pytest.mark.parametrize("status", [git.push.PushStatus.PUSHED, git.push.PushStatus.HELD,
                                    git.push.PushStatus.LOST,
                                    git.push.PushStatus.UNVERIFIED])
def test_only_a_refusal_is_repairable(status):
    assert not git.push.PushResult(status, sha="1a2b3c4d", branch="feat/x").repairable


# ── the retry ───────────────────────────────────────────────────────────────


@pytest.fixture
def count_pushes(monkeypatch) -> list[tuple[str, ...]]:
    """Record every `git push` the owner issues, and let them all through."""
    seen: list[tuple[str, ...]] = []
    real_run = git.client.run

    def counting(*args, **kwargs):
        if args and args[0] == "push":
            seen.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", counting)
    return seen


def test_lost_push_retries_once_and_recovers(pushable, monkeypatch):
    """The retry skips the gates, so a transient loss costs seconds."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    hook = _lose_pushes(remote)
    seen: list[tuple[str, ...]] = []
    real_run = git.client.run

    def heal_after_first(*args, **kwargs):
        if args and args[0] == "push":
            seen.append(args)
            # Drop the losing hook once the first push has been recorded, so the
            # retry lands — which is what a transient drop looks like.
            if len(seen) == 1:
                result = real_run(*args, **kwargs)
                hook.unlink()
                return result
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", heal_after_first)
    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.PUSHED
    assert result.retry is git.push.Retry.ATTEMPTED
    assert result.remote_sha == sha
    assert "--no-verify" in seen[1]


def test_retry_is_bounded_at_one(pushable, count_pushes):
    """A remote that always loses the push must not loop."""
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)

    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.LOST
    assert result.retry is git.push.Retry.ATTEMPTED
    assert len(count_pushes) == 2


def test_a_landed_push_is_not_retried(pushable, count_pushes):
    wt, _ = pushable
    _commit(wt, "work")

    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.PUSHED
    assert result.retry is git.push.Retry.NONE
    assert len(count_pushes) == 1


def test_retry_blocked_when_head_moved(pushable, monkeypatch):
    """The gates approved a commit that is no longer HEAD."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    _lose_pushes(remote)
    real_run = git.client.run

    def move_head(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args and args[0] == "push":
            _commit(wt, "later work")
        return result

    monkeypatch.setattr(git.client, "run", move_head)
    result = git.push.push(wt, gated=False, sha=sha)

    assert result.status is git.push.PushStatus.LOST
    assert result.retry is git.push.Retry.HEAD_MOVED


def test_retry_blocked_when_tree_dirty(pushable, monkeypatch):
    """This repo's pre-push regenerates files; a dirty tree is not what it saw."""
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)
    real_run = git.client.run

    def dirty(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args and args[0] == "push":
            (wt / "regenerated.txt").write_text("hook output\n")
        return result

    monkeypatch.setattr(git.client, "run", dirty)
    result = git.push.push(wt, gated=False)

    assert result.status is git.push.PushStatus.LOST
    assert result.retry is git.push.Retry.DIRTY


def test_retry_safety_check_reads_wt_path_not_an_inherited_git_dir(pushable, monkeypatch):
    """The retry's HEAD/dirty check must answer for *wt_path*, not for whatever
    repository an inherited `GIT_DIR` names.

    `GIT_DIR` is pointed at the bare remote, whose `main` sits one commit
    behind *wt* right after the losing hook rewinds it — exactly what
    `_retry_block` would see if the `env` it was handed never reached
    `head_sha`/`is_dirty`, and would misread as the gated commit no longer
    being HEAD, blocking a retry that is in fact perfectly safe.
    """
    wt, remote = pushable
    sha = _commit(wt, "work")
    hook = _lose_pushes(remote)
    monkeypatch.setenv("GIT_DIR", str(remote))
    real_run = git.client.run
    seen: list[tuple[str, ...]] = []

    def heal_after_first(*args, **kwargs):
        if args and args[0] == "push":
            seen.append(args)
            if len(seen) == 1:
                result = real_run(*args, **kwargs)
                hook.unlink()
                return result
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", heal_after_first)
    result = git.push.push(
        wt, gated=False, sha=sha, branch="main", env=gitenv.git_env_clear(),
    )

    assert result.status is git.push.PushStatus.PUSHED
    assert result.retry is git.push.Retry.ATTEMPTED
    assert result.remote_sha == sha
