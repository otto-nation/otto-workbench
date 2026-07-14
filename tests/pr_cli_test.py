"""Tests for pr CLI helper functions."""

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

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

import pr_state  # noqa: E402


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
    pr_state.update_ci_domain(state, pr_state.CIDomain(conclusion="success", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(
        finding_counts={"S": 1}, verdict=pr_state.ReviewVerdict.APPROVE.value, updated_at="t",
    ))
    pr_state.update_comments(state, pr_state.CommentsSummary(
        blocking_reviewers=[], updated_at="t",
    ))
    result = pr_cli._merge_readiness(state)
    assert "ready" in result.lower()


def test_merge_readiness_ci_failing():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci_domain(state, pr_state.CIDomain(conclusion="failure", updated_at="t"))
    pr_state.update_review(state, pr_state.ReviewSummary(updated_at="t"))
    pr_state.update_comments(state, pr_state.CommentsSummary(updated_at="t"))
    result = pr_cli._merge_readiness(state)
    assert "CI failing" in result


def test_merge_readiness_must_fix():
    import pr_state
    state = pr_state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr_state.update_ci_domain(state, pr_state.CIDomain(conclusion="success", updated_at="t"))
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
    expected = {"status", "ci", "review", "comments",
                "fix", "rebase", "gc"}
    assert set(pr_cli._COMMANDS.keys()) == expected


def test_commands_registry_entries_have_help():
    for name, entry in pr_cli._COMMANDS.items():
        assert "help" in entry, f"{name} missing 'help'"
        assert isinstance(entry["help"], str)


def test_commands_with_script_key():
    """Commands backed by an external script carry a 'script' key."""
    has_script = {"ci", "review", "comments", "rebase"}
    for name in has_script:
        assert "script" in pr_cli._COMMANDS[name], f"{name} missing 'script'"


def test_custom_handlers_are_registered():
    """_CUSTOM contains the expected non-pure-delegate commands."""
    expected_custom = {"status", "review", "comments", "fix", "gc"}
    assert set(pr_cli._CUSTOM.keys()) == expected_custom


def test_internal_commands_have_no_script():
    internal = {"status", "fix", "gc"}
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
    ctx = _make_ctx(pr_number=None)
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
def test_cmd_review_no_self_when_original_pr(mock_run):
    """--pr consumed by global parser still prevents --self injection."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review([], ctx, original_pr="1206")
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_no_self_when_ctx_has_pr(mock_run):
    """Auto-detected PR number in context prevents --self injection."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=99)
    pr_cli.cmd_review([], ctx)
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
def test_cmd_review_no_self_when_branch_positional(mock_run):
    """A branch name positional should not trigger --self injection."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=None)
    pr_cli.cmd_review(["kgn/go-update"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd
    assert "kgn/go-update" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_review_passes_flags_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["--self", "--fix", "--no-post"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--no-post" in cmd


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
        ctx = _make_ctx(pr_number=42)
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
        ctx = _make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--post", "--submit"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert "--submit" in cmd


def test_cmd_review_post_fails_without_review_file(tmp_path):
    import review_common
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()
    with patch.object(review_common, "REVIEWS_DIR", reviews_dir):
        ctx = _make_ctx(pr_number=42)
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
        ctx = _make_ctx(pr_number=42)
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
def test_run_delegate_auto_detected_forwards_pr(mock_run):
    """When neither flag was given and ctx has a PR, forward --pr (not --branch)."""
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(branch="feat/my-feature", pr_number=99)
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
    ctx = _make_ctx(branch="feat/my-feature", pr_number=None)
    entry = {"script": "review-threads", "help": "x"}
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


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_branch_flag_does_not_pass_both_to_delegate(mock_resolve, mock_run):
    """pr --branch feat/foo comments must forward only --branch, not --pr."""
    mock_resolve.return_value = _make_ctx(branch="feat/foo", pr_number=42)
    mock_run.return_value = MagicMock(returncode=0)
    _run_main("--branch", "feat/foo", "--repo-dir", "/path", "comments")
    cmd = mock_run.call_args[0][0]
    assert "--branch" in cmd
    assert cmd[cmd.index("--branch") + 1] == "feat/foo"
    assert "--pr" not in cmd


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_auto_detected_forwards_pr_only(mock_resolve, mock_run):
    """Bare 'pr comments' (no flags) forwards auto-detected --pr, not --branch."""
    mock_resolve.return_value = _make_ctx(branch="feat/derived", pr_number=42)
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
    ctx = _make_ctx()
    pr_cli.cmd_comments([], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-threads")
    assert "--triage" not in cmd
    assert "--resolve" not in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_comments_triage_passes_flag(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_comments(["--triage"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-threads")
    assert "--triage" in cmd


@patch("pr_cli.subprocess.run")
def test_cmd_comments_resolve_passes_flag(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
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
        ctx = _make_ctx(pr_number=42)
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
        ctx = _make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--repair"], ctx)
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/review-rebuild")


def test_cmd_review_repair_no_pr_fails():
    ctx = _make_ctx(pr_number=None)
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
        ctx = _make_ctx(pr_number=42)
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
        ctx = _make_ctx(pr_number=42)
        rc = pr_cli.cmd_review(["--summary"], ctx)
    assert rc == 1


# ── cmd_review mutual exclusivity ─────────────────────────────────────────


def test_cmd_review_mutual_exclusivity():
    ctx = _make_ctx()
    rc = pr_cli.cmd_review(["--post", "--repair"], ctx)
    assert rc == 1


def test_cmd_review_mutual_exclusivity_three():
    ctx = _make_ctx()
    rc = pr_cli.cmd_review(["--post", "--repair", "--summary"], ctx)
    assert rc == 1


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
        finding_counts={"M": 1}, verdict=pr_state.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
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
        finding_counts={}, verdict=pr_state.ReviewVerdict.APPROVE.value, updated_at="t",
    ))
    mock_load.return_value = state
    ctx = _make_ctx()
    rc = pr_cli.cmd_fix([], ctx)
    assert rc == 0
    assert not mock_run.called


# ── _render_review_section ────────────────────────────────────────────────────


def test_render_review_section_error_status():
    import pr_state
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.APPROVE.value,
        status=pr_state.ReviewStatus.ERROR.value, updated_at="t",
    )
    lines = pr_cli._render_review_section(rev)
    assert "[ERROR]" in lines[0]


def test_render_review_section_completed_status():
    import pr_state
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.APPROVE.value,
        status=pr_state.ReviewStatus.COMPLETED.value, updated_at="t",
    )
    lines = pr_cli._render_review_section(rev)
    assert "[ERROR]" not in lines[0]


def test_render_review_section_empty_status():
    import pr_state
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.APPROVE.value, updated_at="t",
    )
    lines = pr_cli._render_review_section(rev)
    assert "[ERROR]" not in lines[0]


def test_render_review_section_disapprove_verdict():
    import pr_state
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.DISAPPROVE.value,
        updated_at="t",
    )
    lines = pr_cli._render_review_section(rev)
    assert "[DISAPPROVED]" in lines[0]


def test_render_review_section_disapprove_and_error():
    import pr_state
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.DISAPPROVE.value,
        status=pr_state.ReviewStatus.ERROR.value, updated_at="t",
    )
    lines = pr_cli._render_review_section(rev)
    assert "[ERROR]" in lines[0]
    assert "[DISAPPROVED]" in lines[0]


# ── SIGINT handling ──────────────────────────────────────────────────────────


@patch("pr_cli.subprocess.run")
@patch("pr_cli.pr_context.resolve")
def test_main_installs_sigint_handler(mock_resolve, mock_run):
    """main() installs a SIGINT handler so Ctrl+C exits cleanly without a traceback."""
    import signal
    mock_resolve.return_value = _make_ctx()
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
