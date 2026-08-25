"""Tests for lib/gitenv.py, and that it still agrees with lib/gitenv.sh."""

import os
import re
from pathlib import Path

from conftest import _load_lib

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "lib" / "gitenv.sh"

gitenv = _load_lib("gitenv")


def _shell_overrides():
    """The variable names lib/gitenv.sh unsets, read out of the script."""
    body = SHELL.read_text()
    match = re.search(r"git_env_clear\(\)\s*\{(.*?)\n\}", body, re.DOTALL)
    assert match, "lib/gitenv.sh no longer defines git_env_clear()"
    unset = re.search(r"unset\s+(.*)", match.group(1), re.DOTALL)
    assert unset, "git_env_clear() no longer unsets anything"
    return set(unset.group(1).replace("\\\n", " ").split())


def test_the_two_halves_name_the_same_variables():
    """Which variables have to go is one fact about git. A gate written in
    Python must not clear a shorter list than one written in bash."""
    assert _shell_overrides() == set(gitenv.GIT_ENV_OVERRIDES)


def test_clearing_drops_every_override():
    env = {name: "/somewhere" for name in gitenv.GIT_ENV_OVERRIDES}
    assert gitenv.git_env_clear(env) == {}


def test_clearing_keeps_everything_else():
    assert gitenv.git_env_clear({"PATH": "/bin", "GIT_DIR": "x"}) == {"PATH": "/bin"}


def test_an_absent_override_is_not_an_error():
    assert gitenv.git_env_clear({"PATH": "/bin"}) == {"PATH": "/bin"}


def test_the_caller_environment_is_left_alone(monkeypatch):
    """A copy, not a mutation — one honest subprocess should not change the
    environment of everything the caller runs afterwards."""
    monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
    assert "GIT_DIR" not in gitenv.git_env_clear()
    assert os.environ["GIT_DIR"] == "/somewhere/.git"
