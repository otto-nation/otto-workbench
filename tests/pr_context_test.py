"""Tests for pr_context library."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_context
import pr_target
from pr_context import (
    _parse_pr_input, _resolve_branch, default_branch, resolve_bare_repo_worktree,
    find_worktree_for_branch, ResolvedContext, update_to_remote,
    fetch_and_reset, create_worktree_for_branch,
)


# ── PR input parsing ────────────────────────────────────────────────────────


def test_parse_pr_input_number():
    assert _parse_pr_input("42") == 42


def test_parse_pr_input_url():
    assert _parse_pr_input("https://github.com/owner/repo/pull/123") == 123


def test_parse_pr_input_url_trailing_slash():
    assert _parse_pr_input("https://github.com/owner/repo/pull/456/") == 456


def test_parse_pr_input_branch_name_raises():
    with pytest.raises(ValueError, match="Cannot parse PR number"):
        _parse_pr_input("ibarsi/ENG-2239/migration-stream-jsonl")


def test_parse_pr_input_simple_branch_raises():
    with pytest.raises(ValueError, match="Cannot parse PR number"):
        _parse_pr_input("feat-auth")


# ── ResolvedContext ─────────────────────────────────────────────────────────


def test_resolved_context_is_frozen():
    ctx = ResolvedContext(
        repo="owner/repo", branch="main", pr_number=1,
        worktree_root=Path("/tmp"), head_sha="abc",
        target_dir=Path("/tmp/target"),
    )
    with pytest.raises(AttributeError):
        ctx.repo = "other"


def test_resolved_context_fields():
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/auth",
        pr_number=None, worktree_root=Path("/wt"), head_sha="def",
        target_dir=Path("/wt/target"),
    )
    assert ctx.repo == "owner/repo"
    assert ctx.branch == "feat/auth"
    assert ctx.pr_number is None
    assert ctx.head_sha == "def"


# ── require_worktree ───────────────────────────────────────────────────────


def test_require_worktree_returns_path():
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/auth",
        pr_number=None, worktree_root=Path("/wt"), head_sha="def",
        target_dir=Path("/wt/target"),
    )
    assert ctx.require_worktree() == Path("/wt")


def test_require_worktree_exits_with_actionable_message(capsys):
    ctx = ResolvedContext(
        repo="owner/repo", branch="feat/auth",
        pr_number=None, worktree_root=None, head_sha="",
        target_dir=Path("/tmp/target"),
    )
    with pytest.raises(SystemExit) as exc:
        ctx.require_worktree()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "No worktree for 'feat/auth'" in err
    assert "wt switch feat/auth" in err
    assert "--repo-dir" in err


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


@patch("pr_context.subprocess.run")
def test_fetch_and_reset_runs_fetch_then_reset(mock_run):
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


@patch("pr_context.log")
@patch("pr_context.subprocess.run")
def test_fetch_and_reset_skips_when_on_another_branch(mock_run, mock_log):
    """Regression: resetting main/ while a feature branch sits in it ate two commits."""
    runs = _safe_reset_runs()
    runs[1] = MagicMock(returncode=0, stdout="feat/other\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "main")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "not main" in mock_log.warn.call_args.args[0]


@patch("pr_context.log")
@patch("pr_context.subprocess.run")
def test_fetch_and_reset_skips_on_uncommitted_changes(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[2] = MagicMock(returncode=0, stdout=" M file.py\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("pr_context.log")
@patch("pr_context.subprocess.run")
def test_fetch_and_reset_skips_on_unpushed_commits(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[3] = MagicMock(returncode=0, stdout="2\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "2 unpushed" in mock_log.warn.call_args.args[0]


@patch("pr_context.log")
@patch("pr_context.subprocess.run")
def test_fetch_and_reset_skips_on_detached_head(mock_run, mock_log):
    runs = _safe_reset_runs()
    runs[1] = MagicMock(returncode=0, stdout="HEAD\n")
    mock_run.side_effect = runs
    fetch_and_reset("/wt", "feat/x")
    assert not any("reset" in c.args[0] for c in mock_run.call_args_list)
    assert "detached HEAD" in mock_log.warn.call_args.args[0]


@patch("pr_context.subprocess.run", side_effect=Exception("network error"))
def test_fetch_and_reset_survives_fetch_exception(mock_run):
    fetch_and_reset("/wt", "feat/x")


# ── update_to_remote ───────────────────────────────────────────────────────


def _make_ctx(**overrides):
    defaults = dict(
        repo="owner/repo", branch="feat/x",
        pr_number=1, worktree_root=Path("/wt"), head_sha="aaa",
        target_dir=Path("/wt/target"),
    )
    defaults.update(overrides)
    return ResolvedContext(**defaults)


def test_update_to_remote_noop_without_worktree():
    ctx = _make_ctx(worktree_root=None)
    assert update_to_remote(ctx) is ctx


def test_update_to_remote_noop_without_branch():
    ctx = _make_ctx(branch="", pr_number=None)
    assert update_to_remote(ctx) is ctx


@patch("pr_context.log")
@patch("pr_context._current_branch_quiet", return_value="other-branch")
def test_update_to_remote_skips_on_branch_mismatch(mock_branch, mock_log):
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    mock_log.info.assert_called_once()
    assert "other-branch" in mock_log.info.call_args.args[0]


@patch("pr_context._current_branch_quiet", return_value="feat/x")
@patch("pr_context.log")
@patch("pr_context.subprocess.run")
def test_update_to_remote_skips_on_uncommitted_changes(mock_run, mock_log, _mock_branch):
    mock_run.return_value = MagicMock(returncode=0, stdout="M dirty.py\n")
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx
    mock_log.warn.assert_called_once()
    assert "uncommitted" in mock_log.warn.call_args.args[0]


@patch("pr_context._current_branch_quiet", return_value="feat/x")
@patch("pr_context.subprocess.run")
def test_update_to_remote_skips_on_fetch_failure(mock_run, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),       # status --porcelain (clean)
        MagicMock(returncode=1),                   # fetch fails
    ]
    ctx = _make_ctx()
    assert update_to_remote(ctx) is ctx


@patch("pr_context._current_branch_quiet", return_value="feat/x")
@patch("pr_context._head_sha", return_value="aaa111")
@patch("pr_context.subprocess.run")
def test_update_to_remote_skips_when_already_current(mock_run, mock_sha, _mock_branch):
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=""),           # status --porcelain (clean)
        MagicMock(returncode=0),                       # fetch
        MagicMock(returncode=0, stdout="aaa111\n"),    # rev-parse origin/branch
    ]
    ctx = _make_ctx(head_sha="aaa111")
    assert update_to_remote(ctx) is ctx


@patch("pr_context._current_branch_quiet", return_value="feat/x")
@patch("pr_context.log")
@patch("pr_context._head_sha", return_value="local111")
@patch("pr_context.subprocess.run")
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


@patch("pr_context._current_branch_quiet", return_value="feat/x")
@patch("pr_context.log")
@patch("pr_context._head_sha", return_value="old111")
@patch("pr_context.subprocess.run")
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


# ── Branch resolution ──────────────────────────────────────────────────────


@patch("pr_context.subprocess.run", side_effect=FileNotFoundError)
@patch("pr_context._current_branch", return_value="fallback-branch")
def test_resolve_branch_uses_hint_on_missing_script(mock_current, mock_run):
    assert _resolve_branch("some-hint") == "some-hint"
    mock_current.assert_not_called()


@patch("pr_context.subprocess.run", side_effect=FileNotFoundError)
@patch("pr_context._current_branch", return_value="fallback-branch")
def test_resolve_branch_falls_back_on_missing_script_no_hint(mock_current, mock_run):
    assert _resolve_branch("") == "fallback-branch"
    mock_current.assert_called_once_with(None)


@patch("pr_context.subprocess.run")
@patch("pr_context._current_branch", return_value="fallback-branch")
def test_resolve_branch_returns_hint_on_failure(mock_current, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    assert _resolve_branch("bad-hint") == "bad-hint"
    mock_current.assert_not_called()


@patch("pr_context.subprocess.run")
def test_resolve_branch_returns_stdout(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="isaac/feat/resolved_branch\n")
    assert _resolve_branch("resolved") == "isaac/feat/resolved_branch"


# ── Default branch ────────────────────────────────────────────────────────


@patch("pr_context.subprocess.run")
def test_default_branch_strips_the_remote_prefix(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/main\n",
    )
    assert default_branch() == "main"


@patch("pr_context.subprocess.run")
def test_default_branch_is_not_hardcoded_to_main(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/trunk\n",
    )
    assert default_branch() == "trunk"


@patch("pr_context.subprocess.run")
def test_default_branch_falls_back_when_origin_head_is_unset(mock_run):
    """An unfetched clone has no origin/HEAD; callers need a base ref anyway."""
    # returncode is not inspected — the implementation only checks stdout.strip()
    # for truthiness.  A zero exit with empty stdout is the real trigger for the
    # "main" fallback (git symbolic-ref exits 0 but prints nothing when origin/HEAD
    # is unset on some git versions).
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    assert default_branch() == "main"


@patch("pr_context.subprocess.run", side_effect=OSError("no git"))
def test_default_branch_falls_back_when_git_is_missing(mock_run):
    assert default_branch() == "main"


@patch("pr_context.subprocess.run")
def test_default_branch_scopes_the_lookup_to_the_given_directory(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/main\n",
    )
    default_branch("/wt/feature")
    # Scoped via subprocess's cwd, matching every other git call in this module.
    assert mock_run.call_args.kwargs["cwd"] == "/wt/feature"
    assert mock_run.call_args[0][0][0] == "git"


# ── find_worktree_for_branch ──────────────────────────────────────────────


_WORKTREE_LIST_HIJACKED = (
    "worktree /repo\n"
    "bare\n"
    "\n"
    "worktree /repo/main\n"
    "HEAD f94475d\n"
    "branch refs/heads/feat/x\n"
    "\n"
    "worktree /repo/feat-other\n"
    "HEAD abc1234\n"
    "branch refs/heads/feat/other\n"
)


@patch("pr_context.subprocess.run")
def test_find_worktree_for_branch_prefers_exact_tag(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=_WORKTREE_LIST_HIJACKED)
    assert find_worktree_for_branch("feat/other") == Path("/repo/feat-other")


@patch("pr_context.subprocess.run")
def test_find_worktree_for_branch_ignores_dir_named_like_another_branch(mock_run):
    """Regression: /repo/main holding feat/x was returned as main's worktree.

    review-threads then hard-reset it to origin/main, destroying feat/x.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout=_WORKTREE_LIST_HIJACKED)
    assert find_worktree_for_branch("main") is None


