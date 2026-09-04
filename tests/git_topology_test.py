"""Worktree and bare-repo topology."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from git import topology as git_topology  # noqa: E402


def _porcelain(*entries: tuple[str, str | None], bare: str | None = None) -> str:
    """``git worktree list --porcelain`` output for (path, branch) entries.

    A None branch renders as a detached-HEAD entry. *bare* prepends the bare
    repository's own entry at that path — present in real `wt list` output and
    dropped by the parser, so every fixture exercising that drop passes it
    instead of hand-rolling the block.
    """
    blocks = []
    if bare is not None:
        blocks.append(f"worktree {bare}\nbare\n")
    blocks += [
        f"worktree {path}\nHEAD abc1234\n"
        + (f"branch refs/heads/{branch}\n" if branch else "detached\n")
        for path, branch in entries
    ]
    return "\n".join(blocks)


@patch("git.topology.subprocess.run")
def test_worktree_entries_names_its_fields(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/main", "main"), ("/repo/feat-x", "feat/x"), bare="/repo/.git",
    ))

    entries = git_topology.worktree_entries("/repo")

    assert [e.path for e in entries] == [Path("/repo/main"), Path("/repo/feat-x")]
    assert [e.branch for e in entries] == ["main", "feat/x"]


@patch("git.topology.subprocess.run")
def test_worktree_entries_drops_the_bare_repo(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/main", "main"), ("/repo/feat-x", "feat/x"), bare="/repo/.git",
    ))

    assert all(e.path.name != ".git" for e in git_topology.worktree_entries("/repo"))


# ── Bare-repo helpers (unit) ───────────────────────────────────────────────


@patch.object(git_topology, "subprocess")
def test_is_bare_repo_true(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout="true\n")
    assert git_topology.is_bare_repo("/some/path") is True


@patch.object(git_topology, "subprocess")
def test_is_bare_repo_false(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout="false\n")
    assert git_topology.is_bare_repo("/some/path") is False


@patch.object(git_topology, "subprocess")
def test_find_worktree_for_branch_found(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout=_porcelain(
        ("/home/user/repo/feat-branch", "feat/branch"),
        ("/home/user/repo/main", "main"),
    ))
    result = git_topology.find_worktree_for_branch("feat/branch")
    assert result == Path("/home/user/repo/feat-branch")


@patch.object(git_topology, "subprocess")
def test_find_worktree_for_branch_not_found(mock_sub):
    mock_sub.run.return_value = MagicMock(stdout=_porcelain(
        ("/home/user/repo/main", "main"),
    ))
    result = git_topology.find_worktree_for_branch("nonexistent")
    assert result is None


@patch.object(git_topology, "subprocess")
def test_find_worktree_for_branch_detached_head_fallback(mock_sub):
    """Detached-HEAD worktree has no branch — falls back to sanitized dir name."""
    mock_sub.run.return_value = MagicMock(stdout=_porcelain(
        ("/home/user/repo/isaac-feat-auth", None),
        ("/home/user/repo/main", "main"),
    ))
    result = git_topology.find_worktree_for_branch("isaac/feat/auth")
    assert result == Path("/home/user/repo/isaac-feat-auth")


@patch.object(git_topology, "subprocess")
def test_find_worktree_for_branch_prefers_branch_over_dir_name(mock_sub):
    """When git reports the branch, prefer it over the directory-name fallback."""
    mock_sub.run.return_value = MagicMock(stdout=_porcelain(
        ("/home/user/repo/wrong-dir", "feat/branch"),
        ("/home/user/repo/feat-branch", None),
    ))
    result = git_topology.find_worktree_for_branch("feat/branch")
    assert result == Path("/home/user/repo/wrong-dir")


@patch.object(git_topology, "subprocess")
def test_find_worktree_for_branch_sanitized_no_match(mock_sub):
    """Neither the branch nor a sanitized dir name matches — returns None."""
    mock_sub.run.return_value = MagicMock(stdout=_porcelain(
        ("/home/user/repo/other-branch", None),
        ("/home/user/repo/main", "main"),
    ))
    result = git_topology.find_worktree_for_branch("feat/nonexistent")
    assert result is None


# ── _current_branch detached HEAD ─────────────────────────────────────────


@patch.object(git_topology, "subprocess")
def test_current_branch_detached_head_exits(mock_sub):
    """current_branch exits when HEAD is detached."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout="HEAD\n")
    with pytest.raises(SystemExit):
        git_topology.current_branch("/repo")


