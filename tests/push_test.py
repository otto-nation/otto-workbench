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

import signal
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git import client as git_client  # noqa: E402
from core import proc  # noqa: E402
from git import push  # noqa: E402
from core import timeouts  # noqa: E402
from core.trail import Trail  # noqa: E402

from conftest import _last_event, git_in, run_checked, seed_repo  # noqa: E402

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
    """A `git_client.run` that fails the test if anything reaches it."""
    raise AssertionError(f"git ran when it should not have: {cmd}")


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


def test_remote_head_is_not_fooled_by_a_branch_ending_in_the_same_name(pushable):
    """An `ls-remote` pattern matches any ref whose tail spells it.

    `alt/main` answers a query for `main`, and git sorts its output — which is
    why the impostor is named to sort ahead of the real branch. Reading the
    first line would hand back a SHA belonging to something else, reporting a
    landed push as lost or, worse, the reverse.
    """
    wt, _ = pushable
    on_main = git_client.head_sha(cwd=wt)
    git_in(wt, "checkout", "-q", "-b", "alt/main")
    _commit(wt, "the impostor")
    git_in(wt, "push", "-q", "origin", "alt/main")

    assert push.remote_head(wt, "main") == on_main


# ── holds ───────────────────────────────────────────────────────────────────


def test_holds_answers_for_the_commit_the_remote_has(pushable):
    wt, _ = pushable
    assert push.holds(wt, git_client.head_sha(cwd=wt)) is True


def test_holds_answers_for_an_earlier_commit_a_later_push_carried_out(pushable):
    """Ancestry, not equality — a round's commit rides out on the next one's push."""
    wt, _ = pushable
    earlier = git_client.head_sha(cwd=wt)
    _commit(wt, "a later round")
    git_in(wt, "push", "-q", "origin", "main")

    assert push.holds(wt, earlier) is True


def test_holds_declines_a_commit_that_never_left(pushable):
    wt, _ = pushable
    local = _commit(wt, "not pushed")
    assert push.holds(wt, local) is False


def test_holds_declines_a_branch_the_remote_does_not_have(pushable):
    wt, _ = pushable
    git_in(wt, "checkout", "-q", "-b", "feat/unpushed")
    assert push.holds(wt, _commit(wt, "on a new branch")) is False


def test_an_unreachable_remote_reads_as_pending(pushable):
    """Deferring a citation is the safe answer; publishing a dead link is not."""
    wt, _ = pushable
    sha = git_client.head_sha(cwd=wt)
    git_in(wt, "remote", "set-url", "origin", str(wt / "nope.git"))
    assert push.holds(wt, sha) is False


def test_holds_reads_the_remote_rather_than_the_tracking_ref(pushable):
    """A lost push leaves `origin/main` pointing at the commit that never arrived.

    Which is exactly the failure a caller asks this to rule out, so answering
    from the local tracking ref would answer yes for it.
    """
    wt, remote = pushable
    _lose_pushes(remote)
    lost = _commit(wt, "the push that vanishes")
    git_in(wt, "push", "-q", "origin", "main")

    assert git_client.out("rev-parse", "origin/main", cwd=wt) == lost
    assert push.holds(wt, lost) is False


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
    assert push.remote_head(wt, "main") != sha


def test_a_held_push_reads_nothing_from_the_repository(pushable, monkeypatch):
    """The gate is asked first, so a held run does not shell out at all.

    It carries only what the caller passed in — callers that report a SHA for a
    held commit have one of their own, and the draft names the command rather
    than the commit.
    """
    wt, _ = pushable
    _commit(wt, "work")
    monkeypatch.setattr(push.git_client, "run", _never_runs)
    result = push.push(wt, gated=True)
    assert result.status is push.PushStatus.HELD
    assert result.sha == ""
    assert push.push(wt, gated=True, sha="abc1234").sha == "abc1234"


# ── what a refusal records ──────────────────────────────────────────────────


_HOOK_DUMP = (
    "Running pre-push checks for: .claude,dev-ci,lib-go,svc-product\n"
    + "\n".join(f"pkl-drift: generated file {n} matches" for n in range(80))
    + "\n✗ pre-commit: lint:ts failed\n"
)


