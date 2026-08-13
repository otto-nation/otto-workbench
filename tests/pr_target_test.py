"""Tests for the run-target path contract.

SLUG_VECTORS is a cross-repo fixture: ui-code's TypeScript mirror asserts
against the same table. Changing a row changes where live runs look for their
own state, in a repo whose tests cannot see this one.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import pr_target


SLUG_VECTORS = [
    ("main", "main"),
    ("isaac/fix/target_scoped_run_lock", "isaac-fix-target_scoped_run_lock"),
    ("feat/a--b", "feat-a--b"),
    ("release/v1.2.3", "release-v1.2.3"),
    ("/leading/", "leading"),
    ("dependabot/npm_and_yarn/foo/bar-1.0.0", "dependabot-npm_and_yarn-foo-bar-1.0.0"),
]


@pytest.mark.parametrize("branch,expected", SLUG_VECTORS)
def test_slug_vectors(branch, expected):
    assert pr_target.slug(branch) == expected


def test_target_dir_is_rooted_at_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    assert pr_target.target_dir("otto-workbench", "feat/x") == (
        tmp_path / "pr" / "otto-workbench-feat-x"
    )


def test_target_dir_follows_a_moved_state_root(tmp_path, monkeypatch):
    """state_dir() resolves per call, so #624 phase 4 carries pr/ for free."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "old"))
    before = pr_target.target_dir("repo", "main")
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "new"))
    after = pr_target.target_dir("repo", "main")
    assert before != after
    assert after.parent.parent == tmp_path / "new"


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/otto-nation/otto-workbench.git", "otto-workbench"),
    ("https://github.com/otto-nation/otto-workbench", "otto-workbench"),
    ("git@github.com:otto-nation/otto-workbench.git", "otto-workbench"),
    ("git@github.com:otto-workbench.git", "otto-workbench"),
    ("/srv/mirrors/otto-workbench/", "otto-workbench"),
])
def test_repo_name_parsing(url, expected):
    assert pr_target._repo_name(url) == expected


def _git_repo(path: Path, origin: str, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


def test_target_dir_for_checkout_matches_target_dir(tmp_path, monkeypatch):
    """The two derivations of one identity, asserted equal rather than assumed."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "git@github.com:acme/widget.git", "feat/login")
    assert pr_target.target_dir_for_checkout(wt) == pr_target.target_dir("widget", "feat/login")


def test_target_dir_for_checkout_prefers_origin_over_any_api_name(tmp_path, monkeypatch):
    """The derived directory name comes from `origin`, not any API-reported name.

    No `gh` call exists in pr_target at all, so there is no code path here that
    a network name could reach — asserted by the module's contents, not by
    breaking PATH to prove git can't shell out.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "https://github.com/acme/renamed-clone.git", "main")
    assert pr_target.target_dir_for_checkout(wt).name == "renamed-clone-main"


def test_target_dir_for_checkout_is_none_without_an_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "wt"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    assert pr_target.target_dir_for_checkout(path) is None


def test_target_dir_for_checkout_is_none_on_detached_head(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "git@github.com:acme/widget.git")
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                                    "PATH": "/usr/bin:/bin"})
    subprocess.run(["git", "-C", str(wt), "checkout", "-q", "--detach", "HEAD"], check=True)
    assert pr_target.target_dir_for_checkout(wt) is None
