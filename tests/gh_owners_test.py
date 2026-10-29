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
import gh.write_shapes

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
    "bash -c 'gh issue create --title x'",
    "sh -c 'gh pr comment 1 --body x'",
    "zsh -c 'gh issue create --title x'",
    "sh -ec 'gh issue comment 1 -b hi'",
    "zsh -xc 'gh issue create --title x'",
    "env -S'gh issue comment 1 -b hi'",
    "env --split-string='gh issue comment 1 -b hi'",
    "bash -c 'cd . && gh pr view 1'",
])
def test_a_reparsed_string_is_resolved_like_the_statements_it_runs(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    theirs = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    assert gh.owners.check_command(command, str(ours)).ok
    if gh.write_shapes.command_is_write(command):
        assert "stranger" in gh.owners.check_command(command, str(theirs)).reason


def test_a_reparsed_cd_still_makes_the_cwd_target_unknown(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    verdict = gh.owners.check_command("sudo bash -c 'cd ../s; gh pr comment 1 -b x'", str(ours))
    assert not verdict.ok and "changes directory" in verdict.reason


def test_a_reparsed_export_shifts_gh_repo(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    command = "bash -c 'export GH_REPO=stranger/lib; gh pr comment 1 -b x'"
    assert not gh.owners.check_command(command, str(ours)).ok


@pytest.mark.parametrize("command", [
    "bash -lc 'gh issue comment 1 -b hi --repo stranger/lib'",
    "xargs gh issue create --title",
    "xargs -n 1 gh issue create --title",
    "eval eval eval eval 'gh pr comment 1 -b x'",
    "bash run.sh -c 'gh pr comment 1 -b x'",
    "env -S '${X} gh pr comment 1 -b x'",
])
def test_opaque_wrappers_are_refused_even_at_home(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    assert not gh.owners.check_command(command, str(repo)).ok


@pytest.mark.parametrize("command", [
    "command -v gh",
    "xargs grep -n 'pr create'",
    "sudo apt install gh",
    "time gh pr list",
    "command gh pr view 3",
    "env -u FOO gh pr list",
])
def test_transparent_wrapper_reads_are_allowed_unconfigured(github_owners, command):
    github_owners({"work": "not-a-list"})
    assert gh.owners.check_command(command, "/").ok


@pytest.mark.parametrize("command", [
    "command gh issue comment 1 -b hi",
    "exec gh issue comment 1 -b hi",
    "sudo gh issue comment 1 -b hi",
    "time gh issue comment 1 -b hi",
    "env -u FOO gh issue comment 1 -b hi",
])
def test_transparent_wrapper_writes_resolve_normally(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    stranger = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    assert gh.owners.check_command(command, str(ours)).ok
    assert not gh.owners.check_command(command, str(stranger)).ok


def test_env_unsetting_gh_repo_refuses_a_cwd_write(github_owners, tmp_path):
    github_owners({"work": ["ours"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:ours/svc.git")
    assert not gh.owners.check_command(
        "env -u GH_REPO gh issue comment 1 -b hi", str(repo)).ok


def test_placeholder_hostname_after_value_flag_unions_the_default_host(
        github_owners, tmp_path):
    github_owners({"work": ["ghe.corp/acme"]})
    verdict = gh.owners.check_command(
        "GH_REPO=acme/x gh api -X POST -q --hostname=ghe.corp repos/{owner}/{repo}/issues",
        str(tmp_path))
    assert not verdict.ok


@pytest.mark.parametrize("command", [
    "gh pr merge 3 --squash --repo ours/svc",
    "gh pr merge 3 --merge --repo ours/svc",
    "gh pr merge 3 --rebase --repo ours/svc",
    "gh pr merge 3 --delete-branch --repo ours/svc",
    "gh pr merge 3 -d --repo ours/svc",
    "gh pr merge 3 --auto --repo ours/svc",
    "gh pr merge 3 --admin --repo ours/svc",
])
def test_boolean_merge_flags_do_not_union_cwd_for_a_following_repo(
        github_owners, tmp_path, command, monkeypatch):
    github_owners({"work": ["ours"]})
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gh.owners.check_command(command, str(plain)).ok


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


@pytest.mark.parametrize("command", [
    "sudo env GH_REPO=stranger/lib gh issue comment 1 -b hi",
    "time sudo gh issue comment 1 -b hi --repo stranger/lib",
    "command time gh issue comment 1 -b hi --repo stranger/lib",
    "time env GH_REPO=stranger/lib gh issue comment 1 -b hi",
    "sudo GH_REPO=stranger/lib gh issue comment 1 -b hi",
    "sudo -iu bob gh issue comment 1 -b hi --repo stranger/lib",
    "sudo -su bob gh issue comment 1 -b hi --repo stranger/lib",
    "/usr/bin/time -f %e gh issue comment 1 -b hi --repo stranger/lib",
    "/usr/bin/env GH_REPO=stranger/lib gh issue comment 1 -b hi",
])
def test_chained_and_clustered_wrappers_resolve_to_the_stranger(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    verdict = gh.owners.check_command(command, str(ours))
    assert not verdict.ok and "stranger" in verdict.reason


@pytest.mark.parametrize("command", [
    "sudo env -u GH_REPO gh issue comment 1 -b hi",
    "env -S 'gh issue comment 1 -b hi --repo stranger/lib'",
    "env -S 'GH_REPO=stranger/lib gh issue comment 1 -b hi'",
    "env -C ../s gh issue comment 1 -b hi",
    "env --chdir=../s gh issue comment 1 -b hi",
    "env --chd=../s gh issue comment 1 -b hi",
    "sudo -D ../s gh issue comment 1 -b hi",
    "sudo --chdir=../s gh issue comment 1 -b hi",
    "sudo --frobnicate gh issue comment 1 -b hi",
    "xargs sudo gh issue comment 1 -b hi",
    "xargs -0I {} gh issue comment {} -b hi",
])
def test_opaque_or_directory_changing_wrappers_are_refused_at_home(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    assert not gh.owners.check_command(command, str(ours)).ok


@pytest.mark.parametrize("command", [
    "time sudo gh issue comment 1 -b hi",
    "sudo -iu bob gh issue comment 1 -b hi",
    "command time gh issue comment 1 -b hi",
    "/usr/bin/time -f %e gh issue comment 1 -b hi",
    "env - gh issue comment 1 -b hi",
])
def test_chained_wrapper_writes_resolve_against_the_cwd(github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    stranger = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    assert gh.owners.check_command(command, str(ours)).ok
    assert not gh.owners.check_command(command, str(stranger)).ok


@pytest.mark.parametrize("command", [
    "sudo command -v gh",
    "command -pv gh",
    "time sudo gh pr list",
    "sudo env -u FOO gh pr list",
    "sudo -iu bob ls",
    "env -C /tmp ls",
    "/usr/bin/time -f %e make test",
])
def test_chained_wrapper_reads_are_allowed_unconfigured(github_owners, command):
    github_owners({"work": "not-a-list"})
    assert gh.owners.check_command(command, "/").ok


def _fork(tmp_path):
    fork = remote_repo(tmp_path / "f", "git@github.com:me/lib.git")
    git_in(fork, "remote", "add", "upstream", "git@github.com:stranger/lib.git")
    return fork


def test_a_fork_refusal_for_gh_says_pass_repo(github_owners, tmp_path):
    github_owners({"personal": ["me"]})
    verdict = gh.owners.check_command("gh pr create --draft", str(_fork(tmp_path)))
    assert not verdict.ok and "pass --repo" in verdict.reason


def test_a_fork_refusal_for_a_workbench_command_does_not_offer_a_missing_flag(github_owners, tmp_path):
    github_owners({"personal": ["me"]})
    verdict = gh.owners.check_command("pr create --draft", str(_fork(tmp_path)))
    assert not verdict.ok
    assert "pass --repo" not in verdict.reason and "remove or rename the remote" in verdict.reason


def test_a_fork_refusal_from_the_client_names_the_users_override(github_owners, tmp_path):
    github_owners({"personal": ["me"]})
    verdict = gh.owners.check_gh_call(("pr", "create", "--draft"), cwd=_fork(tmp_path))
    assert not verdict.ok
    assert "pass --repo" not in verdict.reason and gh.owners.ALLOW_UPSTREAM_ENV in verdict.reason


@pytest.mark.parametrize("command", [
    "timeout 60 gh pr comment 1 -b hi",
    "nice -n 5 gh pr create --draft",
    "nohup gh pr comment 1 -b hi",
    "stdbuf -oL gh pr comment 1 -b hi",
    "ionice -c2 gh pr comment 1 -b hi",
    "chrt -f 10 gh pr comment 1 -b hi",
    "taskset -c 0 gh pr comment 1 -b hi",
    "doas gh pr comment 1 -b hi",
    "gh pr -R stranger/lib comment 1 -b hi",
    "gh api -X=POST repos/stranger/lib/issues -f title=x",
])
def test_an_upstream_write_behind_a_process_wrapper_or_odd_flag_spelling_is_refused(
        github_owners, tmp_path, command):
    github_owners({"work": ["ours"]})
    theirs = remote_repo(tmp_path / "s", "git@github.com:stranger/lib.git")
    ours = remote_repo(tmp_path / "o", "git@github.com:ours/svc.git")
    verdict = gh.owners.check_command(command, str(theirs if "stranger" not in command else ours))
    assert not verdict.ok and "stranger" in verdict.reason


@pytest.mark.parametrize("command", [
    "gh repo -R stranger/lib deploy-key add key.pub",
    "gh repo deploy-key add key.pub --repo stranger/lib",
    "gh repo autolink create T- 'https://x/<num>' -R stranger/lib",
])
def test_nested_repo_writes_are_refused_for_an_upstream_repo(github_owners, tmp_path, command):
    github_owners({"work": ["otto-nation"]})
    assert not gh.owners.check_command(command, str(tmp_path)).ok


def test_nested_repo_write_is_allowed_for_an_own_repo(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    assert gh.owners.check_command(
        "gh repo deploy-key add key.pub -R otto-nation/svc", str(tmp_path)).ok


def test_an_account_key_write_names_no_repository(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:otto-nation/svc.git")
    assert not gh.owners.check_command("gh ssh-key add key.pub", str(repo)).ok