def test_a_refusal_records_the_verdict_not_the_banner(monkeypatch):
    """The banner is the first 500 characters; the verdict is the last line."""
    monkeypatch.setattr(push.git_client, "run",
                        lambda *a, **k: proc.CmdResult(1, "", _HOOK_DUMP))
    trail = Trail.start(script="test", context={})

    result = push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x",
                       trail=trail)

    recorded = _last_event()["data"]["error"]
    assert "✗ pre-commit: lint:ts failed" in recorded
    assert "Running pre-push checks" not in recorded
    assert result.status is push.PushStatus.REFUSED


def test_a_refusal_keeps_the_whole_hook_output(monkeypatch):
    monkeypatch.setattr(push.git_client, "run",
                        lambda *a, **k: proc.CmdResult(1, "", _HOOK_DUMP))
    trail = Trail.start(script="test", context={})

    result = push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x",
                       trail=trail)

    assert result.log is not None
    assert "Running pre-push checks" in result.log.read_text()
    assert "✗ pre-commit: lint:ts failed" in result.log.read_text()


def test_a_refusal_still_records_the_sha_and_branch(monkeypatch):
    monkeypatch.setattr(push.git_client, "run",
                        lambda *a, **k: proc.CmdResult(1, "", "denied"))
    trail = Trail.start(script="test", context={})

    push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x", trail=trail)

    data = _last_event()["data"]
    assert data["sha"] == "1a2b3c4d"
    assert data["branch"] == "feat/x"


def test_a_push_without_a_trail_still_reports_the_refusal(monkeypatch):
    monkeypatch.setattr(push.git_client, "run",
                        lambda *a, **k: proc.CmdResult(1, "", "denied"))

    result = push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x")

    assert result.status is push.PushStatus.REFUSED
    assert result.log is None


def test_the_refused_report_names_the_full_output(capsys, tmp_path):
    artifact = tmp_path / "214e9758c739-1-push.log"
    artifact.write_text("everything the hook said")
    result = push.PushResult(
        push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.HOOK, output="✗ lint:ts failed", log=artifact,
    )

    push.report(result, "/tmp/wt")

    assert f"full output: {artifact}" in capsys.readouterr().err


def test_the_refused_report_omits_the_line_when_there_is_no_artifact(capsys):
    result = push.PushResult(
        push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.HOOK, output="✗ lint:ts failed",
    )

    push.report(result, "/tmp/wt")

    assert "full output:" not in capsys.readouterr().err


# ── the refusal classifier ──────────────────────────────────────────────────


@pytest.mark.parametrize("output,expected", [
    ("! [rejected]  main -> main (non-fast-forward)", push.Refusal.DIVERGED),
    ("Updates were rejected because the remote contains work.\nfetch first",
     push.Refusal.DIVERGED),
    ("! [rejected] main -> main (stale info)", push.Refusal.DIVERGED),
    ("Updates were rejected because the tip is behind its remote counterpart.",
     push.Refusal.DIVERGED),
    ("ssh: Could not resolve host: github.com", push.Refusal.TRANSPORT),
    ("fatal: Could not read from remote repository.", push.Refusal.TRANSPORT),
    ("Permission denied (publickey).", push.Refusal.TRANSPORT),
    ("validate-all failed\nerror: failed to push some refs to 'origin'",
     push.Refusal.HOOK),
    ("something nobody has seen before", push.Refusal.OTHER),
    ("Read from remote host github.com: Connection reset by peer",
     push.Refusal.DROPPED),
    ("client_loop: send disconnect: Broken pipe", push.Refusal.DROPPED),
    ("fatal: The remote end hung up unexpectedly", push.Refusal.DROPPED),
])
def test_classify_names_the_refusal(output, expected):
    assert push.classify(output) == expected


def test_a_hook_rejection_outranks_nothing_it_shares_words_with():
    """A transport failure also prints the generic refusal line; it wins."""
    output = ("fatal: Could not read from remote repository.\n"
              "error: failed to push some refs to 'origin'")
    assert push.classify(output) is push.Refusal.TRANSPORT


# What a push killed by a mid-transfer reset actually printed, from #1262. Every
# other classification in this module matches something in it, which is why the
# ordering in `classify` is the whole fix: the ssh diagnostic reads as transport
# and the trailing line reads as a hook rejection.
_RESET_DUMP = (
    "Read from remote host github.com: Connection reset by peer\n"
    "client_loop: send disconnect: Broken pipe\n"
    "fatal: Could not read from remote repository.\n"
    "error: failed to push some refs to 'origin'\n"
)


