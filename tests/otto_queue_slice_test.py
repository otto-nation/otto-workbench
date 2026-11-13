"""Linux integration: a job runs in the worker's slice, and an over-cap job is OOM-killed while the worker lives.

Skipped wherever `systemd-run --user --scope` cannot place a process in a
memory-capped scope: CI's ubuntu-24.04 has no user bus, and macOS has no
systemd. The slice is a runtime unit under $XDG_RUNTIME_DIR, named without a
dash (systemd reads a dash as nesting), and removed afterwards.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.slots  # noqa: E402
import otto_queue.supervisor  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryClass, EntryKind, EntryState, NewEntry  # noqa: E402

PY = sys.executable
SHIM = REPO_ROOT / "ai" / "bin" / "otto-queue"
CAP = 64 * 1024 * 1024


def _memory_capped_scopes() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("systemd-run") is None:
        return False
    props = [f"--property={p}" for p in otto_queue.supervisor.SCOPE_PROPERTIES]
    probe = subprocess.run(
        ["systemd-run", "--user", "--scope", "--quiet", "--collect", *props, f"--property=MemoryMax={CAP}",
         "--", "sh", "-c", "cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.max"],
        capture_output=True, text=True, timeout=30)
    return probe.returncode == 0 and probe.stdout.strip() == str(CAP)


pytestmark = pytest.mark.skipif(not _memory_capped_scopes(),
                                reason="needs systemd-run --user with a delegated memory controller")


@pytest.fixture
def capped_slice():
    name = f"ottoqueuetest{os.getpid()}.slice"
    unit = Path(os.environ["XDG_RUNTIME_DIR"]) / "systemd" / "user" / name
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"[Slice]\nMemoryMax={CAP}\nMemoryHigh=infinity\nMemorySwapMax=0\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=30)
    yield name
    subprocess.run(["systemctl", "--user", "stop", name], timeout=30)
    unit.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=30)


def _enqueue(argv):
    return Ledger.open().resolve(NewEntry(argv=tuple(argv), cwd="/", kind=EntryKind.JOB),
                                 force=False, pid=None).entry.id


def _await_terminal(entry_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        entry = Ledger.open().get(entry_id)
        if not entry.state.live:
            return entry
        time.sleep(0.1)
    pytest.fail(f"entry {entry_id} never finished")


def test_a_job_runs_in_its_own_scope_under_the_slice(tmp_path, capped_slice):
    out = tmp_path / "cgroup"
    entry_id = _enqueue(("sh", "-c", f"cat /proc/self/cgroup > {out}"))
    subprocess.run([str(SHIM), "worker", "--idle", "1", "--slice", capped_slice], check=True, timeout=120)
    assert Ledger.open().get(entry_id).state is EntryState.DONE
    assert f"/{capped_slice}/otto-queue-{entry_id}-1.scope" in out.read_text()


def test_an_over_cap_job_is_killed_and_the_worker_survives(capped_slice):
    entry_id = _enqueue((PY, "-c", "b = b'x' * (256 * 1024 * 1024)"))
    worker = subprocess.Popen([str(SHIM), "worker", "--idle", "3", "--slice", capped_slice])
    try:
        entry = _await_terminal(entry_id)
        assert worker.poll() is None
    finally:
        worker.wait(timeout=60)
    assert (entry.state, entry.exit_status) == (EntryState.FAILED, 128 + 9)


class _OneHeavy:
    def limit(self, entry_class):
        return 1

    def verdict(self, entry_class, running):
        return type("V", (), {"admit": True, "reason": "room"})()


def _await_running(entry_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        entry = Ledger.open().get(entry_id)
        if entry.state is EntryState.RUNNING:
            return entry
        time.sleep(0.1)
    pytest.fail(f"entry {entry_id} never started")


def test_a_running_scoped_job_holds_its_slot(capped_slice):
    entry_id = _enqueue(("sleep", "5"))
    worker = subprocess.Popen([str(SHIM), "worker", "--idle", "3", "--slice", capped_slice])
    try:
        _await_running(entry_id)
        refused = otto_queue.slots.try_admit(Ledger.open().root, _OneHeavy(), EntryClass.HEAVY)
        assert not refused.admitted
        _await_terminal(entry_id)
        granted = otto_queue.slots.try_admit(Ledger.open().root, _OneHeavy(), EntryClass.HEAVY)
        assert granted.admitted
        granted.slot.release()
    finally:
        worker.wait(timeout=60)
