"""Tests for pr.context shared resolution module."""

import sys
from pathlib import Path
from unittest.mock import call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.topology
import pr.context
import pr.target
from pr.context import PRHead


def test_pr_and_branch_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        pr.context.resolve(pr_ref="123", branch="feat/foo")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo"))
@patch.object(git.topology, "current_branch_quiet", return_value="feat/bar")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_head", return_value=PRHead(branch="feat/bar", sha="pr-sha"))
def test_pr_only_resolves(mock_head, mock_sha, mock_repo, mock_current, mock_top,
                          mock_repo_name):
    ctx = pr.context.resolve(pr_ref="42")
    assert ctx.pr_number == 42
    assert ctx.branch == "feat/bar"
    assert ctx.repo == "owner/repo"
    assert ctx.current_branch == "feat/bar"
    assert ctx.head_sha == "pr-sha"


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "find_worktree_by_branch", return_value=None)
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(git.topology, "resolve_branch", return_value="feat/baz")
@patch.object(pr.context, "_pr_from_branch",
              return_value=pr.context.BranchPR(number=99))
def test_branch_only_resolves(mock_pr, mock_resolve, mock_sha, mock_repo,
                              mock_find_wt, mock_current, mock_top,
                              mock_repo_name):
    ctx = pr.context.resolve(branch="baz")
    assert ctx.pr_number == 99
    assert ctx.branch == "feat/baz"
    assert ctx.repo == "owner/repo"


# ── Bare-repo handling ─────────────────────────────────────────────────────


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=True)
@patch.object(git.topology, "resolve_bare_repo_worktree", return_value=Path("/wt/main"))
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="def456")
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "current_branch", return_value="main")
@patch.object(pr.context, "_pr_from_current", return_value=pr.context.BranchPR())
def test_bare_repo_finds_worktree(mock_pr, mock_branch, mock_quiet, mock_sha, mock_repo,
                                  mock_resolve_wt, mock_bare, mock_top, mock_repo_name):
    ctx = pr.context.resolve()
    assert ctx.worktree_root == Path("/wt/main")
    assert ctx.repo == "owner/repo"
    assert ctx.current_branch == "main"
    mock_repo.assert_called_once_with("/wt/main")
    mock_resolve_wt.assert_called_once_with(None, None)


@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=True)
@patch.object(git.topology, "resolve_bare_repo_worktree", return_value=None)
def test_bare_repo_no_worktree_no_args_exits(mock_resolve_wt, mock_bare, mock_top):
    with pytest.raises(SystemExit):
        pr.context.resolve()


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=True)
@patch.object(git.topology, "resolve_bare_repo_worktree", return_value=None)
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(git.topology, "resolve_branch", return_value="feat/thing")
@patch.object(pr.context, "_pr_from_branch",
              return_value=pr.context.BranchPR(number=42))
def test_bare_repo_with_branch_continues(mock_pr, mock_resolve, mock_repo,
                                         mock_resolve_wt, mock_bare, mock_top,
                                         mock_repo_name):
    ctx = pr.context.resolve(branch="thing")
    assert ctx.worktree_root is None
    assert ctx.branch == "feat/thing"
    assert ctx.head_sha == ""


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=True)
@patch.object(git.topology, "find_worktree_by_branch",
              return_value=Path("/wt/isaac-improve-ci-failures-skill"))
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(git.topology, "current_branch_quiet", return_value="isaac/improve-ci-failures-skill")
@patch.object(git.topology, "resolve_branch", return_value="isaac/improve-ci-failures-skill")
@patch.object(pr.context, "_pr_from_branch",
              return_value=pr.context.BranchPR(number=42))
def test_bare_repo_fuzzy_branch_resolves_worktree(
    mock_pr, mock_resolve, mock_quiet, mock_sha, mock_repo,
    mock_find_wt, mock_bare, mock_top, mock_repo_name,
):
    """Bare repo with dash-separated branch hint finds slash-separated worktree."""
    ctx = pr.context.resolve(branch="isaac-improve-ci-failures-skill")
    assert ctx.worktree_root == Path("/wt/isaac-improve-ci-failures-skill")
    assert ctx.branch == "isaac/improve-ci-failures-skill"
    mock_find_wt.assert_called_once_with("isaac-improve-ci-failures-skill", None)


