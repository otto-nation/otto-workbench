"""Tests for `pr rebase --push-only --expect`: a lease the caller names itself."""

import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import commit_all, git_out, init_repo, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import core.publishing  # noqa: E402
import core.run_lock  # noqa: E402
import gh.owner_hooks  # noqa: E402
import git.client  # noqa: E402
import pr.context  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.types  # noqa: E402

from pr_rebase_support import _TARGET, _lands, _pushed


def _no_record():
    state = mock.MagicMock()
    state.rebase.updated_at = ""
    state.rebase.lease_expect = ""
    state.rebase.commits_replayed = 0
    state.rebase.conflicts_resolved = 0
    state.rebase.files_resolved = []
    state.rebase.files_stale = []
    state.rebase.fork_point = ""
    state.rebase.squashed_commits = 0
    return state


def test_expect_pushes_a_hand_rebase_with_no_recorded_rebase():
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init", return_value=_no_record()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(git.client, "commits_ahead", return_value=2), \
         _lands(_pushed()) as owner:
        assert rebase.commands.cmd_push("/fake", ctx, target_ref=_TARGET, expect="deadbee") == 0
    assert owner.call_args.kwargs["args"] == (
        "--force-with-lease=refs/heads/isaac/feat/x:deadbee",)


# passes-at-base: a missing record is already refused; --expect must not change that
def test_without_expect_a_missing_record_is_still_refused():
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init", return_value=_no_record()), \
         _lands(_pushed()) as owner:
        assert rebase.commands.cmd_push("/fake", ctx, target_ref=_TARGET) == 1
    owner.assert_not_called()


def test_expect_reaches_cmd_push_from_the_command_line(monkeypatch):
    seen = {}
    monkeypatch.setattr(rebase.commands, "cmd_push", lambda cwd, ctx, **k: seen.update(k) or 0)
    monkeypatch.setattr(rebase.commands, "_resolve",
                        lambda args: rebase.commands.RebaseTarget(make_ctx(), "/wt", "origin/main"))
    fake_ctx = mock.MagicMock()
    fake_ctx.require_worktree.return_value = Path("/fake")
    monkeypatch.setattr(pr.context, "resolve", lambda **k: fake_ctx)
    monkeypatch.setattr(core.run_lock, "claim_for_process", lambda *a, **k: None)
    monkeypatch.setattr(cli.pr_rebase.Trail, "start", lambda **k: mock.MagicMock())
    assert cli.pr_rebase.main(["--push-only", "--expect", "deadbee"]) == 0
    assert seen["expect"] == "deadbee"


def test_expect_without_push_only_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.pr_rebase.main(["--expect", "deadbee"])
    assert exc.value.code == 2
    assert "--expect only applies with --push-only" in capsys.readouterr().err


def test_the_rebase_force_push_goes_through_the_one_push_path():
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    seen = {}

    def push_head(cwd, branch, **kw):
        seen.update(kw, cwd=cwd, branch=branch)
        return True

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init", return_value=_no_record()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: None), \
         mock.patch.object(git.client, "commits_ahead", return_value=2), \
         mock.patch.object(rebase.commands, "push_head", push_head):
        assert rebase.commands.cmd_push("/fake", ctx, target_ref=_TARGET, expect="deadbee") == 0
    assert (seen["cwd"], seen["branch"], seen["expect"]) == ("/fake", "isaac/feat/x", "deadbee")


def test_push_only_preserves_the_recorded_replay_shape():
    """`--push-only` must not wipe the squash and fork-point the rebase recorded."""
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    state = _no_record()
    state.rebase.updated_at = "t"
    state.rebase.lease_expect = "deadbee"
    state.rebase.fork_point = "abc1234"
    state.rebase.squashed_commits = 4
    saved = []

    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.types, "load_or_init", return_value=state), \
         mock.patch.object(
             rebase.types.RebaseOutcome, "save",
             lambda self, c: saved.append(self),
         ), \
         mock.patch.object(git.client, "commits_ahead", return_value=1), \
         _lands(_pushed()):
        assert rebase.commands.cmd_push("/fake", ctx, target_ref=_TARGET) == 0

    assert saved[0].fork_point == "abc1234"
    assert saved[0].squashed_commits == 4