@patch("pr_context.subprocess.run")
def test_find_worktree_dir_named_matches_regardless_of_occupant(mock_run):
    """The lenient lookup answers "which directory", not "which branch"."""
    mock_run.return_value = MagicMock(returncode=0, stdout=_WORKTREE_LIST_HIJACKED)
    assert pr_context.find_worktree_dir_named("main") == Path("/repo/main")


@patch("pr_context.subprocess.run")
def test_find_worktree_dir_named_skips_the_bare_repo(mock_run):
    """The bare entry is not a checkout and must never be handed back as one."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout="worktree /repo/main\nbare\n",
    )
    assert pr_context.find_worktree_dir_named("main") is None


@patch("pr_context.subprocess.run")
def test_find_worktree_for_branch_still_matches_detached_head_by_name(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "worktree /repo\nbare\n\n"
            "worktree /repo/main\nHEAD f94475d\ndetached\n"
        ),
    )
    assert find_worktree_for_branch("main") == Path("/repo/main")


@patch("pr_context.subprocess.run")
def test_find_worktree_for_branch_handles_paths_with_spaces_and_brackets(mock_run):
    """The human listing packs path and [branch] onto one line; porcelain doesn't."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "worktree /repo/we ird [x]\n"
            "HEAD abc1234\n"
            "branch refs/heads/spacey\n"
        ),
    )
    assert find_worktree_for_branch("spacey") == Path("/repo/we ird [x]")


