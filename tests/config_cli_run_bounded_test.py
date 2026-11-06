"""Tests for `lib/config_cli.py run-bounded` — the bounded runner bash syncs through.

The sync path used to run `pi update` unbounded with inherited stdin. This
subcommand is how it now shares a timeouts.REFRESH bound and a process-group
kill, so a hung git fetch or npm install becomes exit 1 with the reason on
stderr and nothing on stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import core.proc
import core.timeouts

config_cli = _load_lib("config_cli")


def test_run_bounded_zero_exits_0(capsys, monkeypatch):
    monkeypatch.setattr(core.proc, "run", lambda *_a, **_k: core.proc.CmdResult(
        returncode=0,
        stdout="secret",
    ))
    assert config_cli.main(["run-bounded", "--", "true"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_run_bounded_nonzero_exits_1_with_reason_on_stderr(capsys, monkeypatch):
    monkeypatch.setattr(core.proc, "run", lambda *_a, **_k: core.proc.CmdResult(
        returncode=7,
        stderr="denied",
    ))
    assert config_cli.main(["run-bounded", "--", "pi", "update"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "denied" in captured.err
    assert "Traceback" not in captured.err


def test_run_bounded_timeout_exits_1_with_reason_naming_the_timeout(capsys, monkeypatch):
    monkeypatch.setattr(core.proc, "run", lambda *_a, **_k: core.proc.CmdResult(
        returncode=core.proc.TIMEOUT_RETURNCODE,
        stderr="timed out after 900s: pi update",
    ))
    assert config_cli.main(["run-bounded", "--", "pi", "update"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "timed out after 900s" in captured.err
    assert "Traceback" not in captured.err


def test_run_bounded_passes_kill_process_group(monkeypatch):
    seen = {}

    def fake_run(cmd, *, timeout, kill_process_group=False, **_kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        seen["kill_process_group"] = kill_process_group
        return core.proc.CmdResult(returncode=0)

    monkeypatch.setattr(core.proc, "run", fake_run)
    assert config_cli.main(["run-bounded", "--", "pi", "update"]) == 0
    assert seen["kill_process_group"] is True
    assert seen["timeout"] == core.timeouts.REFRESH
    assert seen["cmd"] == ["pi", "update"]
