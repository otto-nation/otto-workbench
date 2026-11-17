"""Worktree listing and indexing."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import git.topology  # noqa: E402

from conftest import git_in, seed_repo  # noqa: E402
from git_topology_support import paused_rebase  # noqa: E402


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

    entries = git.topology.worktree_entries("/repo")

    assert [e.path for e in entries] == [Path("/repo/main"), Path("/repo/feat-x")]
    assert [e.branch for e in entries] == ["main", "feat/x"]


def test_worktree_entries_warns_when_git_exits_nonzero(tmp_path, capsys):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    assert git.topology.worktree_entries(str(empty)) == []
    err = capsys.readouterr().err
    assert str(empty) in err
    assert "not a git repository" in err


@patch("git.topology.subprocess.run")
def test_worktree_entries_drops_the_bare_repo(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/main", "main"), ("/repo/feat-x", "feat/x"), bare="/repo/.git",
    ))

    assert all(e.path.name != ".git" for e in git.topology.worktree_entries("/repo"))


# ── WorktreeIndex ────────────────────────────────────────────


@patch("git.topology.subprocess.run")
def test_worktrees_by_branch_lists_once_for_n_lookups(mock_run):
    """N for_branch lookups on one index issue one porcelain listing."""
    mock_run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/main", "main"), ("/repo/feat-x", "feat/x"), ("/repo/feat-y", "feat/y"),
    ))
    index = git.topology.worktrees_by_branch("/repo")
    assert [index.for_branch(b) for b in ("main", "feat/x", "feat/y")] == [
        Path("/repo/main"), Path("/repo/feat-x"), Path("/repo/feat-y")]
    argv = ["git", "worktree", "list", "--porcelain"]
    assert sum(1 for c in mock_run.call_args_list if (c.args[0] if c.args else [])[:4] == argv) == 1


@patch.object(git.topology, "rebasing_branch")
@patch("git.topology.subprocess.run")
def test_worktree_index_holds_attached_rebasing_and_named(mock_run, mock_rebasing):
    mock_run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/main", "main"), ("/repo/feat-x", "feat/x"),
        ("/repo/unrelated", None), ("/repo/feat-z", None), bare="/repo/.git",
    ))
    mock_rebasing.side_effect = lambda cwd: "feat/y" if cwd == "/repo/unrelated" else None
    index = git.topology.worktrees_by_branch("/repo")
    assert index.branches == {"main": Path("/repo/main"), "feat/x": Path("/repo/feat-x"), "feat/y": Path("/repo/unrelated")}
    assert index.detached_names == {"feat-z": Path("/repo/feat-z")}


def test_worktrees_by_branch_nonexistent_cwd_is_empty():
    index = git.topology.worktrees_by_branch("/no/such/worktree-index-cwd")
    assert index.branches == {} and index.detached_names == {} and index.for_branch("main") is None


@patch("git.topology.subprocess.run")
def test_worktree_index_for_branch_ignores_attached_sanitized_name(mock_run):
    """An attached isaac-feat-auth worktree is not isaac/feat/auth."""
    mock_run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/isaac-feat-auth", "isaac-feat-auth"), ("/repo/main", "main"),
    ))
    assert git.topology.worktrees_by_branch("/repo").for_branch("isaac/feat/auth") is None


def test_worktrees_by_branch_real_git_attached_and_detached_named(tmp_path):
    repo = seed_repo(tmp_path / "repo")
    attached, detached = tmp_path / "feat-x", tmp_path / "feat-y"
    git_in(repo, "worktree", "add", "-b", "feat/x", str(attached))
    git_in(repo, "worktree", "add", "--detach", str(detached))
    index = git.topology.worktrees_by_branch(str(repo))
    assert index.for_branch("feat/x").resolve() == attached.resolve()
    assert index.for_branch("feat/y").resolve() == detached.resolve()


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_found(mock_sub):
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/home/user/repo/feat-branch", "feat/branch"),
        ("/home/user/repo/main", "main"),
    ))
    result = git.topology.find_worktree_for_branch("feat/branch")
    assert result == Path("/home/user/repo/feat-branch")


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_not_found(mock_sub):
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/home/user/repo/main", "main"),
    ))
    result = git.topology.find_worktree_for_branch("nonexistent")
    assert result is None


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_detached_head_fallback(mock_sub):
    """Detached-HEAD worktree has no branch — falls back to sanitized dir name."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/home/user/repo/isaac-feat-auth", None),
        ("/home/user/repo/main", "main"),
    ))
    result = git.topology.find_worktree_for_branch("isaac/feat/auth")
    assert result == Path("/home/user/repo/isaac-feat-auth")