# ── Branch resolution ──────────────────────────────────────────────────────


@patch("git.topology.subprocess.run", side_effect=FileNotFoundError)
@patch("git.topology.current_branch", return_value="fallback-branch")
def test_resolve_branch_uses_hint_on_missing_script(mock_current, mock_run):
    assert git_topology.resolve_branch("some-hint") == "some-hint"
    mock_current.assert_not_called()


@patch("git.topology.subprocess.run", side_effect=FileNotFoundError)
@patch("git.topology.current_branch", return_value="fallback-branch")
def test_resolve_branch_falls_back_on_missing_script_no_hint(mock_current, mock_run):
    assert git_topology.resolve_branch("") == "fallback-branch"
    mock_current.assert_called_once_with(None)


@patch("git.topology.subprocess.run")
@patch("git.topology.current_branch", return_value="fallback-branch")
def test_resolve_branch_returns_hint_on_failure(mock_current, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
    assert git_topology.resolve_branch("bad-hint") == "bad-hint"
    mock_current.assert_not_called()


@patch("git.topology.subprocess.run")
def test_resolve_branch_returns_stdout(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="isaac/feat/resolved_branch\n")
    assert git_topology.resolve_branch("resolved") == "isaac/feat/resolved_branch"


@patch("git.topology.subprocess.run")
@patch("git.topology.current_branch", return_value="fallback-branch")
def test_resolve_branch_quotes_what_resolve_branch_said(
        _mock_current, mock_run, capsys):
    """Regression: resolve-branch's diagnosis was dropped for a generic line."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="",
        stderr="error: no branch matches 'bad-hint'\n")

    assert git_topology.resolve_branch("bad-hint") == "bad-hint"
    err = capsys.readouterr().err
    assert "no branch matches 'bad-hint'" in err
    assert "as-is" in err


@patch("git.topology.subprocess.run")
@patch("git.topology.current_branch", return_value="fallback-branch")
def test_resolve_branch_degrades_when_the_script_says_nothing(
        _mock_current, mock_run, capsys):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

    assert git_topology.resolve_branch("bad-hint") == "bad-hint"
    warning = capsys.readouterr().err.splitlines()[0]
    assert warning.endswith("resolve-branch could not resolve 'bad-hint' (exit 1)")


# ── _current_branch ─────────────────────────────────────────────────────────


def _stub_run(monkeypatch, returncode, stdout="", stderr=""):
    """Make the next subprocess call return a canned result."""
    monkeypatch.setattr(
        git_topology.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd, returncode, stdout, stderr),
    )


def test_current_branch_quotes_git_stderr(monkeypatch, capsys):
    _stub_run(monkeypatch, 128, stderr=(
        "fatal: not a git repository (or any of the parent directories): .git"))

    with pytest.raises(SystemExit) as excinfo:
        git_topology.current_branch()

    assert excinfo.value.code == 1
    assert "not a git repository" in capsys.readouterr().err


# ── Default branch ────────────────────────────────────────────────────────


@patch("git.topology.subprocess.run")
def test_default_branch_strips_the_remote_prefix(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/main\n",
    )
    assert git_topology.default_branch() == "main"


@patch("git.topology.subprocess.run")
def test_default_branch_is_not_hardcoded_to_main(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/trunk\n",
    )
    assert git_topology.default_branch() == "trunk"


@patch("git.topology.subprocess.run")
def test_default_branch_falls_back_when_origin_head_is_unset(mock_run):
    """An unfetched clone has no origin/HEAD; callers need a base ref anyway."""
    # returncode is not inspected — the implementation only checks stdout.strip()
    # for truthiness.  A zero exit with empty stdout is the real trigger for the
    # "main" fallback (git symbolic-ref exits 0 but prints nothing when origin/HEAD
    # is unset on some git versions).
    mock_run.return_value = MagicMock(returncode=0, stdout="")
    assert git_topology.default_branch() == "main"


@patch("git.topology.subprocess.run", side_effect=OSError("no git"))
def test_default_branch_falls_back_when_git_is_missing(mock_run):
    assert git_topology.default_branch() == "main"


@patch("git.topology.subprocess.run")
def test_default_branch_scopes_the_lookup_to_the_given_directory(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout="refs/remotes/origin/main\n",
    )
    git_topology.default_branch("/wt/feature")
    # Scoped via subprocess's cwd, matching every other git call in this module.
    assert mock_run.call_args.kwargs["cwd"] == "/wt/feature"
    assert mock_run.call_args[0][0][0] == "git"


# ── find_worktree_for_branch ──────────────────────────────────────────────


def _hijacked_worktree_list() -> str:
    """A bare repo whose main/ dir holds feat/x, plus feat-other's own worktree."""
    return _porcelain(
        ("/repo/main", "feat/x"), ("/repo/feat-other", "feat/other"), bare="/repo",
    )


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_prefers_exact_tag(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=_hijacked_worktree_list())
    assert git_topology.find_worktree_for_branch("feat/other") == Path("/repo/feat-other")


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_ignores_dir_named_like_another_branch(mock_run):
    """Regression: /repo/main holding feat/x was returned as main's worktree.

    review-threads then hard-reset it to origin/main, destroying feat/x.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout=_hijacked_worktree_list())
    assert git_topology.find_worktree_for_branch("main") is None


@patch("git.topology.subprocess.run")
def test_find_worktree_dir_named_matches_regardless_of_occupant(mock_run):
    """The lenient lookup answers "which directory", not "which branch"."""
    mock_run.return_value = MagicMock(returncode=0, stdout=_hijacked_worktree_list())
    assert git_topology.find_worktree_dir_named("main") == Path("/repo/main")


@patch("git.topology.subprocess.run")
def test_find_worktree_dir_named_skips_the_bare_repo(mock_run):
    """The bare entry is not a checkout and must never be handed back as one."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_porcelain(bare="/repo/main"),
    )
    assert git_topology.find_worktree_dir_named("main") is None


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_still_matches_detached_head_by_name(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_porcelain(("/repo/main", None), bare="/repo"),
    )
    assert git_topology.find_worktree_for_branch("main") == Path("/repo/main")


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_handles_paths_with_spaces_and_brackets(mock_run):
    """The human listing packs path and [branch] onto one line; porcelain doesn't."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_porcelain(("/repo/we ird [x]", "spacey")),
    )
    assert git_topology.find_worktree_for_branch("spacey") == Path("/repo/we ird [x]")


# ── Bare-repo worktree resolution ─────────────────────────────────────────


@patch("git.topology.find_worktree_for_branch")
def test_resolve_bare_repo_worktree_prefers_branch(mock_find):
    mock_find.return_value = Path("/wt/feat-branch")
    result = git_topology.resolve_bare_repo_worktree(None, "feat/branch")
    assert result == Path("/wt/feat-branch")
    mock_find.assert_called_once_with("feat/branch", None)


@patch("git.topology.create_worktree_for_branch")
@patch("git.topology.find_worktree_for_branch")
def test_resolve_bare_repo_worktree_creates_missing_branch_worktree(mock_find, mock_create):
    """A requested branch with no worktree gets one created, not main's."""
    mock_find.return_value = None
    mock_create.return_value = Path("/wt/nonexistent")
    result = git_topology.resolve_bare_repo_worktree(None, "nonexistent")
    assert result == Path("/wt/nonexistent")
    mock_create.assert_called_once_with("nonexistent", None)


