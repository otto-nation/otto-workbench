"""gh.owners: which owners are ours, and whether a write may go where it is going."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.github_owners
import config.workbench_config
import gh.owner_targets
import gh.owners

from conftest import git_in, remote_repo


def _owner(spec):
    return config.github_owners.parse_owner(spec)


def _target(*specs):
    return gh.owner_targets.Target(
        tuple(_owner(s) for s in specs), gh.owner_targets.TargetSource.REMOTES, "the remotes at /x")


def test_an_empty_config_is_unconfigured():
    assert not gh.owners.OwnerMap.load().configured


def test_owners_are_read_from_the_global_file_only(github_owners, tmp_path, monkeypatch):
    """A cloned repo's .workbench.yml must not be able to declare itself ours."""
    project = remote_repo(tmp_path / "clone", "git@github.com:stranger/lib.git")
    (project / ".workbench.yml").write_text("github:\n  owners:\n    mine: [stranger]\n")
    monkeypatch.chdir(project)
    assert config.workbench_config.load_config(project).github.owners == {"mine": ["stranger"]}
    assert not gh.owners.OwnerMap.load().configured
    github_owners({"work": ["otto-nation"]})
    assert gh.owners.OwnerMap.load().classify(_owner("stranger")) is gh.owners.UPSTREAM


def test_classification_is_case_insensitive_and_host_aware(github_owners):
    github_owners({"work": ["Otto-Nation", "ghe.corp/platform"], "personal": ["me"]})
    owners = gh.owners.OwnerMap.load()
    assert owners.classify(_owner("otto-nation")) == gh.owners.Group("work")
    assert owners.classify(_owner("GHE.CORP/Platform")) == gh.owners.Group("work")
    assert owners.classify(_owner("platform")).upstream
    assert owners.hosts() == frozenset({"github.com", "ghe.corp"})


def test_a_duplicate_owner_is_a_config_error(github_owners):
    github_owners({"work": ["acme"], "personal": ["ACME"]})
    with pytest.raises(config.workbench_config.ConfigError) as exc:
        gh.owners.OwnerMap.load()
    assert "acme" in str(exc.value)


def test_a_group_that_is_not_a_list_is_a_config_error(github_owners):
    github_owners({"work": "acme"})
    with pytest.raises(config.workbench_config.ConfigError):
        gh.owners.OwnerMap.load()


def test_unconfigured_refuses_and_names_the_fix():
    verdict = gh.owners.check_target(_target("anyone"), gh.owners.OwnerMap())
    assert not verdict.ok
    assert "otto-workbench config set github.owners." in verdict.reason


def test_an_upstream_target_is_refused(github_owners):
    github_owners({"work": ["otto-nation"]})
    target = gh.owner_targets.Target((_owner("stranger"),), gh.owner_targets.TargetSource.EXPLICIT, "--repo stranger/lib")
    verdict = gh.owners.check_target(target, gh.owners.OwnerMap.load())
    assert not verdict.ok
    assert "stranger" in verdict.reason and "for the user" in verdict.reason


def test_remotes_with_an_upstream_among_them_need_an_explicit_repo(github_owners):
    github_owners({"work": ["me"]})
    verdict = gh.owners.check_target(_target("me", "acme"), gh.owners.OwnerMap.load())
    assert not verdict.ok
    assert "--repo" in verdict.reason


def test_remotes_spanning_two_groups_need_an_explicit_repo(github_owners):
    github_owners({"work": ["acme"], "personal": ["me"]})
    verdict = gh.owners.check_target(_target("me", "acme"), gh.owners.OwnerMap.load())
    assert not verdict.ok
    assert "personal" in verdict.reason and "work" in verdict.reason


def test_an_empty_target_is_refused(github_owners):
    github_owners({"work": ["acme"]})
    target = gh.owner_targets.Target(
        (), gh.owner_targets.TargetSource.REMOTES, "the remotes at /x")
    verdict = gh.owners.check_target(target, gh.owners.OwnerMap.load())
    assert not verdict.ok


def test_an_own_target_is_allowed_with_its_group(github_owners):
    github_owners({"work": ["acme"]})
    verdict = gh.owners.check_target(_target("acme"), gh.owners.OwnerMap.load())
    assert verdict.ok and verdict.group == "work"


def test_check_command_lets_a_read_through_without_config(github_owners):
    github_owners({"work": "not-a-list"})
    assert gh.owners.check_command("gh pr view 1", "/").ok


