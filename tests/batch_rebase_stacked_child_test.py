"""A stacked child after its parent is rebased looks partially landed.

The batch rebase step should replay only the child's unique commits, using
`--fork-point merge-base(parent_pre_rebase_head, child)` (not `--force`, which
replays the parent's old commits onto the rewritten parent). A
`rebase_refused` decision whose payload names that fork point should offer an
action that runs that argv.
"""

import sys
from pathlib import Path

from conftest import git_in, git_out, init_repo, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.steps  # noqa: E402
import gh.landed  # noqa: E402


def _commit(repo: Path, name: str, subject: str) -> str:
    (repo / name).write_text(f"{name}\n")
    git_in(repo, "add", name)
    git_in(repo, "commit", "-q", "-m", subject)
    return git_out(repo, "rev-parse", "HEAD").strip()


def _subjects(repo: Path, since: str, until: str = "HEAD") -> list[str]:
    """Commit subjects *since*..*until*, oldest first — the set a rebase replays."""
    return git_out(
        repo, "log", "--reverse", "--no-merges", "--format=%s",
        f"{since}..{until}",
    ).splitlines()


def _ok_ancestor(repo: Path, commit: str, of: str) -> bool:
    return run_checked(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, of],
        check=False,
    ).returncode == 0


def _stacked(tmp_path, *, extra_parent: bool = False) -> Path:
    """main, a two-commit parent, and a child stacked on the parent.

    Each commit owns its own file so a later rebase onto advanced main is
    clean. *extra_parent* adds a third parent commit after the child forks,
    so the parent's tip is no longer an ancestor of the child.
    """
    repo = init_repo(tmp_path / "repo")
    _commit(repo, "root.txt", "chore: root")
    git_in(repo, "checkout", "-q", "-b", "parent")
    _commit(repo, "p1.txt", "feat: parent one")
    _commit(repo, "p2.txt", "feat: parent two")
    git_in(repo, "checkout", "-q", "-b", "child")
    _commit(repo, "c1.txt", "feat: child")
    if extra_parent:
        git_in(repo, "checkout", "-q", "parent")
        _commit(repo, "p3.txt", "feat: parent three")
    return repo


def _advance_main_and_rebase_parent(repo: Path) -> str:
    """Advance main, rebase parent onto it, return the parent's pre-rebase tip."""
    git_in(repo, "checkout", "-q", "parent")
    old_parent = git_out(repo, "rev-parse", "HEAD").strip()
    git_in(repo, "checkout", "-q", "main")
    _commit(repo, "main2.txt", "chore: advance main")
    git_in(repo, "checkout", "-q", "parent")
    git_in(repo, "rebase", "main")
    return old_parent


def test_rebasing_the_parent_makes_the_child_look_partially_landed(tmp_path):
    """The child's prefix is the parent's old commits; they match the rewrite."""
    repo = _stacked(tmp_path)
    old_parent = _advance_main_and_rebase_parent(repo)

    partial = gh.landed.partial_landing(str(repo), target_ref="parent", rev="child")

    assert partial is not None
    assert partial.landed == 2
    assert partial.unlanded == 1
    assert partial.fork_point == old_parent
    assert partial.fork_subject == "feat: parent two"


def test_force_replays_parent_commits_fork_point_replays_child_only(tmp_path):
    """`git rebase parent` on the child replays P1, P2, C1 — not just C1."""
    repo = _stacked(tmp_path)
    old_parent = _advance_main_and_rebase_parent(repo)

    force_set = _subjects(repo, "parent", "child")
    fork_set = _subjects(repo, old_parent, "child")

    assert force_set == ["feat: parent one", "feat: parent two", "feat: child"]
    assert fork_set == ["feat: child"]


def test_parent_grew_after_fork_so_old_tip_is_not_the_fork_point(tmp_path):
    """Parent grew after the child forked; the real fork is an earlier commit.

    The parent's pre-rebase tip is not on the child, so passing that tip to
    `--fork-point` is rejected. merge-base(parent_pre_rebase_head, child) is
    the last shared parent commit.
    """
    repo = _stacked(tmp_path, extra_parent=True)
    parent_pre = git_out(repo, "rev-parse", "parent").strip()
    git_in(repo, "checkout", "-q", "child")

    assert not _ok_ancestor(repo, parent_pre, "child")
    fork = git_out(repo, "merge-base", parent_pre, "child").strip()
    assert fork != parent_pre
    assert _ok_ancestor(repo, fork, "child")
    assert git_out(repo, "log", "-1", "--format=%s", fork).strip() == "feat: parent two"
    assert _subjects(repo, fork, "child") == ["feat: child"]

    _advance_main_and_rebase_parent(repo)
    partial = gh.landed.partial_landing(str(repo), target_ref="parent", rev="child")
    assert partial is not None
    assert partial.fork_point == fork
    assert partial.fork_subject == "feat: parent two"


def test_batch_rebase_argv_uses_merge_base_of_parent_pre_rebase_head(tmp_path):
    """When the parent grew after the child forked, the fork point is earlier.

    Parent's recorded `pre_rebase_head` is not an ancestor of the child; the
    argv must name merge-base(that tip, child), not the tip itself.
    """
    repo = _stacked(tmp_path, extra_parent=True)
    parent_pre = git_out(repo, "rev-parse", "parent").strip()
    git_in(repo, "checkout", "-q", "child")
    expected = git_out(repo, "merge-base", parent_pre, "child").strip()
    assert expected != parent_pre
    assert git_out(repo, "log", "-1", "--format=%s", expected).strip() == (
        "feat: parent two"
    )

    argv = batch.steps.step_argv(
        batch.model.Step.REBASE, "pr", str(repo),
        fork_point=expected,
    )

    assert "--fork-point" in argv
    assert argv[argv.index("--fork-point") + 1] == expected
    assert "--force" not in argv


