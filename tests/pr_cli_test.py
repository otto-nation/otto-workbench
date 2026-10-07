"""Tests for pr CLI helper functions: targets, global flags, delegate argv, SIGINT and create."""


import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from conftest import command_spec, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402

import cli.dispatch  # noqa: E402
import core.children  # noqa: E402
import core.publishing  # noqa: E402

from pr_cli_support import _TEST_PR, _run_main, _delegate_cmd


# ── _is_pr_target ──────────────────────────────────────────────────────────


def test_is_pr_target_number():
    assert cli.pr._is_pr_target("42") is True


def test_is_pr_target_url():
    assert cli.pr._is_pr_target("https://github.com/owner/repo/pull/123") is True


def test_is_pr_target_branch():
    assert cli.pr._is_pr_target("isaac/feat/foo") is False


def test_is_pr_target_none():
    assert cli.pr._is_pr_target(None) is False


def test_is_pr_target_empty():
    assert cli.pr._is_pr_target("") is False


# ── help passthrough ─────────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_global_flags_after_subcommand(mock_resolve, mock_call):
    """Global flags like --repo-dir work after the subcommand name."""
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    _run_main("rebase", "--repo-dir", "/some/path")
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args[1]
    assert call_kwargs["repo_dir"] == "/some/path"


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_global_flags_before_subcommand(mock_resolve, mock_call):
    """Global flags also work before the subcommand name."""
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    _run_main("--repo-dir", "/some/path", "rebase")
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args[1]
    assert call_kwargs["repo_dir"] == "/some/path"


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_global_flags_mixed_with_subcommand_flags(mock_resolve, mock_call):
    """--repo-dir after subcommand doesn't swallow subcommand-specific flags."""
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    _run_main("rebase", "--fix", "--repo-dir", "/some/path")
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[1]["repo_dir"] == "/some/path"
    cmd = mock_call.call_args[0][1]
    assert "--fix" in cmd


