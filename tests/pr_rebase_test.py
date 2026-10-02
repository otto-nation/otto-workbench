"""Tests for cli.pr_rebase: the published schema, cmd_start, main and cmd_push."""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import assert_no_worktree_exit, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402

import git.topology  # noqa: E402
import git.client  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.types  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.stash  # noqa: E402
import rebase.target  # noqa: E402
import rebase.pr_snapshot  # noqa: E402
import pr.context  # noqa: E402
import core.run_lock

from pr_rebase_support import _pushed, _lands, _TARGET, _OTHER_BASE, _OTHER_TARGET


def _tool_schema(capsys) -> dict:
    """The --tool-schema document, which parse_args prints before resolving."""
    with mock.patch("sys.argv", ["pr-rebase", "--tool-schema"]), pytest.raises(SystemExit):
        cli.pr_rebase.main()
    return json.loads(capsys.readouterr().out)


def test_landed_exit_code_is_published_as_a_reportable_outcome(capsys):
    """The MCP layer renders any code outside ok_exit_codes as a tool error."""
    assert rebase.types.REFUSAL_EXIT in _tool_schema(capsys)["ok_exit_codes"]


def test_force_is_published_in_the_input_schema(capsys):
    """The skill can only offer the override if the schema names the flag."""
    assert "force" in _tool_schema(capsys)["input_schema"]["properties"]


def test_main_threads_force_into_cmd_start():
    exit_code, _, mock_start = _run_main(0, "--force")

    assert exit_code == 0
    assert mock_start.call_args.kwargs["force"] is True


def test_main_leaves_the_preflight_armed_by_default():
    _, _, mock_start = _run_main(0)

    assert mock_start.call_args.kwargs["force"] is False


def test_main_rebases_a_master_repo_onto_origin_master():
    """A repo whose default branch is not main must not be sent to origin/main."""
    _, _, mock_start = _run_main(0, default_branch="master")

    assert mock_start.call_args.kwargs["target_ref"] == "origin/master"


def test_main_rebases_onto_the_pr_base_rather_than_the_default_branch():
    """A stacked or release-branch PR replays onto its own base.

    Rebasing it onto the trunk would replay the parent's commits too and then
    force-push the result — silently, with no error to notice.
    """
    _, _, mock_start = _run_main(0, pr_base=_OTHER_BASE, default_branch="main")

    assert mock_start.call_args.kwargs["target_ref"] == _OTHER_TARGET


def test_main_lets_onto_override_every_probe():
    _, _, mock_start = _run_main(
        0, "--onto", "upstream/trunk", pr_base=_OTHER_BASE, default_branch="master",
    )

    assert mock_start.call_args.kwargs["target_ref"] == "upstream/trunk"


def test_main_threads_one_ref_into_both_commands():
    """cmd_push records the base, so a second resolution could disagree."""
    _, mock_push, mock_start = _run_main(0, "--push", default_branch="master")

    assert mock_start.call_args.kwargs["target_ref"] == "origin/master"
    assert mock_push.call_args.kwargs["target_ref"] == "origin/master"


def test_main_resolves_the_target_ref_once():
    """Each resolution can hit the GitHub API, and both answers must agree."""
    with mock.patch.object(rebase.target, "resolve_target_ref",
                           return_value=_TARGET) as resolve:
        _run_main(0)

    resolve.assert_called_once()


def test_main_aborts_without_asking_the_network():
    """Abort is the escape hatch for a hung rebase — it cannot need a round trip."""
    with mock.patch.object(rebase.types, "recorded_target_base",
                           return_value="origin/release/1.2"), \
         mock.patch.object(rebase.target, "resolve_target_ref") as resolve, \
         mock.patch.object(cli.pr_rebase, "cmd_abort", return_value=0) as abort:
        _run_main(0, "--abort")

    resolve.assert_not_called()
    assert abort.call_args.kwargs["target_ref"] == "origin/release/1.2"


# ── cmd_start ──────────────────────────────────────────────────────────────


