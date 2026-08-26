"""Tests for lib/git_remote.py, and that it still agrees with lib/git_remote.sh.

The two halves answer the same question for different callers — the bash one for
both pre-push hooks, the AI Taskfile automation and the surface gate, the Python
one for `ai/lib/pr_context.py` — so a repository must not be told its trunk is
`master` by one and `main` by the other. Rather than compare the source text
(the ladder is a procedure, not a list), every case below builds one repository
and asks both.
"""

import subprocess
from pathlib import Path

import pytest

from conftest import _load_lib, git_in

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "lib" / "git_remote.sh"

git_remote = _load_lib("git_remote")


def _make_clone(tmp_path: Path, initial_branch: str, extra_branches=()) -> Path:
    """A clone of a bare remote whose HEAD symref was never set.

    The shape an unfetched clone and a `wt-init`-converted repo both end up in:
    the clone happened before the remote had a commit for HEAD to point at, so
    there is no `refs/remotes/origin/HEAD` and the candidate ladder is what
    decides the answer.
    """
    remote = tmp_path / "remote.git"
    clone = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", "-q", f"--initial-branch={initial_branch}",
                    str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True,
                   capture_output=True)
    git_in(clone, "-c", "user.name=t", "-c", "user.email=t@t",
           "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
    git_in(clone, "push", "-q", "origin", initial_branch)
    for branch in extra_branches:
        git_in(clone, "push", "-q", "origin", f"{initial_branch}:{branch}")

    # No `git fetch` anywhere above, and asserted rather than trusted: git 2.46
    # sets `refs/remotes/origin/HEAD` on a fetch that finds it missing, which
    # would hand every case below its answer from the symref rung and leave the
    # candidate ladder — the thing these tests are about — never run.
    symref = subprocess.run(["git", "-C", str(clone), "symbolic-ref",
                             "refs/remotes/origin/HEAD"], capture_output=True)
    assert symref.returncode != 0, "fixture leaked an origin/HEAD symref"
    return clone


def _shell(function: str, *args: str, cwd: Path):
    """Call one lib/git_remote.sh function, returning (status, stdout)."""
    result = subprocess.run(
        ["bash", "-c", f'. "$1"; shift; "$@"', "_", str(SHELL), function, *args],
        capture_output=True, text=True, cwd=str(cwd), timeout=30,
    )
    return result.returncode, result.stdout.strip()


# Each case is (initial_branch, extra_branches, expected default branch name).
# `develop` alone is the rung where the ladder runs out and the literal wins.
LADDER_CASES = [
    ("main", (), "main"),
    ("master", (), "master"),
    ("develop", (), "main"),
    ("develop", ("master",), "master"),
    ("develop", ("main", "master"), "main"),
]


@pytest.mark.parametrize("initial,extra,expected", LADDER_CASES)
def test_the_two_halves_walk_the_same_ladder(tmp_path, initial, extra, expected):
    clone = _make_clone(tmp_path, initial, extra)

    status, shell_answer = _shell("resolve_default_branch", cwd=clone)
    assert status == 0
    assert shell_answer == expected
    assert git_remote.resolve_default_branch(str(clone)) == expected


def test_the_two_halves_follow_the_symref(tmp_path):
    """`git remote set-head` outranks the candidate list in both halves."""
    clone = _make_clone(tmp_path, "trunk", ("main",))
    git_in(clone, "remote", "set-head", "origin", "trunk")

    assert _shell("resolve_default_branch", cwd=clone) == (0, "trunk")
    assert git_remote.resolve_default_branch(str(clone)) == "trunk"


def test_the_two_halves_refuse_the_same_guess(tmp_path):
    """The rung where the name is a literal, not a ref anybody fetched.

    Bash says so by returning 1 and printing nothing, Python by returning None.
    A caller about to run `git diff` needs that, whichever language it is in.
    """
    clone = _make_clone(tmp_path, "develop")

    assert git_remote.resolve_default_branch(str(clone)) == "main"
    status, output = _shell("default_base_ref", cwd=clone)
    assert status != 0
    assert output == ""
    assert git_remote.default_base_ref(str(clone)) is None


def test_the_two_halves_name_the_same_base_ref(tmp_path):
    clone = _make_clone(tmp_path, "master")

    assert _shell("default_base_ref", cwd=clone) == (0, "origin/master")
    assert git_remote.default_base_ref(str(clone)) == "origin/master"


def test_the_two_halves_agree_on_which_remote(tmp_path):
    """One spelling of "origin", so a rename would not half-land."""
    shell_remote = subprocess.run(
        ["bash", "-c", '. "$1"; printf %s "$GIT_REMOTE"', "_", str(SHELL)],
        capture_output=True, text=True, timeout=30,
    ).stdout
    assert shell_remote == git_remote.GIT_REMOTE


def test_remote_branch_ref_exists_answers_for_a_fetched_branch(tmp_path):
    clone = _make_clone(tmp_path, "trunk")

    assert git_remote.remote_branch_ref_exists("trunk", str(clone))
    assert not git_remote.remote_branch_ref_exists("main", str(clone))


def test_a_directory_that_is_not_a_repo_still_answers(tmp_path):
    """resolve_default_branch always answers — that is its half of the contract.

    default_base_ref is the one allowed to refuse, and here it must, because
    there is no repository behind the name it would otherwise hand to git.
    """
    assert git_remote.resolve_default_branch(str(tmp_path)) == "main"
    assert git_remote.default_base_ref(str(tmp_path)) is None
