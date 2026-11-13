"""Tests for otto_queue.supervisor: starting the worker and confirming it by its lock.

The supervisors are stubs on PATH that record their arguments; one variant
starts a stand-in worker that holds `queue/worker.lock` for a few seconds.
"""

import contextlib
import os
import signal
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.log  # noqa: E402
import otto_queue.lease  # noqa: E402
import otto_queue.paths  # noqa: E402
import otto_queue.supervisor  # noqa: E402

PY = sys.executable

# Holds the worker lock under argv[2] for 5 s: a worker the stub supervisor "started".
STAND_IN = (
    "import sys, time; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
    "import otto_queue.lease; held = otto_queue.lease.take_worker(Path(sys.argv[2])); time.sleep(5)"
)

USER_MANAGER = "user.slice/user-1000.slice/user@1000.service"
USER_SCOPE = f"/{USER_MANAGER}/app.slice/otto.scope"


@pytest.fixture(autouse=True)
def _reset_oom_policy():
    otto_queue.supervisor._oom_policy_ok = None
    yield
    otto_queue.supervisor._oom_policy_ok = None


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    pid_file = tmp_path / "stand-in.pid"
    yield bin_dir, tmp_path / "calls", pid_file
    if pid_file.exists():
        with contextlib.suppress(ProcessLookupError, ValueError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


def _stub(bin_dir, name, calls, body):
    path = bin_dir / name
    path.write_text(f'#!/usr/bin/env bash\necho "$*" >> "{calls}"\n{body}\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _calls(calls):
    return calls.read_text().splitlines() if calls.exists() else []


def _delegate_memory(root, controllers="cpu memory pids"):
    (root / "proc" / "self").mkdir(parents=True, exist_ok=True)
    (root / "proc" / "self" / "cgroup").write_text(f"0::{USER_SCOPE}\n")
    node = root / "sys" / "fs" / "cgroup" / USER_MANAGER
    node.mkdir(parents=True, exist_ok=True)
    (node / "cgroup.controllers").write_text(f"{controllers}\n")


def _usable(root, slice_name="workbench.slice"):
    return otto_queue.supervisor.scopes_usable(slice_name, root=root)


def test_wake_starts_the_systemd_unit_and_confirms_it_by_the_lock(stubs, monkeypatch):
    monkeypatch.setattr(otto_queue.supervisor, "RESTART_EVERY_S", 5.0)
    bin_dir, calls, pid_file = stubs
    root = otto_queue.paths.queue_root()
    _stub(bin_dir, "systemctl", calls,
          f'"{PY}" -c "{STAND_IN}" "{LIB_DIR}" "{root}" > /dev/null 2>&1 &\necho $! > "{pid_file}"')
    woke = otto_queue.supervisor.wake(root, platform="linux", confirm_s=5.0, poll_s=0.05)
    assert woke.alive
    assert _calls(calls) == ["--user start --no-block otto-queue-worker.service"]


def test_wake_kickstarts_the_launchd_agent(stubs):
    bin_dir, calls, _ = stubs
    _stub(bin_dir, "launchctl", calls, "exit 0")
    otto_queue.supervisor.wake(otto_queue.paths.queue_root(), platform="darwin", confirm_s=0.3, poll_s=0.05)
    assert _calls(calls)[0] == f"kickstart gui/{os.getuid()}/com.otto-workbench.queue"


def test_a_failing_start_is_reported_at_once(stubs):
    bin_dir, calls, _ = stubs
    _stub(bin_dir, "systemctl", calls, 'echo "Unit otto-queue-worker.service not found." >&2\nexit 5')
    woke = otto_queue.supervisor.wake(otto_queue.paths.queue_root(), platform="linux", confirm_s=5.0)
    assert not woke.alive and "not found" in woke.reason
    assert len(_calls(calls)) == 1


@pytest.mark.parametrize(
    "platform, binary, burst",
    [
        ("linux", "systemctl", True),
        ("darwin", "launchctl", False),
    ],
)
def test_a_start_that_brings_up_nothing_is_reissued_then_given_up(stubs, platform, binary, burst):
    bin_dir, calls, _ = stubs
    _stub(bin_dir, binary, calls, "exit 0")
    woke = otto_queue.supervisor.wake(
        otto_queue.paths.queue_root(), platform=platform, confirm_s=2.5, poll_s=0.05)
    assert not woke.alive and "did not start" in woke.reason
    assert ("systemctl --user status" in woke.reason) is burst
    assert len(_calls(calls)) >= 2


def test_a_running_worker_is_not_started_again(stubs):
    bin_dir, calls, _ = stubs
    _stub(bin_dir, "systemctl", calls, "exit 0")
    root = otto_queue.paths.queue_root()
    held = otto_queue.lease.take_worker(root)
    try:
        assert otto_queue.supervisor.wake(root, platform="linux").alive
    finally:
        held.release()
    assert _calls(calls) == []


def test_a_platform_with_no_supervisor_is_not_alive():
    woke = otto_queue.supervisor.wake(otto_queue.paths.queue_root(), platform="sunos5")
    assert not woke.alive and "no worker supervisor" in woke.reason


def test_wake_when_systemctl_is_missing_is_not_started(stubs, monkeypatch):
    bin_dir, _, _ = stubs
    monkeypatch.setenv("PATH", str(bin_dir))
    woke = otto_queue.supervisor.wake(otto_queue.paths.queue_root(), platform="linux", confirm_s=0.2)
    assert not woke.alive
    assert "systemctl" in woke.reason


def test_scopes_are_unusable_when_systemd_run_refuses(stubs, tmp_path):
    bin_dir, calls, _ = stubs
    _delegate_memory(tmp_path)
    _stub(bin_dir, "systemd-run", calls, "exit 1")
    assert not _usable(tmp_path)
    assert "--slice=workbench.slice" in _calls(calls)[0]


def test_scopes_usable_when_systemd_run_runs_the_command(stubs, tmp_path):
    bin_dir, calls, _ = stubs
    _delegate_memory(tmp_path)
    _stub(bin_dir, "systemd-run", calls,
          'while [[ $# -gt 0 && "$1" != "--" ]]; do shift; done\nshift\nexec "$@"')
    assert _usable(tmp_path)
    assert "--scope" in _calls(calls)[0]


def test_scopes_usable_without_oom_policy_when_systemd_rejects_it(stubs, tmp_path):
    bin_dir, calls, _ = stubs
    _delegate_memory(tmp_path)
    _stub(bin_dir, "systemd-run", calls,
          'if [[ "$*" == *"--property=OOMPolicy=continue"* ]]; then exit 1; fi\nexit 0')
    assert _usable(tmp_path)
    wrapped = otto_queue.supervisor.scope_argv(
        ["true"], slice_name="workbench.slice", unit="otto-queue-job-1")
    assert not any("OOMPolicy" in part for part in wrapped)
    assert _usable(tmp_path)
    again = otto_queue.supervisor.scope_argv(
        ["true"], slice_name="workbench.slice", unit="otto-queue-job-2")
    assert not any("OOMPolicy" in part for part in again)


def test_scopes_usable_keeps_oom_policy_when_systemd_accepts_it(stubs, tmp_path):
    bin_dir, calls, _ = stubs
    _delegate_memory(tmp_path)
    _stub(bin_dir, "systemd-run", calls, "exit 0")
    assert _usable(tmp_path)
    wrapped = otto_queue.supervisor.scope_argv(
        ["true"], slice_name="workbench.slice", unit="otto-queue-job-1")
    assert "--property=OOMPolicy=continue" in wrapped


def test_scopes_unusable_without_memory_controller(stubs, tmp_path, monkeypatch):
    bin_dir, calls, _ = stubs
    _delegate_memory(tmp_path, controllers="cpu pids")
    _stub(bin_dir, "systemd-run", calls, "exit 0")
    warnings = []
    monkeypatch.setattr(core.log, "warn", warnings.append)
    assert not _usable(tmp_path)
    assert _calls(calls) == []
    assert any("memory" in message for message in warnings)


def test_scopes_usable_when_memory_is_delegated(stubs, tmp_path):
    bin_dir, calls, _ = stubs
    _delegate_memory(tmp_path, controllers="cpu memory pids")
    _stub(bin_dir, "systemd-run", calls, "exit 0")
    assert _usable(tmp_path)
    assert "--scope" in _calls(calls)[0]