@patch("cli.pr._reference_parser")
@patch("pr.context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_help_flag_skips_context_resolution(mock_resolve, mock_help):
    rc = _run_main("ci", "--help")
    assert rc == 0
    mock_help.assert_called_once()
    assert mock_help.call_args[0][0] == "ci"
    mock_resolve.assert_not_called()


@patch("cli.pr._reference_parser")
@patch("pr.context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_help_short_flag_skips_context_resolution(mock_resolve, mock_help):
    rc = _run_main("ci", "-h")
    assert rc == 0
    mock_help.assert_called_once()
    assert mock_help.call_args[0][0] == "ci"
    mock_resolve.assert_not_called()


@patch("pr.context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_review_help_describes_the_mode_flags_pr_routes(_resolve, capsys):
    """`pr review --help` shows `--post` as the mode that posts an existing review.

    The bare `review` parser reads `--post` as "post when this run finishes", so
    help printed from it sends a reader looking to publish a finished review
    file somewhere other than `pr review --post`.
    """
    assert _run_main("review", "--help") == 0
    out = capsys.readouterr().out
    assert "usage: pr review" in out
    assert "Post an existing review to GitHub" in out
    for flag in ("--repair", "--summary", "--list"):
        assert flag in out


# ── delegate_argv ────────────────────────────────────────────────────────


def test_run_delegate_builds_command():
    ctx = make_ctx()
    entry = command_spec(script="ci-check")
    cmd = cli.dispatch.delegate_argv(entry, ["--run", "99"], ctx)
    assert "--repo-dir" in cmd
    assert "/wt" in cmd[cmd.index("--repo-dir") + 1]
    assert "--run" in cmd
    assert "99" in cmd


def test_run_delegate_passes_argv_through():
    ctx = make_ctx()
    entry = command_spec(script="pr-rebase")
    cmd = cli.dispatch.delegate_argv(
        entry, ["--fix", "--push", "--unknown-future-flag"], ctx)
    assert "--fix" in cmd
    assert "--push" in cmd
    assert "--unknown-future-flag" in cmd


def test_run_delegate_returns_exit_code():
    """What a delegate answers is what `pr` exits with.

    The seam's own endings — `sys.exit`, a bare return, a message — are
    covered against real modules in `pr_comments_test.py`; this is the one
    assertion that `dispatch.delegate_argv`'s result is what reaches
    `publishing.call_entry_point` at all.
    """
    ns = SimpleNamespace(main=lambda argv, **kw: 3)
    with patch("core.publishing.importlib.import_module", return_value=ns):
        assert core.publishing.call_entry_point("mod:main", []) == 3


# ── delegate_argv branch/pr injection ─────────────────────────────────────


def test_run_delegate_forwards_only_original_pr():
    """When the user provided --pr, only --pr is forwarded (not --branch)."""
    ctx = make_ctx(branch="feat/my-feature", pr_number=99)
    entry = command_spec(script="review-threads")
    cmd = cli.dispatch.delegate_argv(entry, [], ctx, original_pr="99")
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


def test_run_delegate_prefers_pr_over_original_branch():
    """When the user provided --branch but a PR was resolved, forward --pr."""
    ctx = make_ctx(branch="feat/my-feature", pr_number=99)
    entry = command_spec(script="review-threads")
    cmd = cli.dispatch.delegate_argv(
        entry, [], ctx, original_branch="feat/my-feature")
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


def test_run_delegate_falls_back_to_original_branch_without_pr():
    """When the user provided --branch and no PR was resolved, forward --branch."""
    ctx = make_ctx(branch="feat/my-feature", pr_number=None)
    entry = command_spec(script="review-threads")
    cmd = cli.dispatch.delegate_argv(
        entry, [], ctx, original_branch="feat/my-feature")
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/my-feature"
    assert "--pr" not in cmd


def test_run_delegate_auto_detected_forwards_pr():
    """When neither flag was given and ctx has a PR, forward --pr (not --branch)."""
    ctx = make_ctx(branch="feat/my-feature", pr_number=99)
    entry = command_spec(script="review-threads")
    cmd = cli.dispatch.delegate_argv(entry, [], ctx)
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


def test_run_delegate_auto_detected_no_pr_forwards_branch():
    """When neither flag was given and ctx has no PR, forward --branch."""
    ctx = make_ctx(branch="feat/my-feature", pr_number=None)
    entry = command_spec(script="review-threads")
    cmd = cli.dispatch.delegate_argv(entry, [], ctx)
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/my-feature"
    assert "--pr" not in cmd


def test_run_delegate_omits_branch_when_none():
    ctx = make_ctx(branch="", pr_number=None)
    entry = command_spec(script="ci-check")
    cmd = cli.dispatch.delegate_argv(entry, [], ctx)
    assert "--branch" not in cmd
    assert "--pr" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_pr_flag_does_not_pass_both_to_delegate(mock_resolve, mock_call):
    """Regression: pr --pr 1927 comments must not pass both --branch and --pr."""
    mock_resolve.return_value = make_ctx(branch="feat/derived", pr_number=1927)
    mock_call.return_value = 0
    _run_main("--pr", "1927", "--repo-dir", "/path", "comments")
    cmd = mock_call.call_args[0][1]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "1927"
    assert "--branch" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_branch_flag_prefers_resolved_pr(mock_resolve, mock_call):
    """pr --branch feat/foo comments forwards --pr when a PR was resolved."""
    mock_resolve.return_value = make_ctx(branch="feat/foo", pr_number=42)
    mock_call.return_value = 0
    _run_main("--branch", "feat/foo", "--repo-dir", "/path", "comments")
    cmd = mock_call.call_args[0][1]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "42"
    assert "--branch" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_auto_detected_forwards_pr_only(mock_resolve, mock_call):
    """Bare 'pr comments' (no flags) forwards auto-detected --pr, not --branch."""
    mock_resolve.return_value = make_ctx(branch="feat/derived", pr_number=42)
    mock_call.return_value = 0
    _run_main("--repo-dir", "/path", "comments")
    cmd = mock_call.call_args[0][1]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "42"
    assert "--branch" not in cmd


# ── positional target forwarding ────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_positional_branch_not_forwarded_as_extra(mock_resolve, mock_call):
    """Regression: 'pr rebase my-branch' must not pass my-branch as a bare positional."""
    mock_resolve.return_value = make_ctx(branch="my-branch", pr_number=None)
    mock_call.return_value = 0
    _run_main("rebase", "my-branch")
    cmd = _delegate_cmd(mock_call)
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "my-branch"
    assert cmd.count("my-branch") == 1, f"Branch appeared {cmd.count('my-branch')} times: {cmd}"


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_positional_pr_number_not_forwarded_as_extra(mock_resolve, mock_call):
    """Regression: 'pr ci 42' must not pass 42 as a bare positional."""
    mock_resolve.return_value = make_ctx(pr_number=42)
    mock_call.return_value = 0
    _run_main("ci", "42")
    cmd = _delegate_cmd(mock_call)
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "42"
    assert cmd.count("42") == 1, f"PR number appeared {cmd.count('42')} times: {cmd}"


# ── Stop handling ────────────────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
@pytest.mark.parametrize("signum, code", [
    (signal.SIGINT, 130), (signal.SIGTERM, 143), (signal.SIGHUP, 129),
])
def test_main_installs_the_stop_handler(mock_resolve, mock_call, signum, code):
    """main() answers every stop signal by stopping its children and exiting 128+N.

    Ctrl+C exits cleanly without a traceback, and a supervisor's SIGTERM no
    longer ends the process with the agents it started still running. The
    autouse `_isolated_stop_handling` fixture restores the handlers and the
    child registry this leaves behind.
    """
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    original = signal.getsignal(signum)
    _run_main("--repo-dir", "/path", "rebase")
    handler = signal.getsignal(signum)
    assert handler is not original
    assert handler is not signal.SIG_DFL
    with pytest.raises(SystemExit) as exc_info:
        handler(signum, None)
    assert exc_info.value.code == code
    assert core.children.stopping(), "the handler exited without stopping the children"
