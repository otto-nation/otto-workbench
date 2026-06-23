"""Tests for pr CLI helper functions."""

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless pr script via importlib
_pr_path = str(BIN_DIR / "pr")
_loader = importlib.machinery.SourceFileLoader("pr_cli", _pr_path)
_spec = importlib.util.spec_from_loader("pr_cli", _loader, origin=_pr_path)
pr_cli = importlib.util.module_from_spec(_spec)
pr_cli.__file__ = _pr_path
_spec.loader.exec_module(pr_cli)
# Register so @patch("pr_cli.subprocess.run") can resolve the module
sys.modules.setdefault("pr_cli", pr_cli)


# ── _parse_review_summary ──────────────────────────────────────────────────


def test_parse_review_summary_valid():
    output = 'REVIEW_SUMMARY:{"repo":"owner/repo","verdict":"approve","findings":{"M":0,"S":1,"total":1}}'
    result = pr_cli._parse_review_summary(output)
    assert result["verdict"] == "approve"
    assert result["findings"]["S"] == 1


def test_parse_review_summary_multiline():
    output = "Some output\nMore output\nREVIEW_SUMMARY:{\"verdict\":\"changes_requested\"}\nTrailing"
    result = pr_cli._parse_review_summary(output)
    assert result["verdict"] == "changes_requested"


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
    pr_state.update_ci(state, pr_state.CISummary(conclusion="success", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={"S": 1}, verdict="approve", updated_at="t",
    ))
    pr_state.update_comments(state, pr_state.CommentsSummary(
        blocking_reviewers=[], updated_at="t",
    ))
    result = pr_cli._merge_readiness(state)
    assert "ready" in result.lower()


