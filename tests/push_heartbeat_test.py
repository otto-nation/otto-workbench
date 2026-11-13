"""A push that waits says so, and is never killed for it."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.heartbeat  # noqa: E402
import git.push  # noqa: E402

from push_support import _commit, _install_pre_push, pushable  # noqa: E402,F401


def test_malformed_interval_warns_and_uses_default(monkeypatch, capsys):
    monkeypatch.setenv(git.push.PUSH_HEARTBEAT_ENV, "abc")
    beat = git.push._Heartbeat("b", hooks_skipped=False)
    assert beat.interval == git.push._PUSH_HEARTBEAT_DEFAULT
    assert "warning" in capsys.readouterr().err


def test_interval_parsing():
    f = core.heartbeat.interval_from_env
    assert f("X", 60.0, None) == 60.0
    assert f("X", 60.0, "") == 60.0
    assert f("X", 60.0, "0") == 0.0
    with pytest.raises(ValueError, match="X"):
        f("X", 60.0, "-1")


def test_a_slow_hook_gets_a_heartbeat_and_the_push_still_lands(pushable, monkeypatch, capsys):
    wt, _ = pushable
    _install_pre_push(wt, "sleep 3\nexit 0\n")
    _commit(wt, "one")
    monkeypatch.setenv(git.push.PUSH_HEARTBEAT_ENV, "1")
    result = git.push.push(wt, gated=False, owner_check=None)
    assert result.ok
    err = capsys.readouterr().err
    assert "still pushing main" in err and "never killed" in err


def test_zero_disables_the_heartbeat(pushable, monkeypatch, capsys):
    wt, _ = pushable
    _install_pre_push(wt, "sleep 2\nexit 0\n")
    _commit(wt, "one")
    monkeypatch.setenv(git.push.PUSH_HEARTBEAT_ENV, "0")
    assert git.push.push(wt, gated=False, owner_check=None).ok
    assert "still pushing" not in capsys.readouterr().err


def test_a_skipped_hook_names_the_transfer(pushable, monkeypatch, capsys):
    wt, remote = pushable
    _install_pre_push(wt, "exit 1\n")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nsleep 2\nexit 0\n")
    hook.chmod(0o755)
    _commit(wt, "one")
    monkeypatch.setenv(git.push.PUSH_HEARTBEAT_ENV, "1")
    assert git.push.push(wt, gated=False, owner_check=None, args=("--no-verify",)).ok
    assert "waiting on the transfer" in capsys.readouterr().err
