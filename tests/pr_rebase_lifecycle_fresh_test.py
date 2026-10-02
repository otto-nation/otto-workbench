"""Tests for rebase.lifecycle.fresh: checkout, the landed preflight and the replay."""

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.landed  # noqa: E402
import git.client  # noqa: E402
import git.regenerate  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.refusals  # noqa: E402
import pr.domains  # noqa: E402

from pr_rebase_support import (
    _unconfigured,
    _TARGET,
    _git,
    _LANDED_BRANCH,
    _LANDED_PR,
    _completed,
    _landed_ctx,
)


# ── _fresh ──────────────────────────────────────────────────────────────────


def test_fresh_no_dirty_check():
    """_fresh no longer checks for uncommitted changes (handled by cmd_start auto-stash)."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"

    status_cmds = []

    def fake_run(cmd, **kwargs):
        if "--porcelain" in cmd:
            status_cmds.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    assert len(status_cmds) == 0


def test_fresh_delegates_to_drive_on_paused_rebase():
    """_fresh calls _drive_to_completion when rebase is in progress after initial start."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"

    def fake_run(cmd, **kwargs):
        if "--porcelain" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0) as mock_drive:
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.FIX, target_ref=_TARGET,
        )

    assert result == 0
    mock_drive.assert_called_once_with(
        "/fake", ctx, rebase.types.RunMode.FIX, target_ref=_TARGET, force=False,
        tally=rebase.types.ResolutionTally(), lease=None, verify=True,
        snapshot=None, trail=None,
    )


@pytest.mark.parametrize("paused", [False, True])
def test_fresh_forwards_no_verify_to_whichever_path_lands(paused):
    """A clean replay lands from rebase_success, a paused one via the drive.

    Both are reached from `fresh`, so `--no-verify` dropped on either branch
    pushes through the very hook the operator asked to skip.
    """
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "feat/my-branch"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=paused), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0) as success, \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0) as drive:
        rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.FIX, target_ref=_TARGET,
            verify=False,
        )

    lander = drive if paused else success
    assert lander.call_args.kwargs["verify"] is False


def test_fresh_skips_checkout_when_on_correct_branch():
    """No checkout when current_branch already matches ctx.branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "feat/my-branch"
    checkout_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    assert len(checkout_calls) == 0


def _fake_run_without_local_branch(checkout_calls):
    """A git stub for a worktree that does not have ctx.branch locally yet.

    `rev-parse --verify` is the question _checkout_target_branch asks first, and
    a blanket success would answer "the branch is already here" — which is a
    different path with a different checkout.
    """
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    return fake_run


def test_fresh_checks_out_branch_on_detached_head():
    """Detached HEAD (current_branch=None) triggers checkout -B."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = None
    checkout_calls = []

    with mock.patch("subprocess.run", side_effect=_fake_run_without_local_branch(checkout_calls)), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    assert len(checkout_calls) == 1
    assert checkout_calls[0] == ["git", "checkout", "-B", "feat/my-branch", "origin/feat/my-branch"]


def test_fresh_checks_out_branch_on_wrong_branch():
    """Wrong current_branch triggers checkout -B to ctx.branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "other-branch"
    checkout_calls = []

    with mock.patch("subprocess.run", side_effect=_fake_run_without_local_branch(checkout_calls)), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    assert len(checkout_calls) == 1
    assert checkout_calls[0] == ["git", "checkout", "-B", "feat/my-branch", "origin/feat/my-branch"]


def test_fresh_refuses_to_check_out_into_default_branch_worktree():
    """Regression: checking out into main/ let the next main sync eat the branch."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "main"
    checkout_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            checkout_calls.append(cmd)
        stdout = "refs/remotes/origin/main\n" if "symbolic-ref" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1
    assert len(checkout_calls) == 0


def test_fresh_refuses_to_rebase_the_default_branch():
    """The protected-branch check follows origin/HEAD, not a hardcoded 'main'."""
    ctx = mock.MagicMock()
    ctx.branch = "trunk"
    ctx.current_branch = "trunk"

    def fake_run(cmd, **kwargs):
        stdout = "refs/remotes/origin/trunk\n" if "symbolic-ref" in cmd else ""
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1


# Whether the local ref exists picks between two different checkouts, so a
# failure has to propagate from both — one blanket stub would only ever prove
# whichever path its rev-parse answer happened to select.
@pytest.mark.parametrize("local_ref_exists", [False, True])
def test_fresh_checkout_failure_returns_error(local_ref_exists):
    """Checkout failure aborts with return code 1, whichever checkout ran."""
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = None

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "checkout"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error: pathspec")
        if cmd[:3] == ["git", "rev-parse", "--verify"] and not local_ref_exists:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False):
        result = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1