# ── Bare-repo worktree resolution ─────────────────────────────────────────


@patch("pr_context.find_worktree_for_branch")
def testresolve_bare_repo_worktree_prefers_branch(mock_find):
    mock_find.return_value = Path("/wt/feat-branch")
    result = resolve_bare_repo_worktree(None, "feat/branch")
    assert result == Path("/wt/feat-branch")
    mock_find.assert_called_once_with("feat/branch", None)


@patch("pr_context.create_worktree_for_branch")
@patch("pr_context.find_worktree_for_branch")
def testresolve_bare_repo_worktree_creates_missing_branch_worktree(mock_find, mock_create):
    """A requested branch with no worktree gets one created, not main's."""
    mock_find.return_value = None
    mock_create.return_value = Path("/wt/nonexistent")
    result = resolve_bare_repo_worktree(None, "nonexistent")
    assert result == Path("/wt/nonexistent")
    mock_create.assert_called_once_with("nonexistent", None)


@patch("pr_context.create_worktree_for_branch", return_value=None)
@patch("pr_context.find_worktree_for_branch")
def testresolve_bare_repo_worktree_never_substitutes_default(mock_find, mock_create):
    """Regression: returning main's worktree here let callers hijack main/."""
    mock_find.return_value = None
    result = resolve_bare_repo_worktree(None, "nonexistent")
    assert result is None
    # find_worktree_for_branch must never be consulted for the default branch
    # when an explicit branch was requested.
    for call in mock_find.call_args_list:
        assert call.args[0] != "main"