@patch("git.topology.create_worktree_for_branch", return_value=None)
@patch("git.topology.find_worktree_for_branch")
def test_resolve_bare_repo_worktree_never_substitutes_default(mock_find, mock_create):
    """Regression: returning main's worktree here let callers hijack main/."""
    mock_find.return_value = None
    result = git_topology.resolve_bare_repo_worktree(None, "nonexistent")
    assert result is None
    # find_worktree_for_branch must never be consulted for the default branch
    # when an explicit branch was requested.
    for call in mock_find.call_args_list:
        assert call.args[0] != "main"


@patch("git.topology.find_worktree_dir_named", return_value=None)
@patch("git.topology.find_worktree_for_branch", return_value=None)
@patch("git.topology.subprocess.run")
def test_resolve_bare_repo_worktree_returns_none(mock_run, mock_find, mock_named):
    mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
    result = git_topology.resolve_bare_repo_worktree(None, None)
    assert result is None


@patch("git.topology.find_worktree_dir_named", return_value=Path("/repo/main"))
@patch("git.topology.find_worktree_for_branch", return_value=None)
@patch("git.topology.subprocess.run")
def test_resolve_bare_repo_worktree_falls_back_to_dir_name(mock_run, mock_find, mock_named):
    """No branch requested: a main/ holding someone else's branch is still a cwd.

    Regression: tightening find_worktree_for_branch made this return None, and
    the callers that dereference worktree_root without a guard blew up.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
    assert git_topology.resolve_bare_repo_worktree(None, None) == Path("/repo/main")


@patch("git.topology.find_worktree_for_branch")
@patch("git.topology.resolve_branch", return_value="isaac/improve-ci-failures-skill")
def test_resolve_bare_repo_worktree_fuzzy_resolves_branch(mock_resolve, mock_find):
    """Bare repo resolution uses fuzzy matching when exact branch hint doesn't match."""
    mock_find.side_effect = [None, Path("/wt/isaac-improve-ci-failures-skill")]
    result = git_topology.resolve_bare_repo_worktree(None, "isaac-improve-ci-failures-skill")
    assert result == Path("/wt/isaac-improve-ci-failures-skill")
    assert mock_find.call_count == 2
    mock_find.assert_any_call("isaac-improve-ci-failures-skill", None)
    mock_find.assert_any_call("isaac/improve-ci-failures-skill", None)


