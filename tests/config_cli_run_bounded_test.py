"""Tests for `lib/config_cli.py run-bounded` — the bounded runner bash syncs through.

The sync path used to run `pi update` unbounded with inherited stdin. This
subcommand is how it now shares a timeouts.REFRESH bound and a process-group
kill, so a hung git fetch or npm install becomes exit 1 with the reason on
stderr and nothing on stdout.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import _load_lib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import core.proc
import core.timeouts

config_cli = _load_lib("config_cli")

CONFIG_CLI = Path(__file__).resolve().parent.parent / "lib" / "config_cli.py"


@pytest.fixture(autouse=True)
def _restore_stop_signals():
    """`run-bounded` installs stop handlers, which are process-level state."""
    saved = {sig: signal.getsignal(sig) for sig in core.proc.STOP_SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


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


def test_run_bounded_relays_only_when_asked(monkeypatch):
    seen = []

    def fake_run(cmd, *, timeout, kill_process_group=False, relay=None):
        seen.append(relay)
        return core.proc.CmdResult(returncode=0)

    monkeypatch.setattr(core.proc, "run", fake_run)
    assert config_cli.main(["run-bounded", "--", "true"]) == 0
    assert config_cli.main(["run-bounded", "--relay", "--", "true"]) == 0
    assert seen == [None, sys.stderr]


def test_run_bounded_accepts_the_sync_tier(monkeypatch):
    seen = {}

    def fake_run(cmd, *, timeout, **_kwargs):
        seen["timeout"] = timeout
        return core.proc.CmdResult(returncode=0)

    monkeypatch.setattr(core.proc, "run", fake_run)
    assert config_cli.main(["run-bounded", "--tier", "SYNC", "--", "true"]) == 0
    assert seen["timeout"] == core.timeouts.SYNC


def test_run_bounded_installs_the_stop_handler(monkeypatch):
    monkeypatch.setattr(core.proc, "run", lambda *_a, **_k: core.proc.CmdResult(returncode=0))
    config_cli.main(["run-bounded", "--", "true"])
    assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL


def test_run_bounded_relay_streams_output_to_stderr():
    proc = subprocess.run(
        [sys.executable, str(CONFIG_CLI), "run-bounded", "--tier", "QUICK", "--relay",
         "--", "sh", "-c", "echo out-line; echo err-line >&2"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert "out-line" in proc.stderr
    assert "err-line" in proc.stderr


def _wait_for(condition, what):
    deadline = time.monotonic() + 20
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _is_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_run_bounded_sigterm_stops_the_child_it_started(tmp_path):
    """An outer bound's SIGTERM must reach the child, which is in its own session."""
    pidfile = tmp_path / "child.pid"
    runner = subprocess.Popen(
        [sys.executable, str(CONFIG_CLI), "run-bounded", "--tier", "REFRESH", "--",
         "sh", "-c", f"echo $$ > {shlex.quote(str(pidfile))}; exec sleep 300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(lambda: pidfile.exists() and pidfile.read_text().strip(),
                  "the child never started")
        child = int(pidfile.read_text())
        runner.send_signal(signal.SIGTERM)
        runner.wait(timeout=20)
        _wait_for(lambda: _is_gone(child), "the child outlived the runner")
    finally:
        runner.kill()
        runner.wait()