@patch("pr_context.find_worktree_dir_named", return_value=None)
@patch("pr_context.find_worktree_for_branch", return_value=None)
@patch("pr_context.subprocess.run")
def testresolve_bare_repo_worktree_returns_none(mock_run, mock_find, mock_named):
    mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
    result = resolve_bare_repo_worktree(None, None)
    assert result is None


@patch("pr_context.find_worktree_dir_named", return_value=Path("/repo/main"))
@patch("pr_context.find_worktree_for_branch", return_value=None)
@patch("pr_context.subprocess.run")
def testresolve_bare_repo_worktree_falls_back_to_dir_name(mock_run, mock_find, mock_named):
    """No branch requested: a main/ holding someone else's branch is still a cwd.

    Regression: tightening find_worktree_for_branch made this return None, and
    the callers that dereference worktree_root without a guard blew up.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
    assert resolve_bare_repo_worktree(None, None) == Path("/repo/main")


@patch("pr_context.find_worktree_for_branch")
@patch("pr_context._resolve_branch", return_value="isaac/improve-ci-failures-skill")
def test_resolve_bare_repo_worktree_fuzzy_resolves_branch(mock_resolve, mock_find):
    """Bare repo resolution uses fuzzy matching when exact branch hint doesn't match."""
    mock_find.side_effect = [None, Path("/wt/isaac-improve-ci-failures-skill")]
    result = resolve_bare_repo_worktree(None, "isaac-improve-ci-failures-skill")
    assert result == Path("/wt/isaac-improve-ci-failures-skill")
    assert mock_find.call_count == 2
    mock_find.assert_any_call("isaac-improve-ci-failures-skill", None)
    mock_find.assert_any_call("isaac/improve-ci-failures-skill", None)


# ── create_worktree_for_branch ─────────────────────────────────────────────


@patch("pr_context.subprocess.run")
def test_create_worktree_for_branch_returns_path(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='✓ created\n{"action":"created","path":"/wt/feat-x"}\n',
    )
    assert create_worktree_for_branch("feat/x") == Path("/wt/feat-x")
    assert mock_run.call_args.args[0][:3] == ["wt", "switch", "feat/x"]


