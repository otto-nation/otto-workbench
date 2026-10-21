"""Bringing a worktree in line with its remote, and the guards that refuse to."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import git.topology  # noqa: E402
import pr.context  # noqa: E402
import pr.sync  # noqa: E402
from pr.sync import fetch_and_reset, update_to_remote  # noqa: E402

from core.proc import CmdResult  # noqa: E402

from conftest import make_ctx  # noqa: E402


@pytest.fixture(autouse=True)
def fetches(monkeypatch):
    """Every fetch `pr.sync` asks `git.client` for, answered without a network.

    Fetches go through `git.client`, not the `subprocess.run` these tests queue
    answers on. Each call is recorded as `(cwd, args)`; `answers` holds the
    results to give, in order — a `CmdResult`, or an exception to raise — and
    an empty queue answers success. Other git calls run as they would.
    """
    real = pr.sync.git.client.run
    record = MagicMock(calls=[], answers=[])

    def run(*args, cwd=None, **kwargs):
        if not args or args[0] != "fetch":
            return real(*args, cwd=cwd, **kwargs)
        record.calls.append((str(cwd), args))
        answer = record.answers.pop(0) if record.answers else CmdResult()
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(pr.sync.git.client, "run", run)
    return record


# ── fetch_and_reset ────────────────────────────────────────────────────────


def _safe_reset_runs():
    """subprocess.run results for a worktree that is safe to hard-reset.

    Patching `pr.sync.subprocess.run` also serves `git.topology.current_branch_quiet`'s
    own `rev-parse --abbrev-ref` call below (the first result in this list) — both
    modules import the same `subprocess` module object, so a patch on its `run`
    attribute reaches every caller regardless of which module name it was applied
    through. That is why these tests do not also mock `current_branch_quiet` directly.
    """
    return [
        MagicMock(returncode=0, stdout="feat/x\n"),     # rev-parse --abbrev-ref
        MagicMock(returncode=0, stdout=""),             # status --porcelain (clean)
        MagicMock(returncode=0, stdout="0\n"),          # rev-list (0 unpushed)
        MagicMock(returncode=0),                        # reset --hard
    ]


@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_issues_fetch_then_reset_in_call_order(mock_run, fetches):
    mock_run.side_effect = _safe_reset_runs()
    fetch_and_reset("/wt", "feat/x")
    assert fetches.calls == [("/wt", ("fetch", "origin", "feat/x"))]
    assert mock_run.call_count == 4
    reset_call = mock_run.call_args_list[3].args[0]
    assert "reset" in reset_call
    assert "--hard" in reset_call
    assert "origin/feat/x" in reset_call


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_regression_skips_wrong_branch(mock_run, mock_log):
    """Regression: resetting main/ while a feature branch sits in it ate two commits."""
    runs = _safe_reset_runs()
    runs[0] = MagicMock(returncode=0, stdout="feat/other\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "main")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "not main" in mock_log.warn.call_args.args[0]


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_skips_on_uncommitted_changes(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[1] = MagicMock(returncode=0, stdout=" M file.py\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_skips_on_unpushed_commits(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[2] = MagicMock(returncode=0, stdout="2\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "2 unpushed" in mock_log.warn.call_args.args[0]


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_skips_on_detached_head(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[0] = MagicMock(returncode=0, stdout="HEAD\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "detached HEAD" in mock_log.warn.call_args.args[0]


@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_reads_status_without_a_bound(mock_run):
    """A cold `status` on a large tree outlasts any flat bound — and a timeout
    here reads as dirty, refusing a reset the tree was safe for."""
    mock_run.side_effect = _safe_reset_runs()
    fetch_and_reset("/wt", "feat/x")
    status_call = mock_run.call_args_list[1]
    assert "status" in status_call.args[0]
    assert status_call.kwargs["timeout"] is None


@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_leaves_a_vanished_worktree_alone(mock_run, fetches):
    """`git.client` raises for a missing cwd; that is a failed fetch, not a crash."""
    fetches.answers.append(FileNotFoundError(2, "No such file or directory", "/wt"))
    fetch_and_reset("/wt", "feat/x")
    mock_run.assert_not_called()


@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_does_not_reset_after_a_failed_fetch(mock_run, fetches):
    fetches.answers.append(CmdResult(1, "", "fatal: unable to access origin"))
    fetch_and_reset("/wt", "feat/x")
    mock_run.assert_not_called()


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_blocks_when_the_status_read_failed(mock_run, mock_log):
    """Regression: a `status` that failed is not a worktree that came back clean.

    Read as clean, the guard finds no blocker and the hard reset below it
    destroys whatever was uncommitted.
    """
    runs = _safe_reset_runs()
    runs[1] = MagicMock(returncode=128, stdout="",
                        stderr="fatal: .git/index: index file smaller than expected")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not be read" in mock_log.warn.call_args.args[0]


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_blocks_when_the_status_read_timed_out(mock_run, mock_log):
    """A timeout raises rather than returning, and must not escape the guard.

    Left uncaught it aborts the command from inside a safety check; folded into
    a non-zero result it blocks the reset, which is the same answer every other
    unreadable state gets.
    """
    runs = _safe_reset_runs()
    runs[1] = subprocess.TimeoutExpired(cmd=["git", "status", "--porcelain"], timeout=1)
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not be read" in mock_log.warn.call_args.args[0]


@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_fetch_and_reset_blocks_when_unpushed_commits_cannot_be_counted(mock_run, mock_log):
    """The other half of the same guard: a rev-list that never ran counted nothing."""
    runs = _safe_reset_runs()
    runs[2] = MagicMock(returncode=128, stdout="",
                        stderr="fatal: bad revision 'origin/feat/x..HEAD'")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not be counted" in mock_log.warn.call_args.args[0]


@pytest.fixture(autouse=True)
def _pin_default_branch():
    """Resolve the trunk without spending a subprocess call.

    Autouse and file-scoped: every test in this file drives `pr.sync` by
    queueing `subprocess.run` results in order, and
    `patch("pr.sync.subprocess.run")` patches the real module rather than a
    local alias — so any git the resolver runs of its own would eat an entry
    meant for a later read and slide every assertion after it by one. Applied even
    to tests earlier in the file that never call `default_branch` at all
    (`fetch_and_reset` and friends): the patch is a no-op for those, but
    narrowing the fixture to only the tests that need it would make it easy to
    miss when the next test added here does.
    """
    with patch("git.topology.default_branch", return_value="main"):
        yield


# ── update_to_remote ───────────────────────────────────────────────────────


def _make_ctx(**overrides):
    """A context on the branch these tests' current_branch_quiet mock returns."""
    return make_ctx(**{"branch": "feat/x", "pr_number": 1, "head_sha": "aaa",
                       **overrides})