def test_a_drop_outranks_the_refusal_line_it_prints_too():
    """The observed #1262 output: neither the tests nor the remote said no.

    Classified as HOOK it tells the operator their checks failed, which is the
    misdirection the issue is about — the gates had all printed a tick.
    """
    assert push.classify(_RESET_DUMP) is push.Refusal.DROPPED


@pytest.mark.parametrize("output", [
    "Permission denied (publickey).",
    "ssh: Could not resolve host: github.com",
    "ERROR: Repository not found.",
])
def test_an_auth_failure_is_not_a_drop(output):
    """Nothing was established, so the remote has nothing to be asked about.

    Verifying these would ask `ls-remote` to reach a remote the credentials just
    failed against, and report a push that never happened as one that could not
    be confirmed.
    """
    assert push.classify(output) is push.Refusal.TRANSPORT


# ── the drop predicate ──────────────────────────────────────────────────────


def test_a_signal_death_with_no_output_is_a_drop():
    """A git that took a signal on the way down usually says nothing at all."""
    assert push._dropped(proc.CmdResult(-signal.SIGPIPE, "", ""))


def test_a_plain_refusal_is_not_a_drop():
    assert not push._dropped(
        proc.CmdResult(1, "", "error: failed to push some refs to 'origin'"))


def test_a_shells_141_is_not_how_a_signal_arrives_here():
    """`proc.run` reports a signal as the negated number, never as 128 + it.

    141 is the shell's rendering of SIGPIPE and reaches no Python here, so a
    predicate written against it would be dead code that never fires.
    """
    assert not push._dropped(proc.CmdResult(128 + signal.SIGPIPE, "", ""))


def test_a_killed_push_speaks_through_the_signal_when_it_said_nothing():
    """An empty excerpt under the headline is the unreadable failure #1262 names."""
    spoken = push._push_output(proc.CmdResult(-signal.SIGPIPE, "", ""))
    assert "SIGPIPE" in spoken


def test_a_killed_push_that_did_speak_is_quoted_rather_than_summarised():
    assert push._push_output(
        proc.CmdResult(-signal.SIGPIPE, "", _RESET_DUMP)) == _RESET_DUMP


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
    real_run = git_client.run
    seen: list[tuple[str, ...]] = []

    def dropping(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args and args[0] == "push":
            seen.append(args)
            return proc.CmdResult(-signal.SIGPIPE, "", _RESET_DUMP)
        return result

    monkeypatch.setattr(git_client, "run", dropping)
    return seen


def test_a_dropped_push_the_remote_took_is_pushed(pushable, drops_the_connection):
    """git could not say it landed; `ls-remote` can, and it did."""
    wt, _ = pushable
    sha = _commit(wt, "work")

    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.PUSHED
    assert result.refusal is push.Refusal.DROPPED
    assert result.remote_sha == sha
    assert result.retry is push.Retry.NONE
    assert len(drops_the_connection) == 1


def test_a_dropped_push_the_remote_never_took_is_lost_and_retried(
        pushable, drops_the_connection):
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)

    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.LOST
    assert result.retry is push.Retry.ATTEMPTED
    assert len(drops_the_connection) == 2


