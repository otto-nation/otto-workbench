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

# Module object, not just names: the clock tests patch core.suite_watch.time.
import core.suite_watch
from core.suite_watch import (
    ENV_INTERVAL,
    STATUS_ENV,
    Inflight,
    PsRow,
    bats_file_from_command,
    child_status,
    descendants,
    format_duration,
    format_heartbeat,
    heartbeat_interval,
    parse_etime,
    parse_ps_line,
    pytest_inflight,
    run_supervised,
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


def test_format_heartbeat_collapses_one_file_running_several_tests():
    line = format_heartbeat(
        "bats", 300, 12,
        [
            Inflight("wt_cleanup.bats", 30),
            Inflight("claude_settings.bats", 7),
            Inflight("claude_settings.bats", 281),
            Inflight("claude_settings.bats", 44),
        ],
    )
    assert line.endswith(
        "running: claude_settings.bats ×3 (4m41s), wt_cleanup.bats (30s)"
    )


def test_format_heartbeat_names_time_the_machine_spent_asleep():
    line = format_heartbeat("bats", 1260, 2, [], asleep_s=330)
    assert "21m00s elapsed (machine asleep for 5m30s of it), 2 jobs" in line


def test_a_suspend_shows_up_as_asleep_time(monkeypatch):
    started = core.suite_watch._Clocks(wall=1000.0, mono=50.0)
    # 600s of wall time passed, but the monotonic clock only saw 240s of it.
    monkeypatch.setattr(core.suite_watch.time, "time", lambda: 1600.0)
    monkeypatch.setattr(core.suite_watch.time, "monotonic", lambda: 290.0)
    assert started.since(60) == core.suite_watch.Elapsed(wall_s=600, asleep_s=360)


def test_clock_jitter_under_one_interval_is_not_sleep(monkeypatch):
    started = core.suite_watch._Clocks(wall=1000.0, mono=50.0)
    monkeypatch.setattr(core.suite_watch.time, "time", lambda: 1120.5)
    monkeypatch.setattr(core.suite_watch.time, "monotonic", lambda: 170.0)
    assert started.since(60) == core.suite_watch.Elapsed(wall_s=120, asleep_s=0)


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


@pytest.mark.parametrize(
    "command",
    [
        # bats-core runs `bats-exec-file <flags> <index> <file> <list>`.
        "/usr/libexec/bats-core/bats-exec-file 3 /t/claude_settings.bats /tmp/list",
        "/usr/libexec/bats-core/bats-exec-file --gather-test-outputs-in /tmp/g "
        "-T -x --trace 3 /t/claude_settings.bats /tmp/list",
        # `ps` flattens argv, so a path with a space arrives in pieces.
        "/usr/libexec/bats-core/bats-exec-file 3 /my repo/t/claude_settings.bats /tmp/list",
    ],
)
def test_bats_file_from_command_finds_the_file_past_flags_and_index(command):
    assert bats_file_from_command(command) == "claude_settings.bats"


def test_bats_file_from_command_without_a_file_is_none():
    assert bats_file_from_command("/x/bats-exec-file --trace 3") is None
    assert bats_file_from_command("/usr/bin/python3 other.bats") is None


def test_parse_ps_line_splits_command_on_the_fourth_field():
    row = parse_ps_line("  42  7  01:02 /usr/bin/bats-exec-file /x/y.bats")
    assert row == PsRow(42, 7, "01:02", "/usr/bin/bats-exec-file /x/y.bats")
    assert (row.pid, row.ppid) == (42, 7)


def test_descendants_walk_ppid_not_the_whole_table():
    rows = [
        PsRow(2, 1, "0:01", "child"),
        PsRow(3, 2, "0:01", "grand"),
        PsRow(9, 8, "0:01", "cousin"),
    ]
    found = descendants(1, rows)
    assert [r.pid for r in found] == [2, 3]


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
        "'bats-exec-file', '--trace', '3', '/tmp/claude_settings.bats', '/tmp/list']); "
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


def test_the_module_run_directly_rejects_a_missing_command():
    """The shell wrapper catches this first; the module has its own message."""
    result = subprocess.run(
        [sys.executable, str(LIB_DIR / "core" / "suite_watch.py"), "--suite", "bats"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "no command given after --" in result.stderr


def test_the_cli_rejects_a_missing_command():
    result = _cli("--suite", "bats", "--")
    assert result.returncode == 2
    assert "no command given" in result.stderr


def test_the_status_dir_reaches_the_child_but_not_this_process(tmp_path, monkeypatch):
    monkeypatch.delenv(STATUS_ENV, raising=False)
    seen = tmp_path / "seen"
    code = run_supervised(
        [sys.executable, "-c",
         f"import os, pathlib; pathlib.Path({str(seen)!r}).write_text(os.environ[{STATUS_ENV!r}])"],
        suite="pytest", jobs=None, interval=30.0,
    )
    assert code == 0
    assert seen.read_text()
    assert STATUS_ENV not in os.environ


def test_a_disabled_heartbeat_hides_an_inherited_status_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(STATUS_ENV, "/outer/supervisors/dir")
    seen = tmp_path / "seen"
    code = run_supervised(
        [sys.executable, "-c",
         f"import os, pathlib; pathlib.Path({str(seen)!r}).write_text("
         f"repr(os.environ.get({STATUS_ENV!r})))"],
        suite="pytest", jobs=None, interval=0.0,
    )
    assert code == 0
    assert seen.read_text() == "None"


def test_a_group_wide_signal_reaches_the_child_once(tmp_path):
    """`claim-job-slots` signals the whole group; the child must not hear it twice."""
    started, count = tmp_path / "started", tmp_path / "count"
    child = (
        "import pathlib, signal, time\n"
        "n = 0\n"
        "def h(*_):\n"
        "    global n\n"
        "    n += 1\n"
        "signal.signal(signal.SIGTERM, h)\n"
        f"pathlib.Path({str(started)!r}).touch()\n"
        "time.sleep(0.5)\n"
        "while not n:\n"
        "    time.sleep(0.05)\n"
        "time.sleep(0.5)\n"
        f"pathlib.Path({str(count)!r}).write_text(str(n))\n"
    )
    proc = subprocess.Popen(
        [str(CLI), "--suite", "bats", "--", sys.executable, "-c", child],
        env=dict(os.environ, **{ENV_INTERVAL: "30"}),
        start_new_session=True,
    )
    try:
        _await_path(started, "the child never started")
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert count.read_text() == "1"


class _FakePluginManager:
    def __init__(self, names):
        self._names = names

    def hasplugin(self, name):
        return name in self._names


class _FakeConfig:
    def __init__(self, *plugins):
        self.pluginmanager = _FakePluginManager(set(plugins))


_LOCATION = ("tests/demo.py", 1, "test_a")


def test_the_xdist_controller_writes_no_status_file(tmp_path, monkeypatch):
    import suite_status_support

    monkeypatch.setenv(STATUS_ENV, str(tmp_path))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(suite_status_support, "_CONFIG", _FakeConfig("dsession"))
    suite_status_support.pytest_runtest_logstart("tests/demo.py::test_a", _LOCATION)
    assert list(tmp_path.iterdir()) == []


def test_a_worker_and_a_plain_run_still_write_their_status_file(tmp_path, monkeypatch):
    import suite_status_support

    monkeypatch.setenv(STATUS_ENV, str(tmp_path))
    monkeypatch.setattr(suite_status_support, "_CONFIG", _FakeConfig("dsession"))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    suite_status_support.pytest_runtest_logstart("tests/demo.py::test_a", _LOCATION)
    assert [p.name for p in tmp_path.iterdir()] == ["gw3"]
    suite_status_support.pytest_runtest_logfinish("tests/demo.py::test_a", _LOCATION)
    assert list(tmp_path.iterdir()) == []

    monkeypatch.delenv("PYTEST_XDIST_WORKER")
    monkeypatch.setattr(suite_status_support, "_CONFIG", _FakeConfig())
    suite_status_support.pytest_runtest_logstart("tests/demo.py::test_b", _LOCATION)
    assert [p.name for p in tmp_path.iterdir()] == ["main"]
