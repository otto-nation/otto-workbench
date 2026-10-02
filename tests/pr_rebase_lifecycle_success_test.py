"""Tests for rebase.lifecycle.rebase_success: its report, the push and the lease."""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import git.land  # noqa: E402
import core.report  # noqa: E402
import rebase.types  # noqa: E402
import rebase.land  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.pr_snapshot  # noqa: E402
from git.land import CommitStatus  # noqa: E402

from pr_rebase_support import _LANDED_SHA, _LEASE, _RESUME, _pushed, _lands, _TARGET


def _held() -> git.land.LandResult:
    """The owner's answer with the publishing gate shut — what `--no-push` gets."""
    return git.land.LandResult(CommitStatus.PUSH_HELD, sha=_LANDED_SHA, resume=_RESUME)


def test_rebase_success_emits_stale_files():
    """Stale files reach both the emitted JSON and the persisted state."""
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally(
        files=["pnpm-lock.yaml"], stale=["pnpm-lock.yaml"], commits=1,
    )
    saved = []

    with mock.patch.object(git.client, "commits_ahead", return_value=1), \
         mock.patch.object(
             rebase.types.RebaseOutcome, "save",
             lambda self, c: saved.append(self),
         ), \
         mock.patch.object(core.report, "emit_json") as mock_emit:
        rc = rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.PUSH, tally, target_ref=_TARGET,
        )

    assert rc == 0
    assert saved[0].files_stale == ["pnpm-lock.yaml"]
    assert mock_emit.call_args[0][0]["files_stale"] == ["pnpm-lock.yaml"]


def test_rebase_success_emits_the_files_it_resolved():
    """The skill parses these keys, so they survive into the JSON.

    Every conflict a run meets is one it resolved: rerere is held off, so there
    is no second class of file that arrived from a recorded resolution.
    """
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally(files=["a.py"], commits=1)
    saved = []

    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(
             rebase.types.RebaseOutcome, "save",
             lambda self, c: saved.append(self),
         ), \
         mock.patch.object(core.report, "emit_json") as mock_emit:
        rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.PUSH, tally, target_ref=_TARGET,
        )

    report = mock_emit.call_args[0][0]
    assert report["files_resolved"] == ["a.py"]
    assert report["conflicts_resolved"] == 1
    assert "files_replayed" not in report
    assert saved[0].files_resolved == ["a.py"]


# passes-at-base: the repair candidate set this change narrowed but did not alter
def test_resolved_files_are_candidates_for_the_prepush_repair():
    """A resolved file can be what trips the pre-push hook.

    `resolved_files` is the candidate set `fix_push_failures` matches a failing
    hook's output against, and a file absent from it is dropped from `targets`
    entirely — the no-match fallback re-adds the candidates, not the omission.
    """
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally(
        files=["a.py", "pnpm-lock.yaml"], commits=1,
    )

    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch.object(
             rebase.land, "land_rebased", return_value=_pushed(),
         ) as mock_land:
        rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.FIX, tally, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert mock_land.call_args.kwargs["resolved_files"] == [
        "a.py", "pnpm-lock.yaml",
    ]


# passes-at-base: the dedup this change kept after dropping the replayed list
def test_a_file_resolved_in_two_commits_is_one_repair_candidate():
    """Deduplicated, as `fix_push_failures` expects.

    One file conflicting in several replayed commits is absorbed into the tally
    once per commit, and the repair wants the set rather than the occurrences.
    """
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally()
    tally.absorb(rebase.types.Resolution(files=["go.sum"]))
    tally.commits += 1
    tally.absorb(rebase.types.Resolution(files=["go.sum"]))
    tally.commits += 1

    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch.object(
             rebase.land, "land_rebased", return_value=_pushed(),
         ) as mock_land:
        rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.FIX, tally, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert mock_land.call_args.kwargs["resolved_files"] == ["go.sum"]


