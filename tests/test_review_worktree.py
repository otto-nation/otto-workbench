"""Tests for review_worktree library."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.worktree import (
    WorktreeResult, cleanup_self_review_worktree, cleanup_worktree,
    detached_worktree_at, find_repo_root, resolve_branch_input, resolve_wt_path,
    setup_pr_worktree, switch_to_branch, switch_to_pr_branch,
)


# ── WorktreeResult ────────────────────────────────────────────────────────────


def test_worktree_result_is_frozen():
    r = WorktreeResult(path="/tmp/wt", cleanup_ref="feat/auth", is_fallback=False)
    with pytest.raises(AttributeError):
        r.path = "/other"


def test_worktree_result_fields():
    r = WorktreeResult(path="/tmp/wt", cleanup_ref="pr-42-review", is_fallback=True)
    assert r.path == "/tmp/wt"
    assert r.cleanup_ref == "pr-42-review"
    assert r.is_fallback is True


# ── setup_pr_worktree ─────────────────────────────────────────────────────────


@patch("core.proc.subprocess.run")
def test_setup_pr_worktree_via_wt(mock_run):
    wt_json = json.dumps({"path": "/repos/repo/pr-42", "branch": "pr:42"})

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "reset" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = setup_pr_worktree("owner/repo", 42, "/repos/repo")

    assert result.path == "/repos/repo/pr-42"
    assert result.is_fallback is False
    assert result.cleanup_ref == "pr:42"


@patch("core.proc.subprocess.run")
def test_setup_pr_worktree_fallback_when_wt_fails(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd and "pull/42/head" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = setup_pr_worktree("owner/repo", 42, "/repos/repo")

    assert result.path == "/repos/repo/.worktrees/pr-42-review"
    assert result.is_fallback is True
    assert result.cleanup_ref == "/repos/repo/.worktrees/pr-42-review"


@patch("core.proc.subprocess.run")
def test_setup_pr_worktree_unshallows_if_needed(mock_run):
    calls_made = []

    def side_effect(cmd, **kwargs):
        calls_made.append(cmd)
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "true\n"
            return m
        if cmd[0] == "git" and "--unshallow" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            wt_json = json.dumps({"path": "/repos/repo/pr-7", "branch": "pr:7"})
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "reset" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    setup_pr_worktree("owner/repo", 7, "/repos/repo")

    unshallow_calls = [c for c in calls_made if "git" in c[0] and "--unshallow" in c]
    assert len(unshallow_calls) == 1


@patch("review.worktree.pr_sync.fetch_and_reset")
@patch("core.proc.subprocess.run")
def test_setup_pr_worktree_fetches_and_resets_on_wt_success(mock_run, mock_far):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            wt_json = json.dumps({"path": "/repos/repo/pr-10", "branch": "feat/thing"})
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = setup_pr_worktree("owner/repo", 10, "/repos/repo", pr_head="feat/thing")

    mock_far.assert_called_once_with("/repos/repo/pr-10", "feat/thing")


@patch("core.proc.subprocess.run")
def test_setup_pr_worktree_raises_on_total_failure(mock_run):
    def side_effect(cmd, **kwargs):
        # Both streams are strings, as a real CompletedProcess carries them:
        # the failure path quotes what git said, and a MagicMock there is not
        # something `str.join` can render.
        m = MagicMock(stdout="", stderr="")
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd and "pull/" in str(cmd):
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 1
            m.stderr = "worktree add failed"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect

    with pytest.raises(RuntimeError, match="Failed to create worktree"):
        setup_pr_worktree("owner/repo", 42, "/repos/repo")


@patch("core.proc.subprocess.run")
def test_setup_pr_worktree_quotes_what_git_said(mock_run):
    """The raise names the cause, rather than only the step that failed.

    `check=True` gave the caller a CalledProcessError whose message is the
    command line, so the operator saw that a fetch failed and never why.
    """
    def side_effect(cmd, **kwargs):
        m = MagicMock(stdout="", stderr="fatal: couldn't find remote ref pull/42/head")
        if cmd[0] == "git" and "--is-shallow-repository" in cmd:
            m.returncode = 0
            m.stdout = "false\n"
            return m
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        m.returncode = 1
        return m

    mock_run.side_effect = side_effect

    with pytest.raises(RuntimeError, match="couldn't find remote ref"):
        setup_pr_worktree("owner/repo", 42, "/repos/repo")


# ── detached_worktree_at ──────────────────────────────────────────────────────


def _detach_side_effect(local_shas: list[str], fetchable: str = "", add_rc: int = 0):
    """Simulate a repo that holds *local_shas* and can fetch *fetchable*."""
    calls = []

    def side_effect(cmd, **kwargs):
        calls.append(cmd)
        m = MagicMock()
        m.stdout = ""
        if "cat-file" in cmd:
            m.returncode = 0 if any(s in cmd[-1] for s in local_shas) else 1
        elif "fetch" in cmd:
            m.returncode = 0
            if fetchable and fetchable in cmd:
                local_shas.append(fetchable)
        elif "worktree" in cmd and "add" in cmd:
            m.returncode = add_rc
        else:
            m.returncode = 0
        return m

    return side_effect, calls


@patch("core.proc.subprocess.run")
def test_detached_worktree_at_checks_out_a_local_commit(mock_run):
    mock_run.side_effect, calls = _detach_side_effect(["abc1234"])

    result = detached_worktree_at("abc1234", "/repos/repo", "recover-pr-42")

    assert result == WorktreeResult(
        path="/repos/repo/.worktrees/recover-pr-42",
        cleanup_ref="/repos/repo/.worktrees/recover-pr-42",
        is_fallback=True,
    )
    assert not [c for c in calls if "fetch" in c]


@patch("core.proc.subprocess.run")
def test_detached_worktree_at_sanitizes_the_label(mock_run):
    mock_run.side_effect, _ = _detach_side_effect(["abc1234"])

    result = detached_worktree_at("abc1234", "/repos/repo", "recover-feat/x")

    assert result is not None
    assert result.path == "/repos/repo/.worktrees/recover-feat-x"


@patch("core.proc.subprocess.run")
def test_detached_worktree_at_fetches_a_missing_commit(mock_run):
    mock_run.side_effect, calls = _detach_side_effect([], fetchable="abc1234")

    result = detached_worktree_at("abc1234", "/repos/repo", "recover-pr-42")

    assert result is not None
    assert [c for c in calls if "fetch" in c] == [
        ["git", "fetch", "origin", "abc1234"]
    ]


@patch("core.proc.subprocess.run")
def test_detached_worktree_at_returns_none_when_unfetchable(mock_run):
    mock_run.side_effect, calls = _detach_side_effect([])

    assert detached_worktree_at("abc1234", "/repos/repo", "recover-pr-42") is None
    assert not [c for c in calls if "worktree" in c and "add" in c]


@patch("core.proc.subprocess.run")
def test_detached_worktree_at_returns_none_when_add_fails(mock_run):
    mock_run.side_effect, _ = _detach_side_effect(["abc1234"], add_rc=1)

    assert detached_worktree_at("abc1234", "/repos/repo", "recover-pr-42") is None


# ── switch_to_branch ──────────────────────────────────────────────────────────


@patch("core.proc.subprocess.run")
def test_switch_to_branch_via_wt(mock_run):
    wt_json = json.dumps({"path": "/repos/repo/feat-auth"})

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            m.returncode = 0
            m.stdout = wt_json + "\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("feat/auth", "/repos/repo")

    assert result is not None
    assert result.path == "/repos/repo/feat-auth"
    assert result.is_fallback is False
    assert result.cleanup_ref == "feat/auth"


@patch("core.proc.subprocess.run")
def test_switch_to_branch_fallback(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "rev-parse" in cmd:
            m.returncode = 0
            m.stdout = "/repos/repo"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("main", "/repos/repo")

    assert result is not None
    assert result.path == "/repos/repo/self-review-main"
    assert result.is_fallback is True
    assert result.cleanup_ref == "/repos/repo/self-review-main"


@patch("core.proc.subprocess.run")
def test_switch_to_branch_sanitizes_slashes(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 0
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("feat/auth", "/repos/repo")

    assert result is not None
    assert result.path == "/repos/repo/self-review-feat-auth"


@patch("core.proc.subprocess.run")
def test_switch_to_branch_returns_none_on_total_failure(mock_run):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "wt" and "switch" in cmd:
            raise FileNotFoundError("wt not found")
        if cmd[0] == "git" and "fetch" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "remove" in cmd:
            m.returncode = 0
            return m
        if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
            m.returncode = 1
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_branch("feat/auth", "/repos/repo")

    assert result is None


# ── switch_to_pr_branch ───────────────────────────────────────────────────────


@patch("review.worktree.switch_to_branch")
@patch("core.proc.subprocess.run")
def test_switch_to_pr_branch_delegates(mock_run, mock_switch):
    expected_result = WorktreeResult(path="/repos/repo/feat-x", cleanup_ref="feat/x", is_fallback=False)
    mock_switch.return_value = expected_result

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "gh":
            m.returncode = 0
            m.stdout = '{"headRefName": "feat/x"}'
            return m
        if cmd[0] == "git" and "rev-parse" in cmd:
            m.returncode = 0
            m.stdout = "main\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_pr_branch(42, "owner/repo", "/repos/repo")

    assert result == expected_result
    mock_switch.assert_called_once_with("feat/x", "/repos/repo")


@patch("review.worktree.switch_to_branch")
@patch("core.proc.subprocess.run")
def test_switch_to_pr_branch_already_on_branch(mock_run, mock_switch):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "gh":
            m.returncode = 0
            m.stdout = '{"headRefName": "feat/x"}'
            return m
        if cmd[0] == "git" and "rev-parse" in cmd:
            m.returncode = 0
            m.stdout = "feat/x\n"
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_pr_branch(42, "owner/repo", "/repos/repo")

    assert result is None
    mock_switch.assert_not_called()


@patch("review.worktree.switch_to_branch")
@patch("core.proc.subprocess.run")
def test_switch_to_pr_branch_returns_none_when_pr_head_unknown(mock_run, mock_switch):
    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if cmd[0] == "gh":
            m.returncode = 1
            m.stdout = ""
            return m
        m.returncode = 0
        m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    result = switch_to_pr_branch(42, "owner/repo", "/repos/repo")

    assert result is None
    mock_switch.assert_not_called()


# ── cleanup_worktree ──────────────────────────────────────────────────────────


@patch("core.proc.subprocess.run")
def test_cleanup_worktree_fallback_uses_git_remove(mock_run):
    mock_run.return_value.returncode = 0
    result = WorktreeResult(
        path="/repos/repo/.worktrees/pr-42-review",
        cleanup_ref="/repos/repo/.worktrees/pr-42-review",
        is_fallback=True,
    )
    cleanup_worktree(result, "/repos/repo")

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "git" in cmd[0]
    assert "worktree" in cmd
    assert "remove" in cmd
    assert "--force" in cmd
    assert result.path in cmd


@patch("core.proc.subprocess.run")
def test_cleanup_worktree_skips_non_fallback(mock_run):
    result = WorktreeResult(
        path="/repos/repo/pr-42",
        cleanup_ref="pr:42",
        is_fallback=False,
    )
    cleanup_worktree(result, "/repos/repo")

    mock_run.assert_not_called()


@patch("core.proc.subprocess.run")
def test_cleanup_worktree_none_is_noop(mock_run):
    cleanup_worktree(None, "/repos/repo")
    mock_run.assert_not_called()


@patch("review.worktree.git_client.run")
def test_cleanup_worktree_fallback_swallows_errors(mock_run):
    """A git that cannot even be launched must not replace the review's error."""
    mock_run.side_effect = OSError("git not found")
    result = WorktreeResult(
        path="/repos/repo/.worktrees/pr-42-review",
        cleanup_ref="/repos/repo/.worktrees/pr-42-review",
        is_fallback=True,
    )
    cleanup_worktree(result, "/repos/repo")