def test_a_dropped_push_recovers_when_the_retry_lands(pushable, monkeypatch):
    """The gates passed seconds ago for this commit, so the retry costs a transfer."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    hook = _lose_pushes(remote)
    seen: list[tuple[str, ...]] = []
    real_run = git_client.run

    def drop_then_heal(*args, **kwargs):
        if args and args[0] == "push":
            seen.append(args)
            if len(seen) == 1:
                real_run(*args, **kwargs)
                hook.unlink()
                return proc.CmdResult(-signal.SIGPIPE, "", _RESET_DUMP)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git_client, "run", drop_then_heal)
    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.PUSHED
    assert result.retry is push.Retry.ATTEMPTED
    assert result.remote_sha == sha
    assert "--no-verify" in seen[1]


def test_an_auth_failure_is_refused_without_asking_the_remote(monkeypatch):
    """Asking a remote the credentials just failed against reports UNVERIFIED.

    That is a legibility regression, not a fix: a push that never happened would
    be reported as one that could not be confirmed.
    """
    monkeypatch.setattr(
        push.git_client, "run",
        lambda *a, **k: proc.CmdResult(128, "", "Permission denied (publickey)."))
    monkeypatch.setattr(push, "remote_head", _never_runs)

    result = push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x")

    assert result.status is push.PushStatus.REFUSED
    assert result.refusal is push.Refusal.TRANSPORT


def test_a_hook_rejection_is_refused_without_asking_the_remote(monkeypatch):
    refused = f"{_HOOK_DUMP}error: failed to push some refs to 'origin'\n"
    monkeypatch.setattr(push.git_client, "run",
                        lambda *a, **k: proc.CmdResult(1, "", refused))
    monkeypatch.setattr(push, "remote_head", _never_runs)

    result = push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x")

    assert result.status is push.PushStatus.REFUSED
    assert result.refusal is push.Refusal.HOOK


def test_a_dropped_push_reaches_the_trail(pushable, drops_the_connection):
    """`otto-log` should hold the event, since the console line says it recovered."""
    wt, _ = pushable
    sha = _commit(wt, "work")
    trail = Trail.start(script="test", context={})

    push.push(wt, gated=False, trail=trail)

    event = _last_event()
    assert event["data"]["sha"] == sha
    assert "dropped" in event["detail"]


def test_a_dropped_refusal_is_not_repairable():
    """Nothing in the tree was rejected, so there is nothing for a fix pass."""
    assert not push.PushResult(
        push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.DROPPED).repairable


@pytest.mark.parametrize("refusal", [push.Refusal.HOOK, push.Refusal.DIVERGED,
                                     push.Refusal.TRANSPORT, push.Refusal.OTHER])
def test_every_other_refusal_is_repairable(refusal):
    assert push.PushResult(
        push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=refusal).repairable


@pytest.mark.parametrize("status", [push.PushStatus.PUSHED, push.PushStatus.HELD,
                                    push.PushStatus.LOST,
                                    push.PushStatus.UNVERIFIED])
def test_only_a_refusal_is_repairable(status):
    assert not push.PushResult(status, sha="1a2b3c4d", branch="feat/x").repairable


# ── the retry ───────────────────────────────────────────────────────────────


@pytest.fixture
def count_pushes(monkeypatch) -> list[tuple[str, ...]]:
    """Record every `git push` the owner issues, and let them all through."""
    seen: list[tuple[str, ...]] = []
    real_run = git_client.run

    def counting(*args, **kwargs):
        if args and args[0] == "push":
            seen.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(git_client, "run", counting)
    return seen


def test_lost_push_retries_once_and_recovers(pushable, monkeypatch):
    """The retry skips the gates, so a transient loss costs seconds."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    hook = _lose_pushes(remote)
    seen: list[tuple[str, ...]] = []
    real_run = git_client.run

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

    monkeypatch.setattr(git_client, "run", heal_after_first)
    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.PUSHED
    assert result.retry is push.Retry.ATTEMPTED
    assert result.remote_sha == sha
    assert "--no-verify" in seen[1]


def test_retry_is_bounded_at_one(pushable, count_pushes):
    """A remote that always loses the push must not loop."""
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)

    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.LOST
    assert result.retry is push.Retry.ATTEMPTED
    assert len(count_pushes) == 2


def test_a_landed_push_is_not_retried(pushable, count_pushes):
    wt, _ = pushable
    _commit(wt, "work")

    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.PUSHED
    assert result.retry is push.Retry.NONE
    assert len(count_pushes) == 1