@patch("pr_context.subprocess.run")
def test_create_worktree_for_branch_passes_cwd(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout='{"path":"/wt/feat-x"}\n',
    )
    create_worktree_for_branch("feat/x", "/repo")
    assert mock_run.call_args.args[0][-2:] == ["-C", "/repo"]


@patch("pr_context.subprocess.run")
def test_create_worktree_for_branch_returns_none_when_wt_fails(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    assert create_worktree_for_branch("feat/x") is None


@patch("pr_context.subprocess.run")
def test_create_worktree_for_branch_survives_malformed_json(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="{not json}\n")
    assert create_worktree_for_branch("feat/x") is None


# ── target_dir / PR head resolution ────────────────────────────────────────


def test_resolve_exits_when_a_prs_head_branch_cannot_be_resolved(monkeypatch, capsys):
    """No borrowing the caller's branch — that is the bug this issue is about."""
    monkeypatch.setattr(pr_context, "_resolve_worktree",
                        lambda cwd, pr, branch: (Path("/wt"), "/wt"))
    monkeypatch.setattr(pr_context, "_detect_repo", lambda cwd=None: "acme/widget")
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "deadbeef")
    monkeypatch.setattr(pr_context, "_pr_head", lambda repo, n: (None, ""))
    monkeypatch.setattr(pr_context, "_current_branch",
                        lambda cwd=None: pytest.fail("must not read the caller's branch"))

    with pytest.raises(SystemExit) as excinfo:
        pr_context.resolve(pr="2973")

    assert excinfo.value.code == 1
    assert "2973" in capsys.readouterr().err


def test_resolve_exits_when_a_prs_head_sha_cannot_be_resolved(monkeypatch, capsys):
    """A branch with no SHA is a partial result too — never stamp the caller's."""
    monkeypatch.setattr(pr_context, "_resolve_worktree",
                        lambda cwd, pr, branch: (Path("/wt"), "/wt"))
    monkeypatch.setattr(pr_context, "_detect_repo", lambda cwd=None: "acme/widget")
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "caller-sha")
    monkeypatch.setattr(pr_context, "_pr_head", lambda repo, n: ("feat/x", ""))
    monkeypatch.setattr(pr_context, "_current_branch",
                        lambda cwd=None: pytest.fail("must not read the caller's branch"))

    with pytest.raises(SystemExit) as excinfo:
        pr_context.resolve(pr="2973")

    assert excinfo.value.code == 1
    assert "2973" in capsys.readouterr().err


def test_resolve_stamps_the_prs_head_sha_not_the_callers(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pr_context, "_resolve_worktree",
                        lambda cwd, pr, branch: (Path("/wt"), "/wt"))
    monkeypatch.setattr(pr_context, "_detect_repo", lambda cwd=None: "acme/widget")
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "caller-sha")
    monkeypatch.setattr(pr_context, "_current_branch_quiet", lambda cwd=None: "other")
    monkeypatch.setattr(pr_context, "_pr_head", lambda repo, n: ("feat/login", "pr-sha"))
    monkeypatch.setattr(pr_target, "repo_name_from_origin", lambda cwd=None: "widget")

    ctx = pr_context.resolve(pr="2973")

    assert ctx.head_sha == "pr-sha"
    assert ctx.branch == "feat/login"


