"""Bringing a worktree in line with its remote, and the guards that refuse to."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import git_topology  # noqa: E402
import pr_context  # noqa: E402
import pr_sync  # noqa: E402
from pr_sync import fetch_and_reset, update_to_remote  # noqa: E402

from conftest import make_ctx  # noqa: E402


@patch("pr_sync.git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_runs_fetch_then_reset(mock_run, _branch):
    # One result per guard read in call order: fetch, status --porcelain
    # (clean), rev-list --count (0 unpushed), reset --hard. A blanket
    # stdout="" for every call fails _unpushed_count's isdigit() check and
    # blocks the reset this test means to exercise.
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),
        MagicMock(returncode=0, stdout=""),
        MagicMock(returncode=0, stdout="0"),
        MagicMock(returncode=0, stdout=""),
    ]

    pr_sync.fetch_and_reset("/wt", "feat/x")

    argvs = [call.args[0] for call in mock_run.call_args_list]
    assert ["git", "-C", "/wt", "fetch", "origin", "feat/x"] in argvs
    assert ["git", "-C", "/wt", "reset", "--hard", "origin/feat/x"] in argvs


@patch("pr_sync.git_topology.current_branch_quiet", return_value="main")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_skips_when_on_another_branch(mock_run, _branch):
    mock_run.return_value = MagicMock(returncode=0, stdout="")

    pr_sync.fetch_and_reset("/wt", "feat/x")

    argvs = [call.args[0] for call in mock_run.call_args_list]
    assert not any("reset" in argv for argv in argvs)


# ── fetch_and_reset ────────────────────────────────────────────────────────


def _safe_reset_runs():
    """subprocess.run results for a worktree that is safe to hard-reset."""
    return [
        MagicMock(returncode=0),                        # fetch
        MagicMock(returncode=0, stdout="feat/x\n"),     # rev-parse --abbrev-ref
        MagicMock(returncode=0, stdout=""),             # status --porcelain (clean)
        MagicMock(returncode=0, stdout="0\n"),          # rev-list (0 unpushed)
        MagicMock(returncode=0),                        # reset --hard
    ]


@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_issues_fetch_then_reset_in_call_order(mock_run):
    mock_run.side_effect = _safe_reset_runs()
    fetch_and_reset("/wt", "feat/x")
    assert mock_run.call_count == 5
    fetch_call = mock_run.call_args_list[0].args[0]
    assert "fetch" in fetch_call
    assert "origin" in fetch_call
    assert "feat/x" in fetch_call
    reset_call = mock_run.call_args_list[4].args[0]
    assert "reset" in reset_call
    assert "--hard" in reset_call
    assert "origin/feat/x" in reset_call


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_regression_skips_wrong_branch(mock_run, mock_log):
    """Regression: resetting main/ while a feature branch sits in it ate two commits."""
    runs = _safe_reset_runs()
    runs[1] = MagicMock(returncode=0, stdout="feat/other\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "main")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "not main" in mock_log.warn.call_args.args[0]


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_skips_on_uncommitted_changes(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[2] = MagicMock(returncode=0, stdout=" M file.py\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_skips_on_unpushed_commits(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[3] = MagicMock(returncode=0, stdout="2\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "2 unpushed" in mock_log.warn.call_args.args[0]


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_skips_on_detached_head(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[1] = MagicMock(returncode=0, stdout="HEAD\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "detached HEAD" in mock_log.warn.call_args.args[0]


@patch("pr_sync.subprocess.run", side_effect=Exception("network error"))
def test_fetch_and_reset_survives_fetch_exception(mock_run):
    fetch_and_reset("/wt", "feat/x")


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_blocks_when_the_status_read_failed(mock_run, mock_log):
    """Regression: a `status` that failed is not a worktree that came back clean.

    Read as clean, the guard finds no blocker and the hard reset below it
    destroys whatever was uncommitted.
    """
    runs = _safe_reset_runs()
    runs[2] = MagicMock(returncode=128, stdout="",
                        stderr="fatal: .git/index: index file smaller than expected")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not be read" in mock_log.warn.call_args.args[0]


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_blocks_when_the_status_read_timed_out(mock_run, mock_log):
    """A timeout raises rather than returning, and must not escape the guard.

    Left uncaught it aborts the command from inside a safety check; folded into
    a non-zero result it blocks the reset, which is the same answer every other
    unreadable state gets.
    """
    runs = _safe_reset_runs()
    runs[2] = subprocess.TimeoutExpired(cmd=["git", "status", "--porcelain"], timeout=1)
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not be read" in mock_log.warn.call_args.args[0]


@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_fetch_and_reset_blocks_when_unpushed_commits_cannot_be_counted(mock_run, mock_log):
    """The other half of the same guard: a rev-list that never ran counted nothing."""
    runs = _safe_reset_runs()
    runs[3] = MagicMock(returncode=128, stdout="",
                        stderr="fatal: bad revision 'origin/feat/x..HEAD'")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not be counted" in mock_log.warn.call_args.args[0]


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


@patch("pr_sync.log")
@patch("git_topology.current_branch_quiet", return_value="other-branch")
def test_update_to_remote_skips_on_branch_mismatch(mock_branch, mock_log):
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    mock_log.info.assert_called_once()
    assert "other-branch" in mock_log.info.call_args.args[0]


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_skips_on_uncommitted_changes(mock_run, mock_log, _mock_branch):
    mock_run.return_value = MagicMock(returncode=0, stdout="M dirty.py\n")
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    mock_log.warn.assert_called_once()
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.log")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_skips_when_the_status_read_failed(mock_run, mock_log, _mock_branch):
    """The same guard from the other entry point — no fetch, and no reset."""
    mock_run.return_value = MagicMock(
        returncode=128, stdout="", stderr="fatal: .git/index: index file corrupt",
    )
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    assert mock_run.call_count == 1
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.log")
@patch("pr_context._head_sha", return_value="local111")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_skips_when_unpushed_commits_cannot_be_counted(
    mock_run, mock_sha, mock_log, _mock_branch,
):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),             # status --porcelain (clean)
        MagicMock(returncode=0),                         # fetch
        MagicMock(returncode=0, stdout="remote222\n"),   # rev-parse origin/branch
        MagicMock(returncode=128, stdout="", stderr="fatal: bad revision"),
    ]
    ctx = _make_ctx(head_sha="local111")
    assert update_to_remote(ctx) is ctx
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "could not count" in mock_log.warn.call_args.args[0].lower()


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_skips_on_fetch_failure(mock_run, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),       # status --porcelain (clean)
        MagicMock(returncode=1),                   # fetch fails
    ]
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_context._head_sha", return_value="aaa111")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_skips_when_already_current(mock_run, mock_sha, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),           # status --porcelain (clean)
        MagicMock(returncode=0),                       # fetch
        MagicMock(returncode=0, stdout="aaa111\n"),    # rev-parse origin/branch
    ]
    ctx = _make_ctx(head_sha="aaa111")
    assert update_to_remote(ctx) is ctx


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.log")
@patch("pr_context._head_sha", return_value="local111")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_skips_on_unpushed_commits(mock_run, mock_sha, mock_log, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0),                        # fetch
        MagicMock(returncode=0, stdout="remote222\n"),  # rev-parse origin/branch
        MagicMock(returncode=0, stdout="2\n"),          # rev-list (2 unpushed)
    ]
    ctx = _make_ctx(head_sha="local111")
    assert update_to_remote(ctx) is ctx
    mock_log.warn.assert_called_once()
    assert "unpushed" in mock_log.warn.call_args.args[0]


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_sync.log")
@patch("pr_context._head_sha", return_value="old111")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_resets_when_safe(mock_run, mock_sha, mock_log, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0),                        # fetch
        MagicMock(returncode=0, stdout="new222\n"),     # rev-parse origin/branch
        MagicMock(returncode=0, stdout="0\n"),          # rev-list (0 unpushed)
        MagicMock(returncode=0),                        # reset --hard
    ]
    ctx = _make_ctx(head_sha="old111")
    result = update_to_remote(ctx)
    assert result.head_sha == "new222"
    assert result.branch == "feat/x"
    reset_call = mock_run.call_args_list[4].args[0]
    assert "reset" in reset_call
    assert "--hard" in reset_call


def test_update_to_remote_preserves_the_target_dir(monkeypatch, tmp_path):
    """dataclasses.replace, so a new field cannot be dropped by hand-retyping."""
    ctx = pr_context.ResolvedContext(
        repo="acme/widget", branch="feat/a", pr_number=1,
        worktree_root=tmp_path, head_sha="old", current_branch="feat/a",
        target_dir=tmp_path / "target",
    )
    monkeypatch.setattr(git_topology, "current_branch_quiet", lambda cwd=None: "feat/a")
    monkeypatch.setattr(pr_sync, "_worktree_is_dirty", lambda cwd: False)
    monkeypatch.setattr(pr_sync, "_unpushed_count", lambda cwd, branch: 0)
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "old")

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        out = "new-sha" if "rev-parse" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(pr_sync.subprocess, "run", fake_run)
    updated = pr_sync.update_to_remote(ctx)

    assert updated.head_sha == "new-sha"
    assert updated.target_dir == ctx.target_dir


def _reset_run_sequence(reset_result):
    """The subprocess results update_to_remote consumes before reset --hard."""
    return [
        MagicMock(returncode=0, stdout=""),            # status --porcelain (clean)
        MagicMock(returncode=0),                        # fetch
        MagicMock(returncode=0, stdout="new222\n"),     # rev-parse origin/branch
        MagicMock(returncode=0, stdout="0\n"),          # rev-list (0 unpushed)
        reset_result,
    ]


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_context._head_sha", return_value="old111")
@patch("pr_sync.subprocess.run")
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


@patch("git_topology.current_branch_quiet", return_value="feat/x")
@patch("pr_context._head_sha", return_value="old111")
@patch("pr_sync.subprocess.run")
def test_update_to_remote_degrades_when_reset_says_nothing(
        mock_run, _mock_sha, _mock_branch, capsys):
    """No stderr leaves the action and the exit code — never a dangling separator."""
    mock_run.side_effect = _reset_run_sequence(
        MagicMock(returncode=1, stdout="", stderr=""))

    ctx = _make_ctx(head_sha="old111")
    assert update_to_remote(ctx) is ctx
    warning = capsys.readouterr().err.splitlines()[0]
    assert warning.endswith("git reset --hard origin/feat/x failed (exit 1)")