@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=False)
def test_not_git_repo_exits(mock_bare, mock_top):
    with pytest.raises(SystemExit):
        pr.context.resolve()


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo"))
@patch.object(git.topology, "current_branch_quiet", return_value=None)
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_head", return_value=PRHead(branch="feat/bar", sha="pr-sha"))
def test_resolve_sets_current_branch_none_on_detached_head(
    mock_head, mock_sha, mock_repo, mock_quiet, mock_top, mock_repo_name,
):
    """current_branch is None when worktree is in detached HEAD."""
    ctx = pr.context.resolve(pr_ref="42")
    assert ctx.current_branch is None
    assert ctx.branch == "feat/bar"


# ── Branch-aware worktree resolution ──────────────────────────────────────


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "find_worktree_for_branch", return_value=Path("/repo/feat-branch"))
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
@patch.object(pr.context, "_pr_from_branch",
              return_value=pr.context.BranchPR(number=42))
def test_branch_redirects_to_correct_worktree(
    mock_pr, mock_resolve, mock_sha, mock_repo,
    mock_find_wt, mock_current, mock_top, mock_repo_name,
):
    """When --branch points to a different worktree, resolve() uses that worktree."""
    ctx = pr.context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/feat-branch")
    mock_find_wt.assert_called_once_with("feat/branch", "/repo/main")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/feat-branch"))
@patch.object(git.topology, "current_branch_quiet", return_value="feat/branch")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
@patch.object(pr.context, "_pr_from_branch",
              return_value=pr.context.BranchPR(number=42))
def test_branch_matching_cwd_stays_in_place(
    mock_pr, mock_resolve, mock_sha, mock_repo, mock_current, mock_top, mock_repo_name,
):
    """When --branch matches CWD's branch, stay in the current worktree."""
    ctx = pr.context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/feat-branch")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/feat-other"))
@patch.object(git.topology, "current_branch_quiet", return_value="feat/other")
@patch.object(git.topology, "default_branch", return_value="main")
@patch.object(git.topology, "find_worktree_for_branch", return_value=None)
@patch.object(git.topology, "create_worktree_for_branch")
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_from_branch", return_value=pr.context.BranchPR())
def test_branch_no_worktree_from_a_feature_checkout_falls_back_to_cwd(
    mock_pr, mock_sha_unused, mock_repo, mock_resolve, mock_create, mock_find_wt,
    mock_default, mock_current, mock_top, mock_repo_name,
):
    """From another feature branch's checkout, no worktree is created: stay put."""
    ctx = pr.context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/feat-other")
    mock_create.assert_not_called()


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "default_branch", return_value="main")
@patch.object(git.topology, "find_worktree_for_branch", return_value=None)
@patch.object(git.topology, "create_worktree_for_branch", return_value=Path("/repo/feat-branch"))
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_from_branch", return_value=pr.context.BranchPR())
def test_branch_no_worktree_from_the_default_checkout_creates_one(
    mock_pr, mock_sha_unused, mock_repo, mock_resolve, mock_create, mock_find_wt,
    mock_default, mock_current, mock_top, mock_repo_name,
):
    """From the default branch's worktree, a branch with none gets its own.

    Falling back to main/ is what made pr rebase refuse ("Refusing to check out
    ... into the main worktree") for a branch that simply had no checkout yet.
    """
    ctx = pr.context.resolve(branch="feat-branch")
    assert ctx.worktree_root == Path("/repo/feat-branch")
    mock_create.assert_called_once_with("feat-branch", "/repo/main")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "default_branch", return_value="main")
@patch.object(git.topology, "find_worktree_for_branch", return_value=None)
@patch.object(git.topology, "create_worktree_for_branch", return_value=None)
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_from_branch", return_value=pr.context.BranchPR())
def test_a_worktree_that_cannot_be_created_falls_back_to_cwd(
    mock_pr, mock_sha_unused, mock_repo, mock_resolve, mock_create, mock_find_wt,
    mock_default, mock_current, mock_top, mock_repo_name,
):
    """wt failing leaves the run where it was, for the caller's own refusal to name."""
    ctx = pr.context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/main")
    mock_create.assert_called_once()