# ── cleanup_self_review_worktree ──────────────────────────────────────────────


def test_cleanup_self_review_worktree_uses_explicit_repo_dir(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "review.worktree.cleanup_worktree",
        lambda result, repo_dir: seen.append((result, repo_dir)),
    )
    wt = WorktreeResult(path="/tmp/wt", cleanup_ref="x", is_fallback=True)
    cleanup_self_review_worktree(wt, "/explicit")
    assert seen == [(wt, "/explicit")]


def test_cleanup_self_review_worktree_falls_back_to_toplevel(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out", lambda *a, **kw: "/from/git")
    seen = []
    monkeypatch.setattr(
        "review.worktree.cleanup_worktree",
        lambda result, repo_dir: seen.append(repo_dir),
    )
    cleanup_self_review_worktree(None, "")
    assert seen == ["/from/git"]


def test_cleanup_self_review_worktree_swallows_unusable_git(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        MagicMock(side_effect=OSError("git not found")),
    )
    cleanup = MagicMock()
    monkeypatch.setattr("review.worktree.cleanup_worktree", cleanup)
    cleanup_self_review_worktree(None, "")
    cleanup.assert_not_called()


# ── resolve_wt_path ───────────────────────────────────────────────────────────


def test_resolve_wt_path_returns_git_toplevel(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **k: "/repos/widget",
    )
    assert resolve_wt_path("", "feat/x") == "/repos/widget"
    assert resolve_wt_path("/ignored", "feat/x") == "/repos/widget"


def test_resolve_wt_path_bare_repo_uses_topology(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out", lambda *a, **k: "")
    monkeypatch.setattr(
        "review.worktree.git_topology.is_bare_repo", lambda cwd=None: True)
    monkeypatch.setattr(
        "review.worktree.git_topology.resolve_bare_repo_worktree",
        lambda cwd, branch: Path("/repos/widget/feat-x"),
    )
    assert resolve_wt_path("", "feat/x") == "/repos/widget/feat-x"


def test_resolve_wt_path_bare_repo_missing_worktree_exits(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out", lambda *a, **k: "")
    monkeypatch.setattr(
        "review.worktree.git_topology.is_bare_repo", lambda cwd=None: True)
    monkeypatch.setattr(
        "review.worktree.git_topology.resolve_bare_repo_worktree",
        lambda cwd, branch: None,
    )
    monkeypatch.setattr(
        "review.worktree.git_topology.default_branch", lambda cwd=None: "main")
    with pytest.raises(SystemExit) as exc:
        resolve_wt_path("", "feat/x")
    assert exc.value.code == 1


def test_resolve_wt_path_not_a_repo_exits(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out", lambda *a, **k: "")
    monkeypatch.setattr(
        "review.worktree.git_topology.is_bare_repo", lambda cwd=None: False)
    with pytest.raises(SystemExit) as exc:
        resolve_wt_path("/not/git", "")
    assert exc.value.code == 1


# ── resolve_branch_input ──────────────────────────────────────────────────────


def test_resolve_branch_input_uses_resolve_branch(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0, stdout="feat/real\n"),
    )
    assert resolve_branch_input("feat/fuzzy", "/repo") == "feat/real"


def test_resolve_branch_input_falls_back_on_failure(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=1, stdout=""),
    )
    assert resolve_branch_input("feat/fuzzy", "/repo") == "feat/fuzzy"


def test_resolve_branch_input_falls_back_on_exception(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        MagicMock(side_effect=FileNotFoundError("resolve-branch")),
    )
    assert resolve_branch_input("feat/fuzzy", "") == "feat/fuzzy"


# ── find_repo_root ────────────────────────────────────────────────────────────


def test_find_repo_root_explicit_dir_that_exists(tmp_path, monkeypatch):
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("review.worktree.gh_client.out", gh)
    assert find_repo_root("owner/widget", str(tmp_path)) == str(tmp_path)
    gh.assert_not_called()


def test_find_repo_root_explicit_dir_missing_is_empty(tmp_path, monkeypatch):
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("review.worktree.gh_client.out", gh)
    assert find_repo_root("owner/widget", str(tmp_path / "nope")) == ""


def test_find_repo_root_toplevel_basename_match(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/Users/me/git/personal/widget",
    )
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("review.worktree.gh_client.out", gh)
    assert find_repo_root("owner/widget") == "/Users/me/git/personal/widget"


def test_find_repo_root_bare_container_parent_heuristic(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/Users/me/git/personal/widget/main",
    )
    gh = MagicMock(side_effect=AssertionError("gh should not run"))
    monkeypatch.setattr("review.worktree.gh_client.out", gh)
    assert find_repo_root("owner/widget") == "/Users/me/git/personal/widget"


def test_find_repo_root_falls_through_to_gh_and_find(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/somewhere/else",
    )
    monkeypatch.setattr(
        "review.worktree.gh_client.out",
        lambda *a, **kw: "widget",
    )
    monkeypatch.setattr(
        "review.worktree.os.path.expanduser", lambda p: "/home/me/git")
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(
            stdout="/home/me/git/org/widget\n", returncode=0),
    )
    assert find_repo_root("owner/widget") == "/home/me/git/org/widget"


def test_find_repo_root_without_a_repo_name_returns_empty(monkeypatch):
    """A slug with no name half leaves nothing to search ``~/git`` for.

    This used to be the case where ``gh repo view --json name`` came back
    empty. The name is now taken from the slug the caller already passed, so
    the only way to have none is to pass none.
    """
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/somewhere/else",
    )
    find_run = MagicMock(side_effect=AssertionError("find should not run"))
    monkeypatch.setattr("review.worktree.subprocess.run", find_run)
    assert find_repo_root("") == ""
    find_run.assert_not_called()


