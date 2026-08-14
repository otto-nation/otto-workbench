"""Tests for pr CLI helper functions."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Captured before any test patches pr_cli.subprocess.run — pr_cli.subprocess is
# the subprocess module itself, so that patch is global and this is the only
# handle left on the real thing.
_REAL_SUBPROCESS_RUN = subprocess.run

# Import the extensionless pr script via importlib
_pr_path = str(BIN_DIR / "pr")
_loader = importlib.machinery.SourceFileLoader("pr_cli", _pr_path)
_spec = importlib.util.spec_from_loader("pr_cli", _loader, origin=_pr_path)
pr_cli = importlib.util.module_from_spec(_spec)
pr_cli.__file__ = _pr_path
_spec.loader.exec_module(pr_cli)
# Register so @patch("pr_cli.subprocess.run") can resolve the module
sys.modules.setdefault("pr_cli", pr_cli)

import pr_state  # noqa: E402
import run_lock  # noqa: E402
import workbench_paths  # noqa: E402

from conftest import assert_no_worktree_exit, make_ctx  # noqa: E402


# ── _parse_review_summary ──────────────────────────────────────────────────


def test_parse_review_summary_valid():
    output = 'REVIEW_SUMMARY:{"repo":"owner/repo","verdict":"approve","findings":{"M":0,"S":1,"total":1}}'
    result = pr_cli._parse_review_summary(output)
    assert result["verdict"] == pr_state.ReviewVerdict.APPROVE.value
    assert result["findings"]["S"] == 1


def test_parse_review_summary_multiline():
    output = "Some output\nMore output\nREVIEW_SUMMARY:{\"verdict\":\"changes_requested\"}\nTrailing"
    result = pr_cli._parse_review_summary(output)
    assert result["verdict"] == pr_state.ReviewVerdict.CHANGES_REQUESTED.value


def test_parse_review_summary_missing():
    assert pr_cli._parse_review_summary("no summary here") is None


def test_parse_review_summary_invalid_json():
    assert pr_cli._parse_review_summary("REVIEW_SUMMARY:{invalid}") is None


# ── _is_pr_target ──────────────────────────────────────────────────────────


def test_is_pr_target_number():
    assert pr_cli._is_pr_target("42") is True


def test_is_pr_target_url():
    assert pr_cli._is_pr_target("https://github.com/owner/repo/pull/123") is True


def test_is_pr_target_branch():
    assert pr_cli._is_pr_target("isaac/feat/foo") is False


def test_is_pr_target_none():
    assert pr_cli._is_pr_target(None) is False


def test_is_pr_target_empty():
    assert pr_cli._is_pr_target("") is False


# ── _merge_readiness ────────────────────────────────────────────────────────


def test_merge_readiness_all_green():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.CIDomain(conclusion="success", updated_at="t"))
    pr_state.apply(state, pr_state.ReviewSummary(
        finding_counts={"S": 1}, verdict=pr_state.ReviewVerdict.APPROVE.value, updated_at="t",
    ))
    pr_state.apply(state, pr_state.CommentsSummary(
        blocking_reviewers=[], updated_at="t",
    ))
    result = pr_cli._merge_readiness(state)
    assert "ready" in result.lower()


def test_merge_readiness_ci_failing():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.CIDomain(conclusion="failure", updated_at="t"))
    pr_state.apply(state, pr_state.ReviewSummary(updated_at="t"))
    pr_state.apply(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "CI failing" in result


def test_merge_readiness_must_fix():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.CIDomain(conclusion="success", updated_at="t"))
    pr_state.apply(state, pr_state.ReviewSummary(
        finding_counts={"M": 2}, updated_at="t",
    ))
    pr_state.apply(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "must-fix" in result


def test_merge_readiness_not_checked():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    result = pr_cli._merge_readiness(state)
    assert "not checked" in result


def test_merge_readiness_review_incomplete():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.CIDomain(conclusion="success", updated_at="t"))
    pr_state.apply(state, pr_state.ReviewSummary(
        status="partial", finding_counts={}, updated_at="t",
    ))
    pr_state.apply(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "review incomplete" in result


def test_merge_readiness_review_error():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.CIDomain(conclusion="success", updated_at="t"))
    pr_state.apply(state, pr_state.ReviewSummary(
        status="error", updated_at="t",
    ))
    pr_state.apply(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "review incomplete" in result


# ── _COMMANDS registry ────────────────────────────────────────────────────


def test_commands_registry_exists():
    """Registry dict drives all subcommand registration."""
    assert hasattr(pr_cli, "_COMMANDS")
    assert isinstance(pr_cli._COMMANDS, dict)


def test_commands_registry_has_all_subcommands():
    # Keep this set in sync with _COMMANDS in ai/claude/bin/pr
    expected = {"create", "status", "ci", "review", "comments",
                "fix", "rebase", "describe", "gc"}
    assert set(pr_cli._COMMANDS.keys()) == expected


def test_commands_registry_entries_have_help():
    for name, entry in pr_cli._COMMANDS.items():
        assert "help" in entry, f"{name} missing 'help'"
        assert isinstance(entry["help"], str)


def test_commands_with_script_key():
    """Commands backed by an external script carry a 'script' key."""
    has_script = {"ci", "review", "comments", "rebase", "describe"}
    for name in has_script:
        assert "script" in pr_cli._COMMANDS[name], f"{name} missing 'script'"


def test_custom_handlers_are_registered():
    """_CUSTOM contains the expected non-pure-delegate commands."""
    expected_custom = {"create", "status", "review", "comments", "fix", "gc"}
    assert set(pr_cli._CUSTOM.keys()) == expected_custom


def test_internal_commands_have_no_script():
    internal = {"create", "status", "fix", "gc"}
    for name in internal:
        assert "script" not in pr_cli._COMMANDS[name], f"{name} should not have 'script'"


def test_sub_command_prefix():
    assert pr_cli._COMMANDS["gc"].get("prefix") is None


# ── help passthrough ─────────────────────────────────────────────────────


def _run_main(*argv):
    """Run pr_cli.main() with the given argv, catching SystemExit."""
    mock_trail = MagicMock()
    with patch("sys.argv", ["pr"] + list(argv)), \
         patch("pr_cli.Trail.start", return_value=mock_trail):
        try:
            pr_cli.main()
        except SystemExit as e:
            return e.code
    return None


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_global_flags_after_subcommand(mock_resolve, mock_run):
    """Global flags like --repo-dir work after the subcommand name."""
    mock_resolve.return_value = make_ctx()
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("rebase", "--repo-dir", "/some/path")
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args[1]
    assert call_kwargs["repo_dir"] == "/some/path"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_global_flags_before_subcommand(mock_resolve, mock_run):
    """Global flags also work before the subcommand name."""
    mock_resolve.return_value = make_ctx()
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--repo-dir", "/some/path", "rebase")
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args[1]
    assert call_kwargs["repo_dir"] == "/some/path"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_global_flags_mixed_with_subcommand_flags(mock_resolve, mock_run):
    """--repo-dir after subcommand doesn't swallow subcommand-specific flags."""
    mock_resolve.return_value = make_ctx()
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("rebase", "--fix", "--repo-dir", "/some/path")
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args[1]["repo_dir"] == "/some/path"
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_help_flag_skips_context_resolution(mock_resolve, mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    rc = _run_main("ci", "--help")
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/ci-check")
    assert "--help" in cmd
    mock_resolve.assert_not_called()


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_help_short_flag_skips_context_resolution(mock_resolve, mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    rc = _run_main("ci", "-h")
    assert rc == 0
    mock_resolve.assert_not_called()


# ── _run_delegate ─────────────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_run_delegate_builds_command(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    entry = {"script": "ci-check", "help": "x"}
    pr_cli._run_delegate(entry, ["--run", "99"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/ci-check")
    assert "--repo-dir" in cmd
    assert "/wt" in cmd[cmd.index("--repo-dir") + 1]
    assert "--run" in cmd
    assert "99" in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_includes_prefix(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    entry = {"script": "claude-review", "prefix": ["gc"], "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/claude-review")
    assert cmd[1] == "gc"


@patch("pr_cli.subprocess.run")
def test_run_delegate_passes_argv_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    entry = {"script": "pr-rebase", "help": "x"}
    pr_cli._run_delegate(entry, ["--fix", "--push", "--unknown-future-flag"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--push" in cmd
    assert "--unknown-future-flag" in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_returns_exit_code(mock_run):
    mock_run.return_value = MagicMock(returncode=3)
    ctx = make_ctx()
    entry = {"script": "pr-rebase", "help": "x"}
    rc = pr_cli._run_delegate(entry, [], ctx)
    assert rc == 3


# ── cmd_review auto-self ──────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_cmd_review_injects_self_when_no_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(pr_number=None)
    pr_cli.cmd_review([], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_pr_number(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_review(["123"], ctx)
    cmd = mock_run.call_args[0][0]
    self_count = cmd.count("--self")
    assert self_count == 0


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_pr_url(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_review(["https://github.com/owner/repo/pull/99"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_original_pr(mock_run):
    """--pr consumed by global parser still prevents --self injection."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_review([], ctx, original_pr="1206")
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_ctx_has_pr(mock_run):
    """Auto-detected PR number in context prevents --self injection."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(pr_number=99)
    pr_cli.cmd_review([], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_double_self(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_review(["--self"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd.count("--self") == 1


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_branch_positional(mock_run):
    """A branch name positional should not trigger --self injection."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(pr_number=None)
    pr_cli.cmd_review(["kgn/go-update"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd
    assert "kgn/go-update" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_passes_flags_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_review(["--self", "--fix", "--no-post"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--no-post" in cmd


# ── cmd_review --recover mode flag ───────────────────────────────────────


def test_review_recover_mutually_exclusive_with_post():
    """--recover and --post are mutually exclusive."""
    ctx = make_ctx()
    rc = pr_cli.cmd_review(["--recover", "--post"], ctx)
    assert rc == 1


def test_review_recover_passes_through_to_delegate():
    """--recover alone is forwarded to claude-review."""
    ctx = make_ctx()
    with patch("pr_cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        pr_cli.cmd_review(["--recover", "42"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--recover" in cmd


# ── cmd_review --post ────────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_cmd_review_post_delegates_to_review_post(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    import review_common
    reviews_dir = tmp_path / "reviews"
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir(parents=True)
    (review_dir / "review.md").write_text("# Review")
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--post"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-post")
    assert "--pr" in cmd
    assert "--review-file" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_post_passes_submit(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0)
    import review_common
    reviews_dir = tmp_path / "reviews"
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir(parents=True)
    (review_dir / "review.md").write_text("# Review")
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--post", "--submit"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert "--submit" in cmd


def test_cmd_review_post_fails_without_review_file(tmp_path):
    import review_common
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--post"], ctx)
    assert rc == 1


@patch("pr_cli.subprocess.run")
def test_cmd_review_post_finds_review_via_meta(mock_run, tmp_path):
    """--post discovers a review stored under a non-canonical directory name."""
    mock_run.return_value = MagicMock(returncode=0)
    import review_common
    reviews_dir = tmp_path / "reviews"
    alt_dir = reviews_dir / "repo-self-some-branch"
    alt_dir.mkdir(parents=True)
    (alt_dir / "review.md").write_text("# Review")
    (alt_dir / "meta.json").write_text(json.dumps({
        "repo": "owner/repo", "pr_number": "42",
    }))
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--post"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-post")
    assert str(alt_dir / "review.md") in cmd


# ── _run_delegate branch/pr injection ────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_run_delegate_forwards_only_original_pr(mock_run):
    """When the user provided --pr, only --pr is forwarded (not --branch)."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(branch="feat/my-feature", pr_number=99)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx, original_pr="99")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_prefers_pr_over_original_branch(mock_run):
    """When the user provided --branch but a PR was resolved, forward --pr."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(branch="feat/my-feature", pr_number=99)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx, original_branch="feat/my-feature")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_falls_back_to_original_branch_without_pr(mock_run):
    """When the user provided --branch and no PR was resolved, forward --branch."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(branch="feat/my-feature", pr_number=None)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx, original_branch="feat/my-feature")
    cmd = mock_run.call_args[0][0]
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/my-feature"
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_auto_detected_forwards_pr(mock_run):
    """When neither flag was given and ctx has a PR, forward --pr (not --branch)."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(branch="feat/my-feature", pr_number=99)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_auto_detected_no_pr_forwards_branch(mock_run):
    """When neither flag was given and ctx has no PR, forward --branch."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(branch="feat/my-feature", pr_number=None)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/my-feature"
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_omits_branch_when_none(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx(branch="", pr_number=None)
    entry = {"script": "ci-check", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--branch" not in cmd
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_pr_flag_does_not_pass_both_to_delegate(mock_resolve, mock_run):
    """Regression: pr --pr 1927 comments must not pass both --branch and --pr."""
    mock_resolve.return_value = make_ctx(branch="feat/derived", pr_number=1927)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--pr", "1927", "--repo-dir", "/path", "comments")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "1927"
    assert "--branch" not in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_branch_flag_prefers_resolved_pr(mock_resolve, mock_run):
    """pr --branch feat/foo comments forwards --pr when a PR was resolved."""
    mock_resolve.return_value = make_ctx(branch="feat/foo", pr_number=42)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--branch", "feat/foo", "--repo-dir", "/path", "comments")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "42"
    assert "--branch" not in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_auto_detected_forwards_pr_only(mock_resolve, mock_run):
    """Bare 'pr comments' (no flags) forwards auto-detected --pr, not --branch."""
    mock_resolve.return_value = make_ctx(branch="feat/derived", pr_number=42)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--repo-dir", "/path", "comments")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "42"
    assert "--branch" not in cmd


# ── cmd_comments ────────────────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_cmd_comments_plain_delegates_to_review_threads(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_comments([], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-threads")
    assert "--triage" not in cmd
    assert "--resolve" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_comments_triage_passes_flag(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_comments(["--triage"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-threads")
    assert "--triage" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_comments_resolve_passes_flag(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    pr_cli.cmd_comments(["--resolve"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-threads")
    assert "--resolve" in cmd


# ── cmd_review --repair ────────────────────────────────────────────────────


@patch("pr_cli._update_review_state")
def test_cmd_review_repair_succeeds_with_review_file(mock_update, tmp_path):
    import review_common
    reviews_dir = tmp_path / "reviews"
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir(parents=True)
    (review_dir / "review.md").write_text("## Nit\n- **[N1]** path:1 — style\n")
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--repair"], ctx)
    assert rc == 0
    mock_update.assert_called_once()


@patch("pr_cli.subprocess.run")
def test_cmd_review_repair_falls_back_to_rebuild(mock_run, tmp_path):
    import review_common
    reviews_dir = tmp_path / "reviews"
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir(parents=True)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--repair"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-rebuild")


def test_cmd_review_repair_no_pr_fails():
    ctx = make_ctx(pr_number=None)
    rc = pr_cli.cmd_review(["--repair"], ctx)
    assert rc == 1


# ── cmd_review --summary ───────────────────────────────────────────────────


def test_cmd_review_summary_outputs_json(tmp_path, capsys):
    import review_common
    reviews_dir = tmp_path / "reviews"
    review_dir = reviews_dir / "repo-42"
    review_dir.mkdir(parents=True)
    (review_dir / "review.md").write_text("## Must fix\n- **[M1]** path:1 — bug\n")
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--summary"], ctx)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("REVIEW_SUMMARY:")
    data = json.loads(out.removeprefix("REVIEW_SUMMARY:"))
    assert data["findings"]["must_fix"] == 1


def test_cmd_review_summary_fails_without_review(tmp_path):
    import review_common
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--summary"], ctx)
    assert rc == 1


# ── cmd_review mutual exclusivity ─────────────────────────────────────────


def test_cmd_review_mutual_exclusivity():
    ctx = make_ctx()
    rc = pr_cli.cmd_review(["--post", "--repair"], ctx)
    assert rc == 1


def test_cmd_review_mutual_exclusivity_three():
    ctx = make_ctx()
    rc = pr_cli.cmd_review(["--post", "--repair", "--summary"], ctx)
    assert rc == 1


# ── cmd_fix ─────────────────────────────────────────────────────────────────


@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_no_state_returns_error(mock_load):
    mock_load.return_value = None
    ctx = make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 1


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_dispatches_review_when_findings(mock_load, mock_run):
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.ReviewSummary(
        finding_counts={"M": 1}, verdict=pr_state.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    ))
    mock_load.return_value = state
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 0
    cmd = _first_call_containing(mock_run, "claude-review")
    assert "--self" in cmd
    assert "--fix" in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_skips_review_when_no_findings(mock_load, mock_run):
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.ReviewSummary(
        finding_counts={}, verdict=pr_state.ReviewVerdict.APPROVE.value, updated_at="t",
    ))
    mock_load.return_value = state
    mock_run.return_value = MagicMock(returncode=0)
    ctx = make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 0
    assert not _calls_containing(mock_run, "claude-review")


def _calls_containing(mock_run, script: str) -> list[list[str]]:
    """Calls that invoked this script.

    Matched on the basename of argv[0], not a suffix scan across every
    argument: a --repo-dir whose path happened to end in a script name would
    otherwise pass for an invocation of that script.
    """
    return [
        call[0][0] for call in mock_run.call_args_list
        if Path(call[0][0][0]).name == script
    ]


def _first_call_containing(mock_run, script: str) -> list[str]:
    calls = _calls_containing(mock_run, script)
    assert calls, f"{script} was never invoked"
    return calls[0]


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_describes_last(mock_load, mock_run):
    """The description must reflect the branch state after all fix passes complete."""
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.apply(state, pr_state.ReviewSummary(
        finding_counts={"M": 1}, verdict=pr_state.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    ))
    mock_load.return_value = state
    mock_run.return_value = MagicMock(returncode=0)
    pr_cli.cmd_fix([], ctx=make_ctx())
    scripts = [Path(call[0][0][0]).name for call in mock_run.call_args_list]
    assert scripts[-1] == "pr-describe"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_does_not_forward_argv_to_describe(mock_load, mock_run):
    """--fix and friends mean nothing to pr-describe."""
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    mock_load.return_value = state
    mock_run.return_value = MagicMock(returncode=0)
    pr_cli.cmd_fix(["--verbose"], ctx=make_ctx())
    cmd = _first_call_containing(mock_run, "pr-describe")
    assert "--verbose" not in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_reports_a_failing_describe(mock_load, mock_run):
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    mock_load.return_value = state
    mock_run.return_value = MagicMock(returncode=1)
    assert pr_cli.cmd_fix([], ctx=make_ctx()) == 1


# ── positional target forwarding ────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_positional_branch_not_forwarded_as_extra(mock_resolve, mock_run):
    """Regression: 'pr rebase my-branch' must not pass my-branch as a bare positional."""
    mock_resolve.return_value = make_ctx(branch="my-branch", pr_number=None)
    _probe_real_delegates(mock_run)
    _run_main("rebase", "my-branch")
    cmd = _delegate_cmd(mock_run)
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "my-branch"
    assert cmd.count("my-branch") == 1, f"Branch appeared {cmd.count('my-branch')} times: {cmd}"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_positional_pr_number_not_forwarded_as_extra(mock_resolve, mock_run):
    """Regression: 'pr ci 42' must not pass 42 as a bare positional."""
    mock_resolve.return_value = make_ctx(pr_number=42)
    _probe_real_delegates(mock_run)
    _run_main("ci", "42")
    cmd = _delegate_cmd(mock_run)
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "42"
    assert cmd.count("42") == 1, f"PR number appeared {cmd.count('42')} times: {cmd}"


# ── positional target vs. flag arity (issue #685) ──────────────────────────


def _probe_real_delegates(mock_run):
    """Let --value-flags probes reach the real delegate; stub every other run.

    The point of these tests is that the wrapper reads arity off the delegate's
    own parser, so the delegate has to be the one answering. Everything else
    stays mocked — no delegate does its real work here.
    """
    def side_effect(cmd, *args, **kwargs):
        if pr_cli.VALUE_FLAGS_FLAG in cmd:
            return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)
        return MagicMock(returncode=0)

    mock_run.side_effect = side_effect
    return mock_run


def _delegate_cmd(mock_run):
    """The argv of the last non-probe subprocess call."""
    calls = [c for c in mock_run.call_args_list
             if pr_cli.VALUE_FLAGS_FLAG not in c[0][0]]
    assert calls, "no delegate was dispatched"
    return calls[-1][0][0]


def _probe_scripts(mock_run):
    """Basenames of the delegates asked for their flag arity."""
    return [Path(c[0][0][0]).name for c in mock_run.call_args_list
            if pr_cli.VALUE_FLAGS_FLAG in c[0][0]]


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_reply_id_is_not_eaten_as_the_positional_target(mock_resolve, mock_run):
    """Regression #685: --reply's value is its argument, not the PR number."""
    mock_resolve.return_value = make_ctx(pr_number=None, branch=None)
    _probe_real_delegates(mock_run)
    _run_main("comments", "--reply", "3777767789",
              "--body-file", "/tmp/reply.md", "--repo-dir", "/path")
    cmd = _delegate_cmd(mock_run)
    assert cmd[cmd.index("--reply") + 1] == "3777767789"
    assert cmd[cmd.index("--body-file") + 1] == "/tmp/reply.md"
    assert "--pr" not in cmd
    assert mock_resolve.call_args[1]["pr"] is None
    assert mock_resolve.call_args[1]["branch"] is None


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_body_file_path_is_not_eaten_after_an_inline_reply(mock_resolve, mock_run):
    """Regression #685: --reply=ID self-contained, so --body-file's path survives too."""
    mock_resolve.return_value = make_ctx(pr_number=None, branch=None)
    _probe_real_delegates(mock_run)
    _run_main("comments", "--reply=3777767789", "--body-file=/tmp/reply.md")
    cmd = _delegate_cmd(mock_run)
    assert "--reply=3777767789" in cmd
    assert "--body-file=/tmp/reply.md" in cmd
    assert mock_resolve.call_args[1]["branch"] is None


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_reply_value_survives_an_explicit_branch(mock_resolve, mock_run):
    """An explicit --branch skips classification entirely; extra stays intact."""
    mock_resolve.return_value = make_ctx(branch="some/branch", pr_number=None)
    _probe_real_delegates(mock_run)
    _run_main("comments", "--branch", "some/branch",
              "--reply", "123", "--body-file", "/tmp/x.md")
    cmd = _delegate_cmd(mock_run)
    assert cmd[cmd.index("--reply") + 1] == "123"
    assert cmd[cmd.index("--body-file") + 1] == "/tmp/x.md"
    assert _probe_scripts(mock_run) == [], "no ambiguity, so no probe"


@pytest.mark.parametrize("flag", ["--fix", "--triage"])
@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_target_after_a_boolean_flag_is_still_the_target(mock_resolve, mock_run, flag):
    """A boolean flag consumes nothing, so the token after it is the PR number."""
    mock_resolve.return_value = make_ctx(pr_number=3057)
    _probe_real_delegates(mock_run)
    _run_main("comments", flag, "3057")
    cmd = _delegate_cmd(mock_run)
    assert cmd[cmd.index("--pr") + 1] == "3057"
    assert flag in cmd
    assert cmd.count("3057") == 1, f"PR number appeared twice: {cmd}"
    assert _probe_scripts(mock_run) == ["review-threads"]


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_review_takes_a_bare_pr_number(mock_resolve, mock_run):
    mock_resolve.return_value = make_ctx(pr_number=None, branch=None)
    _probe_real_delegates(mock_run)
    _run_main("review", "3057")
    cmd = _delegate_cmd(mock_run)
    assert cmd[0].endswith("/claude-review")
    assert cmd[cmd.index("--pr") + 1] == "3057"
    assert "--self" not in cmd
    assert _probe_scripts(mock_run) == ["claude-review"]


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_no_positional_candidate_skips_the_probe(mock_resolve, mock_run):
    """The common case must not pay for a delegate spawn."""
    mock_resolve.return_value = make_ctx()
    _probe_real_delegates(mock_run)
    _run_main("comments", "--triage")
    assert _probe_scripts(mock_run) == []


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_status_needs_no_delegate_to_classify(mock_resolve, mock_run, worktree,
                                              stub_state_dir):
    """`pr status` is internal, has no delegate, and takes no positional."""
    mock_resolve.return_value = make_ctx(worktree_root=worktree)
    _probe_real_delegates(mock_run)
    assert _run_main("--repo-dir", str(worktree), "status") == 0
    assert _probe_scripts(mock_run) == []


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_internal_command_still_classifies_a_positional(mock_resolve, mock_run,
                                                        worktree, stub_state_dir):
    """`pr fix 3057` has no delegate to ask, but 3057 is still the target."""
    mock_resolve.return_value = make_ctx(worktree_root=worktree, pr_number=3057)
    _probe_real_delegates(mock_run)
    with patch("pr_cli.pr_state.load_state", return_value=None):
        _run_main("--repo-dir", str(worktree), "fix", "3057")
    assert mock_resolve.call_args[1]["pr"] == "3057"
    assert _probe_scripts(mock_run) == []


# ── _positional_index ──────────────────────────────────────────────────────


def test_positional_index_skips_a_flag_value():
    extra = ["--reply", "3777767789", "--body-file", "/tmp/reply.md"]
    assert pr_cli._positional_index(extra, frozenset({"--reply", "--body-file"})) == -1


def test_positional_index_finds_a_target_after_a_boolean_flag():
    assert pr_cli._positional_index(["--triage", "3057"], frozenset({"--reply"})) == 1


def test_positional_index_treats_inline_values_as_self_contained():
    extra = ["--reply=1", "3057"]
    assert pr_cli._positional_index(extra, frozenset({"--reply"})) == 1


def test_positional_index_removes_the_token_it_identified():
    """Index, not value: a target that repeats a flag's value must not misfire."""
    extra = ["--reply", "3057", "--triage", "3057"]
    idx = pr_cli._positional_index(extra, frozenset({"--reply"}))
    assert idx == 3
    extra.pop(idx)
    assert extra == ["--reply", "3057", "--triage"]


def test_positional_index_without_arity_matches_the_historical_scan():
    assert pr_cli._positional_index(["--reply", "3057"], frozenset()) == 1


# ── _delegate_value_flags ──────────────────────────────────────────────────


def test_delegate_value_flags_reads_the_real_delegate():
    flags = pr_cli._delegate_value_flags(pr_cli._COMMANDS["comments"])
    assert {"--reply", "--body-file", "--track"} <= flags
    assert "--triage" not in flags
    assert "--fix" not in flags


def test_delegate_value_flags_is_empty_for_an_internal_command():
    assert pr_cli._delegate_value_flags(pr_cli._COMMANDS["fix"]) == frozenset()


def test_delegate_value_flags_degrades_when_the_delegate_is_missing():
    assert pr_cli._delegate_value_flags({"script": "no-such-delegate"}) == frozenset()


@patch("pr_cli.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1))
def test_delegate_value_flags_degrades_on_a_hung_delegate(_mock_run):
    assert pr_cli._delegate_value_flags({"script": "ci-check"}) == frozenset()


@patch("pr_cli.subprocess.run")
def test_delegate_value_flags_degrades_on_a_nonzero_exit(mock_run):
    mock_run.return_value = MagicMock(returncode=2, stdout="--reply\n", stderr="")
    assert pr_cli._delegate_value_flags({"script": "ci-check"}) == frozenset()


@patch("pr_cli.subprocess.run")
def test_delegate_value_flags_reprints_a_refusal(mock_run, capsys):
    """Degrading is silent misclassification, so the delegate's reason is surfaced."""
    mock_run.return_value = MagicMock(
        returncode=2, stdout="",
        stderr="ci-check: --value-flags: --track declares nargs='+'\n",
    )
    assert pr_cli._delegate_value_flags({"script": "ci-check"}) == frozenset()
    err = capsys.readouterr().err
    assert "ci-check --value-flags" in err
    assert "--track declares nargs='+'" in err


@patch("pr_cli.subprocess.run")
def test_delegate_value_flags_stays_quiet_when_the_probe_says_nothing(mock_run, capsys):
    mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="  \n")
    assert pr_cli._delegate_value_flags({"script": "ci-check"}) == frozenset()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "command",
    sorted(name for name, entry in pr_cli._COMMANDS.items() if entry.get("script")),
)
def test_every_delegate_answers_the_probe(command):
    """CI gate for the arity protocol: a flag it cannot describe fails here first.

    The refusal in tool_parser only reaches a human who happens to run the
    ambiguous form of the command, so this asserts the whole registry up front —
    adding an unsupported nargs to any delegate breaks the build, not a user.
    """
    script = str(BIN_DIR / pr_cli._COMMANDS[command]["script"])
    probe = _REAL_SUBPROCESS_RUN(
        [script, pr_cli.VALUE_FLAGS_FLAG],
        capture_output=True, text=True, timeout=pr_cli.VALUE_FLAGS_TIMEOUT,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.split(), f"{script} answered the probe with nothing"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_a_failed_probe_still_dispatches_the_command(mock_resolve, mock_run):
    """Introspection is best-effort: a broken probe must not fail the run."""
    mock_resolve.return_value = make_ctx(pr_number=3057)
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    with patch("pr_cli._delegate_value_flags", return_value=frozenset()):
        assert _run_main("comments", "--triage", "3057") == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-threads")
    assert "--triage" in cmd


# ── SIGINT handling ──────────────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_installs_sigint_handler(mock_resolve, mock_run):
    """main() installs a SIGINT handler so Ctrl+C exits cleanly without a traceback."""
    import signal
    mock_resolve.return_value = make_ctx()
    mock_run.return_value = MagicMock(returncode=0)
    original = signal.getsignal(signal.SIGINT)
    try:
        _run_main("--repo-dir", "/path", "rebase")
        handler = signal.getsignal(signal.SIGINT)
        assert handler is not original
        assert handler is not signal.SIG_DFL
        with pytest.raises(SystemExit) as exc_info:
            handler(None, None)
        assert exc_info.value.code == 130
    finally:
        signal.signal(signal.SIGINT, original)


# ── cmd_create ─────────────────────────────────────────────────────────────


class TestCmdCreate:
    """Tests for pr create subcommand."""

    @patch("pr_cli.subprocess.run")
    def test_create_delegates_to_task(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        ctx = make_ctx(pr_number=None)
        rc = pr_cli.cmd_create(["--no-issue", "--draft"], ctx)
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "task"
        assert "--global" in cmd
        assert "pr:create" in cmd
        assert "--" in cmd
        after_sep = cmd[cmd.index("--") + 1:]
        assert "--no-issue" in after_sep
        assert "--draft" in after_sep

    @patch("pr_cli.subprocess.run")
    def test_create_passes_repo_dir(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        ctx = make_ctx(worktree_root=Path("/tmp/my-worktree"), pr_number=None)
        pr_cli.cmd_create([], ctx)
        cmd = mock_run.call_args[0][0]
        assert "REPO_DIR=/tmp/my-worktree" in cmd

    @patch("pr_cli.subprocess.run")
    def test_create_returns_nonzero_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        ctx = make_ctx(pr_number=None)
        rc = pr_cli.cmd_create([], ctx)
        assert rc == 1

    @patch("pr_cli.subprocess.run")
    def test_create_no_args_still_delegates(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        ctx = make_ctx(pr_number=None)
        rc = pr_cli.cmd_create([], ctx)
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "pr:create" in cmd
        assert "--" not in cmd, "empty argv should not produce a -- separator"


# ── worktree_root guards ───────────────────────────────────────────────────


def test_cmd_status_without_a_worktree_exits_with_guidance(capsys):
    assert_no_worktree_exit(capsys, "feat/test", pr_cli.cmd_status,
                            [], make_ctx(worktree_root=None))


def test_cmd_fix_without_a_worktree_exits_with_guidance(capsys):
    assert_no_worktree_exit(capsys, "feat/test", pr_cli.cmd_fix,
                            [], make_ctx(worktree_root=None))


def test_review_state_lands_with_the_pr_not_the_caller(tmp_path):
    """A team review from a repo root must not clobber that root's own state."""
    import pr_context
    caller = tmp_path / "repo-root"
    caller.mkdir()
    target = tmp_path / "pr" / "widget-feat-login"
    ctx = pr_context.ResolvedContext(
        repo="acme/widget", branch="feat/login", pr_number=2973,
        worktree_root=caller, head_sha="pr-sha", current_branch="main",
        target_dir=target,
    )

    pr_cli._update_review_state(
        {"review_file": "r.md", "verdict": "approve", "head_sha": "pr-sha",
         "findings": {"total": 0}},
        ctx,
    )

    assert (target / pr_state.STATE_FILE).is_file()
    # Nothing at all under the caller's checkout: state is keyed on the run's
    # target now, so the caller's tree should not gain a state file anywhere.
    assert not list(caller.rglob(pr_state.STATE_FILE))
    written = pr_state.load_state(target)
    assert written.identity.pr_number == 2973
    assert written.identity.head_sha == "pr-sha"
    assert written.identity.worktree_root == str(caller)


# ── run lock wiring ─────────────────────────────────────────────────────────


def _lock_file(target_dir):
    return Path(target_dir) / run_lock.LOCK_FILE


@pytest.fixture
def stub_state_dir(worktree, monkeypatch):
    """Resolve the worktree's state dir without asking git.

    The tests that take this also mock `pr_cli.subprocess.run` — which is the
    subprocess module itself, so the resolver's own `git rev-parse` would be
    answered by that mock and the state dir would land wherever a MagicMock
    stringifies to. What they cover is the lock wiring, not the resolution.
    """
    path = worktree / ".git" / workbench_paths.WORKTREE_STATE_DIRNAME
    monkeypatch.setattr(workbench_paths, "worktree_state_dir", lambda root: path)
    return path


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_locks_the_target_for_a_mutating_command(
        mock_resolve, mock_run, worktree, stub_state_dir):
    """worktree_root and target_dir are different directories here on purpose:
    a lock keyed on worktree_root (the old bug) would land in the worktree, not
    in the target."""
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    mock_run.return_value = MagicMock(returncode=0)
    seen = {}
    mock_run.side_effect = lambda *a, **k: (
        seen.update(env=os.environ.get(run_lock.LOCK_ENV)),
        MagicMock(returncode=0),
    )[1]
    _run_main("--repo-dir", str(worktree), "comments")
    assert _lock_file(target).is_file()
    assert not _lock_file(worktree).exists()
    # The delegate has to inherit the marker, or it would deadlock on us.
    assert seen["env"] == str(target)


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_locks_a_bare_repo_run(mock_resolve, mock_run, tmp_path):
    """Regression: a bare repo (no worktree_root) used to skip the lock
    entirely via the old `if ctx.worktree_root:` guard. target_dir is never
    None, so a bare-repo run now takes a real lock like any other."""
    target = tmp_path / "target"
    mock_resolve.return_value = make_ctx(worktree_root=None, target_dir=target)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--repo-dir", "/nonexistent", "comments")
    assert _lock_file(target).is_file()


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_does_not_lock_for_status(mock_resolve, mock_run, worktree,
                                       stub_state_dir):
    """status is read-only, so it must never block on a run in flight."""
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--repo-dir", str(worktree), "status")
    assert not _lock_file(target).exists()


@patch("pr_cli.review_gc.prune_merged_targets", return_value=0)
@patch("pr_cli.review_gc.prune_merged_reviews", return_value=0)
@patch("pr_cli.review_gc.gc_reviews", return_value=0)
@patch("pr_cli.pr_context.resolve")
def test_main_locks_for_gc(mock_resolve, _gc, _prune, _prune_targets, worktree):
    """gc deletes the state directory, so it is not safe to run unlocked.

    Takes no stub_state_dir, unlike its neighbours: nothing here mocks
    `pr_cli.subprocess.run`, so the real `git rev-parse` answers for the
    worktree and the state dir resolves on its own.
    """
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    _run_main("--repo-dir", str(worktree), "gc")
    assert _lock_file(target).is_file()
    assert not _lock_file(worktree).exists()


@patch("pr_cli.review_gc.prune_merged_targets", return_value=0)
@patch("pr_cli.review_gc.prune_merged_reviews", return_value=0)
@patch("pr_cli.review_gc.gc_reviews", return_value=0)
@patch("pr_cli.pr_context.resolve")
def test_gc_skips_own_target_when_pruning(
        mock_resolve, _gc, _prune, mock_prune_targets, worktree):
    """cmd_gc must pass its own target as `skip` — gc holds that lock, so a
    prune that tried it would either deadlock or delete live state."""
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    _run_main("--repo-dir", str(worktree), "gc")
    assert mock_prune_targets.call_args.kwargs["skip"] == target


@patch("pr_cli.review_gc.prune_merged_targets", return_value=0)
@patch("pr_cli.review_gc.prune_merged_reviews", return_value=0)
@patch("pr_cli.review_gc.gc_reviews", return_value=0)
@patch("pr_cli.pr_context.resolve")
def test_gc_skips_legacy_sweep_from_a_bare_repo(
        mock_resolve, _gc, _prune, _prune_targets, tmp_path):
    """A bare repo has a target but no worktree_root — there is no worktree
    to sweep legacy artifacts out of."""
    target = tmp_path / "target"
    mock_resolve.return_value = make_ctx(worktree_root=None, target_dir=target)
    with patch("pr_cli._sweep_legacy_state") as mock_sweep:
        _run_main("--repo-dir", "/nonexistent", "gc")
    mock_sweep.assert_not_called()


@patch("pr_cli.pr_context.resolve")
def test_main_reports_contention_and_exits_1(mock_resolve, worktree, capsys):
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    busy = run_lock.LockBusy(
        {"pid": 15461, "command": "pr review --self --fix", "started": "t"}, target)

    with patch("pr_cli.run_lock.acquire", side_effect=busy):
        code = _run_main("--repo-dir", str(worktree), "comments")
    assert code == 1
    err = capsys.readouterr().err
    assert "pr review --self --fix" in err
    assert "15461" in err


def test_gc_sweeps_legacy_worktree_artifacts_but_keeps_the_trail(tmp_path):
    legacy = tmp_path / workbench_paths.LEGACY_WORKTREE_STATE_DIRNAME
    legacy.mkdir()
    (legacy / pr_state.STATE_FILE).write_text("{}")
    (legacy / run_lock.LOCK_FILE).write_text("{}")
    (legacy / "trail.jsonl").write_text('{"event":"x"}\n')

    assert pr_cli._sweep_legacy_state(tmp_path) == 1

    assert not (legacy / pr_state.STATE_FILE).exists()
    assert not (legacy / "run.lock").exists()
    assert (legacy / "trail.jsonl").is_file()


def test_gc_legacy_sweep_is_idempotent(tmp_path):
    legacy = tmp_path / workbench_paths.LEGACY_WORKTREE_STATE_DIRNAME
    legacy.mkdir()
    assert pr_cli._sweep_legacy_state(tmp_path) == 0
