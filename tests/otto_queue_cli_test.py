"""Tests for the otto-queue CLI, through the real shim where a process boundary matters."""

import contextlib
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.otto_queue  # noqa: E402
import core.proc  # noqa: E402
import core.timeouts  # noqa: E402
import otto_queue.paths  # noqa: E402
import otto_queue.ticket  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryClass, EntryKind, EntryState, NewEntry  # noqa: E402

SHIM = REPO_ROOT / "ai" / "bin" / "otto-queue"
PY = sys.executable


def _shim(*args):
    return subprocess.run([str(SHIM), *args], capture_output=True, text=True, timeout=120)


def _shim_env(args, env):
    return subprocess.run([str(SHIM), *args], capture_output=True, text=True, timeout=120, env=env)


def _no_supervisor_env(tmp_path):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("systemctl", "launchctl"):
        path = stubs / name
        path.write_text(
            '#!/usr/bin/env bash\necho "Could not find service otto-queue-worker" >&2\nexit 5\n'
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}"}


def _light(code):
    return _shim("run", "--ticket", "--class", "light", "--", PY, "-c", f"raise SystemExit({code})")


def _await_running():
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        running = [e for e in Ledger.open().entries() if e.state is EntryState.RUNNING]
        if running:
            return running[0]
        time.sleep(0.05)
    pytest.fail("no entry reached running")


def _await_path(path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    pytest.fail(f"{path} was never created")


def test_run_exits_with_the_commands_status_and_records_it():
    assert _light(3).returncode == 3
    [entry] = Ledger.open().entries()
    assert (entry.state, entry.exit_status, entry.entry_class) == (EntryState.FAILED, 3, EntryClass.LIGHT)


def test_run_returns_75_and_records_tempfail():
    assert _light(75).returncode == 75
    assert Ledger.open().entries()[0].state is EntryState.TEMPFAIL


def test_a_heavy_ticket_with_nothing_running_is_admitted():
    proc = _shim("run", "--ticket", "--", PY, "-c", "pass")
    assert proc.returncode == 0
    assert Ledger.open().entries()[0].entry_class is EntryClass.HEAVY


def test_run_without_a_command_is_a_usage_error():
    proc = _shim("run", "--ticket")
    assert proc.returncode == 2 and "needs a command" in proc.stderr


def test_run_without_ticket_is_refused():
    proc = _shim("run", "--", PY, "-c", "pass")
    assert proc.returncode == 2 and "--ticket" in proc.stderr


def test_status_json_prints_one_object_per_entry(capsys):
    _light(0)
    _light(3)
    assert cli.otto_queue.main(["status", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(r["state"], r["exit_status"], r["argv"][0]) for r in rows] == [("done", 0, PY), ("failed", 3, PY)]


def test_status_text_shows_the_budget_and_each_entry(capsys):
    _light(0)
    assert cli.otto_queue.main(["status"]) == 0
    first, second = capsys.readouterr().out.splitlines()
    assert first.startswith("heavy 0/") and "light 0/" in first
    assert "#1" in second and "done" in second and "exit 0" in second


def test_cancel_of_an_unknown_entry_exits_1():
    assert cli.otto_queue.main(["cancel", "99"]) == 1


def test_status_on_a_busy_ledger_exits_without_a_traceback(capsys, monkeypatch):
    Ledger.open()
    holder = sqlite3.connect(str(otto_queue.paths.db_path(Ledger.open().root)), isolation_level=None,
                             timeout=0)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(core.timeouts, "LOCAL", 0.2)
        code = cli.otto_queue.main(["status"])
        err = capsys.readouterr().err
        assert code == os.EX_TEMPFAIL
        assert "Traceback" not in err
        assert "otto-queue:" in err
    finally:
        holder.close()


def test_ctrl_c_on_the_join_path_exits_130(monkeypatch):
    ledger = Ledger.open()
    held = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="join-k"),
                          force=False, pid=os.getpid())

    def boom(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(otto_queue.ticket.time, "sleep", boom)
    try:
        assert cli.otto_queue.main(
            ["run", "--ticket", "--class", "light", "--key", "join-k", "--", PY, "-c", "pass"]
        ) == core.proc.INTERRUPT_RETURNCODE
    finally:
        held.lease.release()


def test_cancel_stops_a_running_ticket(tmp_path):
    marker = tmp_path / "running"
    proc = subprocess.Popen([str(SHIM), "run", "--ticket", "--class", "light", "--",
                             PY, "-c",
                             f"import pathlib, time; pathlib.Path({str(marker)!r}).write_text('1'); time.sleep(30)"],
                            start_new_session=True)
    try:
        _await_path(marker)
        entry = _await_running()
        assert cli.otto_queue.main(["cancel", str(entry.id)]) == 0
        assert proc.wait(timeout=30) == 128 + signal.SIGTERM
        ended = Ledger.open().get(entry.id)
        assert (ended.state, ended.exit_status) == (EntryState.CANCELLED, 128 + signal.SIGTERM)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_cancel_of_a_finished_entry_exits_1():
    _light(0)
    assert cli.otto_queue.main(["cancel", "1"]) == 1


def test_run_no_reuse_runs_a_finished_key_again():
    _shim("run", "--ticket", "--class", "light", "--key", "k", "--", PY, "-c", "pass")
    _shim("run", "--ticket", "--class", "light", "--key", "k", "--no-reuse", "--", PY, "-c", "pass")
    assert len([e for e in Ledger.open().entries() if e.state is EntryState.DONE]) == 2


def test_job_exec_is_not_advertised():
    proc = _shim("--help")
    assert proc.returncode == 0
    usage = proc.stdout.splitlines()[0]
    assert "job-exec" not in proc.stdout
    assert "job-exec" not in usage
    assert "job-exec" not in SHIM.read_text()


def test_job_exec_runs_a_queued_job():
    entry_id = Ledger.open().resolve(NewEntry(argv=(PY, "-c", "raise SystemExit(4)"), cwd="/", kind=EntryKind.JOB),
                                     force=False, pid=None).entry.id
    assert _shim("job-exec", str(entry_id)).returncode == 4
    assert Ledger.open().get(entry_id).state is EntryState.FAILED


def test_submit_without_a_supervisor_runs_here_and_says_so(tmp_path):
    result = _shim_env(["submit", "--", PY, "-c", "raise SystemExit(3)"], _no_supervisor_env(tmp_path))
    assert result.returncode == 3
    assert "as a ticket here" in result.stderr and "Could not find service" in result.stderr


def test_submit_queue_only_without_a_supervisor_exits_69_and_runs_nothing(tmp_path):
    ran = tmp_path / "ran"
    result = _shim_env(["submit", "--queue-only", "--", PY, "-c", f"open({str(ran)!r}, 'w')"],
                       _no_supervisor_env(tmp_path))
    assert result.returncode == 69
    assert "withdrew entry" in result.stderr
    assert not ran.exists()


def test_status_shows_a_jobs_kind_and_retry_time(capsys):
    ledger = Ledger.open()
    entry_id = ledger.resolve(NewEntry(argv=("true",), cwd="/", kind=EntryKind.JOB), force=False, pid=None).entry.id
    ledger.mark_running(entry_id, os.getpid())
    ledger.requeue(entry_id, os.EX_TEMPFAIL, time.time() + 600)
    assert cli.otto_queue.main(["status"]) == 0
    line = capsys.readouterr().out.splitlines()[1]
    assert "job" in line and "retry in 10m, attempt 2" in line
