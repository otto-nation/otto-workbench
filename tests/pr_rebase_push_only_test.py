"""Tests for `pr rebase --push-only --expect`: a lease the caller names itself."""

import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import core.run_lock  # noqa: E402
import git.client  # noqa: E402
import pr.context  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.inspect  # noqa: E402
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