def test_retry_blocked_when_head_moved(pushable, monkeypatch):
    """The gates approved a commit that is no longer HEAD."""
    wt, remote = pushable
    sha = _commit(wt, "work")
    _lose_pushes(remote)
    real_run = git_client.run

    def move_head(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args and args[0] == "push":
            _commit(wt, "later work")
        return result

    monkeypatch.setattr(git_client, "run", move_head)
    result = push.push(wt, gated=False, sha=sha)

    assert result.status is push.PushStatus.LOST
    assert result.retry is push.Retry.HEAD_MOVED


def test_retry_blocked_when_tree_dirty(pushable, monkeypatch):
    """This repo's pre-push regenerates files; a dirty tree is not what it saw."""
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)
    real_run = git_client.run

    def dirty(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if args and args[0] == "push":
            (wt / "regenerated.txt").write_text("hook output\n")
        return result

    monkeypatch.setattr(git_client, "run", dirty)
    result = push.push(wt, gated=False)

    assert result.status is push.PushStatus.LOST
    assert result.retry is push.Retry.DIRTY


# ── the resume command ──────────────────────────────────────────────────────


def _result(status, refusal=None, sha="1a2b3c4d", branch="feat/x", args=()):
    return push.PushResult(status, sha=sha, branch=branch, refusal=refusal, args=args)


def test_a_landed_push_needs_no_resume():
    assert push.resume_command(_result(push.PushStatus.PUSHED), "/tmp/wt") == ""


@pytest.mark.parametrize("status", [s for s in push.PushStatus if s is not
                                    push.PushStatus.PUSHED])
def test_every_unfinished_status_names_a_command(status):
    """Rule 2 of the land owner: nothing falls short without saying what finishes it."""
    resume = push.resume_command(_result(status), "/tmp/wt")
    assert resume.startswith("git -C '/tmp/wt' ")


@pytest.mark.parametrize("refusal", list(push.Refusal))
def test_every_refusal_names_a_command(refusal):
    """A refusal kind with no answer would render an empty "Resume:" line."""
    assert push.resume_command(
        _result(push.PushStatus.REFUSED, refusal), "/tmp/wt",
    ).startswith("git -C '/tmp/wt' push")


def test_a_divergence_answers_force_with_lease():
    assert push.resume_command(
        _result(push.PushStatus.REFUSED, push.Refusal.DIVERGED), "/tmp/wt",
    ) == "git -C '/tmp/wt' push --force-with-lease"


@pytest.mark.parametrize("refusal", [push.Refusal.HOOK, push.Refusal.TRANSPORT,
                                     push.Refusal.DROPPED, push.Refusal.OTHER])
def test_no_other_refusal_answers_a_force_push(refusal):
    """A pre-push hook rejection is not divergence — force-pushing is wrong advice."""
    assert "--force" not in push.resume_command(
        _result(push.PushStatus.REFUSED, refusal), "/tmp/wt",
    )


def test_an_unverified_push_is_checked_rather_than_repushed():
    """It has very likely landed; `ls-remote` answers in one round trip."""
    result = push.PushResult(
        push.PushStatus.UNVERIFIED, sha="1a2b3c4d", branch="feat/x", remote="upstream",
    )
    assert push.resume_command(result, "/tmp/wt") == (
        "git -C '/tmp/wt' ls-remote upstream feat/x"
    )


def test_the_resume_replays_what_the_push_was_given():
    """`pr rebase` pushes with a lease; a plain resume is refused a second time."""
    result = _result(push.PushStatus.REFUSED, push.Refusal.HOOK,
                     args=("--force-with-lease",))
    assert push.resume_command(result, "/tmp/wt") == (
        "git -C '/tmp/wt' push --force-with-lease"
    )


def test_a_divergence_does_not_repeat_a_lease_the_push_already_carried():
    result = _result(push.PushStatus.REFUSED, push.Refusal.DIVERGED,
                     args=("--force-with-lease",))
    assert push.resume_command(result, "/tmp/wt").count("--force-with-lease") == 1


def test_a_push_records_the_arguments_it_was_given(monkeypatch):
    """Nothing can replay them later unless the result carried them out."""
    monkeypatch.setattr(push.git_client, "run",
                        lambda *a, **k: proc.CmdResult(1, "", "failed to push some refs"))
    result = push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x",
                       args=["--force-with-lease"])
    assert result.args == ("--force-with-lease",)


def test_the_resume_command_quotes_a_worktree_with_a_space():
    """Unquoted, the path splits and the command runs somewhere else or nowhere."""
    resume = push.resume_command(_result(push.PushStatus.LOST), "/tmp/my wt")
    assert "'/tmp/my wt'" in resume


# ── the report ──────────────────────────────────────────────────────────────


def test_refused_report_trims_a_whole_test_suite_to_its_tail(capsys):
    """A failing pre-push prints its entire suite; the tail is what named it."""
    output = "\n".join(f"line {n}" for n in range(200))
    result = push.PushResult(
        push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.HOOK, output=output,
    )
    push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "line 199" in printed
    assert "line 0" not in printed
    assert printed.count("line ") == proc.TAIL_LINES