def _run_fresh(*, tracker=None, git=None, unrelated=None, force=False,
               current_branch=_LANDED_BRANCH, target_ref=_TARGET):
    """Run _fresh with every half of the preflight forced.

    Returns (exit code, commands run, checkout-seen-by-each-half), the last of
    which is what pins the ordering: the tracker probe must run before the
    checkout and the git signals after it.
    """
    ctx = mock.MagicMock()
    ctx.branch = _LANDED_BRANCH
    ctx.current_branch = current_branch
    commands, saw_checkout = [], {}

    def fake_run(cmd, **kwargs):
        commands.append(list(cmd))
        return _completed(cmd)

    def record(half, report):
        saw_checkout[half] = any(c[:2] == ["git", "checkout"] for c in commands)
        return report

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(rebase.refusals, "tracker_landed_check",
                           side_effect=lambda *_, **kw: record("tracker", tracker)), \
         mock.patch.object(rebase.refusals, "git_landed_check",
                           side_effect=lambda *_, **kw: record("git", git)), \
         mock.patch.object(rebase.refusals, "unrelated_history_check",
                           side_effect=lambda *_, **kw: record("unrelated", unrelated)), \
         mock.patch.object(rebase.refusals, "refuse", return_value=4), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        rc = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, force=force, target_ref=target_ref,
        )

    return rc, commands, saw_checkout


def _landed_report(signal="pr_merged"):
    return rebase.types.RefusalReport(
        branch=_LANDED_BRANCH, signal=signal,
        detail=f"PR #{_LANDED_PR} is merged", pr_number=_LANDED_PR,
    )


def test_fresh_refuses_a_landed_branch_before_touching_the_remote():
    """Regression: rebasing a merged branch force-pushed the deleted remote back."""
    rc, commands, _ = _run_fresh(tracker=_landed_report())

    assert rc == 4
    assert not any(cmd[:2] == ["git", "rebase"] for cmd in commands)
    assert not any(cmd[:2] == ["git", "push"] for cmd in commands)


def test_fresh_asks_the_tracker_before_the_checkout():
    """--prune has just deleted origin/<branch>; the checkout starts from it."""
    rc, commands, saw_checkout = _run_fresh(
        tracker=_landed_report(), current_branch="other",
    )

    assert rc == 4
    assert saw_checkout["tracker"] is False
    assert not any(cmd[:2] == ["git", "checkout"] for cmd in commands)


def test_fresh_probes_the_tracker_even_when_already_on_the_branch():
    """The common case takes the same path — no checkout, same probe."""
    rc, commands, saw_checkout = _run_fresh()

    assert rc == 0
    assert saw_checkout["tracker"] is False
    assert not any(cmd[:2] == ["git", "checkout"] for cmd in commands)


def test_fresh_runs_the_git_signals_after_the_checkout():
    """They compare HEAD, so they mean nothing until the branch is checked out."""
    rc, _, saw_checkout = _run_fresh(
        git=_landed_report("empty_diff"), current_branch="other",
    )

    assert rc == 4
    assert saw_checkout["git"] is True


def test_fresh_refuses_an_unrelated_branch_before_rebasing():
    """The rebase would replay the branch's whole history onto a foreign root."""
    report = rebase.types.RefusalReport(
        branch=_LANDED_BRANCH, signal=rebase.types.RefusalSignal.NO_MERGE_BASE.value,
        detail=f"no commit in common with {_TARGET}",
        status=pr.domains.RebaseStatus.UNRELATED_HISTORY.value,
    )
    rc, commands, saw_checkout = _run_fresh(unrelated=report, current_branch="other")

    assert rc == 4
    # It compares HEAD against the ref, so it means nothing before the checkout.
    assert saw_checkout["unrelated"] is True
    assert not any(cmd[:2] == ["git", "rebase"] for cmd in commands)


def test_fresh_asks_about_unrelated_history_before_the_git_landed_signals():
    """The landed signals compare against a ref an unrelated branch cannot answer for."""
    rc, _, saw_checkout = _run_fresh(
        unrelated=rebase.types.RefusalReport(
            branch=_LANDED_BRANCH,
            signal=rebase.types.RefusalSignal.NO_MERGE_BASE.value,
            detail="no commit in common",
            status=pr.domains.RebaseStatus.UNRELATED_HISTORY.value,
        ),
    )

    assert rc == 4
    assert "git" not in saw_checkout


def test_fresh_skips_every_half_of_the_preflight_under_force():
    """--force must not spend a gh round trip only to ignore the answer."""
    ctx = mock.MagicMock()
    ctx.branch = _LANDED_BRANCH
    ctx.current_branch = _LANDED_BRANCH

    with mock.patch("subprocess.run", side_effect=lambda cmd, **kw: _completed(cmd)), \
         mock.patch.object(rebase.refusals, "tracker_landed_check") as mock_tracker, \
         mock.patch.object(rebase.refusals, "git_landed_check") as mock_git, \
         mock.patch.object(rebase.refusals, "unrelated_history_check") as mock_unrelated, \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, force=True, target_ref=_TARGET,
        )

    mock_tracker.assert_not_called()
    mock_git.assert_not_called()
    mock_unrelated.assert_not_called()