def test_cmd_start_skips_stash_when_rebase_in_progress():
    """Mid-rebase resume must not attempt stash (git index is locked during rebase)."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.stash, "auto_stash") as mock_stash, \
         mock.patch.object(rebase.stash, "restore"), \
         mock.patch.object(rebase.target, "resume_target_ref", return_value=_TARGET), \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0):
        result = cli.pr_rebase.cmd_start(
            "/fake", ctx, rebase.types.RunMode.FIX, target_ref=_TARGET,
        )

    assert result == 0
    mock_stash.assert_not_called()


def test_cmd_start_stashes_before_fresh_rebase():
    """Fresh rebase stashes uncommitted changes before starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.stash, "auto_stash", return_value=False) as mock_stash, \
         mock.patch.object(rebase.stash, "restore"), \
         mock.patch.object(rebase.lifecycle, "fresh", return_value=0):
        result = cli.pr_rebase.cmd_start(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 0
    mock_stash.assert_called_once()


def test_cmd_start_restores_the_stash_when_the_rebase_raises():
    """A bare sequence left the user's work on the stack with nothing said.

    An exception — or the 90-minute kill the skill itself warns about — skipped
    the unstash entirely, because it was a trailing statement rather than a
    `finally`. The run lock has always got this right; the stash had not.
    """
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.stash, "auto_stash", return_value=True), \
         mock.patch.object(rebase.stash, "restore") as restore, \
         mock.patch.object(rebase.lifecycle, "fresh", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            cli.pr_rebase.cmd_start(
                "/fake", mock.MagicMock(), rebase.types.RunMode.PUSH,
                target_ref=_TARGET,
            )

    restore.assert_called_once()


def test_cmd_start_restores_a_held_stash_after_a_resume():
    """The run that finishes the rebase is the one that owes the restore.

    An earlier run held its stash rather than popping it into a conflicted
    index; nothing would ever have popped it if only the fresh path restored.
    """
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.target, "resume_target_ref", return_value=_TARGET), \
         mock.patch.object(rebase.stash, "restore") as restore, \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0):
        cli.pr_rebase.cmd_start(
            "/fake", mock.MagicMock(), rebase.types.RunMode.FIX,
            target_ref=_TARGET,
        )

    restore.assert_called_once()


def test_cmd_start_stash_failure_aborts():
    """When stash fails on a fresh rebase, cmd_start returns 1 without starting."""
    ctx = mock.MagicMock()

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.stash, "auto_stash", return_value=None), \
         mock.patch.object(rebase.lifecycle, "fresh") as mock_fresh:
        result = cli.pr_rebase.cmd_start(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
        )

    assert result == 1
    mock_fresh.assert_not_called()


def test_cmd_start_resume_forwards_the_snapshot():
    """A resumed rebase still needs the snapshot for the open-PR notice.

    Without forwarding it, `drive_to_completion` cannot name the PR a resumed
    force-push is about to rewrite, and the round trip that fetched it is
    thrown away for nothing.
    """
    ctx = mock.MagicMock()
    snapshot = rebase.pr_snapshot.PRSnapshot(state="OPEN", number=1)

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.stash, "restore"), \
         mock.patch.object(rebase.target, "resume_target_ref", return_value=_TARGET), \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0) as drive:
        cli.pr_rebase.cmd_start(
            "/fake", ctx, rebase.types.RunMode.PUSH, target_ref=_TARGET,
            snapshot=snapshot,
        )

    assert drive.call_args.kwargs["snapshot"] is snapshot


# ── main() --push dispatch ──────────────────────────────────────────────────


def _run_main(cmd_start_rc: int, *flags: str,
              pr_base=None, default_branch="main",
              ) -> tuple[int, mock.MagicMock, mock.MagicMock]:
    """Run main() with the given flags. Returns (exit_code, cmd_push, cmd_start).

    The target-ref resolution runs for real off the two probes it consults, so
    a test can move the repo's trunk or the PR's base and watch what main()
    hands the commands.

    The lock is stubbed rather than taken. A MagicMock's `target_dir` is a
    MagicMock, and `main()` now hands it to a lock that creates the directory —
    which, left real, writes a `MagicMock/mock.target_dir/<id>/` tree into
    whatever directory the suite happens to run from. What main() does with the
    lock is pinned in run_lock_test and the pr_cli suites against real paths.
    """
    fake_ctx = mock.MagicMock()
    fake_ctx.worktree_root = Path("/fake")
    fake_ctx.require_worktree.return_value = Path("/fake")
    fake_trail = mock.MagicMock()
    fake_trail.__enter__ = mock.Mock(return_value=fake_trail)
    fake_trail.__exit__ = mock.Mock(return_value=False)

    with mock.patch.object(pr.context, "resolve", return_value=fake_ctx), \
         mock.patch.object(core.run_lock, "claim_for_process"), \
         mock.patch.object(rebase.target, "pr_base_branch", return_value=pr_base), \
         mock.patch.object(git.topology, "default_branch",
                           return_value=default_branch), \
         mock.patch.object(cli.pr_rebase, "Trail") as mock_trail_cls, \
         mock.patch.object(cli.pr_rebase, "cmd_start", return_value=cmd_start_rc) as mock_start, \
         mock.patch.object(cli.pr_rebase, "cmd_push", return_value=0) as mock_push:
        mock_trail_cls.start.return_value = fake_trail
        exit_code = cli.pr_rebase.main([*flags])
    return exit_code, mock_push, mock_start