def test_update_to_remote_noop_without_worktree():
    ctx = _make_ctx(worktree_root=None)
    assert update_to_remote(ctx) is ctx


def test_update_to_remote_noop_without_branch():
    ctx = _make_ctx(branch="", pr_number=None)
    assert update_to_remote(ctx) is ctx


@patch("core.log")
@patch("git.topology.current_branch_quiet", return_value="other-branch")
def test_update_to_remote_skips_on_branch_mismatch(mock_branch, mock_log):
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    mock_log.info.assert_called_once()
    assert "other-branch" in mock_log.info.call_args.args[0]


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_skips_on_uncommitted_changes(mock_run, mock_log, _mock_branch):
    mock_run.return_value = MagicMock(returncode=0, stdout="M dirty.py\n")
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    mock_log.warn.assert_called_once()
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("core.log")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_skips_when_the_status_read_failed(mock_run, mock_log, _mock_branch):
    """The same guard from the other entry point — no fetch, and no reset."""
    mock_run.return_value = MagicMock(
        returncode=128, stdout="", stderr="fatal: .git/index: index file corrupt",
    )
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    assert mock_run.call_count == 1
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("core.log")
@patch("pr.context._head_sha", return_value="local111")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_skips_when_unpushed_commits_cannot_be_counted(
    mock_run, mock_sha, mock_log, _mock_branch,
):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),             # status --porcelain (clean)
        MagicMock(returncode=0, stdout="remote222\n"),   # rev-parse origin/branch
        MagicMock(returncode=128, stdout="", stderr="fatal: bad revision"),
    ]
    ctx = _make_ctx(head_sha="local111")
    assert update_to_remote(ctx) is ctx
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not count" in mock_log.warn.call_args.args[0].lower()


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_skips_on_fetch_failure(mock_run, _mock_branch, fetches):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),       # status --porcelain (clean)
    ]
    fetches.answers.extend([CmdResult(1), CmdResult(1)])
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    assert len(fetches.calls) == 2


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("pr.context._head_sha", return_value="aaa111")
@patch("pr.sync.subprocess.run")
def test_a_bad_guess_at_the_trunk_does_not_sink_the_branchs_own_fetch(
        mock_run, mock_sha, _mock_branch, fetches):
    """A combined fetch failing (e.g. a guessed trunk name absent on the remote) must retry the branch alone rather than abandoning the update."""
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0, stdout="aaa111\n"),     # rev-parse origin/branch
    ]
    fetches.answers.append(CmdResult(1, "", "fatal: couldn't find remote ref main"))
    ctx = _make_ctx(head_sha="aaa111")
    assert update_to_remote(ctx) is ctx
    assert fetches.calls == [
        ("/wt", ("fetch", "origin", "feat/x", "main")),
        ("/wt", ("fetch", "origin", "feat/x")),
    ]


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("pr.context._head_sha", return_value="aaa111")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_skips_when_already_current(mock_run, mock_sha, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),           # status --porcelain (clean)
        MagicMock(returncode=0, stdout="aaa111\n"),    # rev-parse origin/branch
    ]
    ctx = _make_ctx(head_sha="aaa111")
    assert update_to_remote(ctx) is ctx


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("core.log")
@patch("pr.context._head_sha", return_value="local111")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_skips_on_unpushed_commits(mock_run, mock_sha, mock_log, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0, stdout="remote222\n"),  # rev-parse origin/branch
        MagicMock(returncode=0, stdout="2\n"),          # rev-list (2 unpushed)
    ]
    ctx = _make_ctx(head_sha="local111")
    assert update_to_remote(ctx) is ctx
    mock_log.warn.assert_called_once()
    assert "unpushed" in mock_log.warn.call_args.args[0]


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("core.log")
@patch("pr.context._head_sha", return_value="old111")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_resets_when_safe(mock_run, mock_sha, mock_log, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0, stdout="new222\n"),     # rev-parse origin/branch
        MagicMock(returncode=0, stdout="0\n"),          # rev-list (0 unpushed)
        MagicMock(returncode=0),                        # reset --hard
    ]
    ctx = _make_ctx(head_sha="old111")
    result = update_to_remote(ctx)
    assert result.head_sha == "new222"
    assert result.branch == "feat/x"
    reset_call = mock_run.call_args_list[3].args[0]
    assert "reset" in reset_call
    assert "--hard" in reset_call