def test_find_repo_root_names_the_repo_without_gh(monkeypatch):
    """The name half of the slug is the repo's name — asking GitHub for it was
    a GraphQL call that returned its own argument."""
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/somewhere/else",
    )

    def fail(*a, **kw):
        raise AssertionError("find_repo_root must not call gh")

    monkeypatch.setattr("review.worktree.gh_client.out", fail)
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "/home/dev/git/widget\n", ""),
    )
    assert find_repo_root("owner/widget") == "/home/dev/git/widget"


def test_find_repo_root_unusable_git_falls_through_to_gh(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        MagicMock(side_effect=OSError("git not found")),
    )
    monkeypatch.setattr(
        "review.worktree.os.path.expanduser", lambda p: "/home/me/git")
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        lambda *a, **kw: MagicMock(stdout="", returncode=0),
    )
    assert find_repo_root("owner/widget") == ""


def test_find_repo_root_find_exception_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/somewhere/else",
    )
    monkeypatch.setattr(
        "review.worktree.subprocess.run",
        MagicMock(side_effect=TimeoutError("find hung")),
    )
    assert find_repo_root("owner/widget") == ""


def test_find_repo_root_matches_a_mixed_case_checkout(monkeypatch):
    """The slug is case-folded; the directory on disk is not.

    `detect_repo` returns `pr_target.RepoIdentity.label`, which folds A-Z, so a
    repo cloned as `MyProject` arrives here as `myproject`. Comparing that
    byte-for-byte against the directory name would miss the checkout the caller
    is sitting in and send the review off to walk ~/git for a repo it already
    found.
    """
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/home/dev/git/MyProject",
    )

    def fail(*a, **kw):
        raise AssertionError("a matched toplevel must not reach the ~/git walk")

    monkeypatch.setattr("review.worktree.subprocess.run", fail)
    assert find_repo_root("acme/myproject") == "/home/dev/git/MyProject"


def test_find_repo_root_matches_a_mixed_case_bare_container(monkeypatch):
    """Same fold, one level up: a bare-repo container holds the worktrees."""
    monkeypatch.setattr(
        "review.worktree.git_client.out",
        lambda *a, **kw: "/home/dev/git/MyProject/main",
    )

    def fail(*a, **kw):
        raise AssertionError("a matched container must not reach the ~/git walk")

    monkeypatch.setattr("review.worktree.subprocess.run", fail)
    assert find_repo_root("acme/myproject") == "/home/dev/git/MyProject"


def test_find_repo_root_walk_is_case_insensitive(monkeypatch):
    """The ~/git fallback folds too, or it reintroduces the miss one rung down."""
    seen = {}

    monkeypatch.setattr(
        "review.worktree.git_client.out", lambda *a, **kw: "/somewhere/else")

    def record(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "/home/dev/git/MyProject\n", "")

    monkeypatch.setattr("review.worktree.subprocess.run", record)
    monkeypatch.setattr("review.worktree.os.path.expanduser", lambda p: "/home/dev/git")

    assert find_repo_root("acme/myproject") == "/home/dev/git/MyProject"
    assert "-iname" in seen["cmd"]
