"""Tests for the suite heartbeat supervisor."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.suite_watch import (
    ENV_INTERVAL,
    STATUS_ENV,
    Inflight,
    bats_file_from_command,
    child_status,
    descendants,
    format_duration,
    format_heartbeat,
    heartbeat_interval,
    parse_etime,
    parse_ps_line,
    pytest_inflight,
)

CLI = REPO_ROOT / "bin" / "local" / "suite-watch"


def _await_path(path: Path, message: str, seconds: float = 5) -> None:
    """Block until *path* appears, failing the test rather than hanging."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(message)
        time.sleep(0.05)


def _cli(*args, env=None, **kwargs):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=merged,
        **kwargs,
    )


def test_parse_etime_mm_ss():
    assert parse_etime("3:02") == 182


def test_parse_etime_hh_mm_ss():
    assert parse_etime("1:04:10") == 3850


def test_parse_etime_with_days():
    assert parse_etime("1-02:03:04") == 93784


def test_format_duration_matches_the_heartbeat_examples():
    assert format_duration(41) == "41s"
    assert format_duration(182) == "3m02s"
    assert format_duration(250) == "4m10s"


def test_format_heartbeat_never_looks_like_tap():
    line = format_heartbeat(
        "bats", 250, 12,
        [Inflight("claude_settings.bats", 182), Inflight("wt_cleanup.bats", 41)],
    )
    assert line == (
        "⏱ bats: 4m10s elapsed, 12 jobs — running: "
        "claude_settings.bats (3m02s), wt_cleanup.bats (41s)"
    )
    assert not line.startswith(("ok", "not ok", "#", "1.."))


def test_format_heartbeat_without_inflight_still_says_so():
    line = format_heartbeat("pytest", 12, 4, [])
    assert "running: (none)" in line
    assert not line.startswith(("ok", "not ok", "#", "1.."))


def test_heartbeat_interval_defaults_and_disables():
    assert heartbeat_interval("") == 60.0
    assert heartbeat_interval(None) == 60.0
    assert heartbeat_interval("0") == 0.0
    assert heartbeat_interval("0.25") == 0.25


def test_heartbeat_interval_rejects_junk():
    with pytest.raises(ValueError, match="TEST_HEARTBEAT_SECS"):
        heartbeat_interval("nope")
    with pytest.raises(ValueError, match="TEST_HEARTBEAT_SECS"):
        heartbeat_interval("-1")


def test_bats_file_from_command_uses_the_arg_after_exec_file():
    cmd = "/usr/local/libexec/bats-exec-file /tmp/tests/claude_settings.bats"
    assert bats_file_from_command(cmd) == "claude_settings.bats"


def test_parse_ps_line_splits_command_on_the_fourth_field():
    row = parse_ps_line("  42  7  01:02 /usr/bin/bats-exec-file /x/y.bats")
    assert row == (42, 7, "01:02", "/usr/bin/bats-exec-file /x/y.bats")


def test_descendants_walk_ppid_not_the_whole_table():
    rows = [
        (2, 1, "0:01", "child"),
        (3, 2, "0:01", "grand"),
        (9, 8, "0:01", "cousin"),
    ]
    found = descendants(1, rows)
    assert [r[0] for r in found] == [2, 3]


def test_pytest_inflight_reads_worker_status_files(tmp_path):
    (tmp_path / "gw0").write_text("100.0\ntests/demo.py::test_slow\n")
    (tmp_path / "gw1").write_text("118.0\ntests/demo.py::test_fast\n")
    (tmp_path / "gw0.tmp").write_text("should-ignore\n")
    found = pytest_inflight(tmp_path, now=120.0)
    assert [(i.name, i.age_s) for i in found] == [
        ("tests/demo.py::test_slow", 20),
        ("tests/demo.py::test_fast", 2),
    ]


