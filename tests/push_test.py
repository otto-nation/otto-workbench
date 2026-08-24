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
    ("Updates were rejected because the tip is behind its remote counterpart.",
     push.Refusal.DIVERGED),
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


# ── the report ──────────────────────────────────────────────────────────────


def test_output_tail_keeps_only_the_tail_of_a_long_gate_dump():
    tail = push.output_tail("\n".join(str(n) for n in range(50))).splitlines()
    assert len(tail) == push._HOOK_OUTPUT_LINES
    assert tail[-1] == "49"


def test_output_tail_indents_every_line_when_asked():
    assert push.output_tail("a\nb", indent="  ") == "  a\n  b"


def test_output_tail_drops_the_blank_a_missing_stream_leaves():
    """`combined_output` joins two streams; an empty one must not print."""
    assert push.output_tail("\n✗ Pytest failed\n\n") == "✗ Pytest failed"


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
    assert printed.count("line ") == push._HOOK_OUTPUT_LINES


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


def test_every_retry_state_has_a_report_line():
    """A LOST report claiming a retry that never ran is the wrong-reporting
    failure this module exists to remove."""
    assert set(push._RETRY_NOTE) == set(push.Retry)