def test_a_run_with_nothing_to_repair_passes_no_candidates():
    """None, not an empty list — what `land_rebased` reads as 'skip the fix'."""
    ctx = mock.MagicMock()

    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch.object(
             rebase.land, "land_rebased", return_value=_pushed(),
         ) as mock_land:
        rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.FIX,
            rebase.types.ResolutionTally(), target_ref=_TARGET,
            lease=_LEASE,
        )

    assert mock_land.call_args.kwargs["resolved_files"] is None


def test_rebase_success_counts_commits_before_push():
    """commits_replayed excludes commits the push recovery creates.

    The landing can add regeneration and check-fix commits; counting after it
    reported them as replayed from the branch.
    """
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally(files=["a.py"], commits=1)
    ahead = iter([2, 3])

    with mock.patch.object(git.client, "commits_ahead", lambda _, **kw: next(ahead)), \
         _lands(_pushed()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json") as mock_emit:
        rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.FIX, tally, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert mock_emit.call_args[0][0]["commits_replayed"] == 2


def test_rebase_success_conflicts_resolved_counts_files():
    """conflicts_resolved is a file count — rebase_status renders it as 'file(s)'."""
    ctx = mock.MagicMock()
    tally = rebase.types.ResolutionTally(files=["a.py", "b.py", "c.py"], commits=2)

    with mock.patch.object(git.client, "commits_ahead", return_value=5), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json") as mock_emit:
        rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.PUSH, tally, target_ref=_TARGET,
        )

    assert mock_emit.call_args[0][0]["conflicts_resolved"] == 3


def test_rebase_success_in_fix_only_prints_the_push_command(capsys):
    """The force-push is handed to the user, not issued — repository policy.

    The landing still happens: `--no-push` shuts the publishing gate rather than
    skipping the call, so the command the user is handed is the one the owner
    drafted against the real worktree rather than a string composed here.
    """
    ctx = mock.MagicMock()
    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         _lands(_held()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json") as mock_emit:
        rc = rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.FIX_ONLY, target_ref=_TARGET,
            lease=_LEASE,
        )

    assert rc == 0
    # force_pushed is None, which the emitter drops — "not pushed" rather than
    # "push failed", the same shape --no-push already reports.
    assert "force_pushed" not in mock_emit.call_args[0][0]
    assert _RESUME in capsys.readouterr().err


@pytest.mark.parametrize("mode,hinted", [
    (rebase.types.RunMode.REBASE_ONLY, True),
    (rebase.types.RunMode.FIX_ONLY, True),
    (rebase.types.RunMode.PUSH, False),
    (rebase.types.RunMode.FIX, False),
])
def test_manual_push_hint_only_when_the_run_never_pushes(mode, hinted, capsys):
    """The hint keyed on "pushes from here", so PUSH printed it then pushed.

    RunMode.PUSH pushes from main() via cmd_push, which is invisible to the
    rebase-completion path — the condition has to be whether the run reaches
    the remote at all.
    """
    ctx = mock.MagicMock()
    landed = _held() if hinted else _pushed()

    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         _lands(landed), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"):
        rc = rebase.lifecycle.rebase_success(
            "/fake", ctx, mode, target_ref=_TARGET, lease=_LEASE,
        )

    assert rc == 0
    err = capsys.readouterr().err
    assert (_RESUME in err) is hinted
    assert ("--no-push" in err) is hinted
    assert "Rebase complete" in err


# ── Naming the PR a force-push is about to rewrite ──────────────────────────
#
# A branch with an open PR is shared. The push is legitimate — review findings,
# CI fixes, a rebase a reviewer asked for — so this is a notice, never a gate.