@patch("git.topology.create_worktree_for_branch")
@patch("git.topology.resolve_branch", side_effect=lambda hint, cwd=None: hint)
@patch("git.topology.find_worktree_for_branch", return_value=None)
def test_find_bare_repo_worktree_creates_nothing(_mock_find, _mock_resolve, mock_create):
    """The non-creating half of the pair: a command that only reads state must
    not leave a checkout behind as the price of finding its target."""
    assert git_topology.find_bare_repo_worktree(None, "nonexistent") is None
    mock_create.assert_not_called()


@patch("git.topology.create_worktree_for_branch")
@patch("git.topology.find_worktree_dir_named", return_value=None)
@patch("git.topology.find_worktree_for_branch", return_value=None)
@patch("git.topology.subprocess.run")
def test_find_bare_repo_worktree_creates_nothing_without_a_branch(
        mock_run, _mock_find, _mock_named, mock_create):
    mock_run.return_value = MagicMock(returncode=0, stdout="refs/remotes/origin/main\n")
    assert git_topology.find_bare_repo_worktree(None, None) is None
    mock_create.assert_not_called()


# ── create_worktree_for_branch ─────────────────────────────────────────────


@patch("git.topology.subprocess.run")
def test_create_worktree_for_branch_returns_path(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout='✓ created\n{"action":"created","path":"/wt/feat-x"}\n',
    )
    assert git_topology.create_worktree_for_branch("feat/x") == Path("/wt/feat-x")
    assert mock_run.call_args.args[0][:3] == ["wt", "switch", "feat/x"]


