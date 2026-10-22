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
    (["gh", "issue", "create", "-Rstranger/lib"], "stranger"),
    (["gh", "issue", "create", "-R=stranger/lib"], "stranger"),
    (["gh", "-Rstranger/lib", "issue", "create"], "stranger"),
    (["gh", "pr", "merge", "HTTPS://GitHub.com/stranger/lib/pull/3"], "stranger"),
])
def test_an_explicit_target_wins(tmp_path, words, expected):
    resolution = _resolve(words, tmp_path)
    assert resolution.ok
    assert resolution.target.owners == (_owner(expected),)


def test_a_repo_flag_after_a_value_flag_unions_cwd_remotes(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:stranger/lib.git")
    resolution = _resolve(
        ["gh", "pr", "close", "3", "-c", "--repo=ours/svc"], repo)
    assert resolution.ok
    assert _owner("ours") in resolution.target.owners
    assert _owner("stranger") in resolution.target.owners


def test_a_repo_flag_inside_a_body_unions_cwd_remotes(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:stranger/lib.git")
    resolution = _resolve(
        ["gh", "issue", "create", "-t", "x", "-b", "--repo=ours/svc"], repo)
    assert resolution.ok
    assert _owner("ours") in resolution.target.owners
    assert _owner("stranger") in resolution.target.owners


def test_env_builtin_prefix_is_an_explicit_target(tmp_path):
    resolution = _resolve(
        ["env", "GH_REPO=acme/widget", "gh", "issue", "create"], tmp_path)
    assert resolution.ok
    assert resolution.target.owners == (_owner("acme"),)


def test_gh_resolved_pointing_at_a_stranger_is_a_candidate(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    git_in(repo, "config", "remote.origin.gh-resolved", "base:stranger/lib")
    resolution = _resolve(["gh", "issue", "comment", "1"], repo)
    assert resolution.ok
    assert resolution.target.source is gh.owner_targets.TargetSource.REMOTES
    assert resolution.target.owners == (_owner("ours"), _owner("stranger"))


def test_gh_resolved_base_keeps_the_remote_already_in_the_set(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    git_in(repo, "config", "remote.origin.gh-resolved", "base")
    resolution = _resolve(["gh", "issue", "comment", "1"], repo)
    assert resolution.target.owners == (_owner("ours"),)


@pytest.mark.parametrize("endpoint", [
    "repos/ours/svc/../../stranger/lib/issues",
    "repos/ours/svc/./issues",
    "repos/ours/svc/%2e%2e/stranger/lib/issues",
    "repos/ours/svc/%2E%2e/stranger/lib/issues",
    "repos/ours%2flib/issues",
])
def test_api_dot_segments_are_unresolvable(tmp_path, endpoint):
    resolution = _resolve(["gh", "api", "-X", "POST", endpoint], tmp_path)
    assert not resolution.ok
    assert "segment" in resolution.reason or "repos/" in resolution.reason or "%" in resolution.reason


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


def test_ambiguous_hostname_unions_the_default_host(tmp_path):
    resolution = _resolve(
        ["gh", "api", "-X", "POST", "-q", "--hostname=ghe.corp", "repos/acme/x/issues"],
        tmp_path)
    assert resolution.ok
    assert _owner("ghe.corp/acme") in resolution.target.owners
    assert _owner("acme") in resolution.target.owners


def test_issue_transfer_destination_is_a_candidate(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:acme/widget.git")
    resolution = _resolve(["gh", "issue", "transfer", "12", "stranger/lib"], repo)
    assert resolution.ok
    assert _owner("acme") in resolution.target.owners
    assert _owner("stranger") in resolution.target.owners


def test_an_opaque_wrapper_is_unresolvable(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:acme/widget.git")
    resolution = _resolve(["xargs", "gh", "issue", "create", "--title"], repo)
    assert not resolution.ok
    assert "wrapper" in resolution.reason


def test_ambiguous_hostname_unions_hosts_for_owner_placeholder(tmp_path):
    resolution = _resolve(
        ["GH_REPO=acme/x", "gh", "api", "-X", "POST", "-q", "--hostname=ghe.corp",
         "repos/{owner}/{repo}/issues"],
        tmp_path)
    assert resolution.ok
    assert _owner("ghe.corp/acme") in resolution.target.owners
    assert _owner("acme") in resolution.target.owners


def test_ambiguous_hostname_unions_hosts_for_graphql(tmp_path):
    resolution = gh.owner_targets.resolve_gh_call(
        ("api", "graphql", "-q", "--hostname=ghe.corp", "--input", "-"),
        cwd=str(tmp_path), target_repo="acme/x", known_hosts=KNOWN)
    assert resolution.ok
    assert _owner("ghe.corp/acme") in resolution.target.owners
    assert _owner("acme") in resolution.target.owners


def test_a_boolean_flag_does_not_union_cwd_for_a_following_repo(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:stranger/lib.git")
    resolution = _resolve(
        ["gh", "pr", "merge", "3", "--squash", "--repo=ours/svc"], repo)
    assert resolution.target.owners == (_owner("ours"),)


def test_pr_merge_short_d_is_boolean_so_repo_is_explicit(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:stranger/lib.git")
    resolution = _resolve(
        ["gh", "pr", "merge", "3", "-d", "--repo=ours/svc"], repo)
    assert resolution.target.owners == (_owner("ours"),)


def test_label_short_d_still_unions_cwd_for_a_following_repo(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:stranger/lib.git")
    resolution = _resolve(
        ["gh", "label", "create", "foo", "-d", "--repo=ours/svc"], repo)
    assert _owner("ours") in resolution.target.owners
    assert _owner("stranger") in resolution.target.owners


def test_a_chained_wrapper_resolves_the_inner_env_repo(tmp_path):
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    resolution = _resolve(
        ["sudo", "-iu", "bob", "env", "GH_REPO=stranger/lib", "gh", "issue", "comment", "1"], repo)
    assert resolution.target.owners == (_owner("stranger"),)


@pytest.mark.parametrize("words", [
    ["env", "-C", "../s", "gh", "issue", "comment", "1"],
    ["sudo", "--chdir=../s", "gh", "issue", "comment", "1"],
    ["env", "-S", "gh issue comment 1"],
])
def test_a_wrapper_that_moves_or_reparses_is_unresolvable(tmp_path, words):
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    resolution = _resolve(words, repo)
    assert not resolution.ok and "wrapper" in resolution.reason


def test_a_repo_flag_between_the_group_and_the_verb_is_the_target(tmp_path):
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    resolution = _resolve(["gh", "pr", "-R", "stranger/tool", "comment", "1", "-b", "hi"], ours)
    assert resolution.ok and _owner("stranger") in resolution.target.owners


@pytest.mark.parametrize("command", [
    "env -i gh pr comment 1 -b x",
    "env - gh pr comment 1 -b x",
    "sudo gh pr comment 1 -b x",
    "doas gh pr comment 1 -b x",
    "sudo --preserve-env=PATH gh pr comment 1 -b x",
])
def test_a_wrapper_that_clears_the_environment_does_not_read_the_hooks_gh_repo(
        tmp_path, monkeypatch, command):
    monkeypatch.setenv("GH_REPO", "ours/svc")
    theirs = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    resolution = _resolve(command.split(), theirs)
    assert resolution.ok and _owner("stranger") in resolution.target.owners


@pytest.mark.parametrize("command", [
    "sudo -E gh pr comment 1 -b x",
    "sudo --preserve-env gh pr comment 1 -b x",
    "sudo --preserve-env=GH_REPO gh pr comment 1 -b x",
    "env -i GH_REPO=ours/svc gh pr comment 1 -b x",
    "sudo GH_REPO=ours/svc gh pr comment 1 -b x",
])
def test_a_preserved_or_reassigned_gh_repo_still_names_the_target(tmp_path, monkeypatch, command):
    monkeypatch.setenv("GH_REPO", "ours/svc")
    theirs = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    resolution = _resolve(command.split(), theirs)
    assert resolution.ok and resolution.target.owners == (_owner("ours"),)


def test_sudo_unions_an_inherited_gh_repo_it_may_keep(tmp_path, monkeypatch):
    """sudoers env_keep may pass GH_REPO through, so the inherited value is a candidate too."""
    monkeypatch.setenv("GH_REPO", "stranger/lib")
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    resolution = _resolve("sudo gh pr comment 1 -b x".split(), ours)
    assert set(resolution.target.owners) == {_owner("ours"), _owner("stranger")}


@pytest.mark.parametrize("words,reason", [
    (["gh", "gist", "create", "notes.md"], "authenticated GitHub account"),
    (["gh", "project", "create", "--title", "x"], "--owner"),
    (["gh", "project", "item-add", "1", "--owner", "@me"], "--owner"),
    (["gh", "secret", "set", "T", "--user"], "authenticated GitHub account"),
    (["gh", "repo", "create", "newname"], "OWNER/NAME"),
    (["gh", "repo", "fork", "ours/svc"], "--org"),
])
def test_a_write_owned_by_the_authenticated_account_is_unresolvable(tmp_path, words, reason):
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    resolution = _resolve(words, ours)
    assert not resolution.ok and reason in resolution.reason


@pytest.mark.parametrize("words,expected", [
    (["gh", "project", "item-add", "1", "--owner", "acme"], "acme"),
    (["gh", "project", "create", "--owner=acme", "--title", "x"], "acme"),
    (["gh", "secret", "set", "T", "--org", "acme"], "acme"),
    (["gh", "variable", "set", "T", "-oacme"], "acme"),
    (["gh", "repo", "create", "acme/new", "--public"], "acme"),
    (["gh", "repo", "fork", "ours/svc", "--org", "acme"], "acme"),
    (["gh", "repo", "delete", "acme/widget", "--yes"], "acme"),
    (["gh", "repo", "edit", "https://github.com/acme/widget", "-d", "x"], "acme"),
    (["gh", "repo", "sync", "ghe.corp/acme/widget"], "ghe.corp/acme"),
])
def test_a_scoped_write_names_its_own_owner_over_the_cwd(tmp_path, words, expected):
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    resolution = _resolve(words, ours)
    assert resolution.ok and resolution.target.owners == (_owner(expected),)


def test_a_repo_verb_without_a_repository_falls_back_to_the_cwd(tmp_path):
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    resolution = _resolve(["gh", "repo", "rename", "newname"], ours)
    assert resolution.ok and resolution.target.owners == (_owner("ours"),)