def test_fresh_rebases_a_landed_branch_under_force():
    rc, commands, _ = _run_fresh(tracker=_landed_report(), force=True)

    assert rc == 0
    assert ["git", "rebase", "--autosquash", _TARGET] in [
        _unconfigured(cmd) for cmd in commands
    ]


def test_fresh_rebases_onto_the_resolved_ref():
    """A repo whose trunk is not main must not be replayed onto origin/main.

    The ref reaching `git rebase` is the one the run resolved, so a repo on
    master, a release branch, or a stack parent replays onto its own base.
    """
    rc, commands, _ = _run_fresh(target_ref="origin/master")
    bare = [_unconfigured(cmd) for cmd in commands]

    assert rc == 0
    assert ["git", "rebase", "--autosquash", "origin/master"] in bare
    assert not any(cmd[:2] == ["git", "rebase"] and _TARGET in cmd
                   for cmd in bare)


def test_fresh_prunes_on_fetch():
    """Without --prune the stale remote-tracking ref satisfies --force-with-lease."""
    _, commands, _ = _run_fresh()

    assert ["git", "fetch", "--prune", "origin"] in commands


def test_fresh_falls_back_to_the_git_signals_when_the_tracker_is_unreachable():
    """A gh that cannot answer must not disarm the preflight."""
    ctx = mock.MagicMock()
    ctx.branch = _LANDED_BRANCH
    ctx.current_branch = _LANDED_BRANCH
    seen = []

    with mock.patch("subprocess.run", side_effect=lambda cmd, **kw: _completed(cmd)), \
         mock.patch.object(git.regenerate, "try_run", return_value=None), \
         mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(gh.landed, "diff_is_empty", return_value=True), \
         mock.patch.object(rebase.refusals, "refuse",
                           side_effect=lambda c, r, **kw: (seen.append(r), 4)[1]), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0):
        rc = rebase.lifecycle.fresh(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert rc == 4
    assert seen[0].signal == rebase.types.RefusalSignal.EMPTY_DIFF.value


def _merged_and_deleted_remote(tmp_path) -> Path:
    """A clone whose feature branch merged and whose remote branch is gone.

    Built against real git rather than a stubbed subprocess because the bug is
    in the interaction between `fetch --prune` and the checkout that follows:
    prune drops origin/<branch>, and the checkout starts from that exact ref.
    The worktree is left on an unrelated branch so _fresh has to check out.
    """
    origin = tmp_path / "origin"
    run_checked(["git", "init", "--bare", "-b", "main", str(origin)])
    work = tmp_path / "work"
    run_checked(["git", "clone", str(origin), str(work)])

    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "base.txt").write_text("base\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "-u", "origin", "main")

    _git(work, "checkout", "-b", _LANDED_BRANCH)
    (work / "feature.txt").write_text("feature\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "feature")
    _git(work, "push", "-u", "origin", _LANDED_BRANCH)

    # The merge, as GitHub leaves it: the remote branch is deleted and the
    # worktree is sitting on something else by the time anyone rebases.
    _git(work, "checkout", "-b", "other", "main")
    _git(work, "push", "origin", "--delete", _LANDED_BRANCH)
    return work


def test_fresh_refuses_a_merged_branch_whose_remote_was_pruned(tmp_path, capsys):
    """Regression: prune deleted origin/<branch>, so the checkout failed first.

    The refusal never reached the exact input it exists for — a merged branch —
    because `git checkout -B <branch> origin/<branch>` errored out with a
    generic message and exit 1 before any signal was consulted.
    """
    work = _merged_and_deleted_remote(tmp_path)
    ctx = _landed_ctx(
        worktree_root=work, current_branch="other",
        target_dir=tmp_path / "target",
    )

    answer = gh.landed.TrackerAnswer(
        merged=gh.landed.MergedPR(number=_LANDED_PR),
    )
    with mock.patch.object(gh.landed, "merged_pr", return_value=answer):
        rc = rebase.lifecycle.fresh(
            str(work), ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    captured = capsys.readouterr()
    assert rc == 4
    assert json.loads(captured.out)["signal"] == rebase.types.RefusalSignal.PR_MERGED.value
    assert "Cannot checkout" not in captured.err
    # The prune really happened, so the old order really would have failed here.
    refs = run_checked(["git", "-C", str(work), "branch", "-r"])
    assert f"origin/{_LANDED_BRANCH}" not in refs.stdout
