"""gh.owner_targets: which repository a GitHub write is aimed at."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.github_owners
import gh.owner_targets

from conftest import git_in, remote_repo

KNOWN = frozenset({"ghe.corp"})


def _owner(spec):
    return config.github_owners.parse_owner(spec)


def _resolve(words, cwd, after_cd=False):
    return gh.owner_targets.resolve_statement(words, str(cwd), after_cd=after_cd, known_hosts=KNOWN)


@pytest.fixture(autouse=True)
def _no_inherited_gh_env(monkeypatch):
    monkeypatch.delenv("GH_REPO", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)


@pytest.mark.parametrize("words,expected", [
    (["gh", "issue", "create", "--repo", "Acme/widget"], "acme"),
    (["gh", "issue", "create", "-R", "acme/widget"], "acme"),
    (["gh", "issue", "create", "--repo=acme/widget"], "acme"),
    (["gh", "--repo", "acme/widget", "issue", "create"], "acme"),
    (["gh", "issue", "create", "--repo", "ghe.corp/platform/svc"], "ghe.corp/platform"),
    (["GH_REPO=acme/widget", "gh", "issue", "create"], "acme"),
    (["gh", "pr", "merge", "https://github.com/acme/widget/pull/3"], "acme"),
    (["gh", "api", "-X", "POST", "/repos/acme/widget/issues"], "acme"),
    (["gh", "api", "-X", "POST", "repos/acme/widget/issues", "--hostname", "ghe.corp"], "ghe.corp/acme"),
    (["pr", "review", "https://ghe.corp/acme/widget/pull/9"], "ghe.corp/acme"),
])
def test_an_explicit_target_wins(tmp_path, words, expected):
    resolution = _resolve(words, tmp_path)
    assert resolution.ok
    assert resolution.target.owners == (_owner(expected),)


def test_a_body_url_is_not_an_explicit_target(tmp_path):
    resolution = _resolve(
        ["gh", "issue", "create", "--repo", "ours/x",
         "--body", "https://github.com/upstream/y/issues/1"],
        tmp_path)
    assert resolution.ok
    assert resolution.target.owners == (_owner("ours"),)


def test_an_env_repo_does_not_stand_in_for_an_api_path(tmp_path):
    resolution = _resolve(["GH_REPO=ours/x", "gh", "api", "-X", "POST", "repos/stranger/lib/issues"], tmp_path)
    assert resolution.target.owners == (_owner("stranger"),)


def test_an_env_repo_fills_the_api_owner_placeholder(tmp_path):
    resolution = _resolve(["GH_REPO=acme/x", "gh", "api", "-X", "POST", "repos/{owner}/{repo}/issues"], tmp_path)
    assert resolution.target.owners == (_owner("acme"),)


def test_gh_host_moves_a_bare_repo_to_that_host(tmp_path):
    resolution = _resolve(["GH_HOST=ghe.corp", "gh", "issue", "create", "--repo", "acme/w"], tmp_path)
    assert resolution.target.owners == (_owner("ghe.corp/acme"),)


def test_gh_repo_in_the_environment_is_a_target(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_REPO", "acme/widget")
    assert _resolve(["gh", "issue", "create"], tmp_path).target.owners == (_owner("acme"),)


def test_without_one_every_remote_is_a_candidate(tmp_path):
    repo = remote_repo(tmp_path / "fork", "git@github.com:me/widget.git")
    git_in(repo, "remote", "add", "upstream", "https://github.com/Acme/widget.git")
    resolution = _resolve(["gh", "pr", "create"], repo)
    assert resolution.target.source is gh.owner_targets.TargetSource.REMOTES
    assert resolution.target.owners == (_owner("me"), _owner("acme"))


def test_an_ssh_alias_is_read_as_the_default_host(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com-work:otto-nation/svc.git")
    assert _resolve(["gh", "issue", "create"], repo).target.owners == (_owner("otto-nation"),)


def test_a_non_github_remote_is_not_a_candidate(tmp_path):
    repo = remote_repo(tmp_path / "r", "https://gitlab.com/someone/thing.git")
    git_in(repo, "remote", "add", "backup", "/srv/git/thing.git")
    resolution = _resolve(["gh", "issue", "create"], repo)
    assert not resolution.ok
    assert "--repo" in resolution.reason


def test_the_api_owner_placeholder_means_the_cwd_repo(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:acme/widget.git")
    resolution = _resolve(["gh", "api", "-X", "POST", "repos/{owner}/{repo}/issues"], repo)
    assert resolution.target.owners == (_owner("acme"),)


def test_workbench_repo_dir_reads_that_checkout(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:acme/widget.git")
    resolution = _resolve(["pr", "--repo-dir", str(repo), "create"], tmp_path)
    assert resolution.target.owners == (_owner("acme"),)


@pytest.mark.parametrize("words,why", [
    (["gh", "api", "graphql", "-f", "query=mutation { x }"], "node ID"),
    (["gh", "api", "-X", "POST", "user/keys"], "repos/"),
    (["gh", "issue", "create", "--repo", "widget"], "--repo widget"),
])
def test_an_unresolvable_write_says_why(tmp_path, words, why):
    resolution = _resolve(words, tmp_path)
    assert not resolution.ok
    assert why in resolution.reason


def test_a_cd_makes_the_cwd_unknowable(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:acme/widget.git")
    resolution = _resolve(["gh", "issue", "create"], repo, after_cd=True)
    assert not resolution.ok
    assert "changes directory" in resolution.reason


def test_a_cd_does_not_matter_to_an_explicit_repo(tmp_path):
    assert _resolve(["gh", "issue", "create", "--repo", "acme/w"], tmp_path, after_cd=True).ok


def test_no_repository_at_cwd_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    resolution = _resolve(["gh", "issue", "create"], plain)
    assert not resolution.ok
    assert "not a git repository" in resolution.reason


def test_the_client_resolves_a_graphql_write_by_its_hint(tmp_path):
    resolution = gh.owner_targets.resolve_gh_call(
        ("api", "graphql", "--input", "-"), cwd=str(tmp_path), target_repo="acme/widget", known_hosts=KNOWN)
    assert resolution.target.owners == (_owner("acme"),)


def test_the_client_falls_back_to_the_cwd_repo_for_graphql(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:acme/widget.git")
    resolution = gh.owner_targets.resolve_gh_call(
        ("api", "graphql", "--input", "-"), cwd=str(repo), target_repo="", known_hosts=KNOWN)
    assert resolution.target.owners == (_owner("acme"),)
