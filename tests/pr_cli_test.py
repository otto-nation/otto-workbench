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
    expected = {"status", "ci", "review", "comments", "triage",
                "fix", "rebase", "repair", "post", "gc"}
    assert set(pr_cli._COMMANDS.keys()) == expected


def test_commands_registry_entries_have_help():
    for name, entry in pr_cli._COMMANDS.items():
        assert "help" in entry, f"{name} missing 'help'"
        assert isinstance(entry["help"], str)


def test_delegating_commands_have_script():
    delegating = {"ci", "review", "comments", "triage", "rebase",
                  "repair", "post", "gc"}
    for name in delegating:
        assert "script" in pr_cli._COMMANDS[name], f"{name} missing 'script'"


def test_internal_commands_have_no_script():
    internal = {"status", "fix"}
    for name in internal:
        assert "script" not in pr_cli._COMMANDS[name], f"{name} should not have 'script'"


def test_sub_command_prefix():
    assert pr_cli._COMMANDS["gc"].get("prefix") == ["gc"]
    assert pr_cli._COMMANDS["post"].get("prefix") == ["post"]


# ── _run_delegate ─────────────────────────────────────────────────────────


def _make_ctx(**overrides):
    """Build a minimal ResolvedContext for testing."""
    import pr_context
    defaults = dict(repo="owner/repo", branch="feat/test",
                    pr_number=42, worktree_root=Path("/wt"), head_sha="abc123")
    defaults.update(overrides)
    return pr_context.ResolvedContext(**defaults)


@patch("subprocess.run")
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


@patch("subprocess.run")
def test_run_delegate_includes_prefix(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    entry = {"script": "claude-review", "prefix": ["gc"], "help": "x"}
    pr_cli._run_delegate(entry, [], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd[0].endswith("/claude-review")
    assert cmd[1] == "gc"


@patch("subprocess.run")
def test_run_delegate_passes_argv_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    entry = {"script": "pr-rebase", "help": "x"}
    pr_cli._run_delegate(entry, ["--fix", "--push", "--unknown-future-flag"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--push" in cmd
    assert "--unknown-future-flag" in cmd


@patch("subprocess.run")
def test_run_delegate_returns_exit_code(mock_run):
    mock_run.return_value = MagicMock(returncode=3)
    ctx = _make_ctx()
    entry = {"script": "pr-rebase", "help": "x"}
    rc = pr_cli._run_delegate(entry, [], ctx)
    assert rc == 3


# ── cmd_review auto-self ──────────────────────────────────────────────────


@patch("subprocess.run")
def test_cmd_review_injects_self_when_no_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review([], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" in cmd


@patch("subprocess.run")
def test_cmd_review_no_self_when_pr_number(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["123"], ctx)
    cmd = mock_run.call_args[0][0]
    self_count = cmd.count("--self")
    assert self_count == 0


@patch("subprocess.run")
def test_cmd_review_no_self_when_pr_url(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["https://github.com/owner/repo/pull/99"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--self" not in cmd


@patch("subprocess.run")
def test_cmd_review_no_double_self(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["--self"], ctx)
    cmd = mock_run.call_args[0][0]
    assert cmd.count("--self") == 1


@patch("subprocess.run")
def test_cmd_review_passes_flags_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx()
    pr_cli.cmd_review(["--self", "--fix", "--no-post"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd
    assert "--no-post" in cmd


# ── cmd_post PR fallback ─────────────────────────────────────────────────


@patch("subprocess.run")
def test_cmd_post_injects_pr_number_when_no_target(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=42)
    pr_cli.cmd_post([], ctx)
    cmd = mock_run.call_args[0][0]
    assert "42" in cmd


@patch("subprocess.run")
def test_cmd_post_no_inject_when_target_given(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=42)
    pr_cli.cmd_post(["99"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "99" in cmd
    # 42 should not appear as an extra positional
    positionals = [a for a in cmd if not a.startswith("-") and a != "post"
                   and not a.endswith("claude-review")]
    assert "42" not in positionals


@patch("subprocess.run")
def test_cmd_post_passes_flags_through(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    ctx = _make_ctx(pr_number=42)
    pr_cli.cmd_post(["99", "--submit"], ctx)
    cmd = mock_run.call_args[0][0]
    assert "--submit" in cmd