def test_check_command_refuses_an_upstream_repo_flag(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    verdict = gh.owners.check_command("gh issue create --repo stranger/lib --title x", str(tmp_path))
    assert not verdict.ok and "stranger" in verdict.reason


def test_check_command_allows_an_own_cwd_repo(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:otto-nation/svc.git")
    assert gh.owners.check_command("gh pr comment 1 --body x", str(repo)).ok


def test_check_command_refuses_a_cwd_write_after_a_cd(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:otto-nation/svc.git")
    assert not gh.owners.check_command(f"cd {repo} && gh issue create --title x", str(repo)).ok


def test_check_command_refuses_what_it_cannot_parse(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    verdict = gh.owners.check_command("gh issue create --title 'x", str(tmp_path))
    assert not verdict.ok and "parsed" in verdict.reason


def test_check_command_refuses_a_graphql_mutation_even_at_home(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:otto-nation/svc.git")
    assert not gh.owners.check_command("gh api graphql -f query='mutation { x }'", str(repo)).ok


def test_check_command_refuses_when_one_statement_targets_upstream(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    verdict = gh.owners.check_command(
        "gh issue comment 1 --repo ours/x -b a; gh issue comment 1 --repo stranger/y -b b",
        str(tmp_path))
    assert not verdict.ok and "stranger" in verdict.reason


@pytest.mark.parametrize("command", [
    "export GH_REPO=stranger/lib; gh issue comment 1 -b hi",
    "GH_REPO=stranger/lib; gh issue comment 1 -b hi",
    "declare GH_REPO=stranger/lib; gh issue comment 1 -b hi",
])
def test_an_exported_gh_repo_refuses_a_cwd_write(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    verdict = gh.owners.check_command(command, str(repo))
    assert not verdict.ok
    assert "GH_REPO" in verdict.reason or "GH_HOST" in verdict.reason


def test_an_exported_gh_host_refuses_a_bare_repo(github_owners, tmp_path):
    github_owners({"work": ["acme"]})
    verdict = gh.owners.check_command(
        "export GH_HOST=ghe.corp; gh issue create --repo acme/x --title x", str(tmp_path))
    assert not verdict.ok
    assert "GH_HOST" in verdict.reason


def test_env_builtin_repo_is_honoured_like_a_prefix(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    verdict = gh.owners.check_command(
        "env GH_REPO=stranger/lib gh issue comment 1 -b hi", str(repo))
    assert not verdict.ok and "stranger" in verdict.reason


def test_a_this_statement_prefix_still_wins_over_an_earlier_export(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    verdict = gh.owners.check_command(
        "export GH_REPO=stranger/lib; GH_REPO=ours/svc gh issue create --title x", str(tmp_path))
    assert verdict.ok


def test_check_gh_call_ignores_reads_without_loading_config(github_owners):
    github_owners({"work": "not-a-list"})
    assert gh.owners.check_gh_call(("api", "repos/stranger/lib/issues")).ok


def test_check_gh_call_refuses_an_upstream_api_write(github_owners):
    github_owners({"work": ["otto-nation"]})
    verdict = gh.owners.check_gh_call(("api", "repos/stranger/lib/issues", "--method", "POST"))
    assert not verdict.ok


def test_the_override_is_exactly_one(monkeypatch):
    monkeypatch.setenv(gh.owners.ALLOW_UPSTREAM_ENV, "1")
    assert gh.owners.upstream_allowed_by_user()
    for value in ("", "0", "true", "yes"):
        monkeypatch.setenv(gh.owners.ALLOW_UPSTREAM_ENV, value)
        assert not gh.owners.upstream_allowed_by_user()
    monkeypatch.delenv(gh.owners.ALLOW_UPSTREAM_ENV)
    assert not gh.owners.upstream_allowed_by_user()


@pytest.mark.parametrize("command", [
    "gh pr close 3 -c --repo=ours/svc",
    "gh issue close 3 --comment --repo=ours/svc",
    "gh pr merge 3 --subject --repo=ours/svc",
    "gh label create foo -d --repo=ours/svc",
])
def test_a_repo_flag_after_a_value_flag_is_refused_from_a_stranger_checkout(
        github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    stranger = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    assert not gh.owners.check_command(command, str(stranger)).ok
    assert gh.owners.check_command(command, str(ours)).ok


def test_hostname_after_a_value_flag_unions_the_default_host(github_owners, tmp_path):
    github_owners({"work": ["ghe.corp/acme"]})
    verdict = gh.owners.check_command(
        "gh api -X POST -q --hostname=ghe.corp repos/acme/x/issues", str(tmp_path))
    assert not verdict.ok
    assert gh.owners.check_command(
        "gh api -X POST --hostname ghe.corp repos/acme/x/issues", str(tmp_path)).ok


@pytest.mark.parametrize("command", [
    "export GH_REPO+=stranger/lib; gh issue comment 1 -b hi",
    "GH_REPO+=stranger/lib; export GH_REPO; gh issue comment 1 -b hi",
    "GH_REPO+=stranger/lib gh issue comment 1 -b hi",
])
def test_plus_equals_gh_repo_is_an_assignment(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    assert not gh.owners.check_command(command, str(repo)).ok


@pytest.mark.parametrize("command", [
    "unset GH_REPO; gh issue comment 1 -b hi",
    "unset GH_HOST; gh issue comment 1 -b hi",
])
def test_unset_gh_repo_or_host_refuses_a_cwd_write(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    assert not gh.owners.check_command(command, str(repo)).ok


def test_inherited_gh_repo_is_the_target_without_a_repo_flag(
        github_owners, tmp_path, monkeypatch):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    monkeypatch.setenv("GH_REPO", "stranger/lib")
    verdict = gh.owners.check_command("gh issue comment 1 -b hi", str(repo))
    assert not verdict.ok and "stranger" in verdict.reason


@pytest.mark.parametrize("command", [
    "eval 'gh issue create --title x'",
    "env -i GH_REPO=ours/svc gh issue comment 1 -b hi",
    "env -u X GH_REPO=stranger/lib gh issue comment 1 -b hi",
    "bash -c 'gh issue create --title x'",
    "sh -c 'gh pr comment 1 --body x'",
    "zsh -c 'gh issue create --title x'",
    "command gh issue create --title x",
    "exec gh issue create --title x",
    "xargs gh issue create --title",
    "sudo gh issue create --title x",
    "time gh issue create --title x",
])
def test_opaque_wrappers_are_refused_even_at_home(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    assert not gh.owners.check_command(command, str(repo)).ok


def test_plain_env_assignment_stays_resolvable(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    assert gh.owners.check_command(
        "env GH_REPO=ours/svc gh issue create --title x", str(tmp_path)).ok


def test_issue_transfer_to_a_stranger_is_refused(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    verdict = gh.owners.check_command("gh issue transfer 12 stranger/lib", str(repo))
    assert not verdict.ok and "stranger" in verdict.reason


def test_issue_transfer_to_our_owner_is_allowed(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    assert gh.owners.check_command("gh issue transfer 12 ours/other", str(repo)).ok
