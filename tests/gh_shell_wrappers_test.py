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
