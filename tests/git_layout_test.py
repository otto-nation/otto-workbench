"""Tests for git_layout — where a bare repo's container is, and asking once.

`container_dir` is the one owner of that question, so every caller that walks a
list of checkouts goes through it: the permission sweep, and now
`otto-workbench config get` over the whole project registry. A registry holds
ten worktrees of one repo as readily as ten repos, and they all name the same
shared git dir — which is what the memo is keyed on.
"""

from __future__ import annotations

import pytest
from conftest import _load_lib, add_worktree, run_checked, seed_repo

git_layout = _load_lib("git_layout")


@pytest.fixture(autouse=True)
def _clear_memo():
    """Start and finish each test with nothing remembered.

    The memo lives for the life of the process, which under pytest is the whole
    session rather than one command. Nothing here would collide — every path is
    under its own tmp_path — but a test asserting how many times git ran cannot
    depend on whether another test warmed the entry first.
    """
    git_layout._CONTAINERS.clear()
    yield
    git_layout._CONTAINERS.clear()


def test_a_plain_clone_has_no_container(tmp_path):
    """The parent of an ordinary checkout belongs to somebody else."""
    repo = seed_repo(tmp_path / "repo")
    assert git_layout.container_dir(str(repo)) is None


def test_a_worktree_names_the_directory_holding_the_bare_git(container):
    worktree = container / "main"
    assert git_layout.container_dir(str(worktree)) == str(container)


def test_every_worktree_of_one_repo_gets_the_same_answer(container):
    other = add_worktree(container, "feat")
    assert git_layout.container_dir(str(container / "main")) == str(container)
    assert git_layout.container_dir(str(other)) == str(container)


def test_the_second_worktree_costs_one_git_call(container, monkeypatch):
    """What keeps a walk over a registry flat rather than per-checkout.

    The shared git dir still has to be asked for — it is the key — but the
    `--show-toplevel` reads and the no-working-tree probe behind it are paid
    once per repo instead of once per checkout.
    """
    other = add_worktree(container, "feat")
    real_git = git_layout.git
    calls = []

    def counting_git(repo_root, *args):
        calls.append(args)
        return real_git(repo_root, *args)

    monkeypatch.setattr(git_layout, "git", counting_git)

    git_layout.container_dir(str(container / "main"))
    warm = len(calls)
    assert warm > 1

    git_layout.container_dir(str(other))
    assert len(calls) == warm + 1


def test_the_memo_does_not_answer_for_another_repo(tmp_path, container):
    """Keyed on the shared git dir, so two repos can never share an entry."""
    assert git_layout.container_dir(str(container / "main")) == str(container)
    plain = seed_repo(tmp_path / "plain")
    assert git_layout.container_dir(str(plain)) is None


def test_a_directory_that_is_not_a_repo_is_not_remembered(tmp_path, monkeypatch):
    """Nothing is cached until git has named the shared dir.

    A path that becomes a repo later — a clone a script is about to make — must
    not be stuck on the answer it gave before there was anything there.
    """
    later = tmp_path / "later"
    later.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert git_layout.container_dir(str(later)) is None
    assert git_layout._CONTAINERS == {}


class TestWorktreeFor:
    """The other direction — container → the checkout it stands in for.

    A wrapper over `bin/resolve-worktree` rather than a reimplementation, so
    what is worth asserting here is that the script's exit codes survive the
    trip and that the wrapper does not invent an answer where the script
    declined to give one.
    """

    def test_a_container_names_its_default_branch_checkout(self, container):
        resolved = git_layout.worktree_for(str(container))
        assert resolved.ok
        assert resolved.path == str(container / "main")
        assert resolved.status == git_layout.RESOLVED

    def test_a_worktree_is_not_a_container(self, container):
        """NOT_BARE is the ordinary answer, and it carries no path."""
        resolved = git_layout.worktree_for(str(container / "main"))
        assert not resolved.ok
        assert resolved.path is None
        assert resolved.status == git_layout.NOT_BARE

    def test_a_plain_clone_is_not_a_container(self, tmp_path):
        repo = seed_repo(tmp_path / "repo")
        assert git_layout.worktree_for(str(repo)).status == git_layout.NOT_BARE

    def test_a_container_with_no_worktree_resolves_to_nothing(self, tmp_path):
        """Exit 1 is a refusal to guess, and must not arrive as a path.

        A container naming no checkout has no tree to offer. Answering with
        one anyway is how a tool writes into a worktree nobody asked for.
        """
        seed = seed_repo(tmp_path / "seed")
        root = tmp_path / "container"
        run_checked(["git", "clone", "-q", "--bare", str(seed), str(root / ".git")])
        resolved = git_layout.worktree_for(str(root))
        assert not resolved.ok
        assert resolved.path is None
        assert resolved.status == git_layout.UNRESOLVED

    def test_a_missing_directory_is_a_usage_error(self, tmp_path):
        resolved = git_layout.worktree_for(str(tmp_path / "nowhere"))
        assert resolved.status == git_layout.USAGE

    def test_an_inherited_git_dir_does_not_change_the_answer(self, container, monkeypatch):
        """The environment beats `-C`, and resolve-worktree does its own cd.

        With GIT_DIR set the script exits 128 — outside the codes it documents
        — unless the caller clears it. The pre-push hook exports one, so this
        is the state a gate actually runs in.
        """
        monkeypatch.setenv("GIT_DIR", str(container / ".git"))
        resolved = git_layout.worktree_for(str(container))
        assert resolved.ok
        assert resolved.path == str(container / "main")

    def test_an_unreachable_script_degrades_instead_of_raising(self, container, monkeypatch):
        """`bin/` does not ship in the otto-ai-tools tarball."""
        monkeypatch.setattr(git_layout, "_RESOLVE_WORKTREE", "/nonexistent/resolve-worktree")
        resolved = git_layout.worktree_for(str(container))
        assert not resolved.ok
        assert resolved.status == git_layout.UNAVAILABLE