@patch("git.topology.subprocess.run")
def test_create_worktree_for_branch_passes_cwd(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout='{"path":"/wt/feat-x"}\n',
    )
    git_topology.create_worktree_for_branch("feat/x", "/repo")
    assert mock_run.call_args.args[0][-2:] == ["-C", "/repo"]


@patch("git.topology.subprocess.run")
def test_create_worktree_for_branch_returns_none_when_wt_fails(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    assert git_topology.create_worktree_for_branch("feat/x") is None


@patch("git.topology.subprocess.run")
def test_create_worktree_for_branch_survives_malformed_json(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="{not json}\n", stderr="")
    assert git_topology.create_worktree_for_branch("feat/x") is None


@patch("git.topology.log")
@patch("git.topology.subprocess.run",
       side_effect=FileNotFoundError(2, "No such file or directory", "wt"))
def test_create_worktree_warns_once_when_wt_is_missing(_mock_run, mock_log):
    assert git_topology.create_worktree_for_branch("feat/x") is None
    assert mock_log.warn.call_count == 1
    assert "not installed" in mock_log.warn.call_args.args[0]


@patch("git.topology.log")
@patch("git.topology.subprocess.run",
       side_effect=PermissionError(13, "Permission denied", "wt"))
def test_create_worktree_warns_once_when_wt_cannot_run(_mock_run, mock_log):
    assert git_topology.create_worktree_for_branch("feat/x") is None
    assert mock_log.warn.call_count == 1
    assert "Permission denied" in mock_log.warn.call_args.args[0]


@patch("git.topology.log")
@patch("git.topology.subprocess.run")
def test_create_worktree_warns_once_when_wt_reports_no_path(mock_run, mock_log):
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="error: branch feat/x is checked out")

    assert git_topology.create_worktree_for_branch("feat/x") is None
    assert mock_log.warn.call_count == 1
    assert "branch feat/x is checked out" in mock_log.warn.call_args.args[0]


# ── wt switch ──────────────────────────────────────────────────────────────


def _stub_raise(monkeypatch, exc):
    def boom(*args, **kwargs):
        raise exc
    monkeypatch.setattr(git_topology.subprocess, "run", boom)


def test_wt_switch_says_not_installed_when_wt_is_missing(monkeypatch, capsys):
    _stub_raise(monkeypatch, FileNotFoundError(2, "No such file or directory", "wt"))

    assert git_topology.wt_switch("feat/x") is None
    assert "not installed" in capsys.readouterr().err


def test_wt_switch_does_not_call_a_permission_error_a_missing_binary(monkeypatch, capsys):
    """Regression: every exception rendered as "worktrunk is not available"."""
    _stub_raise(monkeypatch, PermissionError(13, "Permission denied", "wt"))

    assert git_topology.wt_switch("feat/x") is None
    err = capsys.readouterr().err
    assert "not installed" not in err
    assert "Permission denied" in err


def test_wt_switch_reports_a_failed_run_rather_than_returning_none_silently(
        monkeypatch, capsys):
    _stub_run(monkeypatch, 1, stderr="error: no branch named feat/x")

    assert git_topology.wt_switch("feat/x") is None
    assert "no branch named feat/x" in capsys.readouterr().err


def test_wt_switch_stays_quiet_when_it_lands_on_a_worktree(monkeypatch, capsys):
    _stub_run(monkeypatch, 0, stdout='{"path": "/repo/feat-x"}\n')

    assert git_topology.wt_switch("feat/x") == "/repo/feat-x"
    assert capsys.readouterr().err == ""