def test_lost_report_names_the_branch_the_commit_and_the_remote(capsys):
    result = push.PushResult(
        push.PushStatus.LOST, sha="1a2b3c4d5e", branch="feat/x",
        remote_sha="9f8e7d6c5b", retry=push.Retry.ATTEMPTED,
    )
    push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "the remote did not move" in printed
    assert "feat/x" in printed
    assert "1a2b3c4" in printed
    assert "9f8e7d6" in printed
    assert "Retried once" in printed


def test_lost_report_says_when_the_remote_holds_no_such_ref(capsys):
    result = push.PushResult(
        push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x", remote_sha="",
    )
    push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "no such ref" in printed
    assert "Not retried." in printed


def test_a_dropped_push_that_landed_says_the_connection_dropped(capsys):
    """The terminal is full of red ssh diagnostics; the commit arrived anyway."""
    result = push.PushResult(
        push.PushStatus.PUSHED, sha="1a2b3c4d", branch="feat/x",
        remote_sha="1a2b3c4d", refusal=push.Refusal.DROPPED,
    )
    push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "connection dropped" in printed
    assert "1a2b3c4" in printed


def test_a_dropped_push_does_not_claim_git_reported_success(capsys):
    """git reporting success is the one thing that did not happen here."""
    result = push.PushResult(
        push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.DROPPED, retry=push.Retry.ATTEMPTED,
    )
    push.report(result, "/tmp/wt")

    printed = capsys.readouterr().err
    assert "reported success" not in printed
    assert "connection dropped" in printed


def test_a_lost_push_git_reported_as_clean_still_says_so(capsys):
    """The classic lost push is the one git *did* report as a success."""
    result = push.PushResult(
        push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x",
    )
    push.report(result, "/tmp/wt")

    assert "reported success" in capsys.readouterr().err


def test_a_dropped_refusal_does_not_claim_nothing_reached_the_remote(capsys):
    """Whether anything reached it is exactly what a drop leaves unestablished."""
    result = push.PushResult(
        push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.DROPPED, output=_RESET_DUMP,
    )
    push.report(result, "/tmp/wt")

    assert "nothing reached the remote" not in capsys.readouterr().err


def test_the_dropped_report_quotes_what_killed_the_push(capsys):
    """A LOST report prints no output normally; here the signal is the account."""
    result = push.PushResult(
        push.PushStatus.LOST, sha="1a2b3c4d", branch="feat/x",
        refusal=push.Refusal.DROPPED,
        output="git was killed by SIGPIPE (signal 13)",
    )
    push.report(result, "/tmp/wt")

    assert "SIGPIPE" in capsys.readouterr().err


def test_every_retry_state_has_a_report_line():
    """A LOST report claiming a retry that never ran is the wrong-reporting
    failure this module exists to remove."""
    assert set(push._RETRY_NOTE) == set(push.Retry)


# ── the bash bridge ─────────────────────────────────────────────────────────


def test_cli_exit_codes_cover_every_status():
    """A status with no exit code would raise KeyError at the worst moment."""
    assert set(push._EXIT_CODES) == set(push.PushStatus)


def test_cli_exits_zero_on_a_verified_push(pushable):
    wt, _ = pushable
    _commit(wt, "work")
    assert push.main(["--cwd", str(wt), "--branch", "main"]) == 0


def test_cli_reports_a_lost_push(pushable, capsys):
    wt, remote = pushable
    _commit(wt, "work")
    _lose_pushes(remote)
    assert push.main(["--cwd", str(wt), "--branch", "main"]) == 2
    assert "the remote did not move" in capsys.readouterr().err


def test_cli_exits_one_when_git_refuses(pushable, monkeypatch):
    """A refusal and a lost push get different codes so bash can tell them apart."""
    wt, _ = pushable
    _commit(wt, "work")
    monkeypatch.setattr(
        push.git_client, "run",
        lambda *cmd, **kw: proc.CmdResult(1, "", "error: failed to push some refs"),
    )
    assert push.main(["--cwd", str(wt), "--branch", "main"]) == 1


def test_cli_exits_three_when_the_remote_cannot_be_asked(pushable, monkeypatch):
    """Unverified is its own code — the shell warns rather than aborting."""
    wt, _ = pushable
    _commit(wt, "work")
    monkeypatch.setattr(push, "remote_head", lambda *a, **k: None)
    assert push.main(["--cwd", str(wt), "--branch", "main"]) == 3