@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "default_branch", return_value="main")
@patch.object(git.topology, "find_worktree_for_branch", return_value=None)
@patch.object(git.topology, "create_worktree_for_branch")
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
def test_the_local_rung_never_creates_a_worktree(
    mock_resolve, mock_create, mock_find_wt, mock_default, mock_current, mock_top,
):
    """create_missing=False (resolve_local) only reads; it leaves no checkout behind."""
    root, _ = pr.context._resolve_worktree(
        None, pr_ref=None, branch="feat/branch", create_missing=False,
    )
    assert root == Path("/repo/main")
    mock_create.assert_not_called()


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "find_worktree_for_branch", side_effect=[None, Path("/repo/feat")])
@patch.object(git.topology, "resolve_branch", return_value="feat/resolved")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_from_branch", return_value=pr.context.BranchPR())
def test_branch_fuzzy_resolved_finds_worktree(
    mock_pr, mock_sha, mock_repo, mock_resolve, mock_find_wt,
    mock_current, mock_top, mock_repo_name,
):
    """When exact branch hint doesn't match but resolve-branch finds it, use that worktree."""
    ctx = pr.context.resolve(branch="feat-hint")
    assert ctx.worktree_root == Path("/repo/feat")
    assert mock_find_wt.call_count == 2
    mock_find_wt.assert_any_call("feat-hint", "/repo/main")
    mock_find_wt.assert_any_call("feat/resolved", "/repo/main")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value=None)
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(git.topology, "resolve_branch", return_value="feat/branch")
@patch.object(pr.context, "_pr_from_branch", return_value=pr.context.BranchPR())
def test_detached_head_skips_worktree_redirect(
    mock_pr, mock_resolve, mock_sha, mock_repo, mock_current, mock_top, mock_repo_name,
):
    """When CWD is in detached HEAD, skip worktree redirect (can't compare branches)."""
    ctx = pr.context.resolve(branch="feat/branch")
    assert ctx.worktree_root == Path("/repo/main")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet",
              side_effect=lambda cwd=None: "feat/bar" if cwd == "/repo/feat-bar" else "main")
@patch.object(git.topology, "find_worktree_for_branch", return_value=Path("/repo/feat-bar"))
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_pr_head", return_value=PRHead(branch="feat/bar", sha="pr-sha"))
def test_pr_redirects_to_head_branch_worktree(
    mock_head, mock_repo, mock_find_wt, mock_current, mock_top, mock_repo_name,
):
    """--pr from another checkout lands in the head branch's worktree, as --branch does."""
    ctx = pr.context.resolve(pr_ref="42")
    assert ctx.worktree_root == Path("/repo/feat-bar")
    assert ctx.branch == "feat/bar"
    assert ctx.head_sha == "pr-sha"
    assert ctx.current_branch == "feat/bar"
    mock_find_wt.assert_called_once_with("feat/bar", "/repo/main")


def _bare_worktree_for(cwd, branch):
    return Path("/wt/feat-bar") if branch == "feat/bar" else Path("/wt/main")


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=True)
@patch.object(git.topology, "resolve_bare_repo_worktree", side_effect=_bare_worktree_for)
@patch.object(git.topology, "current_branch_quiet", return_value="feat/bar")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_pr_head", return_value=PRHead(branch="feat/bar", sha="pr-sha"))
def test_bare_repo_pr_resolves_head_branch_worktree(
    mock_head, mock_repo, mock_quiet, mock_resolve_wt, mock_bare, mock_top,
    mock_repo_name,
):
    """--pr from a bare container uses the head branch's worktree, not the default branch's."""
    ctx = pr.context.resolve(pr_ref="42")
    assert ctx.worktree_root == Path("/wt/feat-bar")
    assert ctx.current_branch == "feat/bar"
    assert mock_resolve_wt.call_args_list == [call(None, None), call(None, "feat/bar")]