def _published_conflict(tmp_path):
    """A published `feat` that conflicts with `origin/main` on `f`."""
    origin = tmp_path / "origin.git"
    git_out(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    hub = init_repo(tmp_path / "hub")
    git_out(hub, "remote", "add", "origin", str(origin))
    (hub / "f").write_text("base\n")
    commit_all(hub, "base")
    git_out(hub, "push", "-q", "-u", "origin", "main")
    git_out(hub, "checkout", "-q", "-b", "feat")
    (hub / "f").write_text("feat\n")
    commit_all(hub, "feat")
    git_out(hub, "push", "-q", "-u", "origin", "feat")
    planned = git_out(hub, "rev-parse", "refs/remotes/origin/feat").strip()
    git_out(hub, "checkout", "-q", "main")
    (hub / "f").write_text("main\n")
    commit_all(hub, "main2")
    git_out(hub, "push", "-q", "origin", "main")
    git_out(hub, "checkout", "-q", "feat")
    return origin, hub, planned


def _stop_on_conflict(hub, tmp_path):
    ctx = make_ctx(
        worktree_root=hub, branch="feat", current_branch="feat",
        target_dir=tmp_path / "state",
    )
    rc = rebase.lifecycle.fresh(
        str(hub), ctx, rebase.types.RunMode.REBASE_ONLY,
        force=True, target_ref="origin/main", pause_on_conflict=True,
    )
    return ctx, rc


def _hand_continue(hub):
    (hub / "f").write_text("resolved\n")
    git_out(hub, "add", "f")
    git_out(hub, "-c", "core.editor=true", "rebase", "--continue")


def _colleague_moves_feat(tmp_path, origin):
    other = init_repo(tmp_path / "colleague")
    git_out(other, "remote", "add", "origin", str(origin))
    git_out(other, "fetch", "-q", "origin", "feat")
    git_out(other, "checkout", "-q", "-b", "feat", "origin/feat")
    (other / "colleague.txt").write_text("c\n")
    commit_all(other, "COLLEAGUE")
    git_out(other, "push", "-q", "origin", "feat")


def _push_only(hub, ctx):
    with core.publishing.run(post=True), \
         mock.patch.object(gh.owner_hooks, "refuse_outgoing", return_value=None):
        return rebase.commands.cmd_push(
            str(hub), ctx, target_ref="origin/main", verify=False,
        )


def test_a_conflict_stop_records_the_origin_tip_the_rebase_planned_from(tmp_path):
    _origin, hub, planned = _published_conflict(tmp_path)
    ctx, rc = _stop_on_conflict(hub, tmp_path)
    assert rc == rebase.types.CONFLICTS_EXIT
    assert rebase.types.load_or_init(ctx).rebase.lease_expect == planned


def test_push_only_after_a_hand_continue_uses_the_lease_saved_at_the_stop(tmp_path):
    origin, hub, planned = _published_conflict(tmp_path)
    ctx, rc = _stop_on_conflict(hub, tmp_path)
    assert rc == rebase.types.CONFLICTS_EXIT
    _hand_continue(hub)
    assert not rebase.inspect.rebase_in_progress(str(hub))
    assert rebase.types.load_or_init(ctx).rebase.lease_expect == planned

    assert _push_only(hub, ctx) == 0
    assert git_out(origin, "rev-parse", "feat").strip() == git_out(
        hub, "rev-parse", "HEAD",
    ).strip()


def test_push_only_after_a_hand_continue_refuses_when_origin_moved(tmp_path):
    origin, hub, planned = _published_conflict(tmp_path)
    ctx, rc = _stop_on_conflict(hub, tmp_path)
    assert rc == rebase.types.CONFLICTS_EXIT
    _hand_continue(hub)
    _colleague_moves_feat(tmp_path, origin)
    assert rebase.types.load_or_init(ctx).rebase.lease_expect == planned
    assert _push_only(hub, ctx) == 1
    assert "COLLEAGUE" in git_out(origin, "log", "--oneline", "feat")