@patch.object(git.topology, "rebasing_branch", return_value=None)
@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_reads_each_detached_rebase_state_once(mock_sub, mock_rebasing):
    """The rebasing pass and the name pass share one lookup per detached worktree."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/home/user/repo/isaac-feat-auth", None),
        ("/home/user/repo/main", "main"),
    ))
    result = git.topology.find_worktree_for_branch("isaac/feat/auth")
    assert result == Path("/home/user/repo/isaac-feat-auth")
    assert mock_rebasing.call_count == 1


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_prefers_branch_over_dir_name(mock_sub):
    """When git reports the branch, prefer it over the directory-name fallback."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/home/user/repo/wrong-dir", "feat/branch"),
        ("/home/user/repo/feat-branch", None),
    ))
    result = git.topology.find_worktree_for_branch("feat/branch")
    assert result == Path("/home/user/repo/wrong-dir")


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_sanitized_no_match(mock_sub):
    """Neither the branch nor a sanitized dir name matches — returns None."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/home/user/repo/other-branch", None),
        ("/home/user/repo/main", "main"),
    ))
    result = git.topology.find_worktree_for_branch("feat/nonexistent")
    assert result is None


@patch.object(git.topology, "rebasing_branch", return_value=None)
@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_exact_beats_detached_named(mock_sub, _mock_rebasing):
    """An exact branch beats a detached worktree named after the branch."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/other-dir", "feat/x"),
        ("/repo/feat-x", None),
        bare="/repo/.git",
    ))
    result = git.topology.find_worktree_for_branch("feat/x")
    assert result == Path("/repo/other-dir")


@patch.object(git.topology, "rebasing_branch")
@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_rebasing_beats_directory_name(
        mock_sub, mock_rebasing):
    """A detached worktree mid-rebase of the branch beats a directory-name match."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/feat-x", None),
        ("/repo/unrelated-name", None),
        bare="/repo/.git",
    ))
    mock_rebasing.side_effect = lambda cwd: (
        "feat/x" if cwd == "/repo/unrelated-name" else None
    )
    result = git.topology.find_worktree_for_branch("feat/x")
    assert result == Path("/repo/unrelated-name")


@patch.object(git.topology, "rebasing_branch", return_value="other/branch")
@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_named_but_rebasing_another_is_not_returned(
        mock_sub, _mock_rebasing):
    """A detached named worktree that is rebasing another branch is not returned."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/feat-x", None),
        bare="/repo/.git",
    ))
    result = git.topology.find_worktree_for_branch("feat/x")
    assert result is None


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_named_with_different_branch_is_not_returned(
        mock_sub):
    """A named worktree with a different branch checked out is not returned."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/feat-x", "other/branch"),
        bare="/repo/.git",
    ))
    result = git.topology.find_worktree_for_branch("feat/x")
    assert result is None


@patch.object(git.topology, "subprocess")
def test_find_worktree_for_branch_never_returns_the_bare_record(mock_sub):
    """The bare record is never returned."""
    mock_sub.run.return_value = MagicMock(returncode=0, stdout=_porcelain(
        ("/repo/main", "main"),
        bare="/repo/feat-x",
    ))
    result = git.topology.find_worktree_for_branch("feat/x")
    assert result is None


def test_find_worktree_for_branch_finds_a_worktree_mid_rebase(tmp_path):
    wt = paused_rebase(tmp_path, worktree=True)
    repo = tmp_path / "repo"
    found = git.topology.find_worktree_for_branch("feat", str(repo))
    assert found is not None
    assert found.resolve() == wt.resolve()


def test_find_worktree_for_branch_skips_a_renamed_worktree_mid_rebase_of_another_branch(
    tmp_path,
):
    """A directory named for B that is mid-rebase of A is not B's checkout."""
    wt = paused_rebase(tmp_path, worktree=True)
    repo = tmp_path / "repo"
    moved = tmp_path / "other-branch"
    git_in(repo, "worktree", "move", str(wt), str(moved))
    assert git.topology.find_worktree_for_branch("other-branch", str(repo)) is None


# ── find_worktree_for_branch ──────────────────────────────────────────────


def _hijacked_worktree_list() -> str:
    """A bare repo whose main/ dir holds feat/x, plus feat-other's own worktree."""
    return _porcelain(
        ("/repo/main", "feat/x"), ("/repo/feat-other", "feat/other"), bare="/repo",
    )


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_prefers_exact_tag(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout=_hijacked_worktree_list())
    assert git.topology.find_worktree_for_branch("feat/other") == Path("/repo/feat-other")


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_ignores_dir_named_like_another_branch(mock_run):
    """Regression: /repo/main holding feat/x was returned as main's worktree.

    review-threads then hard-reset it to origin/main, destroying feat/x.
    """
    mock_run.return_value = MagicMock(returncode=0, stdout=_hijacked_worktree_list())
    assert git.topology.find_worktree_for_branch("main") is None


@patch("git.topology.subprocess.run")
def test_find_worktree_dir_named_matches_regardless_of_occupant(mock_run):
    """The lenient lookup answers "which directory", not "which branch"."""
    mock_run.return_value = MagicMock(returncode=0, stdout=_hijacked_worktree_list())
    assert git.topology.find_worktree_dir_named("main") == Path("/repo/main")


@patch("git.topology.subprocess.run")
def test_find_worktree_dir_named_skips_the_bare_repo(mock_run):
    """The bare entry is not a checkout and must never be handed back as one."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_porcelain(bare="/repo/main"),
    )
    assert git.topology.find_worktree_dir_named("main") is None


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_still_matches_detached_head_by_name(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_porcelain(("/repo/main", None), bare="/repo"),
    )
    assert git.topology.find_worktree_for_branch("main") == Path("/repo/main")


@patch("git.topology.subprocess.run")
def test_find_worktree_for_branch_handles_paths_with_spaces_and_brackets(mock_run):
    """The human listing packs path and [branch] onto one line; porcelain doesn't."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_porcelain(("/repo/we ird [x]", "spacey")),
    )
    assert git.topology.find_worktree_for_branch("spacey") == Path("/repo/we ird [x]")