def test_merge_readiness_ci_failing():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci(state, pr_state.CISummary(conclusion="failure", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(updated_at="t"))
    pr_state.update_comments(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "CI failing" in result


def test_merge_readiness_must_fix():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci(state, pr_state.CISummary(conclusion="success", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={"M": 2}, updated_at="t",
    ))
    pr_state.update_comments(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "must-fix" in result


def test_merge_readiness_not_checked():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    result = pr_cli._merge_readiness(state)
    assert "not checked" in result


# ── _render_triage_section ─────────────────────────────────────────────────


def test_render_triage_section_not_run():
    import pr_state
    t = pr_state.TriageSummary()
    result = pr_cli._render_triage_section(t)
    assert result == ["**Triage**: not run yet"]


def test_render_triage_section_with_data():
    import pr_state
    t = pr_state.TriageSummary(
        total=5, actionable=2, valid=1, questions=1, updated_at="2024-01-01T00:00:00Z",
    )
    result = pr_cli._render_triage_section(t)
    assert len(result) == 1
    assert "5 threads" in result[0]
    assert "2 actionable" in result[0]
    assert "1 valid" in result[0]
    assert "1 questions" in result[0]


# ── _render_rebase_section ────────────────────────────────────────────────


def test_render_rebase_section_not_run():
    import pr_state
    r = pr_state.RebaseSummary()
    result = pr_cli._render_rebase_section(r)
    assert result == ["**Rebase**: not run yet"]


def test_render_rebase_section_with_data():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert len(result) >= 1
    assert "2 file(s)" in result[0]
    assert "3 commit(s)" in result[0]
    assert "force-pushed" in result[0]


def test_render_rebase_section_no_conflicts():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=5,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert len(result) >= 1
    assert "clean" in result[0].lower()


def test_render_rebase_section_not_pushed():
    import pr_state
    r = pr_state.RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["a.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00Z",
    )
    result = pr_cli._render_rebase_section(r)
    assert "force-pushed" not in result[0]


# ── _COMMANDS registry ────────────────────────────────────────────────────


def test_commands_registry_exists():
    """Registry dict drives all subcommand registration."""
    assert hasattr(pr_cli, "_COMMANDS")
    assert isinstance(pr_cli._COMMANDS, dict)


def test_commands_registry_has_all_subcommands():
    # Keep this set in sync with _COMMANDS in ai/claude/bin/pr
    expected = {"status", "ci", "review", "comments", "triage",
                "fix", "rebase", "repair", "post", "gc"}
    assert set(pr_cli._COMMANDS.keys()) == expected


def test_commands_registry_entries_have_help():
    for name, entry in pr_cli._COMMANDS.items():
        assert "help" in entry, f"{name} missing 'help'"
        assert isinstance(entry["help"], str)


def test_commands_with_script_key():
    """Commands backed by an external script carry a 'script' key."""
    has_script = {"ci", "review", "comments", "triage", "rebase",
                  "repair", "post", "gc"}
    for name in has_script:
        assert "script" in pr_cli._COMMANDS[name], f"{name} missing 'script'"


def test_custom_handlers_are_registered():
    """_CUSTOM contains the expected non-pure-delegate commands."""
    expected_custom = {"status", "review", "fix", "repair", "post"}
    assert set(pr_cli._CUSTOM.keys()) == expected_custom


def test_internal_commands_have_no_script():
    internal = {"status", "fix"}
    for name in internal:
        assert "script" not in pr_cli._COMMANDS[name], f"{name} should not have 'script'"


def test_sub_command_prefix():
    assert pr_cli._COMMANDS["gc"].get("prefix") == ["gc"]
    assert pr_cli._COMMANDS["post"].get("prefix") == ["post"]


# ── help passthrough ─────────────────────────────────────────────────────


def _run_main(*argv):
    """Run pr_cli.main() with the given argv, catching SystemExit."""
    with patch("sys.argv", ["pr"] + list(argv)):
        try:
            pr_cli.main()
        except SystemExit as e:
            return e.code
    return None


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_global_flags_after_subcommand(mock_resolve, mock_run):
    """Global flags like --repo-dir work after the subcommand name."""
    mock_resolve.return_value = _make_ctx()
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("rebase", "--repo-dir", "/some/path")
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args[1]
    assert call_kwargs["repo_dir"] == "/some/path"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_global_flags_before_subcommand(mock_resolve, mock_run):
    """Global flags also work before the subcommand name."""
    mock_resolve.return_value = _make_ctx()
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--repo-dir", "/some/path", "rebase")
    mock_resolve.assert_called_once()
    call_kwargs = mock_resolve.call_args[1]
    assert call_kwargs["repo_dir"] == "/some/path"


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_global_flags_mixed_with_subcommand_flags(mock_resolve, mock_run):
    """--repo-dir after subcommand doesn't swallow subcommand-specific flags."""
    mock_resolve.return_value = _make_ctx()
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


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_help_passthrough_includes_prefix(mock_resolve, mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    rc = _run_main("gc", "--help")
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/claude-review")
    assert cmd[1] == "gc"
    assert cmd[2] == "--help"


# ── _run_delegate ─────────────────────────────────────────────────────────


def _make_ctx(**overrides):
    """Build a minimal ResolvedContext for testing."""
    import pr_context
    defaults = dict(repo="owner/repo", branch="feat/test",
                    pr_number=42, worktree_root=Path("/wt"), head_sha="abc123")
    defaults.update(overrides)
    return pr_context.ResolvedContext(**defaults)


@patch("pr_cli.subprocess.run")
def test_run_delegate_builds_command(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
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
    ctx = _make_ctx()
    entry = {"script": "claude-review", "prefix": ["gc"], "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/claude-review")
    assert cmd[1] == "gc"


@patch("pr_cli.subprocess.run")
def test_run_delegate_passes_argv_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    entry = {"script": "pr-rebase", "help": "x"}
    pr_cli._run_delegate(entry, ["--fix", "--push", "--unknown-future-flag"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--push" in cmd
    assert "--unknown-future-flag" in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_returns_exit_code(mock_run):
    mock_run.return_value = MagicMock(returncode=3)
    ctx = _make_ctx()
    entry = {"script": "pr-rebase", "help": "x"}
    rc = pr_cli._run_delegate(entry, [], ctx)
    assert rc == 3


# ── cmd_review auto-self ──────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_cmd_review_injects_self_when_no_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review([], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_pr_number(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["123"], ctx)
    cmd = mock_run.call_args[0][0]
    self_count = cmd.count("--self")
    assert self_count == 0


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_pr_url(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["https://github.com/owner/repo/pull/99"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_double_self(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["--self"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd.count("--self") == 1


@patch("pr_cli.subprocess.run")
def test_cmd_review_passes_flags_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["--self", "--fix", "--no-post"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--no-post" in cmd


# ── cmd_post PR fallback ─────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_cmd_post_injects_pr_number_when_no_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=42)
    pr_cli.cmd_post([], ctx)
    cmd = mock_run.call_args[0][0]
    assert "42" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_post_no_inject_when_target_given(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=42)
    pr_cli.cmd_post(["99"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "99" in cmd
    # 42 must not appear as a standalone positional target; only allowed as --pr value
    for i, a in enumerate(cmd):
        if a == "42":
            assert i > 0 and cmd[i - 1] == "--pr", f"'42' appears as positional at index {i}"


@patch("pr_cli.subprocess.run")
def test_cmd_post_passes_flags_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=42)
    pr_cli.cmd_post(["99", "--submit"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--submit" in cmd


# ── _run_delegate branch/pr injection ────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_run_delegate_forwards_only_original_pr(mock_run):
    """When the user provided --pr, only --pr is forwarded (not --branch)."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(branch="feat/my-feature", pr_number=99)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx, original_pr="99")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "99"
    assert "--branch" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_forwards_only_original_branch(mock_run):
    """When the user provided --branch, only --branch is forwarded (not --pr)."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(branch="feat/my-feature", pr_number=99)
    entry = {"script": "review-threads", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx, original_branch="feat/my-feature")
    cmd = mock_run.call_args[0][0]
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/my-feature"
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_auto_detected_forwards_branch_only(mock_run):
    """When neither --pr nor --branch was given, forward branch only."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(branch="feat/my-feature", pr_number=99)
    entry = {"script": "review-thread-triage", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/my-feature"
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
def test_run_delegate_omits_branch_when_none(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(branch="", pr_number=None)
    entry = {"script": "ci-check", "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--branch" not in cmd
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_pr_flag_does_not_pass_both_to_delegate(mock_resolve, mock_run):
    """Regression: pr --pr 1927 comments must not pass both --branch and --pr."""
    mock_resolve.return_value = _make_ctx(branch="feat/derived", pr_number=1927)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--pr", "1927", "--repo-dir", "/path", "comments")
    cmd = mock_run.call_args[0][0]
    assert "--pr" in cmd
    assert cmd[cmd.index("--pr") + 1] == "1927"
    assert "--branch" not in cmd


# ── cmd_repair ──────────────────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
def test_cmd_repair_summary_succeeds(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    ctx = _make_ctx(pr_number=42)
    rc = pr_cli.cmd_repair([], ctx)
    assert rc == 0
    assert mock_run.call_count == 1
    cmd = mock_run.call_args[0][0]
    assert "summary" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_repair_falls_back_to_rebuild(mock_run):
    mock_run.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="summary error"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]
    ctx = _make_ctx(pr_number=42)
    rc = pr_cli.cmd_repair([], ctx)
    assert rc == 0
    assert mock_run.call_count == 2
    rebuild_cmd = mock_run.call_args_list[1][0][0]
    assert "rebuild" in rebuild_cmd


@patch("pr_cli.subprocess.run")
def test_cmd_repair_both_fail(mock_run):
    mock_run.side_effect = [
        MagicMock(returncode=1, stdout="", stderr=""),
        MagicMock(returncode=2, stdout="", stderr=""),
    ]
    ctx = _make_ctx(pr_number=42)
    rc = pr_cli.cmd_repair([], ctx)
    assert rc == 2


@patch("pr_cli.subprocess.run")
def test_cmd_repair_uses_argv_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    ctx = _make_ctx(pr_number=None)
    rc = pr_cli.cmd_repair(["99"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert "99" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_repair_uses_ctx_pr_when_no_argv_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    ctx = _make_ctx(pr_number=55)
    rc = pr_cli.cmd_repair([], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert "55" in cmd


# ── cmd_fix ─────────────────────────────────────────────────────────────────


@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_no_state_returns_error(mock_load):
    mock_load.return_value = None
    ctx = _make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 1


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_dispatches_review_when_findings(mock_load, mock_run):
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={"M": 1}, verdict="changes_requested", updated_at="t",
    ))
    mock_load.return_value = state
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 0
    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "--self" in cmd
    assert "--fix" in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_state.load_state")
def test_cmd_fix_skips_review_when_no_findings(mock_load, mock_run):
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={}, verdict="approve", updated_at="t",
    ))
    mock_load.return_value = state
    ctx = _make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 0
    assert not mock_run.called