def _run_main_with_push(cmd_start_rc: int) -> tuple[int, mock.MagicMock]:
    """Run main() with --push and return (exit_code, mock_cmd_push)."""
    exit_code, mock_push, _ = _run_main(cmd_start_rc, "--push")
    return exit_code, mock_push


def test_push_flag_calls_cmd_push_when_start_succeeds():
    """--push must call cmd_push after cmd_start returns 0 (the bug being fixed)."""
    exit_code, mock_push = _run_main_with_push(cmd_start_rc=0)

    mock_push.assert_called_once()
    assert exit_code == 0


@pytest.mark.parametrize("flags,verify", [
    ((), True),
    (("--no-verify",), False),
    (("--fix", "--no-verify"), False),
])
def test_no_verify_reaches_both_commands_that_push(flags, verify):
    """A bare run pushes from cmd_push; `--fix` pushes from inside cmd_start."""
    _, mock_push, mock_start = _run_main(0, *flags)

    assert mock_start.call_args.kwargs["verify"] is verify
    if mock_push.called:
        assert mock_push.call_args.kwargs["verify"] is verify


def test_cmd_push_hands_no_verify_to_the_landing():
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init", return_value=_push_state()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(git.client, "commits_ahead", return_value=1), \
         _lands(_pushed()) as owner:
        cli.pr_rebase.cmd_push("/fake", ctx, target_ref=_TARGET, verify=False)

    assert owner.call_args.kwargs["verify"] is False


@pytest.mark.parametrize("resuming", [False, True])
def test_cmd_start_forwards_no_verify_on_both_paths(resuming):
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=resuming), \
         mock.patch.object(rebase.stash, "auto_stash", return_value=False), \
         mock.patch.object(rebase.stash, "restore"), \
         mock.patch.object(rebase.target, "resume_target_ref", return_value=_TARGET), \
         mock.patch.object(rebase.lifecycle, "fresh", return_value=0) as fresh, \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0) as drive:
        cli.pr_rebase.cmd_start(
            "/fake", mock.MagicMock(), rebase.types.RunMode.FIX,
            target_ref=_TARGET, verify=False,
        )

    started = drive if resuming else fresh
    assert started.call_args.kwargs["verify"] is False


def test_push_flag_skips_cmd_push_on_conflicts():
    """--push must not call cmd_push when cmd_start returns non-zero (e.g. conflicts)."""
    exit_code, mock_push = _run_main_with_push(cmd_start_rc=3)

    mock_push.assert_not_called()
    assert exit_code == 3


# ── --fix --no-push ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("flags,expected", [
    (["--fix"], rebase.types.RunMode.FIX),
    (["--fix", "--no-push"], rebase.types.RunMode.FIX_ONLY),
    ([], rebase.types.RunMode.PUSH),
    (["--no-push"], rebase.types.RunMode.REBASE_ONLY),
])
def test_select_mode_keeps_fix_and_push_independent(flags, expected):
    """--fix says the AI may resolve; --no-push says nothing reaches the remote."""
    args = mock.Mock(fix="--fix" in flags, push="--no-push" not in flags)
    assert cli.pr_rebase._select_mode(args)[0] is expected


def test_fix_with_no_push_does_not_reach_the_remote():
    """`--fix --no-push` force-pushed anyway: main() branched on --fix first."""
    exit_code, mock_push, mock_start = _run_main(0, "--fix", "--no-push")

    mock_push.assert_not_called()
    assert mock_start.call_args[0][2] is rebase.types.RunMode.FIX_ONLY
    assert exit_code == 0


# ── worktree_root guard ─────────────────────────────────────────────────────


def test_main_without_a_worktree_exits_with_guidance(capsys):
    """The old code coerced None to "None" and handed it to git -C."""
    ctx = make_ctx(branch="isaac/feat/x", worktree_root=None, head_sha="abc1234")
    with mock.patch("sys.argv", ["pr-rebase"]), \
         mock.patch.object(pr.context, "resolve", return_value=ctx), \
         mock.patch.object(cli.pr_rebase, "Trail") as mock_trail_cls:
        assert_no_worktree_exit(capsys, "isaac/feat/x", cli.pr_rebase.main)
    mock_trail_cls.start.assert_not_called()


