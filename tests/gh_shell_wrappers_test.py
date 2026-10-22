"""gh.shell_wrappers: which program a statement runs once env/sudo/time/command/exec are peeled."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import gh.shell_wrappers


def _peel(command):
    return gh.shell_wrappers.peel({}, command.split())


@pytest.mark.parametrize("command,words", [
    ("sudo env GH_REPO=x gh issue comment", ("gh", "issue", "comment")),
    ("time sudo command exec gh pr view", ("gh", "pr", "view")),
    ("/usr/bin/time -f %e /usr/bin/env gh pr view", ("gh", "pr", "view")),
    ("sudo -iu bob gh pr view", ("gh", "pr", "view")),
    ("sudo -ubob gh pr view", ("gh", "pr", "view")),
    ("sudo --user=bob -- gh pr view", ("gh", "pr", "view")),
    ("exec -la name gh pr view", ("gh", "pr", "view")),
])
def test_wrappers_are_peeled_in_a_loop(command, words):
    peeled = _peel(command)
    assert not peeled.opaque and peeled.words == words


def test_assignments_and_unsets_accumulate_across_layers():
    peeled = gh.shell_wrappers.peel({"GH_HOST": "h"}, "sudo GH_REPO=a/b env -u GH_HOST gh pr view".split())
    assert peeled.env == {"GH_REPO": "a/b"}
    assert peeled.unset == frozenset({"GH_HOST"})


def test_a_later_assignment_cancels_an_unset():
    peeled = _peel("env -u GH_REPO GH_REPO=a/b gh pr view")
    assert peeled.env == {"GH_REPO": "a/b"} and not peeled.unset


@pytest.mark.parametrize("command", [
    "env -C dir gh pr view",
    "env --chdir dir gh pr view",
    "env -S x",
    "env -iS x",
    "sudo -D dir gh pr view",
    "sudo -R root gh pr view",
    "sudo -e file",
    "sudo --frob gh pr view",
    "env --chd=dir gh pr view",
    "time -Z gh pr view",
    "sudo -u",
])
def test_an_option_outside_the_table_or_one_that_moves_is_opaque(command):
    assert _peel(command).opaque


def test_command_v_is_a_lookup():
    assert _peel("sudo command -pv gh").lookup


def test_an_attached_only_value_does_not_take_the_next_word():
    options = gh.shell_wrappers.parse_options(gh.shell_wrappers.XARGS, ["-i", "gh", "pr"])
    assert options.rest == ("gh", "pr")
    options = gh.shell_wrappers.parse_options(gh.shell_wrappers.XARGS, ["-0I", "{}", "gh"])
    assert options.found == (("-0", ""), ("-I", "{}")) and options.rest == ("gh",)


@pytest.mark.parametrize("command", [
    "timeout 60 gh pr view",
    "timeout -k 5 -s KILL --foreground 1m gh pr view",
    "timeout --signal=TERM 10 gh pr view",
    "nice -n 5 gh pr view",
    "nice -5 gh pr view",
    "nice --adjustment=5 gh pr view",
    "nohup gh pr view",
    "stdbuf -oL -e0 gh pr view",
    "stdbuf --output=L gh pr view",
    "ionice -c2 -n7 gh pr view",
    "chrt -f 10 gh pr view",
    "taskset -c 0-3 gh pr view",
    "doas -u bob gh pr view",
    "nohup timeout 5 nice stdbuf -oL gh pr view",
])
def test_process_wrappers_are_peeled(command):
    peeled = _peel(command)
    assert not peeled.opaque and not peeled.lookup and peeled.words == ("gh", "pr", "view")


@pytest.mark.parametrize("command", ["timeout", "doas -s", "nice -x gh pr view", "chrt -f"])
def test_a_process_wrapper_that_cannot_be_read_is_opaque(command):
    assert _peel(command).opaque


@pytest.mark.parametrize("command", ["ionice -p 12", "chrt -p 5 12", "taskset -p 3 12", "doas -C /etc/doas.conf gh"])
def test_a_process_wrapper_acting_on_a_pid_runs_nothing(command):
    assert _peel(command).lookup


def test_env_i_clears_the_environment_for_certain():
    peeled = gh.shell_wrappers.peel({"GH_REPO": "a/b"}, "env -i GH_HOST=h gh pr view".split())
    assert peeled.views == (gh.shell_wrappers.EnvView({"GH_HOST": "h"}, frozenset()),)


def test_sudo_may_reset_the_environment_so_both_cases_are_kept():
    peeled = gh.shell_wrappers.peel({"GH_REPO": "a/b"}, "sudo GH_HOST=h gh pr view".split())
    assert peeled.views == (
        gh.shell_wrappers.EnvView({"GH_REPO": "a/b", "GH_HOST": "h"}, None),
        gh.shell_wrappers.EnvView({"GH_HOST": "h"}, frozenset()),
    )


@pytest.mark.parametrize("command,kept", [
    ("sudo -E gh pr view", None),
    ("sudo --preserve-env gh pr view", None),
    ("sudo --preserve-env=GH_REPO,PATH gh pr view", frozenset({"GH_REPO", "PATH"})),
])
def test_sudo_preserve_env_keeps_what_it_names(command, kept):
    views = _peel(command).views
    assert views[0].inherits is None
    assert [v.inherits for v in views[1:]] == ([] if kept is None else [kept])