def test_update_to_remote_preserves_the_target_dir(monkeypatch, tmp_path):
    """dataclasses.replace, so a new field cannot be dropped by hand-retyping."""
    ctx = pr.context.ResolvedContext(
        repo="acme/widget", branch="feat/a", pr_number=1,
        worktree_root=tmp_path, head_sha="old", current_branch="feat/a",
        target_dir=tmp_path / "target",
    )
    monkeypatch.setattr(git.topology, "current_branch_quiet", lambda cwd=None: "feat/a")
    monkeypatch.setattr(pr.sync, "_worktree_is_dirty", lambda cwd: False)
    monkeypatch.setattr(pr.sync, "_unpushed_count", lambda cwd, branch: 0)
    monkeypatch.setattr(pr.context, "_head_sha", lambda cwd=None: "old")

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        out = "new-sha" if "rev-parse" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(pr.sync.subprocess, "run", fake_run)
    updated = pr.sync.update_to_remote(ctx)

    assert updated.head_sha == "new-sha"
    assert updated.target_dir == ctx.target_dir


def _reset_run_sequence(reset_result):
    """The subprocess results update_to_remote consumes before reset --hard."""
    return [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0, stdout="new222\n"),     # rev-parse origin/branch
        MagicMock(returncode=0, stdout="0\n"),          # rev-list (0 unpushed)
        reset_result,
    ]


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("pr.context._head_sha", return_value="old111")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_quotes_why_the_reset_failed(
        mock_run, _mock_sha, _mock_branch, capsys):
    """Regression: git's own reason for refusing the reset was thrown away."""
    mock_run.side_effect = _reset_run_sequence(MagicMock(
        returncode=1, stdout="",
        stderr="error: Entry 'docs/a.md' not uptodate. Cannot merge.\n"))

    ctx = _make_ctx(head_sha="old111")
    assert update_to_remote(ctx) is ctx
    err = capsys.readouterr().err
    assert "Entry 'docs/a.md' not uptodate. Cannot merge." in err
    assert "keeping the existing worktree state" in err


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("pr.context._head_sha", return_value="old111")
@patch("pr.sync.subprocess.run")
def test_update_to_remote_degrades_when_reset_says_nothing(
        mock_run, _mock_sha, _mock_branch, capsys):
    """No stderr leaves the action and the exit code — never a dangling separator."""
    mock_run.side_effect = _reset_run_sequence(
        MagicMock(returncode=1, stdout="", stderr=""))

    ctx = _make_ctx(head_sha="old111")
    assert update_to_remote(ctx) is ctx
    warning = capsys.readouterr().err.splitlines()[0]
    assert warning.endswith("git reset --hard origin/feat/x failed (exit 1)")


@patch("git.topology.current_branch_quiet", return_value="feat/x")
@patch("git.topology.default_branch", return_value="main")
@patch("pr.sync.subprocess.run")
def test_the_fetch_brings_the_default_branch_with_it(mock_run, _default, _branch, fetches):
    """What `behind_main` is measured against has to be current, or it undercounts.

    A stale `origin/main` makes the branch look nearer the trunk than it is,
    and the rebase that would have fired never does.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    update_to_remote(_make_ctx())
    assert fetches.calls == [("/wt", ("fetch", "origin", "feat/x", "main"))]


@patch("git.topology.current_branch_quiet", return_value="main")
@patch("git.topology.default_branch", return_value="main")
@patch("pr.sync.subprocess.run")
# passes-at-base: negative case — the trunk's own fetch named one ref before and still must, which is what the added ref could have broken
def test_the_default_branch_is_not_named_twice_in_its_own_fetch(
        mock_run, _default, _branch, fetches):
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    update_to_remote(_make_ctx(branch="main"))
    assert fetches.calls == [("/wt", ("fetch", "origin", "main"))]