def _push_state(lease_expect="abc123"):
    """A recorded rebase, as cmd_push reads it back out of state.json."""
    state = mock.MagicMock()
    state.rebase.updated_at = "t"
    state.rebase.lease_expect = lease_expect
    state.rebase.commits_replayed = 1
    state.rebase.conflicts_resolved = 0
    state.rebase.files_resolved = []
    state.rebase.files_stale = []
    return state


def test_the_default_invocation_names_the_pr_before_pushing(capsys):
    """A bare `pr rebase` is RunMode.PUSH, which pushes from cmd_push.

    The notice first lived only in rebase_success, whose `lands_here` is false
    for exactly this mode — so the single most common way to run the command
    was the one way that force-pushed a reviewed branch silently.
    """
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    snapshot = rebase.pr_snapshot.PRSnapshot(
        state="OPEN", number=1358, url="https://gh/1358", is_draft=False,
    )

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init", return_value=_push_state()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(git.client, "commits_ahead", return_value=1), \
         _lands(_pushed()):
        rc = cli.pr_rebase.cmd_push(
            "/fake", ctx, target_ref=_TARGET, snapshot=snapshot,
        )

    assert rc == 0
    err = capsys.readouterr().err
    assert "https://gh/1358" in err
    assert "ready for review" in err


def test_the_default_invocation_pushes_under_the_recorded_lease():
    """cmd_push must use the tip the rebase recorded, not one rebuilt here."""
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init",
                           return_value=_push_state(lease_expect="deadbee")), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(git.client, "commits_ahead", return_value=1), \
         _lands(_pushed()) as owner:
        cli.pr_rebase.cmd_push("/fake", ctx, target_ref=_TARGET)

    assert owner.call_args.kwargs["args"] == (
        "--force-with-lease=refs/heads/isaac/feat/x:deadbee",
    )


def test_a_lease_recorded_before_the_field_existed_is_refused(capsys):
    """An empty expect claims "the remote has no such ref" — check it.

    A state file written before `lease_expect` shipped deserializes the field
    to "", which is indistinguishable from a branch legitimately not yet
    pushed. Pushing on that claim fails with git's `stale info` and no clue
    why, so the claim is tested against the remote first.
    """
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.inspect, "ref_exists", return_value=True), \
         mock.patch.object(rebase.types, "load_or_init",
                           return_value=_push_state(lease_expect="")), \
         _lands(_pushed()) as owner:
        rc = cli.pr_rebase.cmd_push("/fake", ctx, target_ref=_TARGET)

    assert rc == 1
    owner.assert_not_called()
    assert "origin already has this branch" in capsys.readouterr().err


# ── --push-only ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("extra", [[], ["--no-verify"]])
def test_push_only_pushes_with_the_lease_and_never_rebases(monkeypatch, extra):
    pushed = {}
    monkeypatch.setattr(cli.pr_rebase, "cmd_start",
                        lambda *a, **k: pytest.fail("--push-only must not rebase"))

    def fake_push(cwd, ctx, *, target_ref, verify=True, snapshot=None, trail=None):
        pushed["cwd"], pushed["ref"], pushed["verify"] = cwd, target_ref, verify
        return 0

    monkeypatch.setattr(cli.pr_rebase, "cmd_push", fake_push)
    monkeypatch.setattr(cli.pr_rebase, "_resolve",
                        lambda args: cli.pr_rebase.RebaseTarget(make_ctx(), "/wt", "origin/main"))
    # main() still resolves context, claims the lock, and opens a trail before
    # _run; stub those so this case does not need a real checkout.
    fake_ctx = mock.MagicMock()
    fake_ctx.require_worktree.return_value = Path("/fake")
    fake_trail = mock.MagicMock()
    monkeypatch.setattr(pr.context, "resolve", lambda **k: fake_ctx)
    monkeypatch.setattr(core.run_lock, "claim_for_process", lambda *a, **k: None)
    monkeypatch.setattr(cli.pr_rebase.Trail, "start", lambda **k: fake_trail)
    assert cli.pr_rebase.main(["--push-only", *extra]) == 0
    assert pushed == {"cwd": "/wt", "ref": "origin/main",
                      "verify": "--no-verify" not in extra}


@pytest.mark.parametrize("other", ["--fix", "--abort", "--no-push"])
def test_push_only_refuses_other_modes(other):
    with pytest.raises(SystemExit) as exc:
        cli.pr_rebase.main(["--push-only", other])
    assert exc.value.code == 2