def test_resolve_targets_the_pr_not_the_invoking_directory(monkeypatch, tmp_path):
    """The whole point: two PRs from one CWD get two target dirs."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pr_context, "_resolve_worktree",
                        lambda cwd, pr, branch: (Path("/repo-root"), "/repo-root"))
    monkeypatch.setattr(pr_context, "_detect_repo", lambda cwd=None: "acme/widget")
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "x")
    monkeypatch.setattr(pr_context, "_current_branch_quiet", lambda cwd=None: "main")
    monkeypatch.setattr(pr_target, "repo_name_from_origin", lambda cwd=None: "widget")

    monkeypatch.setattr(pr_context, "_pr_head", lambda repo, n: ("feat/a", "sha-a"))
    first = pr_context.resolve(pr="1")
    monkeypatch.setattr(pr_context, "_pr_head", lambda repo, n: ("feat/b", "sha-b"))
    second = pr_context.resolve(pr="2")

    assert first.target_dir != second.target_dir
    assert first.worktree_root == second.worktree_root
    # Pins the composed value, not just its distinctness: a regression that
    # keyed the path on _detect_repo's "owner/name" instead of the origin-derived
    # repo name would still make the two dirs differ, while violating "repo name
    # comes from git remote get-url origin".
    assert first.target_dir == tmp_path / "pr" / "widget-feat-a"


def test_resolve_targets_the_same_pr_from_any_invoking_directory(monkeypatch, tmp_path):
    """The converse: one PR's branch, launched from two different directories,
    resolves to the same target dir. This is what lets `pr review --self` run
    from inside the PR's own worktree and `pr review 2973` run from the repo
    root take the same lock instead of two independent ones."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pr_context, "_detect_repo", lambda cwd=None: "acme/widget")
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "x")
    monkeypatch.setattr(pr_context, "_current_branch_quiet", lambda cwd=None: "feat/login")
    monkeypatch.setattr(pr_target, "repo_name_from_origin", lambda cwd=None: "widget")
    monkeypatch.setattr(pr_context, "_resolve_branch", lambda hint, cwd=None: hint)
    monkeypatch.setattr(pr_context, "_pr_from_branch", lambda repo, branch: 2973)

    monkeypatch.setattr(pr_context, "_resolve_worktree",
                        lambda cwd, pr, branch: (Path("/repo-root"), "/repo-root"))
    from_root = pr_context.resolve(branch="feat/login", repo_dir="/repo-root")

    monkeypatch.setattr(
        pr_context, "_resolve_worktree",
        lambda cwd, pr, branch: (
            Path("/repo-root/.worktrees/feat-login"), "/repo-root/.worktrees/feat-login",
        ),
    )
    from_worktree = pr_context.resolve(branch="feat/login",
                                       repo_dir="/repo-root/.worktrees/feat-login")

    assert from_root.worktree_root != from_worktree.worktree_root
    assert from_root.target_dir == from_worktree.target_dir


def test_resolve_exits_without_an_origin_remote(monkeypatch, capsys):
    monkeypatch.setattr(pr_context, "_resolve_worktree",
                        lambda cwd, pr, branch: (Path("/wt"), "/wt"))
    monkeypatch.setattr(pr_context, "_detect_repo", lambda cwd=None: "acme/widget")
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "x")
    monkeypatch.setattr(pr_context, "_current_branch_quiet", lambda cwd=None: "main")
    monkeypatch.setattr(pr_context, "_pr_head", lambda repo, n: ("feat/a", "sha"))
    monkeypatch.setattr(pr_target, "repo_name_from_origin", lambda cwd=None: None)

    with pytest.raises(SystemExit) as excinfo:
        pr_context.resolve(pr="1")

    assert excinfo.value.code == 1
    assert "origin" in capsys.readouterr().err


def test_update_to_remote_preserves_the_target_dir(monkeypatch, tmp_path):
    """dataclasses.replace, so a new field cannot be dropped by hand-retyping."""
    ctx = pr_context.ResolvedContext(
        repo="acme/widget", branch="feat/a", pr_number=1,
        worktree_root=tmp_path, head_sha="old", current_branch="feat/a",
        target_dir=tmp_path / "target",
    )
    monkeypatch.setattr(pr_context, "_current_branch_quiet", lambda cwd=None: "feat/a")
    monkeypatch.setattr(pr_context, "_worktree_is_dirty", lambda cwd: False)
    monkeypatch.setattr(pr_context, "_unpushed_count", lambda cwd, branch: 0)
    monkeypatch.setattr(pr_context, "_head_sha", lambda cwd=None: "old")

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        out = "new-sha" if "rev-parse" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(pr_context.subprocess, "run", fake_run)
    updated = pr_context.update_to_remote(ctx)

    assert updated.head_sha == "new-sha"
    assert updated.target_dir == ctx.target_dir