def _rebase_success_with(snapshot, mode=rebase.types.RunMode.FIX):
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         _lands(_pushed()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"):
        return rebase.lifecycle.rebase_success(
            "/fake", ctx, mode, target_ref=_TARGET, lease=_LEASE,
            snapshot=snapshot,
        )


def test_a_ready_pr_is_named_before_the_force_push(capsys):
    rc = _rebase_success_with(rebase.pr_snapshot.PRSnapshot(
        state="OPEN", number=1358, url="https://gh/1358", is_draft=False,
    ))

    err = capsys.readouterr().err
    assert rc == 0
    assert "https://gh/1358" in err
    assert "ready for review" in err
    # The push still happens: this is a notice, not a gate.
    assert "force-pushing" in err


def test_a_draft_is_pushed_to_without_ceremony(capsys):
    """Force-pushing a draft is the normal way to work on one."""
    _rebase_success_with(rebase.pr_snapshot.PRSnapshot(
        state="OPEN", number=1358, url="https://gh/1358", is_draft=True,
    ))
    assert "ready for review" not in capsys.readouterr().err


def test_nothing_is_claimed_when_github_could_not_be_asked(capsys):
    """An unanswered read must not be reported as "no PR"."""
    _rebase_success_with(rebase.pr_snapshot.PRSnapshot())
    assert "ready for review" not in capsys.readouterr().err


def test_a_held_run_says_nothing_about_a_push_it_is_not_making(capsys):
    """--no-push reaches no remote, so there is nobody to warn."""
    _rebase_success_with(
        rebase.pr_snapshot.PRSnapshot(state="OPEN", number=1358, is_draft=False),
        mode=rebase.types.RunMode.FIX_ONLY,
    )
    assert "ready for review" not in capsys.readouterr().err


# ── When no lease can be named ───────────────────────────────────────────────


def test_a_push_with_no_nameable_lease_is_refused(capsys):
    """The remote has the branch and this run never read where it was.

    Both fallbacks are wrong: a bare lease is satisfied by the run's own fetch
    (the clobber), and an empty expect is rejected against a ref that exists.
    Stopping leaves the replay in the worktree, which is recoverable.
    """
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         _lands(_pushed()) as owner, \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"):
        rc = rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.FIX, target_ref=_TARGET,
            lease=None,
        )

    assert rc == 1
    owner.assert_not_called()
    assert "cannot tell what the remote was at" in capsys.readouterr().err


def test_a_run_that_defers_its_push_does_not_need_a_lease(capsys):
    """PUSH pushes from cmd_push, not here, so an unnameable lease stops nothing.

    This is not the --no-push case: RunMode.PUSH still reaches the remote
    (``RunMode.PUSH.reaches_remote`` is ``True``), just seconds later and from
    a different function. REBASE_ONLY/FIX_ONLY are the true --no-push modes,
    and both land here — see
    ``test_a_no_push_run_with_no_nameable_lease_is_refused`` for what they
    require.
    """
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"):
        rc = rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
            lease=None,
        )

    assert rc == 0


def test_a_no_push_run_with_no_nameable_lease_is_refused(capsys):
    """REBASE_ONLY lands in rebase_success and still needs a nameable lease.

    Unlike RunMode.PUSH, --no-push modes never reach a separate cmd_push —
    they land here, and the held push still resolves ``lease.args`` to build
    the printed resume command. Refused the same way FIX is.
    """
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    with mock.patch.object(git.client, "commits_ahead", return_value=2), \
         _lands(_pushed()) as owner, \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"):
        rc = rebase.lifecycle.rebase_success(
            "/fake", ctx, rebase.types.RunMode.REBASE_ONLY, target_ref=_TARGET,
            lease=None,
        )

    assert rc == 1
    owner.assert_not_called()
    assert "cannot tell what the remote was at" in capsys.readouterr().err


@pytest.mark.parametrize("verify", [True, False])
def test_rebase_success_hands_the_hook_decision_to_the_landing(verify):
    """`--fix --no-verify` pushes from here, not from cmd_push."""
    with mock.patch.object(git.client, "commits_ahead", return_value=1), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(core.report, "emit_json"), \
         mock.patch.object(
             rebase.land, "land_rebased", return_value=_pushed(),
         ) as mock_land:
        rebase.lifecycle.rebase_success(
            "/fake", mock.MagicMock(), rebase.types.RunMode.FIX,
            rebase.types.ResolutionTally(), target_ref=_TARGET,
            lease=_LEASE, verify=verify,
        )

    assert mock_land.call_args.kwargs["verify"] is verify