def test_child_status_translates_a_signal_death():
    assert child_status(0) == 0
    assert child_status(7) == 7
    assert child_status(-signal.SIGTERM) == 128 + signal.SIGTERM


def test_heartbeat_goes_to_stderr_and_leaves_stdout_byte_identical():
    payload = "1..1\nok 1 through-stdout\n"
    child = (
        "import sys, time; "
        "sys.stdout.write(" + repr(payload) + "); sys.stdout.flush(); "
        "time.sleep(0.7)"
    )
    result = _cli(
        "--suite", "bats", "--jobs", "2", "--",
        sys.executable, "-c", child,
        env={ENV_INTERVAL: "0.2"},
    )
    assert result.returncode == 0
    assert result.stdout == payload
    assert "⏱ bats:" in result.stderr
    assert "2 jobs" in result.stderr
    assert not any(
        line.startswith(("ok", "not ok", "#", "1.."))
        for line in result.stderr.splitlines()
    )


def test_disabled_heartbeat_does_not_print_and_keeps_stdout():
    payload = "plain\n"
    child = "import sys; sys.stdout.write(" + repr(payload) + ")"
    result = _cli(
        "--suite", "bats", "--",
        sys.executable, "-c", child,
        env={ENV_INTERVAL: "0"},
    )
    assert result.returncode == 0
    assert result.stdout == payload
    assert "⏱" not in result.stderr


def test_the_childs_exit_status_is_propagated():
    result = _cli("--suite", "bats", "--", sys.executable, "-c", "raise SystemExit(7)")
    assert result.returncode == 7


def test_a_signalled_child_is_reported_as_128_plus_the_signal():
    result = _cli(
        "--suite", "bats", "--",
        "sh", "-c", "kill -TERM $$",
        env={ENV_INTERVAL: "0"},
    )
    assert result.returncode == 128 + signal.SIGTERM


def test_a_signal_to_the_wrapper_reaches_the_child(tmp_path):
    started, caught = tmp_path / "started", tmp_path / "caught"
    proc = subprocess.Popen(
        [str(CLI), "--suite", "bats", "--", "sh", "-c",
         f"trap 'touch {caught}; exit 0' TERM; touch {started}; "
         f"while [ ! -f {caught} ]; do sleep 0.05; done"],
        env=dict(os.environ, **{ENV_INTERVAL: "30"}),
    )
    try:
        _await_path(started, "the child never started")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert caught.exists(), "the child never saw the signal sent to the wrapper"


def test_heartbeat_reports_a_descendant_bats_exec_file():
    outer = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.8)', "
        "'bats-exec-file', '/tmp/claude_settings.bats']); "
        "time.sleep(0.7)"
    )
    result = _cli(
        "--suite", "bats", "--jobs", "2", "--",
        sys.executable, "-c", outer,
        env={ENV_INTERVAL: "0.2"},
    )
    assert result.returncode == 0
    assert "claude_settings.bats" in result.stderr


def test_heartbeat_reads_pytest_status_files():
    child = (
        "import os, time, pathlib; "
        f"d = os.environ[{STATUS_ENV!r}]; "
        "pathlib.Path(d, 'gw0').write_text("
        "f'{time.time()}\\ntests/demo.py::test_slow\\n'); "
        "time.sleep(0.7)"
    )
    result = _cli(
        "--suite", "pytest", "--jobs", "3", "--",
        sys.executable, "-c", child,
        env={ENV_INTERVAL: "0.2"},
    )
    assert result.returncode == 0
    assert "tests/demo.py::test_slow" in result.stderr
    assert "⏱ pytest:" in result.stderr


def test_missing_command_is_a_message_not_a_traceback():
    result = _cli("--suite", "bats", "--", "no-such-command-xyz")
    assert result.returncode == 127
    assert "cannot run no-such-command-xyz" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_cli_rejects_a_missing_command():
    result = _cli("--suite", "bats", "--")
    assert result.returncode == 2
    assert "no command given" in result.stderr