def _bare_default_only(cwd, branch):
    return Path("/wt/main") if branch is None else None


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=None)
@patch.object(git.topology, "is_bare_repo", return_value=True)
@patch.object(git.topology, "resolve_bare_repo_worktree", side_effect=_bare_default_only)
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_pr_head", return_value=PRHead(branch="feat/bar", sha="pr-sha"))
def test_bare_repo_pr_keeps_default_worktree_when_head_worktree_unavailable(
    mock_head, mock_repo, mock_sha, mock_quiet, mock_resolve_wt, mock_bare,
    mock_top, mock_repo_name,
):
    """A head branch with no obtainable worktree degrades to the first pass's worktree."""
    ctx = pr.context.resolve(pr_ref="42")
    assert ctx.worktree_root == Path("/wt/main")
    assert ctx.current_branch == "main"
    assert ctx.branch == "feat/bar"
    assert ctx.head_sha == "pr-sha"


# ── is_pr_ref / classify_target ──────────────────────────────────────────


def test_is_pr_ref_number():
    assert pr.context.is_pr_ref("42") is True


def test_is_pr_ref_pr_url():
    assert pr.context.is_pr_ref("https://github.com/owner/repo/pull/123") is True


def test_is_pr_ref_pr_url_trailing_slash():
    assert pr.context.is_pr_ref("https://github.com/owner/repo/pull/456/") is True


def test_is_pr_ref_branch_with_slashes():
    assert pr.context.is_pr_ref("ibarsi/ENG-2239/migration-stream-jsonl") is False


def test_is_pr_ref_simple_branch():
    assert pr.context.is_pr_ref("feat-auth") is False


def test_is_pr_ref_branch_with_numbers():
    assert pr.context.is_pr_ref("isaac/ENG-1234/fix-thing") is False


def test_classify_target_number():
    pr_ref, branch = pr.context.classify_target("42")
    assert pr_ref == "42"
    assert branch is None


def test_classify_target_url():
    pr_ref, branch = pr.context.classify_target("https://github.com/o/r/pull/99")
    assert pr_ref == "https://github.com/o/r/pull/99"
    assert branch is None


def test_classify_target_branch():
    pr_ref, branch = pr.context.classify_target("ibarsi/ENG-2239/migration-stream-jsonl")
    assert pr_ref is None
    assert branch == "ibarsi/ENG-2239/migration-stream-jsonl"


def test_classify_target_simple_branch():
    pr_ref, branch = pr.context.classify_target("feat-auth")
    assert pr_ref is None
    assert branch == "feat-auth"


@patch.object(pr.target, "repo_identity_from_origin",
              return_value=pr.target.RepoIdentity(label="owner/repo", key="repo"))
@patch.object(pr.context, "_git_toplevel", return_value=Path("/repo/main"))
@patch.object(git.topology, "current_branch_quiet", return_value="main")
@patch.object(git.topology, "default_branch", return_value="main")
@patch.object(git.topology, "find_worktree_by_branch", return_value=None)
@patch.object(git.topology, "create_worktree_for_branch", return_value=Path("/repo/feat-branch"))
@patch.object(pr.context, "detect_repo", return_value="owner/repo")
@patch.object(pr.context, "_head_sha", return_value="abc123")
@patch.object(pr.context, "_pr_head",
              return_value=PRHead(branch="feat/branch", sha="pr-sha"))
def test_pr_no_worktree_from_the_default_checkout_creates_one(
    mock_head, mock_sha, mock_repo, mock_create, mock_find_wt,
    mock_default, mock_current, mock_top, mock_repo_name,
):
    """--pr lands in the same freshly-created worktree --branch on the same head would.

    The second ``_resolve_worktree`` pass in ``resolve()`` also defaults
    ``create_missing`` to True, so a PR whose head branch has no worktree of
    its own, resolved from the default branch's checkout, gets one here too.
    """
    ctx = pr.context.resolve(pr_ref="42")
    assert ctx.worktree_root == Path("/repo/feat-branch")
    mock_create.assert_called_once_with("feat/branch", "/repo/main")
