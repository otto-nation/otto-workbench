"""Tests for the run-target path contract.

SLUG_VECTORS is a cross-repo fixture: ui-code's TypeScript mirror asserts
against the same table. Changing a row changes where live runs look for their
own state, in a repo whose tests cannot see this one.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import pr_target


# Cross-repo fixture. ui-code reimplements slug() in TypeScript and asserts
# against this same table; nothing in this repo's CI can see that side, so a row
# edited here drifts silently until a live run looks for state in a directory the
# other implementation never writes. Change a row only together with ui-code.
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


# The URL-to-key contract, one row per form git accepts as an origin. These are
# the assertions, not illustrations of them: a changed row is a changed state
# directory, and any run already holding the old one keeps holding it.
REPO_KEY_VECTORS = [
    ("git@github.com:acme/widget.git", "acme-widget"),
    ("https://github.com/acme/widget.git", "acme-widget"),
    ("https://github.com/acme/widget", "acme-widget"),
    ("https://github.com/acme/widget/", "acme-widget"),
    ("ssh://git@github.com/acme/widget.git", "acme-widget"),
    ("ssh://git@host:2222/acme/widget.git", "acme-widget"),
    ("git://host/acme/widget.git", "acme-widget"),
    ("https://user:token@host/acme/widget", "acme-widget"),
    # file:// carries a scheme but an empty authority, so it names no host: it is
    # a filesystem path and keys as one.
    ("file:///srv/git/widget.git", "widget"),
    # An ~/.ssh/config Host alias. Scp-style with no "user@", which git accepts
    # and which therefore has to be qualified like any other hosted remote.
    ("gitbox:acme/widget.git", "acme-widget"),
    ("git@host:widget.git", "widget"),
    # The whole path below the host, not its last two segments — group-a/platform/api
    # and group-b/platform/api are different repos.
    ("https://gitlab.com/group/subgroup/widget.git", "group-subgroup-widget"),
    ("/srv/git/widget.git", "widget"),
    ("/srv/mirrors/otto-workbench/", "otto-workbench"),
    ("../widget", "widget"),
]


@pytest.mark.parametrize("url,expected", REPO_KEY_VECTORS)
def test_repo_key_vectors(url, expected):
    assert pr_target._repo_key(url) == expected


@pytest.mark.parametrize("a,b", [
    ("git@github.com:acme/api.git", "git@github.com:other-org/api.git"),
    ("gitbox:acme/api.git", "gitbox:other-org/api.git"),
    ("https://gitlab.com/group-a/platform/api.git",
     "https://gitlab.com/group-b/platform/api.git"),
])
def test_the_namespace_is_what_keeps_same_named_repos_apart(a, b):
    """The whole point of qualifying the key — one shared dir is one shared lock."""
    assert pr_target._repo_key(a) != pr_target._repo_key(b)


def test_a_file_url_keys_the_same_as_the_path_it_names():
    """One remote spelled two ways is still one target, so still one run.lock."""
    assert pr_target._repo_key("file:///srv/git/widget.git") == \
        pr_target._repo_key("/srv/git/widget.git")


def _git_repo(path: Path, origin: str, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


@pytest.mark.parametrize("origin,expected", [
    ("git@github.com:acme/widget.git", "acme-widget"),
    ("https://github.com/acme/widget.git", "acme-widget"),
    ("https://github.com/acme/widget", "acme-widget"),
    ("https://github.com/acme/widget/", "acme-widget"),
    ("gitbox:acme/widget.git", "acme-widget"),
    ("file:///srv/git/widget.git", "widget"),
    ("https://gitlab.com/group/subgroup/widget.git", "group-subgroup-widget"),
    ("/srv/git/widget.git", "widget"),
])
def test_repo_key_from_origin_reads_the_remote(tmp_path, origin, expected):
    assert pr_target.repo_key_from_origin(str(_git_repo(tmp_path / "wt", origin))) == expected


def test_repo_key_from_origin_is_none_without_an_origin(tmp_path):
    path = tmp_path / "wt"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    assert pr_target.repo_key_from_origin(str(path)) is None


def test_target_dir_for_checkout_matches_target_dir(tmp_path, monkeypatch):
    """The two derivations of one identity, asserted equal rather than assumed."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "git@github.com:acme/widget.git", "feat/login")
    assert pr_target.target_dir_for_checkout(wt) == pr_target.target_dir(
        "acme-widget", "feat/login")


def test_target_dir_for_checkout_prefers_origin_over_any_api_name(tmp_path, monkeypatch):
    """The derived directory name comes from `origin`, not any API-reported name.

    No `gh` call exists in pr_target at all, so there is no code path here that
    a network name could reach — asserted by the module's contents, not by
    breaking PATH to prove git can't shell out.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "https://github.com/acme/renamed-clone.git", "main")
    assert pr_target.target_dir_for_checkout(wt).name == "acme-renamed-clone-main"


def test_target_dir_for_checkout_is_none_without_an_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "wt"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    assert pr_target.target_dir_for_checkout(path) is None


def test_target_dir_for_checkout_is_none_on_detached_head(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = _git_repo(tmp_path / "wt", "git@github.com:acme/widget.git")
    # Layered onto os.environ, not substituted for it: a replacement env has to
    # guess PATH, and git is not under /usr/bin on a Homebrew install.
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env={**os.environ,
                                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    subprocess.run(["git", "-C", str(wt), "checkout", "-q", "--detach", "HEAD"], check=True)
    assert pr_target.target_dir_for_checkout(wt) is None
